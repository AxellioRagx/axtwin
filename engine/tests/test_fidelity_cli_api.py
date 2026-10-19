"""S4: the `axrange te scorecard` verb, `GET /api/deploys/{id}/scorecard`,
and the golden-output pin.

Three properties this file exists to hold:

  * **CLI and API produce byte-identical row data** for the same deploy —
    the exact `te inventory`-vs-endpoint divergence `planner/inventory.py`
    records as a past review finding (LES-037). Both call
    `axrange.fidelity.runner.scorecard_for_deploy`.
  * **`--json` emits nothing but JSON on stdout**, on success AND failure
    (LES-083).
  * **The published document's shape cannot drift silently** — a small
    scorecard is asserted byte for byte.
"""
from __future__ import annotations

import json

import pytest

from axrange.cli.main import main
from tests.fidelity_support import (
    CAST_BUNDLES,
    MINI_CAST,
    build_mini_deploy,
)

GOLDEN = (
    __import__("pathlib").Path(__file__).resolve().parent
    / "fixtures" / "scorecards" / "mini-cast.scorecard.json"
)


@pytest.fixture
def deployed(tmp_path):
    store, deploy_id = build_mini_deploy(tmp_path, deploy_id="mini-cast-golden")
    store.close()
    return str(tmp_path / "state.db"), deploy_id


def _run_cli(argv, capsys):
    rc = main(argv)
    out = capsys.readouterr()
    return rc, out.out, out.err


# ---------------------------------------------------------------------------
# The golden pin
# ---------------------------------------------------------------------------
def test_scorecard_document_matches_the_golden_document(deployed, capsys):
    db, deploy_id = deployed
    rc, out, err = _run_cli([
        "te", "scorecard", deploy_id, "--db", db, "--reference", str(MINI_CAST),
        "--no-probe", "--no-write", "--json",
    ], capsys)
    assert rc == 0, err
    doc = json.loads(out)
    # `generated_at` is the only wall-clock field; the bundle path is
    # checkout-relative. Everything else must be reproducible.
    doc["generated_at"] = "2026-09-05T00:00:00Z"
    doc["reference"]["bundle"] = "<BUNDLE-DIR>"
    assert doc == json.loads(GOLDEN.read_text())


def test_golden_pins_the_gate_set_and_provisional_flags():
    doc = json.loads(GOLDEN.read_text())
    assert doc["scorecard_version"] == "1"
    assert doc["gate_set"] == {
        "policy": "category: structural + behavioral (spec §3.3)",
        "gating": [1, 2, 3, 5],
        "provisional": [6, 8, 9, 10],
        "deferred": [4, 6, 7],
        "reported": [9, 11],
    }
    provisional = {r["row"] for r in doc["rows"] if r["provisional"]}
    assert provisional == {6, 8, 9, 10}
    assert [r["row"] for r in doc["rows"]] == list(range(1, 12))
    # Every not-computed row carries a reason -- the shape §4.1 requires.
    for r in doc["rows"]:
        if r["verdict"] == "not-computed":
            assert r["reason"]


# ---------------------------------------------------------------------------
# CLI behavior
# ---------------------------------------------------------------------------
def test_json_stdout_is_pure_json(deployed, capsys):
    db, deploy_id = deployed
    rc, out, _err = _run_cli([
        "te", "scorecard", deploy_id, "--db", db, "--reference", str(MINI_CAST),
        "--no-probe", "--no-write", "--json",
    ], capsys)
    assert rc == 0
    json.loads(out)   # would raise on any stray progress line


def test_json_failure_puts_the_diagnostic_on_stderr(tmp_path, deployed, capsys):
    """LES-083: on failure, the diagnostic goes to stderr and the exit code
    carries it -- never half-JSON on stdout."""
    db, deploy_id = deployed
    rc, out, err = _run_cli([
        "te", "scorecard", deploy_id, "--db", db,
        "--reference", str(tmp_path / "nope"), "--no-probe", "--json",
    ], capsys)
    assert rc == 1
    assert out == ""
    assert "axrange te scorecard" in err


