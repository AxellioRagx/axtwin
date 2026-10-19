"""The jsonl sink: the five artifacts, row keys, streaming, provenance.

The sink CONTRACT + registry live in `aximprint.sinks` now
(`WORK_ORDER_egress_sink_unification.md`, D1) — see `tests/test_sinks.py` for
the registry-contract tests (unknown/duplicate/unnamed name) and the
multi-sink driver tests. This file stays scoped to what is specific to
`JsonlSink`: the five artifacts, row keys, streaming, and the vocabulary
observer's run-level bound.

Synthetic events and hand-built windows only — a real capture carries PII and
never enters a test (`LES-003`).
"""
import json
import subprocess
import sys

import pytest

from aximprint.events import FlowEvent, IdentityEvent, SensorHealthEvent
from aximprint.features import MAX_KEYS
from aximprint.features.jsonl import (
    EDGES, IDENTITY, MANIFEST, NODES, WALLCLOCK_FIELD, WINDOWS,
)
from aximprint.learners import extract_assertions
from aximprint.sinks import get_sink
from aximprint.windowing import WindowManager
from aximprint.windowing.windows import WindowResult

T0 = 1_754_000_000.0
W0 = T0 - (T0 % 3600)  # the hourly window T0 falls in; windows are aligned, T0 is not
META = {"source": "synthetic", "plugin": "zeek", "sink": "jsonl",
        "window_seconds": 3600, "subbuckets": 12}
ARTIFACTS = (NODES, EDGES, WINDOWS, IDENTITY)


def _flow(ts, src="10.0.0.21", dst="10.0.0.5", dport=445, state="SF",
          klass="established"):
    return FlowEvent(ts=ts, src_ip=src, dst_ip=dst, src_port=51000, dst_port=dport,
                     proto="tcp", service="smb", duration=1.0,
                     bytes_out=100, bytes_in=200, pkts_out=2, pkts_in=2,
                     state=state, state_class=klass)


def _windows(n_windows=2):
    """Drive real windowing over synthetic events so the artifacts are real shapes."""
    events = []
    for w in range(n_windows):
        base = T0 + w * 3600
        events += [_flow(base + 10), _flow(base + 20, dst="10.0.0.7", dport=88),
                   _flow(base + 30, src="10.0.0.99", dport=23, state="S0",
                         klass="attempted"),
                   IdentityEvent(ts=base + 5, kind="dhcp", ip="10.0.0.21",
                                 mac="aa:bb:cc:dd:ee:01", hostname="ws01",
                                 lease_seconds=86400.0),
                   SensorHealthEvent(ts=base + 40, kind="capture_loss", value=2.5)]
    wm = WindowManager()
    wins = list(wm.process(iter(events))) + list(wm.finalize())
    assert len(wins) == n_windows, "fixture must produce one window per hour"
    return wins


def _run(tmp_path, wins, meta=META):
    """Open a jsonl sink and drive it exactly the way the CLI driver does.

    `write_window` takes `(win, assertions)` (D2) — `extract_assertions` is
    computed here per window, matching the driver, even though `JsonlSink`
    itself ignores the argument this round (no `assertions.jsonl` yet, a
    recorded finding, not fixed here).
    """
    sink = get_sink("jsonl")
    sink.open(str(tmp_path), meta)
    for win in wins:
        sink.write_window(win, extract_assertions(win))
    return sink.close()


def _lines(tmp_path, name):
    return [json.loads(l) for l in (tmp_path / name).read_text().splitlines()]


# -- the five artifacts (brief §5.4) --------------------------------------------

def test_all_five_artifacts_are_written(tmp_path):
    summary = _run(tmp_path, _windows(2))
    for name in ARTIFACTS + (MANIFEST,):
        assert (tmp_path / name).exists(), f"{name} missing"
    manifest = json.loads((tmp_path / MANIFEST).read_text())
    for name in ARTIFACTS:
        assert manifest["rows"][name] == len(_lines(tmp_path, name)) > 0
    assert manifest["rows"] == summary["rows"]
    assert manifest["windows"] == 2 == len(_lines(tmp_path, WINDOWS))


