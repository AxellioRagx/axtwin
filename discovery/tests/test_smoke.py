"""Smoke: synthetic zeek logs -> ingest -> windows -> store -> synth."""
import json
import subprocess
import sys
from pathlib import Path

CONN_HEADER = "\n".join([
    "#separator \\x09",
    "#fields\tts\tid.orig_h\tid.orig_p\tid.resp_h\tid.resp_p\tproto\tservice"
    "\tduration\torig_bytes\tresp_bytes\tconn_state\torig_pkts\tresp_pkts",
])


def _write_zeek(tmp: Path):
    t0 = 1_754_000_000  # aligned-ish epoch base
    conn_rows = []
    # ws01 -> dc: kerberos, established, spread over 2 hours
    for i in range(6):
        ts = t0 + i * 1200
        conn_rows.append(f"{ts}\t10.0.0.21\t51000\t10.0.0.5\t88\ttcp\tkrb"
                         f"\t0.2\t500\t1500\tSF\t5\t5")
    # ws01 -> file server smb
    conn_rows.append(f"{t0 + 100}\t10.0.0.21\t51010\t10.0.0.7\t445\ttcp\tsmb"
                     f"\t3.0\t20000\t900000\tSF\t40\t700")
    # scanner-shaped: attempted, no reply
    conn_rows.append(f"{t0 + 200}\t10.0.0.99\t40000\t10.0.0.50\t23\ttcp\t-"
                     f"\t0.0\t0\t0\tS0\t1\t0")
    # A Zeek log is written in approximate ts order; the ingest's reorder buffer
    # repairs disorder narrower than its horizon, not a fixture built backwards.
    conn_rows.sort(key=lambda line: float(line.split("\t", 1)[0]))
    (tmp / "conn.log").write_text(CONN_HEADER + "\n" + "\n".join(conn_rows) + "\n")
    dhcp = {"ts": t0 + 10, "mac": "aa:bb:cc:dd:ee:01", "assigned_addr": "10.0.0.21",
            "host_name": "ws01", "lease_time": 86400}
    (tmp / "dhcp.log").write_text(json.dumps(dhcp) + "\n")


def test_pipeline_end_to_end(tmp_path):
    zdir = tmp_path / "zeek"
    zdir.mkdir()
    _write_zeek(zdir)
    store = tmp_path / "imprint.duckdb"

    from aximprint.ingest import get_plugin
    from aximprint.learners import extract_assertions
    from aximprint.store import BeliefStore
    from aximprint.synth.synthesizer import synthesize
    from aximprint.windowing import WindowManager

    wm = WindowManager(window_seconds=3600)
    bs = BeliefStore(str(store))
    results = list(wm.process(get_plugin("zeek").events(str(zdir))))
    results += list(wm.finalize())
    assert len(results) == 2, "6x20min flows must span two hourly windows"
    for win in results:
        bs.write_window(win, extract_assertions(win))

    first = results[0]
    # `10.0.0.21` is resolved, so it appears under BOTH names in this window, and that split is
    # what forward-only resolution costs (`Q1`, D2): the very first flow lands at t0, ten seconds
    # BEFORE the dhcp row that identifies the host, so it is attributed to the bare address — the
    # only honest answer at the instant it was resolved — while every later flow goes to
    # `ent-0001`. Nothing rewrites the earlier attribution; the `same_entity_as` assertion below
    # is what joins them, and the store applies it at snapshot read.
    ws01 = first.nodes["ent-0001"]
    assert ws01["client_ratio"] > 0.9
    assert first.nodes["10.0.0.21"]["flows_out"] == 1, \
        "exactly the one flow that preceded the identifying evidence"
    assert "88/tcp" in first.nodes["10.0.0.5"]["listen_ports"]
    scanner = first.nodes["10.0.0.99"]
    # The scanner's one unanswered attempt, read off the normalized class rather than off a
    # Zeek-spelled scalar (`Q34`, 2026-08-26): `state_mix` accounts for every flow this entity
    # originated, so a missing key is a proven zero and this assertion is the whole picture.
    assert scanner["state_mix"] == {"attempted": 1}
    assert any(b["bound_to"] == "aa:bb:cc:dd:ee:01" for b in first.identity_delta)
    # One dhcp row carries a mac AND a host name, so both name one host by direct observation:
    # one entity, two identifiers, no merge between them.
    assert {b["identifier"] for b in first.identity_delta if b["change"] == "opened"} == \
        {"mac:aa:bb:cc:dd:ee:01", "hostname:ws01"}
    assert [(m["from_entity"], m["to_entity"], m["reason"]) for m in first.entity_merges] == \
        [("10.0.0.21", "ent-0001", "address_anchored")]

    snap = bs.snapshot()
    # The merge is APPLIED here, not at write time: the bare address is gone from the namespace
    # and its one flow has been folded into `ent-0001`'s totals.
    ents = {e for e, *_ in snap["entities"]}
    assert {"ent-0001", "10.0.0.5", "10.0.0.7"} <= ents
    assert "10.0.0.21" not in ents, "the pre-anchor rows must resolve into the entity, not sit " \
                                    "beside it as a second host"
    doc = synthesize(snap)["twin_config_draft"]
    dc = next(n for n in doc["nodes"] if n["entity"] == "10.0.0.5")
    assert dc["services"]["provenance"] == "observed"
    assert any(c["proto"] == "krb" for c in doc["conduits"])
    assert any(c["from"] == "ent-0001" for c in doc["conduits"]), \
        "conduits are relabelled by the same merge as the nodes"
    bs.close()