def test_human_output_names_the_verdict_and_the_absent_sidecar(deployed, capsys):
    db, deploy_id = deployed
    rc, out, _err = _run_cli([
        "te", "scorecard", deploy_id, "--db", db, "--reference", str(MINI_CAST),
        "--no-probe", "--no-write",
    ], capsys)
    assert rc == 0
    assert "verdict: INCOMPLETE" in out
    assert "sidecar absent" in out
    assert "provenance sidecar absent" in out
    assert "gate set: gating [1, 2, 3, 5]" in out


def test_exit_code_is_zero_whatever_the_verdict(deployed, capsys):
    """§4.2 / ruled §9.6 (9.4 (c)): the verdict is data, not a process
    failure; CI reading "honestly not golden" as "the tool broke" is the
    trap the default avoids."""
    db, deploy_id = deployed
    rc, _out, _err = _run_cli([
        "te", "scorecard", deploy_id, "--db", db, "--reference", str(MINI_CAST),
        "--no-probe", "--no-write",
    ], capsys)
    assert rc == 0


def test_fail_on_verdict_opts_into_a_gating_exit_code(deployed, capsys):
    db, deploy_id = deployed
    rc, _out, _err = _run_cli([
        "te", "scorecard", deploy_id, "--db", db, "--reference", str(MINI_CAST),
        "--no-probe", "--no-write", "--fail-on-verdict", "--json",
    ], capsys)
    assert rc == 4      # incomplete (row 5 is a gate and was not probed)


def test_writes_the_artifact_beside_the_twin_config(tmp_path, deployed, capsys):
    """§4.2: keyed on `deploy_id`, so a re-certification is a NEW scorecard
    beside the old, not an overwrite."""
    db, deploy_id = deployed
    out_path = tmp_path / "cards" / "card.json"
    rc, _out, err = _run_cli([
        "te", "scorecard", deploy_id, "--db", db, "--reference", str(MINI_CAST),
        "--no-probe", "--out", str(out_path),
    ], capsys)
    assert rc == 0, err
    doc = json.loads(out_path.read_text())
    assert doc["scorecard_version"] == "1"
    assert doc["subject"]["deploy_id"] == deploy_id
    assert any("provenance sidecar" in w for w in doc["warnings"])


def test_default_output_path_is_deploy_keyed():
    from axrange.fidelity import scorecard_output_path

    p = scorecard_output_path(MINI_CAST, "mini-cast", "mini-cast-abc123")
    assert p.name == "mini-cast.scorecard.mini-cast-abc123.json"
    assert p.parent == MINI_CAST


def test_bare_default_resolves_the_latest_te_deploy(deployed, capsys):
    """LES-084: "latest" orders on `updated_at`, a field that advances on
    every transition -- not on create time."""
    db, deploy_id = deployed
    rc, out, err = _run_cli([
        "te", "scorecard", "--db", db, "--reference", str(MINI_CAST),
        "--no-probe", "--no-write", "--json",
    ], capsys)
    assert rc == 0, err
    assert json.loads(out)["subject"]["deploy_id"] == deploy_id


def test_missing_deploy_is_a_refusal_not_a_traceback(tmp_path, capsys):
    from axrange.planner import StateStore

    db = str(tmp_path / "empty.db")
    StateStore(db).close()
    rc, out, err = _run_cli([
        "te", "scorecard", "no-such-deploy", "--db", db, "--no-probe", "--json",
    ], capsys)
    assert rc == 1 and out == "" and "no-such-deploy" in err