def test_rows_carry_their_keys(tmp_path):
    """Self-describing and joinable without positional assumptions (brief §11)."""
    _run(tmp_path, _windows(2))
    starts = {W0, W0 + 3600}

    nodes = _lines(tmp_path, NODES)
    assert {n["window_start"] for n in nodes} == starts
    assert all({"entity", "window_start"} <= n.keys() for n in nodes)
    ws01 = next(n for n in nodes if n["entity"] == "10.0.0.21" and n["window_start"] == W0)
    assert ws01["client_ratio"] == 1.0 and ws01["flows_out"] == 2
    assert ws01["state_mix"] == {"established": 2}

    edges = _lines(tmp_path, EDGES)
    assert all({"window_start", "src", "dst", "proto"} <= e.keys() for e in edges)
    assert {(e["src"], e["dst"], e["proto"]) for e in edges if e["window_start"] == W0} == {
        ("10.0.0.21", "10.0.0.5", "smb"), ("10.0.0.21", "10.0.0.7", "smb"),
        ("10.0.0.99", "10.0.0.5", "smb")}

    bindings = _lines(tmp_path, IDENTITY)
    assert all({"window_start", "kind", "ip", "bound_to"} <= b.keys() for b in bindings)
    assert {b["kind"] for b in bindings} == {"dhcp"}

    health = _lines(tmp_path, WINDOWS)
    assert {h["window_start"] for h in health} == starts
    assert all(h["window_seconds"] == 3600 for h in health)


def test_health_row_carries_the_asterisk(tmp_path):
    """A degraded window must be recognizable from windows.jsonl alone (§5.4 #5)."""
    _run(tmp_path, _windows(1))
    (h,) = _lines(tmp_path, WINDOWS)
    assert h["capture_loss_pct_max"] == 2.5
    # 4 identity rows from ONE dhcp event (`Q1`/`Q9`): the row carries a mac AND a host name, so
    # two bindings OPEN, and end-of-stream publishes both again as `final` with their observation
    # extent. Change-based emission means the count tracks TRANSITIONS, not observations — a
    # second identical dhcp row would add nothing here, which is the whole point.
    assert h["entities_active"] == 4 and h["edge_rows"] == 3 and h["identity_rows"] == 4
    # Rows are sorted by (kind, ip, bound_to, change, from_ts), so the two records of one binding
    # sit adjacent with `final` before `opened` — alphabetical, not chronological. Asserted as it
    # actually is rather than as it reads: the guarantee this file makes is byte-stability, and
    # the ordering that delivers it is a sort key, not an event order (`LES-009` #2).
    assert [(r["bound_to"], r["change"]) for r in _lines(tmp_path, IDENTITY)] == [
        ("aa:bb:cc:dd:ee:01", "final"), ("aa:bb:cc:dd:ee:01", "opened"),
        ("ws01", "final"), ("ws01", "opened")]
    # `resolver_ambiguity` is None, and that is the honest answer rather than a weaker one:
    # this fixture's identity event is appended AFTER its flows (it is not ts-sorted), so every
    # flow endpoint in this window was resolved before any host-scoped binding existed. Nothing
    # resolved through identity evidence at all, which is "no evidence", not "evidence of no
    # ambiguity" — v0 reported `0.0` here from a hardcoded return and that was the false clean
    # bill of health `LES-006` names. See `tests/test_entity_resolution.py` for the non-None case.
    assert h["late_events"] == 0 and h["resolver_ambiguity"] is None
    assert h["resolver_anchored_resolutions"] == 0, "the ambiguity rate's own denominator"
    assert h["event_counts"]["FlowEvent"] == 3
    # absent sensor evidence stays None: not an imputed clean window
    assert h["sensor_pkts_dropped_max"] is None


def test_empty_window_still_emits_a_health_row(tmp_path):
    """Nothing to say is still worth saying — a gap must not be inferred."""
    empty = WindowResult(T0, 3600, {}, [], [], {
        "event_counts": {"SensorHealthEvent": 1}, "capture_loss_pct_max": 40.0,
        "sensor_pkts_dropped_max": None, "sensor_pkt_lag_max": None,
        "late_events": 0, "entities_active": 0, "resolver_ambiguity": 0.0})
    _run(tmp_path, [empty])
    assert (tmp_path / NODES).read_text() == ""
    assert (tmp_path / EDGES).read_text() == ""
    (h,) = _lines(tmp_path, WINDOWS)
    assert h["window_start"] == T0 and h["entities_active"] == 0
    assert h["capture_loss_pct_max"] == 40.0, "a 40%-drop window is data with an asterisk"