def test_ingest_disorder_and_absent_evidence_survive_into_the_store(tmp_path):
    """The whole defect class, end to end, as it appears in a persisted artifact.

    A log whose disorder is wider than the reorder horizon is exactly what the
    window layer had to be hardened to receive: the ingest cannot repair it, the
    accumulators must not be corrupted by it, and the fact must reach something
    durable — `_LogStats.unordered` only ever reached stderr (§5.4 #5).
    """
    import json as _json

    from aximprint.ingest import get_plugin
    from aximprint.learners import extract_assertions
    from aximprint.store import BeliefStore
    from aximprint.windowing import WindowManager

    zdir = tmp_path / "zeek"
    zdir.mkdir()
    t0 = 1_754_000_000
    rows = [f"{t0 + off}\t10.0.0.21\t51000\t10.0.0.5\t445\ttcp\tsmb"
            f"\t0.2\t500\t1500\tSF\t5\t5"
            for off in (0, 1000, 1500, 2000, 500)]  # 500 lands 1500s behind newest
    (zdir / "conn.log").write_text(CONN_HEADER + "\n" + "\n".join(rows) + "\n")

    wm = WindowManager(window_seconds=3600)
    wins = list(wm.process(get_plugin("zeek").events(str(zdir)))) + list(wm.finalize())
    assert len(wins) == 1, "fixture must stay inside one window"
    health = wins[0].health
    assert health["ingest_unordered"] == 1, "the ingest audit reached the window"
    assert health["interarrival_skipped"] == 1, "and the accumulator refused the gap"
    assert health["late_events"] == 0, "in-window disorder is not mis-attribution"
    assert health["capture_loss_pct_max"] is None, "no capture_loss.log = no evidence"
    assert all(v >= 0 for v in wins[0].nodes["10.0.0.21"]["interarrival_q"])
    (edge,) = wins[0].edges
    assert edge["first_ts"] <= edge["last_ts"]

    store = tmp_path / "imprint.duckdb"
    bs = BeliefStore(str(store))
    bs.write_window(wins[0], extract_assertions(wins[0]))
    (stored,) = bs.con.execute("SELECT health FROM window_health").fetchone()
    bs.close()
    stored = _json.loads(stored)
    assert stored["ingest_unordered"] == 1
    assert stored["capture_loss_pct_max"] is None, "None round-trips as JSON null"


def test_cli_runs(tmp_path):
    zdir = tmp_path / "zeek"
    zdir.mkdir()
    _write_zeek(zdir)
    store = tmp_path / "s.duckdb"
    r = subprocess.run([sys.executable, "-m", "aximprint.cli", "ingest",
                        "--source", str(zdir), "--store", str(store)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    r = subprocess.run([sys.executable, "-m", "aximprint.cli", "synth",
                        "--store", str(store)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "twin_config_draft" in r.stdout