# ---------------------------------------------------------------------------
# The probe path, with an injected runner (no cluster, LES-208)
# ---------------------------------------------------------------------------
def test_probe_report_reaches_row_5(tmp_path):
    from axrange.fidelity.runner import QgaProbeRunner, scorecard_for_deploy
    from tests.fidelity_support import FakeGuestAgent

    store, deploy_id = build_mini_deploy(tmp_path, deploy_id="mini-cast-probe")
    try:
        # Every observed listen fact answers -> row 5 passes, and the
        # document stops being `incomplete`.
        ga = FakeGuestAgent(results={
            "web-tier01:80/tcp": ("pass", "HTTP/1.0 200 OK"),
            "web-tier02:80/tcp": ("pass", "HTTP/1.0 200 OK"),
            "web-tier01:443/tcp": ("pass", "TLS 1.3"),
            "web-tier02:443/tcp": ("pass", "TLS 1.3"),
            "db-tier:5432/tcp": ("pass", "TCP connect succeeded"),
        })
        card = scorecard_for_deploy(
            store, deploy_id, reference_dir=str(MINI_CAST),
            twin_configs_dir=[str(CAST_BUNDLES)],
            probe_runner=QgaProbeRunner(node="px1", ga_factory=lambda **_: ga),
            generated_at="2026-09-05T00:00:00Z",
        )
    finally:
        store.close()
    row5 = card.row(5)
    # 5432 has no protocol-aware method, so its `pass` is downgraded to
    # inconclusive and the ROW refuses to pass -- not-computed, never a
    # silent green.
    assert row5.verdict == "not-computed"
    assert row5.measured["passed"] == 2 and row5.measured["inconclusive"] == 1
    assert card.verdict == "incomplete"


def test_include_deferred_runs_row_4_but_never_gates_on_it(tmp_path):
    from axrange.fidelity.runner import QgaProbeRunner, scorecard_for_deploy
    from tests.fidelity_support import FakeGuestAgent

    store, deploy_id = build_mini_deploy(tmp_path, deploy_id="mini-cast-row4")
    try:
        ga = FakeGuestAgent(
            results={}, listens="tcp LISTEN 0 128 0.0.0.0:80 0.0.0.0:*\n",
        )
        card = scorecard_for_deploy(
            store, deploy_id, reference_dir=str(MINI_CAST),
            twin_configs_dir=[str(CAST_BUNDLES)],
            probe_runner=QgaProbeRunner(node="px1", ga_factory=lambda **_: ga),
            include_deferred=True, generated_at="2026-09-05T00:00:00Z",
        )
    finally:
        store.close()
    row4 = card.row(4)
    assert row4.status == "deferred"
    assert row4.verdict == "not-computed"     # reported, never a gate
    # S6: scoped to row-5 TARGET hosts, not the whole roster -- `web-tier01`,
    # `web-tier02` and `db-tier` carry observed listens; `gw` and `sink` do
    # not, so running `ss` on them would buy nothing.
    assert row4.measured["hosts_probed"] == 3
    assert set(row4.measured["listens"]) == {"web-tier01", "web-tier02", "db-tier"}
    assert row4.measured["listens"]["web-tier01"] == ["80/tcp"]


# ---------------------------------------------------------------------------
# API parity
# ---------------------------------------------------------------------------
def test_api_and_cli_row_data_are_byte_identical(deployed, capsys):
    from fastapi.testclient import TestClient

    from axrange.api.app import create_app
    from axrange.api.settings import ApiSettings

    db, deploy_id = deployed
    rc, out, err = _run_cli([
        "te", "scorecard", deploy_id, "--db", db, "--reference", str(MINI_CAST),
        "--no-probe", "--no-write", "--json",
    ], capsys)
    assert rc == 0, err
    cli_doc = json.loads(out)

    app = create_app(ApiSettings(db_path=db))
    resp = TestClient(app).get(
        f"/api/deploys/{deploy_id}/scorecard",
        params={"probe": "false", "reference": str(MINI_CAST)},
    )
    assert resp.status_code == 200
    api_doc = resp.json()

    assert json.dumps(api_doc["rows"], sort_keys=True) == json.dumps(
        cli_doc["rows"], sort_keys=True
    )
    assert api_doc["subject"] == cli_doc["subject"]
    assert api_doc["reference"] == cli_doc["reference"]
    assert api_doc["gate_set"] == cli_doc["gate_set"]
    assert api_doc["verdict"] == cli_doc["verdict"]


def test_api_404s_an_unknown_deploy(deployed):
    from fastapi.testclient import TestClient

    from axrange.api.app import create_app
    from axrange.api.settings import ApiSettings

    db, _deploy_id = deployed
    app = create_app(ApiSettings(db_path=db))
    resp = TestClient(app).get("/api/deploys/nope/scorecard")
    assert resp.status_code == 404