# -- provenance stamp (brief §10) -----------------------------------------------

def test_manifest_stamps_the_run_and_the_build(tmp_path):
    _run(tmp_path, _windows(2))
    m = json.loads((tmp_path / MANIFEST).read_text())
    assert m["source"] == "synthetic" and m["plugin"] == "zeek"
    assert m["window_seconds"] == 3600 and m["subbuckets"] == 12
    assert len(m["build"]["code_fingerprint"]) == 16
    assert m["build"]["aximprint_version"]
    assert m["event_counts"]["FlowEvent"] == 6, "event totals across the whole run"
    assert set(m[WALLCLOCK_FIELD]) == {"started_at", "finished_at", "elapsed_seconds"}


def test_run_meta_may_not_shadow_a_manifest_key(tmp_path):
    with pytest.raises(ValueError, match="shadow manifest key"):
        get_sink("jsonl").open(str(tmp_path), {**META, "rows": "spoofed"})


def test_two_runs_differ_only_in_wallclock(tmp_path):
    """The determinism invariant at the file boundary (LES-002).

    `scripts/verify_determinism.py` runs this across the real pipeline; here it
    pins the sink's own contribution — sorted keys, key-ordered rows, and
    wall-clock confined to one manifest field.
    """
    a, b = tmp_path / "a", tmp_path / "b"
    _run(a, _windows(2))
    _run(b, _windows(2))
    for name in ARTIFACTS:
        assert (a / name).read_bytes() == (b / name).read_bytes(), f"{name} not byte-stable"
    ma, mb = (json.loads((d / MANIFEST).read_text()) for d in (a, b))
    differing = sorted(k for k in set(ma) | set(mb) if ma.get(k) != mb.get(k))
    assert differing == [WALLCLOCK_FIELD], f"non-wallclock manifest drift: {differing}"


# -- streaming ------------------------------------------------------------------

def test_windows_stream_to_disk_and_no_window_is_retained(tmp_path):
    """A 72-window run must not hold window i while accumulating window i+1."""
    sink = get_sink("jsonl")
    sink.open(str(tmp_path), META)
    seen = []
    for win in _windows(3):
        sink.write_window(win, extract_assertions(win))
        seen.append(len((tmp_path / WINDOWS).read_text().splitlines()))
    assert seen == [1, 2, 3], "each window is on disk before the next is offered"
    retained = {k: v for k, v in vars(sink).items() if isinstance(v, (list, tuple))}
    assert not retained, f"sink accumulates across windows: {sorted(retained)}"
    sink.close()


def test_the_sinks_run_level_state_stays_inside_its_stated_bound(tmp_path):
    """The other half of `sinks.base.WindowSink`'s contract, which the check above cannot see.

    "Holds nothing across windows" was never the whole truth and is no longer the
    claim: the categorical index map is a run-level artifact and necessarily spans
    every window. Scanning `vars(sink)` for lists and tuples could not see it at
    all — `_vocab` is an object holding dicts — so the old assertion read far
    stronger than it was. The contract now says the run-level state is *bounded*,
    so the bound is what gets asserted, and it is asserted against rows chosen to
    blow it: every row mints labels no row has used before.
    """
    def keys_held(sink):
        return {name: len(o.in_map) + len(o.oov)
                for name, o in sink._vocab._fields.items()}

    port = 1024
    sink = get_sink("jsonl")
    sink.open(str(tmp_path), META)
    growth = []
    for w in range(8):
        nodes = {}
        for r in range(100):
            labels = [f"{port + i}/tcp" for i in range(4)]
            port += 4
            nodes[f"h{r:03d}"] = {"proto_mix": {f"svc-{port}": 1.0}, "state_mix": {},
                                  "listen_ports": labels}
        sink.write_window(WindowResult(T0 + w * 3600, 3600, nodes, [], [],
                                       {"event_counts": {}}), [])
        growth.append(keys_held(sink))
    sink.close()

    assert max(growth[-1].values()) > 0, "nothing was measured"
    for name, held in growth[-1].items():
        assert held <= 2 * MAX_KEYS, f"{name} holds {held} keys, over its stated bound"
    # Bounded, not merely sublinear: the last three windows added no key at all,
    # while the rows kept minting them.
    assert growth[-3] == growth[-2] == growth[-1], f"still growing at the cap: {growth}"
    assert growth[0] != growth[-1], "fixture must have reached the cap to prove anything"
    # And the archive says the map is truncated rather than reporting itself whole.
    listen = json.loads((tmp_path / MANIFEST).read_text())["categorical"]["fields"][
        "listen_ports"]
    assert listen["vocabulary_truncated"]["occurrences"] > 0
    assert listen["coverage"] < 1.0


