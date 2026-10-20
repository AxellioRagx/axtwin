"""Persona os_family vs the host's OS (corp-twin live run 3, 2026-09-28,
docs/WORK_ORDER_corp_twin.md §11).

The run failed 4/83 `drive-persona` ops with `Agent error: Failed to execute
child process (No such file or directory)`: the cast's `cast-corp-workstation-
{heavy,light}-local` personas declared `os_family: linux` but were bound onto
Windows hosts (win11-finance@v4), so the scripted driver launched `sh`/`python3`
over the Windows QGA. The binding is decidable before anything runs; these
tests pin the three places it is now refused -- the live resolve (from the
twin's recorded plan or the manifests), `tfc validate`'s static twin check,
and a repo guard over every shipped twin's traffic profiles.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from axrange.manifest.loader import load_registry
from axrange.o2control.traffic import (
    AssignmentSpec,
    RoleAssignmentSpec,
    StaticHost,
    TrafficPlanError,
    TrafficProfileSpec,
    check_persona_os_static,
    load_traffic_profile,
    persona_os_mismatch,
    resolve_traffic_bindings,
    static_hosts_from_twin_config,
)
from axrange.personas import registry as persona_registry
from axrange.personas.schema import Persona
from axrange.planner import StateStore
from axrange.planner.pass1_emit import _os_family, _parse_image_ref
from axrange.twinconfig.manifest import load_twin_manifest
from tests.test_traffic_tier import TWIN_DEPLOY, _op_row, _seed_twin

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO.parent / "catalog"  # twin content lives at the project root
FIXTURE_IMAGES = REPO / "tests" / "fixtures"   # the root holding images/manifests/

WINDOWS_PERSONA = "corp-workstation"   # catalog, os_family: windows
LINUX_PERSONA = "dmz-web"              # catalog, os_family: linux


def _os_of(manifests):
    return lambda image: _os_family(_parse_image_ref(image)[0], manifests)


# ---------------------------------------------------------------------------
# persona_os_mismatch
# ---------------------------------------------------------------------------
def test_mismatch_message_names_persona_host_and_both_oses():
    p = Persona(id="p-lin", os_family="linux", drive="scripted")
    msg = persona_os_mismatch(p, "ws01", "windows")
    assert msg is not None
    assert "'p-lin'" in msg and "'ws01'" in msg
    assert "os_family='linux'" in msg and "runs 'windows'" in msg


def test_no_mismatch_when_equal_or_host_os_unknown():
    p = Persona(id="p", os_family="windows", drive="scripted")
    assert persona_os_mismatch(p, "h", "windows") is None
    assert persona_os_mismatch(p, "h", "Windows") is None
    # Unknown OS is never evidence of a mismatch.
    assert persona_os_mismatch(p, "h", None) is None


# ---------------------------------------------------------------------------
# Live resolve
# ---------------------------------------------------------------------------
def _hosts_with_os(ws_os: str):
    # clone-vm records the BARE image id (pass1_emit's `_binding_for_node`).
    return [
        {"host_id": "ws01", "role": "corp-workstation", "vmid": 100001, "ip": "10.0.0.21",
         "image_id": "win11-finance" if ws_os == "windows" else "debian-12-cloud"},
        {"host_id": "dc01", "role": "domain-controller", "vmid": 100003, "ip": "10.0.0.5",
         "image_id": "windows-server-2022"},
        {"host_id": "web01", "role": "dmz-web", "vmid": 100004, "ip": "10.0.0.30",
         "image_id": "debian-12-cloud"},
    ]


def _db_with_recorded_os(tmp_path, recorded: dict[str, str]):
    """A seeded twin whose `wait-os-ready` rows record `recorded[host]` --
    what pass1_emit writes at plan time from the image manifest."""
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        _seed_twin(store, hosts=_hosts_with_os("windows"))
        store.insert_ops(TWIN_DEPLOY, [
            _op_row(TWIN_DEPLOY, f"op-osready-{h}", "wait-os-ready", "OP-HOST-080",
                    {"host": h}, inputs={"os_family": fam})
            for h, fam in recorded.items()
        ])
    finally:
        store.close()
    return db


def test_resolve_refuses_linux_persona_on_recorded_windows_host(tmp_path):
    db = _db_with_recorded_os(tmp_path, {"ws01": "windows", "web01": "linux"})
    spec = TrafficProfileSpec(
        target=TWIN_DEPLOY,
        assignments=[AssignmentSpec(node="ws01", persona=LINUX_PERSONA)],
    )
    store = StateStore(db)
    try:
        with pytest.raises(TrafficPlanError) as ei:
            resolve_traffic_bindings(spec, store)
    finally:
        store.close()
    assert ei.value.status == 400
    assert "cannot run on their host's OS" in ei.value.message
    assert "'ws01'" in ei.value.message and "'dmz-web'" in ei.value.message


def test_resolve_accepts_matching_personas(tmp_path):
    db = _db_with_recorded_os(tmp_path, {"ws01": "windows", "web01": "linux"})
    spec = TrafficProfileSpec(
        target=TWIN_DEPLOY,
        assignments=[
            AssignmentSpec(node="ws01", persona=WINDOWS_PERSONA),
            AssignmentSpec(node="web01", persona=LINUX_PERSONA),
        ],
    )
    store = StateStore(db)
    try:
        res = resolve_traffic_bindings(spec, store)
    finally:
        store.close()
    assert sorted(ra.host_id for ra in res.resolved) == ["web01", "ws01"]


def test_resolve_does_not_trust_a_recorded_linux(tmp_path):
    """pass1_emit's fallback for an unresolved image is `linux`, so a recorded
    `linux` may be fabricated -- it must not refuse a Windows persona."""
    db = _db_with_recorded_os(tmp_path, {"ws01": "linux"})
    spec = TrafficProfileSpec(
        target=TWIN_DEPLOY,
        assignments=[AssignmentSpec(node="ws01", persona=WINDOWS_PERSONA)],
    )
    store = StateStore(db)
    try:
        res = resolve_traffic_bindings(spec, store)
    finally:
        store.close()
    assert [ra.host_id for ra in res.resolved] == ["ws01"]


def test_resolve_uses_manifests_when_given(tmp_path):
    """With manifests (the CLI's --images) the inventory's own OS decides --
    including a Windows persona on a Linux host, which the recorded-plan
    fallback cannot catch."""
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        _seed_twin(store, hosts=_hosts_with_os("linux"))
    finally:
        store.close()
    spec = TrafficProfileSpec(
        target=TWIN_DEPLOY,
        assignments=[AssignmentSpec(node="ws01", persona=WINDOWS_PERSONA)],
    )
    store = StateStore(db)
    try:
        # No manifests: nothing recorded, OS unknown -> not refused.
        resolve_traffic_bindings(spec, store)
        with pytest.raises(TrafficPlanError) as ei:
            resolve_traffic_bindings(spec, store, manifests=load_registry(root=FIXTURE_IMAGES))
    finally:
        store.close()
    assert "runs 'linux'" in ei.value.message


def test_resolve_reports_every_mismatch_at_once(tmp_path):
    db = _db_with_recorded_os(tmp_path, {"ws01": "windows", "dc01": "windows"})
    spec = TrafficProfileSpec(
        target=TWIN_DEPLOY,
        assignments=[
            AssignmentSpec(node="ws01", persona=LINUX_PERSONA),
            AssignmentSpec(node="dc01", persona=LINUX_PERSONA),
        ],
    )
    store = StateStore(db)
    try:
        with pytest.raises(TrafficPlanError) as ei:
            resolve_traffic_bindings(spec, store)
    finally:
        store.close()
    assert ei.value.message.startswith("2 persona binding(s)")


# ---------------------------------------------------------------------------
# Static (offline) check
# ---------------------------------------------------------------------------
def test_static_hosts_fan_out_count_and_skip_unsubstituted_images(tmp_path):
    te = tmp_path / "te.yaml"
    te.write_text(
        "twin_config:\n"
        "  id: t\n"
        "  nodes:\n"
        "    - { name: ws, role: corp-workstation, image: win11-finance@v4, count: 2 }\n"
        "    - { name: dc, role: domain-controller, image: windows-server-2022@v1 }\n"
        "    - { name: x, image: '${IMG:debian-12-cloud@v1}' }\n"
    )
    hosts = static_hosts_from_twin_config(te)
    assert hosts == [
        StaticHost("ws01", "corp-workstation", "win11-finance@v4"),
        StaticHost("ws02", "corp-workstation", "win11-finance@v4"),
        StaticHost("dc", "domain-controller", "windows-server-2022@v1"),
        StaticHost("x", None, None),
    ]


_STATIC_HOSTS = [
    StaticHost("ws01", "corp-workstation", "win11-finance@v4"),
    StaticHost("web01", "dmz-web", "debian-12-cloud@v1"),
    StaticHost("odd", None, "mystery-os@v1"),
]


def test_static_check_node_and_role_entries():
    m = load_registry(root=FIXTURE_IMAGES)
    spec = TrafficProfileSpec(assignments=[
        AssignmentSpec(node="ws01", persona=LINUX_PERSONA),
        AssignmentSpec(role="dmz-web", persona=WINDOWS_PERSONA),
        AssignmentSpec(node="gone", persona=LINUX_PERSONA),      # not declared: skipped
        AssignmentSpec(node="ws01", persona="no-such-persona"),  # unknown: skipped
    ])
    errors, _notes = check_persona_os_static(spec, _STATIC_HOSTS, _os_of(m))
    assert len(errors) == 2
    assert "node:ws01" in errors[0] and "runs 'windows'" in errors[0]
    assert "role:dmz-web" in errors[1] and "runs 'linux'" in errors[1]


def test_static_check_applies_roles_node_overrides_and_notes_selectors():
    m = load_registry(root=FIXTURE_IMAGES)
    spec = TrafficProfileSpec(
        roles=[
            RoleAssignmentSpec(node="ws01", role="kiosk"),
            RoleAssignmentSpec(selector={"segment": "lan"}, role="other"),
        ],
        assignments=[AssignmentSpec(role="kiosk", persona=LINUX_PERSONA)],
    )
    errors, notes = check_persona_os_static(spec, _STATIC_HOSTS, _os_of(m))
    assert len(errors) == 1 and "'ws01'" in errors[0]
    assert any("selector" in n for n in notes)


def test_static_check_notes_an_image_without_os():
    m = load_registry(root=FIXTURE_IMAGES)
    spec = TrafficProfileSpec(assignments=[AssignmentSpec(node="odd", persona=LINUX_PERSONA)])
    errors, notes = check_persona_os_static(spec, _STATIC_HOSTS, _os_of(m))
    assert errors == []
    assert any("mystery-os@v1" in n for n in notes)


# ---------------------------------------------------------------------------
# Repo guard: every shipped twin's traffic profiles bind os-compatible personas
# ---------------------------------------------------------------------------
# Known, pre-existing mismatches outside corp-twin (found by this guard when it
# landed, 2026-09-28): both bind the CATALOG `corp-workstation` persona
# (os_family: windows) onto a debian-12-cloud host. GAP-111 (docs/PRODUCT_GAPS.md);
# STRICT -- fixing one makes this test fail until the entry is removed.
_KNOWN_MISMATCHES = {
    ("ics-ot", "default.yaml"): 1,
    ("sme-flat", "default.yaml"): 1,
}


def _twin_profiles():
    for tw in sorted((CATALOG / "twins").iterdir()):
        if (tw / "twin.yaml").is_file() and (tw / "tfc").is_dir():
            for f in sorted((tw / "tfc").glob("*.yaml")):
                yield tw, f


@pytest.fixture
def _twin_personas(monkeypatch):
    def use(twin_dir: Path):
        loaded = load_twin_manifest(twin_dir)
        monkeypatch.setenv(
            "AXRANGE_PERSONAS_DIR", str(loaded.personas_dir) if loaded.personas_dir else "",
        )
        persona_registry.reload_personas()
        return loaded
    yield use
    monkeypatch.delenv("AXRANGE_PERSONAS_DIR", raising=False)
    persona_registry.reload_personas()


@pytest.mark.parametrize(
    "twin_dir,profile", list(_twin_profiles()),
    ids=[f"{t.name}/{f.name}" for t, f in _twin_profiles()],
)
def test_shipped_twin_profiles_bind_os_compatible_personas(twin_dir, profile, _twin_personas):
    loaded = _twin_personas(twin_dir)
    m = load_registry(root=FIXTURE_IMAGES)
    hosts = static_hosts_from_twin_config(loaded.te_path)
    errors, _notes = check_persona_os_static(load_traffic_profile(profile), hosts, _os_of(m))
    assert len(errors) == _KNOWN_MISMATCHES.get((twin_dir.name, profile.name), 0), errors


def test_corp_twin_workstation_personas_are_windows(_twin_personas):
    """The run-3 fix itself, in both twins that ship the cast."""
    for twin in ("corp-twin", "pit-395a61f04e-1780405200"):
        _twin_personas(CATALOG / "twins" / twin)
        for v in ("heavy-local", "light-local", "heavy", "light", "local", ""):
            pid = "cast-corp-workstation" + (f"-{v}" if v else "")
            assert persona_registry.get(pid).os_family == "windows", (twin, pid)


# ---------------------------------------------------------------------------
# CLI: `tfc validate` runs the static check for a profile in a twin folder
# ---------------------------------------------------------------------------
def _mini_twin(tmp_path: Path, persona_os: str) -> Path:
    tw = tmp_path / "twins" / "mini"
    (tw / "tfc").mkdir(parents=True)
    (tw / "personas").mkdir()
    (tw / "twin.yaml").write_text(
        "twin:\n  id: mini\n  containment: uncontained\n  tiers:\n    te:\n      default: te.yaml\n"
        "    tfc:\n      default: tfc/default.yaml\n"
    )
    (tw / "te.yaml").write_text(
        "twin_config:\n  id: mini\n  segments:\n    - { name: lan, cidr: 10.9.0.0/24 }\n"
        "  nodes:\n    - { name: ws, role: ws, image: win11-finance@v4, net: lan }\n"
    )
    (tw / "personas" / "mini-ws.yaml").write_text(
        f"persona:\n  id: mini-ws\n  os_family: {persona_os}\n  drive: scripted\n"
        "  emits:\n    - { proto: https, ports: [443], peer_role: broadcast, "
        "transport: udp, optional: true, cadence: 'cyclic:30s', volume: low }\n"
    )
    (tw / "tfc" / "default.yaml").write_text(
        "traffic-profile:\n  assignments:\n    - { node: ws, persona: mini-ws }\n"
    )
    return tw


@pytest.mark.parametrize("persona_os,rc", [("linux", 1), ("windows", 0)])
def test_cli_tfc_validate_static_os_check(tmp_path, capsys, monkeypatch, persona_os, rc):
    from axrange.cli.main import main

    tw = _mini_twin(tmp_path, persona_os)
    monkeypatch.delenv("AXRANGE_PERSONAS_DIR", raising=False)
    try:
        got = main([
            "tfc", "validate", str(tw / "tfc" / "default.yaml"),
            "--images", str(FIXTURE_IMAGES), "--db", str(tmp_path / "absent.db"),
        ])
    finally:
        os.environ.pop("AXRANGE_PERSONAS_DIR", None)
        persona_registry.reload_personas()
    out = capsys.readouterr()
    assert got == rc, out
    if rc:
        assert "persona 'mini-ws' is os_family='linux'" in out.err
        assert "Result: FAIL" in out.out
    else:
        assert "persona os check vs te.yaml: OK" in out.out


def test_cli_tfc_validate_without_images_warns_and_skips(tmp_path, capsys, monkeypatch):
    from axrange.cli.main import main

    tw = _mini_twin(tmp_path, "linux")
    monkeypatch.delenv("AXRANGE_PERSONAS_DIR", raising=False)
    try:
        got = main([
            "tfc", "validate", str(tw / "tfc" / "default.yaml"),
            "--db", str(tmp_path / "absent.db"),
        ])
    finally:
        os.environ.pop("AXRANGE_PERSONAS_DIR", None)
        persona_registry.reload_personas()
    out = capsys.readouterr().out
    assert got == 0
    assert "persona os check vs the twin's images skipped" in out