def test_api_400s_a_bad_reference_dir(deployed, tmp_path):
    from fastapi.testclient import TestClient

    from axrange.api.app import create_app
    from axrange.api.settings import ApiSettings

    db, deploy_id = deployed
    app = create_app(ApiSettings(db_path=db))
    resp = TestClient(app).get(
        f"/api/deploys/{deploy_id}/scorecard",
        params={"reference": str(tmp_path / "missing")},
    )
    assert resp.status_code == 400


def test_no_credential_reaches_the_document(deployed, capsys):
    """§6 S4: row 4's probe output can carry hostnames -- check, don't
    assume. Nothing password-shaped may appear anywhere in the artifact."""
    db, deploy_id = deployed
    rc, out, _err = _run_cli([
        "te", "scorecard", deploy_id, "--db", db, "--reference", str(MINI_CAST),
        "--no-probe", "--no-write", "--json",
    ], capsys)
    assert rc == 0
    blob = out.lower()
    for needle in ("password", "passwd", "token", "ssh-rsa", "private key",
                   "api_token", "secret"):
        assert needle not in blob


# ---------------------------------------------------------------------------
# `not-golden` and exit 3
#
# `test_fail_on_verdict_opts_into_a_gating_exit_code` above only reaches exit
# 4 (`incomplete`, because row 5 was not probed). The exit-3 branch had no
# test at all, and no fixture reached `not-golden` end to end: `mini-cast`'s
# `5432/tcp` listen has no protocol-aware method, so even an all-passing
# probe leaves row 5 `not-computed`. Dropping that ONE fact from a COPY of
# the bundle is what makes the verdict reachable -- which is itself worth
# knowing, since the same property holds for any real twin carrying an
# unmapped port.
# ---------------------------------------------------------------------------
def _protocol_aware_only_bundle(tmp_path):
    """A copy of the whole cast catalog with the one non-protocol-aware
    listen fact removed, so row 5 can compute. Copied, never mutated in
    place: the shipped fixture is a golden-pinned artifact."""
    import shutil

    catalog = tmp_path / "cast-copy"
    shutil.copytree(CAST_BUNDLES, catalog)
    bundle = catalog / "mini-cast"
    topology = bundle / "mini-cast.topology.json"
    doc = json.loads(topology.read_text())
    for node in doc["nodes"]:
        if node.get("listen_ports") == ["5432/tcp"]:
            node["listen_ports"] = []
    topology.write_text(json.dumps(doc, indent=2))
    return bundle


def _answering_agent():
    from tests.fidelity_support import FakeGuestAgent

    return FakeGuestAgent(results={
        "web-tier01:80/tcp": ("pass", "HTTP/1.0 200 OK"),
        "web-tier02:80/tcp": ("pass", "HTTP/1.0 200 OK"),
        "web-tier01:443/tcp": ("pass", "TLS 1.3 established"),
        "web-tier02:443/tcp": ("pass", "TLS 1.3 established"),
    })


def test_fail_on_verdict_exits_3_on_not_golden(tmp_path, capsys, monkeypatch):
    """The other half of the `--fail-on-verdict` contract. The probe reaches
    a FAKE guest agent through the runner's only cluster seam
    (`default_ga_factory`), so this never touches a cluster or an
    adapter.conf (LES-208)."""
    import axrange.fidelity.runner as runner

    bundle = _protocol_aware_only_bundle(tmp_path)
    store, deploy_id = build_mini_deploy(tmp_path, deploy_id="mini-cast-notgolden")
    store.close()
    agent = _answering_agent()
    monkeypatch.setattr(
        runner, "default_ga_factory",
        lambda adapter_config=None: ((lambda **_kw: agent), "px1"),
    )

    argv = [
        "te", "scorecard", deploy_id, "--db", str(tmp_path / "state.db"),
        "--reference", str(bundle), "--no-write", "--json",
    ]
    rc, out, err = _run_cli(argv, capsys)
    doc = json.loads(out)
    assert doc["verdict"] == "not-golden", err
    # Rows 1 and 2 genuinely fail; row 5 now COMPUTES and passes -- which is
    # what distinguishes `not-golden` from `incomplete`.
    assert [doc["rows"][n - 1]["verdict"] for n in (1, 2, 3, 5)] == [
        "fail", "fail", "pass", "pass",
    ]
    # The verdict alone is never a process failure.
    assert rc == 0

    rc, _out, _err = _run_cli(argv + ["--fail-on-verdict"], capsys)
    assert rc == 3


