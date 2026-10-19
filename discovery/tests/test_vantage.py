"""The vantage manifest, and the three tiers that fill it in.

`docs/WORK_ORDER_visibility_and_vantage.md` D1/D2, build step 1. Synthetic fixtures only
(`LES-003`) — the appliance host here is invented, and the point of every test is a *shape*.

The property under all of it: **`None` means unknown and never compares equal to anything.** Two
captures that both failed to record a tap must not look like two captures from the same tap, which
is the false-accept D1 exists to close.
"""
import json

import pytest

from aximprint import cli
from aximprint.ingest.base import IngestPlugin
from aximprint.ingest.zeek import sources as zs
from aximprint.vantage import (
    VANTAGE_MANIFEST, Vantage, VantageError, read_vantage, vantage_id, write_vantage,
)

_RECORD = Vantage(vantage="appliance-1:1-4", appliance="appliance-1", streams="1-4",
                  produced_by="harvest", tool_version="0.0.1")


# -- the identity ----------------------------------------------------------------

def test_the_identity_carries_both_the_appliance_and_the_streams():
    """A link on this appliance IS a stream, so one host capturing 1-4 and the same host
    capturing 5-8 are two vantages. Dropping the streams half would merge them."""
    assert vantage_id("ap-1", "1-4") == "ap-1:1-4"
    assert vantage_id("ap-1", "5-8") != vantage_id("ap-1", "1-4")


def test_no_appliance_yields_none_rather_than_a_synthetic_id():
    """A fabricated identity would compare EQUAL to another fabricated one — exactly the
    false-accept this field exists to close."""
    assert vantage_id(None, "1-4") is None
    assert vantage_id("", None) is None


# -- the file --------------------------------------------------------------------

def test_a_manifest_round_trips(tmp_path):
    write_vantage(tmp_path, _RECORD)
    assert read_vantage(tmp_path) == _RECORD


def test_no_manifest_reads_as_none_not_as_an_error(tmp_path):
    """A log directory of unknown origin is the ordinary case — it is what D2 tier 2 exists
    for — so this must not raise."""
    assert read_vantage(tmp_path) is None


@pytest.mark.parametrize("body,match", [
    ("not json at all", "not valid JSON"),
    ('["a", "list"]', "expected a JSON object"),
    ('{"vantage": null, "appliance": null, "streams": null, "produced_by": "x", "novel": 1}',
     "unrecognized key"),
    ('{"vantage": "v"}', "missing required key"),
])
def test_a_manifest_that_cannot_be_trusted_raises_rather_than_degrading(tmp_path, body, match):
    """"Nobody recorded a tap" and "somebody did and this build cannot read it" are OPPOSITE
    findings. Reading the second as the first files a capture under "unknown" while its identity
    sits on disk one line away."""
    (tmp_path / VANTAGE_MANIFEST).write_text(body)
    with pytest.raises(VantageError, match=match):
        read_vantage(tmp_path)


def test_the_manifest_carries_no_wall_clock_field(tmp_path):
    """It is read by `ingest`, and anything `ingest` reads can reach an output (`LES-002`)."""
    write_vantage(tmp_path, _RECORD)
    keys = set(json.loads((tmp_path / VANTAGE_MANIFEST).read_text()))
    assert not {k for k in keys if "time" in k or "stamp" in k or k == "created_at"}


# -- the transport-aware probe ---------------------------------------------------

def test_a_log_directory_probe_finds_its_own_manifest(tmp_path):
    write_vantage(tmp_path, _RECORD)
    assert zs.source_vantage(str(tmp_path)).vantage == "appliance-1:1-4"


def test_a_single_log_file_probe_reads_its_parents_manifest(tmp_path):
    write_vantage(tmp_path, _RECORD)
    log = tmp_path / "conn.log"
    log.write_text("#fields\tts\n")
    assert zs.source_vantage(str(log)).vantage == "appliance-1:1-4"


def test_a_parquet_archive_reports_unknown_structurally(tmp_path):
    """The archive's converter never wrote a vantage, so this transport cannot report a tap even
    where one exists — `Q30`'s shape, one layer down. Unknown, never "none"."""
    write_vantage(tmp_path, _RECORD)
    assert zs.source_vantage(f"parquet:{tmp_path}") is None


def test_the_plugin_contracts_default_is_unknown():
    """Every plugin inherits "this sensor records no vantage" until it has something to report."""
    assert IngestPlugin.probe_vantage(object(), "/anywhere") is None


def test_the_zeek_plugin_probes_without_reading_a_single_event(tmp_path):
    """The reason this is a separate method rather than another lazily-set attribute: a conflict
    must surface BEFORE a forty-day ingest, not after it."""
    from aximprint.ingest.zeek.plugin import ZeekPlugin

    write_vantage(tmp_path, _RECORD)
    assert ZeekPlugin().probe_vantage(str(tmp_path)).appliance == "appliance-1"


# -- the three tiers, resolved ---------------------------------------------------

class _Plugin:
    """A plugin stub carrying only what `_resolve_vantage` consults."""

    def __init__(self, recorded):
        self._recorded = recorded

    def probe_vantage(self, source):
        return self._recorded


def test_the_manifest_is_authoritative_when_present():
    assert cli._resolve_vantage(_Plugin(_RECORD), "/cap", None) == "appliance-1:1-4"


def test_the_flag_fills_in_when_nothing_recorded_one():
    assert cli._resolve_vantage(_Plugin(None), "/cap", "tap-b") == "tap-b"


def test_neither_is_unknown_and_stays_unknown():
    assert cli._resolve_vantage(_Plugin(None), "/cap", None) is None


def test_an_agreeing_flag_is_accepted():
    assert cli._resolve_vantage(_Plugin(_RECORD), "/cap", "appliance-1:1-4") == "appliance-1:1-4"


def test_a_contradicting_flag_is_refused_not_silently_overridden():
    """One of the two is wrong and nothing here can tell which. Quietly preferring the manifest
    would file the run under a tap the operator did not name, while printing nothing."""
    with pytest.raises(SystemExit, match="contradicts the vantage manifest"):
        cli._resolve_vantage(_Plugin(_RECORD), "/cap", "some-other-tap")


# -- and it reaches the store ----------------------------------------------------

def test_the_run_row_records_the_vantage_and_the_network(tmp_path):
    """`runs` is where a PIT later reads both (`ir/materialize._run_context`)."""
    from aximprint.provenance import build_stamp
    from aximprint.store.belief_store import BeliefStore

    store = BeliefStore(str(tmp_path / "s.duckdb"))
    store.write_run({"source": "/cap/one", "plugin": "zeek", "vantage": "ap-1:1-4",
                     "network": "corp"}, build_stamp(), 1, 0.0, 1.0)
    assert store.con.execute("SELECT vantage, network FROM runs").fetchone() == ("ap-1:1-4", "corp")
    assert store.status()["last_run_vantage"] == "ap-1:1-4"


def test_an_undeclared_vantage_is_null_never_the_source_path(tmp_path):
    """`LES-006`: the source path is not a tap identity, and using it as one is precisely how two
    different taps writing to one directory came to compare as the same view."""
    from aximprint.provenance import build_stamp
    from aximprint.store.belief_store import BeliefStore

    store = BeliefStore(str(tmp_path / "s.duckdb"))
    store.write_run({"source": "/cap/one", "plugin": "zeek"}, build_stamp(), 1, 0.0, 1.0)
    assert store.con.execute("SELECT vantage, network FROM runs").fetchone() == (None, None)