def test_write_before_open_fails_loud():
    with pytest.raises(RuntimeError, match="before open"):
        get_sink("jsonl").write_window(_windows(1)[0], [])


def test_feature_field_colliding_with_a_row_key_is_rejected(tmp_path):
    """A key collision is an upstream bug, not a silent overwrite."""
    win = WindowResult(T0, 3600, {"10.0.0.21": {"entity": "spoofed"}}, [], [],
                       {"event_counts": {}})
    with pytest.raises(ValueError, match="collide with the row key"):
        _run(tmp_path, [win])


# -- CLI verb -------------------------------------------------------------------

def test_cli_features_verb(tmp_path):
    zdir = tmp_path / "zeek"
    zdir.mkdir()
    (zdir / "conn.log").write_text(
        "#separator \\x09\n"
        "#fields\tts\tid.orig_h\tid.orig_p\tid.resp_h\tid.resp_p\tproto\tservice"
        "\tduration\torig_bytes\tresp_bytes\tconn_state\torig_pkts\tresp_pkts\n"
        + "\n".join(f"{T0 + i * 1200:.6f}\t10.0.0.21\t51000\t10.0.0.5\t88\ttcp\tkrb"
                    f"\t0.2\t500\t1500\tSF\t5\t5" for i in range(6)) + "\n")
    (zdir / "dhcp.log").write_text(json.dumps(
        {"ts": T0 + 10, "mac": "aa:bb:cc:dd:ee:01", "assigned_addr": "10.0.0.21",
         "host_name": "ws01", "lease_time": 86400}) + "\n")
    out = tmp_path / "features"

    r = subprocess.run([sys.executable, "-m", "aximprint.cli", "features",
                        "--source", str(zdir), "--out", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for name in ARTIFACTS + (MANIFEST,):
        assert (out / name).exists(), f"{name} missing"
    m = json.loads((out / MANIFEST).read_text())
    assert m["windows"] == 2 and m["sink"] == "jsonl"
    assert m["rows"][WINDOWS] == 2


def test_close_before_open_fails_loud():
    """The run-level observer only exists after `open()`; `close()` says so."""
    with pytest.raises(RuntimeError, match="before open"):
        get_sink("jsonl").close()


# -- abort() (`M4`/`M5`) ---------------------------------------------------------

def test_abort_closes_handles_without_writing_a_manifest(tmp_path):
    """`manifest.json`'s sole meaning is "this run finished" — `abort()` must
    release the same file handles `close()` would, but never write it."""
    sink = get_sink("jsonl")
    sink.open(str(tmp_path), META)
    for win in _windows(1):
        sink.write_window(win, extract_assertions(win))
    handles = list(sink._fh.values())  # captured BEFORE abort(), to prove release
    assert handles and all(not fh.closed for fh in handles), "fixture must start with open handles"
    sink.abort()

    assert not (tmp_path / MANIFEST).exists()
    # The row files ARE on disk (each window flushes before the next, same as
    # a clean run) — only the completion artifact is withheld.
    for name in ARTIFACTS:
        assert (tmp_path / name).exists()
    # The SAME handle objects captured above must now be closed — not merely
    # replaced by a fresh, empty `self._fh`, which the check below alone could
    # not tell apart from a real release.
    assert all(fh.closed for fh in handles)
    assert sink._fh == {}


def test_abort_before_open_is_a_safe_no_op():
    """A fresh, never-opened sink has nothing to release."""
    get_sink("jsonl").abort()  # must not raise


def test_abort_after_a_clean_close_is_a_safe_no_op(tmp_path):
    """`close()` already emptied `self._fh`; a caller aborting a sink it just
    (successfully) closed must not raise on the now-empty handle dict."""
    sink = get_sink("jsonl")
    sink.open(str(tmp_path), META)
    for win in _windows(1):
        sink.write_window(win, extract_assertions(win))
    sink.close()
    sink.abort()  # must not raise
    assert (tmp_path / MANIFEST).exists(), "the earlier clean close is unaffected"