def test_attest_visual_moves_row_11_and_nothing_else(deployed, capsys):
    """§3.2: row 11 is `attested` or `not-attested`, never `pass`, and an
    attestation is recorded, never inferred. It is also `reported`, so it
    cannot move the document verdict."""
    db, deploy_id = deployed
    common = [
        "te", "scorecard", deploy_id, "--db", db, "--reference", str(MINI_CAST),
        "--no-probe", "--no-write", "--json",
    ]
    rc, out, _err = _run_cli(common, capsys)
    plain = json.loads(out)
    assert rc == 0 and plain["rows"][10]["verdict"] == "not-attested"

    rc, out, _err = _run_cli(common + ["--attest-visual", "compared side by side"], capsys)
    attested = json.loads(out)
    assert rc == 0
    row11 = attested["rows"][10]
    assert row11["verdict"] == "attested"
    assert row11["measured"]["attestation"]["note"] == "compared side by side"
    # An attestation is operator-asserted evidence, and says so.
    assert row11["measured"]["attestation"]["evidence_class"] == "asserted"
    # It moves that row and NOTHING else.
    assert attested["verdict"] == plain["verdict"] == "incomplete"
    assert attested["rows"][:10] == plain["rows"][:10]


def test_include_deferred_with_no_probe_warns_instead_of_skipping_silently(
    deployed, capsys,
):
    """LES-089: a request that cannot be honoured says so. Row 4 needs a
    guest, so `--include-deferred --no-probe` must warn, not quietly
    produce a document that looks like row 4 was considered."""
    db, deploy_id = deployed
    rc, out, _err = _run_cli([
        "te", "scorecard", deploy_id, "--db", db, "--reference", str(MINI_CAST),
        "--no-probe", "--no-write", "--json", "--include-deferred",
    ], capsys)
    assert rc == 0
    doc = json.loads(out)
    assert any("--include-deferred was requested with --no-probe" in w
               for w in doc["warnings"])
    assert doc["rows"][3]["verdict"] == "not-computed"


def test_golden_file_is_byte_identical_including_key_order(deployed, capsys):
    """The document is meant to be DIFFED -- `schema.py` publishes every key
    always, for exactly that reason. The parsed comparison above cannot see
    key ORDER or the serializer's shape, so a formatter change would slip
    through it; this pins the literal bytes the CLI writes."""
    db, deploy_id = deployed
    rc, out, err = _run_cli([
        "te", "scorecard", deploy_id, "--db", db, "--reference", str(MINI_CAST),
        "--no-probe", "--no-write", "--json",
    ], capsys)
    assert rc == 0, err
    doc = json.loads(out)
    doc["generated_at"] = "2026-09-05T00:00:00Z"
    doc["reference"]["bundle"] = "<BUNDLE-DIR>"
    # The exact serialization `cmd_te_scorecard` uses to WRITE the artifact.
    assert json.dumps(doc, indent=2, sort_keys=False) + "\n" == GOLDEN.read_text()


def test_a_bare_api_get_never_touches_a_guest(deployed, monkeypatch):
    """Probing is OPT-IN on the API (`probe` defaults to False) precisely
    because the probe writes a temporary script into every twin guest it
    reaches. A bare GET must therefore never construct the cluster seam --
    pinned here, because the default flipping would be silent otherwise: the
    endpoint would still answer 200 with a complete-looking document.

    The CLI's opposite default (probe ON) is covered by
    `test_fail_on_verdict_exits_3_on_not_golden`, which passes no
    `--no-probe` and does reach its fake agent.
    """
    from fastapi.testclient import TestClient

    import axrange.fidelity.runner as runner
    from axrange.api.app import create_app
    from axrange.api.settings import ApiSettings

    calls: list = []

    def _boom(adapter_config=None):
        calls.append(adapter_config)
        raise AssertionError("a bare GET must not build a cluster client")

    monkeypatch.setattr(runner, "default_ga_factory", _boom)

    db, deploy_id = deployed
    client = TestClient(create_app(ApiSettings(db_path=db)))
    # `reference` only pins the bundle; the property under test is the
    # `probe` default, which is absent from the query string on purpose.
    ref = {"reference": str(MINI_CAST)}
    bare = client.get(f"/api/deploys/{deploy_id}/scorecard", params=ref)
    assert bare.status_code == 200, bare.text
    assert calls == []
    row5 = bare.json()["rows"][4]
    assert row5["verdict"] == "not-computed"
    assert "?probe=false" in row5["reason"]

    explicit = client.get(
        f"/api/deploys/{deploy_id}/scorecard", params={**ref, "probe": "false"},
    )
    assert bare.json()["rows"] == explicit.json()["rows"]

# ---------------------------------------------------------------------------
# Typed refusals and best-effort inputs (S2, S8)
# ---------------------------------------------------------------------------
def test_an_adapter_failure_during_the_probe_is_a_refusal_not_a_traceback(
    tmp_path, capsys, monkeypatch,
):
    """S2: an unreachable cluster, a QGA timeout or a bad render must reach
    the CLI as a legible refusal and an exit code -- never a traceback, and
    never an HTTP 500 on the API peer."""
    import axrange.fidelity.runner as runner
    from axrange.planner.executor import AdapterTimeout

    store, deploy_id = build_mini_deploy(tmp_path, deploy_id="mini-cast-s2")
    store.close()

    def _boom(adapter_config=None):
        raise AdapterTimeout("cluster did not answer in 60s")

    monkeypatch.setattr(runner, "default_ga_factory", _boom)
    rc, out, err = _run_cli([
        "te", "scorecard", deploy_id, "--db", str(tmp_path / "state.db"),
        "--reference", str(MINI_CAST), "--no-write", "--json",
    ], capsys)
    assert rc == 1
    assert out == ""                       # LES-083: never half-JSON
    assert "the in-guest probe could not run" in err
    assert "--no-probe" in err             # the refusal names the way forward


def test_the_api_maps_a_probe_failure_to_400_not_500(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import axrange.fidelity.runner as runner
    from axrange.api.app import create_app
    from axrange.api.settings import ApiSettings
    from axrange.planner.executor import AdapterError

    store, deploy_id = build_mini_deploy(tmp_path, deploy_id="mini-cast-s2api")
    store.close()

    def _boom(adapter_config=None):
        raise AdapterError("no route to cluster")

    monkeypatch.setattr(runner, "default_ga_factory", _boom)
    app = create_app(ApiSettings(db_path=str(tmp_path / "state.db")))
    resp = TestClient(app).get(
        f"/api/deploys/{deploy_id}/scorecard",
        params={"probe": "true", "reference": str(MINI_CAST)},
    )
    assert resp.status_code == 400
    assert "probe could not run" in resp.json()["error"]


def test_a_broken_images_root_is_reported_on_stderr_not_swallowed(
    deployed, capsys, tmp_path,
):
    """S8: `--images` is best-effort, but never SILENT -- an operator who
    asked for it would otherwise get `os: null` everywhere and no hint why.
    To stderr, so `--json`'s stdout stays pure JSON (LES-083).

    `load_registry` is deliberately TOLERANT -- an unreadable or absent
    manifests/ directory yields an empty registry rather than an exception,
    so "loaded nothing" is the reachable failure and is exactly as invisible
    as a raise would have been.
    """
    db, deploy_id = deployed
    images = tmp_path / "broken-images"
    (images / "manifests").mkdir(parents=True)
    (images / "manifests" / "bad.yaml").write_text("image: [unclosed\n")
    rc, out, err = _run_cli([
        "te", "scorecard", deploy_id, "--db", db, "--reference", str(MINI_CAST),
        "--images", str(images),
        "--no-probe", "--no-write", "--json",
    ], capsys)
    assert rc == 0
    json.loads(out)                        # stdout is still nothing but JSON
    assert "--images" in err and "os` will be null" in err
    assert "no image manifests found" in err
