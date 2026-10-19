"""Categorical vocabulary in the feature manifest (`Q6`).

The acceptance criterion is a consumer that reads `manifest.json` and nothing
else: it must be able to build the index map for every categorical field and
encode a row without a prior pass over `nodes.jsonl`. `_encode_from_manifest`
below is that consumer, written against the manifest alone — it is deliberately
not allowed to import a field name or a container shape from the code.

Synthetic events and hand-built windows only — a real capture carries PII and
never enters a test (`LES-003`). The protocol labels and port numbers here are
invented, not observed.
"""
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from aximprint.events import FLOW_STATE_CLASSES, FlowEvent
from aximprint.features import (
    MAX_KEYS, NODE_CATEGORICAL, OOV_KEY, WEIGHT_KINDS, CategoricalField,
    index_map_digest, load_pinned_vocabulary,
)
from aximprint.features.jsonl import MANIFEST, NODES, WALLCLOCK_FIELD
from aximprint.sinks import get_sink
from aximprint.windowing import WindowManager
from aximprint.windowing.windows import WindowResult

T0 = 1_754_000_000.0
W0 = T0 - (T0 % 3600)
META = {"source": "synthetic", "plugin": "zeek", "sink": "jsonl",
        "window_seconds": 3600, "subbuckets": 12}


def _flow(ts, src="10.0.0.21", dst="10.0.0.5", dport=445, service="smb",
          state="SF", klass="established", nbytes=200):
    return FlowEvent(ts=ts, src_ip=src, dst_ip=dst, src_port=51000, dst_port=dport,
                     proto="tcp", service=service, duration=1.0,
                     bytes_out=100, bytes_in=nbytes, pkts_out=2, pkts_in=2,
                     state=state, state_class=klass)


def _windows(services=("smb", "dns"), ports=(445, 53)):
    """Real windowing over synthetic flows, so the node rows are real shapes."""
    events = [_flow(T0 + 10 + i, dst="10.0.0.5", dport=port, service=svc)
              for i, (svc, port) in enumerate(zip(services, ports))]
    wm = WindowManager()
    return list(wm.process(iter(events))) + list(wm.finalize())


def _run(tmp_path, wins, meta=META, vocabulary=None):
    """`write_window` takes `(win, assertions)` now (D2); `[]` — none of this
    file's tests are about assertions, and `JsonlSink` ignores the argument.
    """
    sink = get_sink("jsonl")
    sink.open(str(tmp_path), meta, vocabulary=vocabulary)
    for win in wins:
        sink.write_window(win, [])
    return sink.close()


def _manifest(tmp_path):
    return json.loads((tmp_path / MANIFEST).read_text())


def _node(**fields):
    """A node row carrying only what a test cares about.

    Every DECLARED categorical field appears here, empty, because that is what
    `windows._close` actually emits — a real row always has the key. Tests that
    distinguish "absent" from "empty" (`rows_absent` vs `rows_empty`) depend on this:
    omitting a declared field would make every fixture row read as absent for it, which
    is a fact about the fixture rather than about the partition being tested.
    """
    return {"proto_mix": {}, "listen_ports": [], "connect_ports": [], "state_mix": {},
            "segments": [], **fields}


def _window(nodes):
    return WindowResult(T0, 3600, nodes, [], [], {"event_counts": {}})


# -- the acceptance criterion: encode from the manifest alone -------------------

def _encode_from_manifest(manifest: dict, row: dict) -> dict:
    """A consumer that has read `manifest.json` and no row file.

    Knows nothing but the manifest: which fields are categorical, what container
    each uses, the column for each key, and the reserved out-of-vocabulary
    column. Returns {field: dense vector}.
    """
    out = {}
    for name, spec in manifest["categorical"]["fields"].items():
        columns = {key: i for i, key in enumerate(spec["index"])}
        vector = [0.0] * (spec["oov_index"] + 1)
        value = row.get(name)
        if spec["container"] == "dict":
            pairs = (value or {}).items()
        else:
            pairs = ((key, 1.0) for key in (value or []))
        for key, weight in pairs:
            vector[columns.get(key, spec["oov_index"])] += float(weight)
        out[name] = vector
    return out


def test_manifest_alone_is_enough_to_encode(tmp_path):
    """The acceptance criterion (work order §4): read the manifest, then stream rows."""
    _run(tmp_path, _windows())
    manifest = _manifest(tmp_path)
    rows = [json.loads(line) for line in (tmp_path / NODES).read_text().splitlines()]

    cat = manifest["categorical"]
    assert cat["artifact"] == NODES, "the manifest says where the map applies"
    assert set(cat["fields"]) == {"proto_mix", "listen_ports", "connect_ports", "state_mix", "segments"}
    # The map describes exactly the rows in the file it names.
    assert all(spec["rows_total"] == manifest["rows"][NODES] == len(rows)
               for spec in cat["fields"].values())

    for row in rows:
        vectors = _encode_from_manifest(manifest, row)
        for name, spec in cat["fields"].items():
            assert len(vectors[name]) == spec["oov_index"] + 1
            # Every key of every row landed in a real column, never the OOV one.
            assert vectors[name][spec["oov_index"]] == 0.0, f"{name} spilled to OOV"
    responder = next(r for r in rows if r["entity"] == "10.0.0.5")
    index = cat["fields"]["listen_ports"]["index"]
    assert _encode_from_manifest(manifest, responder)["listen_ports"][index.index("445/tcp")] == 1.0


def test_manifest_declares_which_fields_are_vocabulary_bearing(tmp_path):
    """A consumer learns closed-vs-open from the manifest, not from two hardcoded names."""
    _run(tmp_path, _windows())
    fields = _manifest(tmp_path)["categorical"]["fields"]
    assert fields["proto_mix"]["vocabulary"] == "observed"
    assert fields["listen_ports"]["vocabulary"] == "observed"
    assert fields["state_mix"]["vocabulary"] == "closed"
    assert fields["proto_mix"]["container"] == "dict"
    assert fields["listen_ports"]["container"] == "list"


def test_closed_field_index_is_the_declared_table_not_the_observed_keys(tmp_path):
    """`state_mix` is the contrast: its columns come from the code, on every capture.

    Declared order, not sorted — appending a class then leaves every existing
    column where it was (DESIGN_BRIEF §11's additive change).
    """
    _run(tmp_path, _windows())
    state = _manifest(tmp_path)["categorical"]["fields"]["state_mix"]
    assert state["index"] == list(FLOW_STATE_CLASSES)
    assert state["index"] != sorted(state["index"]), "declared order, deliberately"
    # Declared-but-unseen members are zero-filled, so a gap is visible as a zero.
    assert state["row_counts"]["unknown"] == 0
    assert state["row_counts"]["established"] > 0


# -- sorted / stable index map --------------------------------------------------

def test_observed_index_is_sorted_and_position_is_the_column(tmp_path):
    win = _window({"a": _node(proto_mix={"zeta": 0.5, "alpha": 0.3, "mid": 0.2})})
    _run(tmp_path, [win])
    proto = _manifest(tmp_path)["categorical"]["fields"]["proto_mix"]
    assert proto["index"] == ["alpha", "mid", "zeta"]
    assert proto["oov_index"] == 3 and proto["oov_key"] == OOV_KEY


def test_index_is_stable_across_runs_regardless_of_row_order(tmp_path):
    """Same keys, different arrival order -> the same map (LES-009)."""
    a = _window({"h1": _node(proto_mix={"zeta": 1.0}), "h2": _node(proto_mix={"alpha": 1.0})})
    b = _window({"h1": _node(proto_mix={"alpha": 1.0}), "h2": _node(proto_mix={"zeta": 1.0})})
    _run(tmp_path / "a", [a])
    _run(tmp_path / "b", [b])
    fa = _manifest(tmp_path / "a")["categorical"]["fields"]["proto_mix"]
    fb = _manifest(tmp_path / "b")["categorical"]["fields"]["proto_mix"]
    assert fa["index"] == fb["index"] == ["alpha", "zeta"]
    assert fa["mass_total"] == fb["mass_total"]


def test_row_counts_are_rows_not_occurrences(tmp_path):
    win = _window({"h1": _node(proto_mix={"alpha": 0.5, "beta": 0.5}),
                   "h2": _node(proto_mix={"alpha": 1.0})})
    _run(tmp_path, [win])
    proto = _manifest(tmp_path)["categorical"]["fields"]["proto_mix"]
    assert proto["row_counts"] == {"alpha": 2, "beta": 1}
    assert proto["rows_total"] == 2 and proto["rows_present"] == 2
    assert proto["mass_total"] == 2.0 and proto["coverage"] == 1.0


def test_an_empty_vocabulary_field_is_representable(tmp_path):
    """A field nothing ever populated: an empty map, an honest None, no crash."""
    _run(tmp_path, [_window({"h1": _node()})])
    proto = _manifest(tmp_path)["categorical"]["fields"]["proto_mix"]
    assert proto["index"] == [] and proto["oov_index"] == 0
    assert proto["rows_present"] == 0 and proto["rows_empty"] == 1
    # No mass to cover is not "covered": None, not 1.0 (LES-006).
    assert proto["coverage"] is None and proto["oov"] == {"rows": 0, "mass": 0.0, "keys": {}}
    assert _encode_from_manifest(_manifest(tmp_path), _node())["proto_mix"] == [0.0]


def test_field_absent_from_a_row_is_distinguished_from_field_present_but_empty(tmp_path):
    """"No evidence" and "evidence of none" are different findings (LES-006)."""
    absent = {"flows_out": 1}                       # no categorical fields at all
    empty = _node()                                 # present, no keys
    _run(tmp_path, [_window({"h1": absent, "h2": empty})])
    proto = _manifest(tmp_path)["categorical"]["fields"]["proto_mix"]
    assert (proto["rows_total"], proto["rows_absent"], proto["rows_empty"]) == (2, 1, 1)


# -- pinning --------------------------------------------------------------------

def _pin(tmp_path, wins, meta=META):
    """Produce a manifest to pin a later run to (capture A)."""
    _run(tmp_path, wins, meta)
    return tmp_path / MANIFEST


def test_pinning_uses_the_prior_index_and_records_what_it_pinned_to(tmp_path):
    pin = _pin(tmp_path / "a", [_window({"h": _node(proto_mix={"alpha": 1.0, "beta": 1.0})})])
    vocab = load_pinned_vocabulary(pin)
    _run(tmp_path / "b", [_window({"h": _node(proto_mix={"alpha": 1.0})})], vocabulary=vocab)

    cat = _manifest(tmp_path / "b")["categorical"]
    assert cat["fields"]["proto_mix"]["vocabulary"] == "pinned"
    assert cat["fields"]["proto_mix"]["index"] == ["alpha", "beta"], "A's map, not B's keys"
    # A pinned key B never saw is zero-filled, not dropped: the column stays.
    assert cat["fields"]["proto_mix"]["row_counts"] == {"alpha": 1, "beta": 0}
    assert cat["pinned_to"]["path"] == str(pin)
    assert len(cat["pinned_to"]["file_sha256"]) == 64
    assert len(cat["pinned_to"]["map_sha256"]) == 64
    assert "proto_mix" in cat["pinned_to"]["fields"]


def test_an_unseen_key_becomes_oov_and_shifts_no_existing_index(tmp_path):
    """The open-world case: B has a key A never saw (work order §3)."""
    pin = _pin(tmp_path / "a", [_window({"h": _node(proto_mix={"alpha": 0.5, "zeta": 0.5})})])
    vocab = load_pinned_vocabulary(pin)
    b = _window({"h": _node(proto_mix={"alpha": 0.25, "middle": 0.5, "zeta": 0.25})})
    _run(tmp_path / "b", [b], vocabulary=vocab)

    proto = _manifest(tmp_path / "b")["categorical"]["fields"]["proto_mix"]
    # "middle" sorts between alpha and zeta: appending or re-sorting would have
    # moved zeta's column. Neither happened.
    assert proto["index"] == ["alpha", "zeta"]
    assert "middle" not in proto["index"]
    assert proto["oov"]["keys"] == {"middle": 1}
    assert proto["oov"]["rows"] == 1 and proto["oov"]["mass"] == 0.5
    assert proto["coverage"] == 0.5, "half this archive's mass is outside its own map"


def test_oov_row_is_counted_once_however_many_unknown_keys_it_carries(tmp_path):
    pin = _pin(tmp_path / "a", [_window({"h": _node(proto_mix={"alpha": 1.0})})])
    b = _window({"h": _node(proto_mix={"alpha": 0.4, "x": 0.3, "y": 0.3})})
    _run(tmp_path / "b", [b], vocabulary=load_pinned_vocabulary(pin))
    proto = _manifest(tmp_path / "b")["categorical"]["fields"]["proto_mix"]
    assert proto["oov"]["rows"] == 1
    assert proto["oov"]["keys"] == {"x": 1, "y": 1}
    assert proto["oov"]["mass"] == pytest.approx(0.6)


def test_encoding_an_unknown_key_lands_in_the_reserved_column(tmp_path):
    """The manifest-only consumer routes an unseen key without renumbering."""
    pin = _pin(tmp_path / "a", [_window({"h": _node(proto_mix={"alpha": 1.0})})])
    _run(tmp_path / "b", [_window({"h": _node(proto_mix={"alpha": 1.0})})],
         vocabulary=load_pinned_vocabulary(pin))
    manifest = _manifest(tmp_path / "b")
    vector = _encode_from_manifest(manifest, _node(proto_mix={"alpha": 0.5, "unseen": 0.5}))
    assert vector["proto_mix"] == [0.5, 0.5], "column 0 = alpha, column 1 = the OOV slot"


def test_pinning_a_closed_field_is_verified_not_obeyed(tmp_path):
    """Our own manifest round-trips as a pin; a disagreeing closed table does not."""
    pin = _pin(tmp_path / "a", _windows())
    cat = _manifest(tmp_path / "a")["categorical"]
    assert cat["fields"]["state_mix"]["index"] == list(FLOW_STATE_CLASSES)
    _run(tmp_path / "b", _windows(), vocabulary=load_pinned_vocabulary(pin))
    assert _manifest(tmp_path / "b")["categorical"]["fields"]["state_mix"]["vocabulary"] == "closed"

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"fields": {"state_mix": {"index": ["established"]}}}))
    with pytest.raises(ValueError, match="closed table and disagrees"):
        load_pinned_vocabulary(bad)


def test_an_append_extended_pin_keeps_every_existing_column(tmp_path):
    """A pin need not be sorted: appending is how a map grows without renumbering."""
    pin = tmp_path / "vocab.json"
    pin.write_text(json.dumps({"fields": {"proto_mix": {"index": ["beta", "alpha", "zeta"]}}}))
    _run(tmp_path / "b", [_window({"h": _node(proto_mix={"alpha": 1.0})})],
         vocabulary=load_pinned_vocabulary(pin))
    proto = _manifest(tmp_path / "b")["categorical"]["fields"]["proto_mix"]
    assert proto["index"] == ["beta", "alpha", "zeta"], "order honored verbatim, never re-sorted"


# -- pin rejection: fail loud, fail closed --------------------------------------

def test_missing_pin_file_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="unreadable"):
        load_pinned_vocabulary(tmp_path / "nope.json")


def test_malformed_pin_is_rejected(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_pinned_vocabulary(bad)


@pytest.mark.parametrize("doc,match", [
    ({"windows": 3}, "no index map"),
    ({"fields": []}, "no index map"),
    ({"fields": {"proto_mix": {}}}, "no 'index' list"),
    ({"fields": {"proto_mix": {"index": ["a", "a"]}}}, "duplicate index entries"),
    ({"fields": {"proto_mix": {"index": [1, 2]}}}, "must be a string"),
    ({"fields": {"proto_mix": {"index": [OOV_KEY]}}}, "reserved"),
    ({"fields": {"made_up": {"index": ["a"]}}}, "unknown categorical field"),
])
def test_pin_shapes_that_are_rejected(tmp_path, doc, match):
    path = tmp_path / "pin.json"
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match=match):
        load_pinned_vocabulary(path)


def test_a_bare_vocabulary_file_and_a_full_manifest_are_both_accepted(tmp_path):
    """`jq .categorical manifest.json > vocab.json` has to work."""
    pin = _pin(tmp_path / "a", _windows())
    full = load_pinned_vocabulary(pin)
    bare = tmp_path / "vocab.json"
    bare.write_text(json.dumps(json.loads(pin.read_text())["categorical"]))
    assert load_pinned_vocabulary(bare).indexes == full.indexes


def test_a_wrong_shaped_categorical_value_is_rejected(tmp_path):
    """An unrecognized shape is rejected, not passed through (CONVENTIONS §3)."""
    with pytest.raises(ValueError, match="must be an object"):
        _run(tmp_path / "a", [_window({"h": _node(proto_mix=["alpha"])})])
    with pytest.raises(ValueError, match="must be an array"):
        _run(tmp_path / "b", [_window({"h": _node(listen_ports={"445/tcp": 1})})])
    with pytest.raises(ValueError, match="non-numeric weight"):
        _run(tmp_path / "c", [_window({"h": _node(proto_mix={"alpha": "lots"})})])
    with pytest.raises(ValueError, match="reserved"):
        _run(tmp_path / "d", [_window({"h": _node(proto_mix={OOV_KEY: 1.0})})])
    # A label list is a set of labels; a repeat would inflate a key's row count.
    with pytest.raises(ValueError, match="duplicate entries"):
        _run(tmp_path / "e", [_window({"h": _node(listen_ports=["445/tcp", "445/tcp"])})])


# -- composite keys: surfaced, not split ----------------------------------------

def test_composite_keys_are_marked_and_the_split_impact_is_reported(tmp_path):
    """Zeek's `service` is a comma-joined set. Report the fragmentation; split nothing."""
    win = _window({"h1": _node(proto_mix={"alpha,beta": 0.75, "gamma": 0.25}),
                   "h2": _node(proto_mix={"beta": 1.0})})
    _run(tmp_path, [win])
    proto = _manifest(tmp_path)["categorical"]["fields"]["proto_mix"]
    comp = proto["composite"]
    assert comp["separator"] == ","
    assert comp["index_keys"] == ["alpha,beta"]
    # 4 keys -> 3 atoms, and `alpha` exists only inside a composite.
    assert proto["index"] == ["alpha,beta", "beta", "gamma"]
    assert comp["index_atoms_if_split"] == ["alpha", "beta", "gamma"]
    assert comp["observed_rows"] == 1 and comp["observed_mass"] == 0.75
    assert comp["observed_mass_fraction"] == 0.375
    # The composite key and its own singleton remain unrelated columns: nothing merged.
    assert proto["row_counts"] == {"alpha,beta": 1, "beta": 1, "gamma": 1}


def test_a_field_with_no_separator_has_no_composite_block(tmp_path):
    _run(tmp_path, _windows())
    fields = _manifest(tmp_path)["categorical"]["fields"]
    assert fields["listen_ports"]["composite"] is None
    assert fields["state_mix"]["composite"] is None


# -- determinism ----------------------------------------------------------------

def test_the_vocabulary_does_not_widen_the_wallclock_exemption(tmp_path):
    """Derived from data, so two runs must agree: the differing key set stays {wallclock}."""
    wins = _windows()
    _run(tmp_path / "a", wins)
    _run(tmp_path / "b", wins)
    ma, mb = (_manifest(tmp_path / d) for d in "ab")
    differing = sorted(k for k in set(ma) | set(mb) if ma.get(k) != mb.get(k))
    assert differing == [WALLCLOCK_FIELD]
    assert ma["categorical"] == mb["categorical"]


# -- CLI ------------------------------------------------------------------------

def _zeek_dir(tmp_path, services=("krb", "dns")):
    zdir = tmp_path / "zeek"
    zdir.mkdir(parents=True)
    rows = "\n".join(
        f"{T0 + i * 1200:.6f}\t10.0.0.21\t51000\t10.0.0.5\t{88 + i}\ttcp\t{svc}"
        f"\t0.2\t500\t1500\tSF\t5\t5"
        for i, svc in enumerate(services))
    (zdir / "conn.log").write_text(
        "#separator \\x09\n"
        "#fields\tts\tid.orig_h\tid.orig_p\tid.resp_h\tid.resp_p\tproto\tservice"
        "\tduration\torig_bytes\tresp_bytes\tconn_state\torig_pkts\tresp_pkts\n"
        + rows + "\n")
    return zdir


def _cli_features(source, out, vocabulary=""):
    argv = [sys.executable, "-m", "aximprint.cli", "features",
            "--source", str(source), "--out", str(out)]
    if vocabulary:
        argv += ["--vocabulary", str(vocabulary)]
    return subprocess.run(argv, capture_output=True, text=True)


def test_cli_pins_capture_b_to_capture_a(tmp_path):
    """Pinning works across captures, end to end through the CLI."""
    a_out, b_out = tmp_path / "a.features", tmp_path / "b.features"
    assert _cli_features(_zeek_dir(tmp_path / "a", ("krb", "dns")), a_out).returncode == 0
    r = _cli_features(_zeek_dir(tmp_path / "b", ("krb", "ldap_tcp")), b_out,
                      vocabulary=a_out / MANIFEST)
    assert r.returncode == 0, r.stderr
    assert "vocabulary pinned to" in r.stdout

    a = _manifest(a_out)["categorical"]["fields"]["proto_mix"]
    b = _manifest(b_out)["categorical"]["fields"]["proto_mix"]
    assert a["vocabulary"] == "observed" and b["vocabulary"] == "pinned"
    assert b["index"] == a["index"], "B encodes against A's map"
    assert "ldap_tcp" not in b["index"] and b["oov"]["keys"].get("ldap_tcp")
    assert b["coverage"] is not None and b["coverage"] < 1.0


def test_cli_rejects_a_malformed_vocabulary_before_writing_anything(tmp_path):
    """Fail closed: no output directory survives a bad pin."""
    pin = tmp_path / "pin.json"
    pin.write_text("{nope")
    out = tmp_path / "features"
    r = _cli_features(_zeek_dir(tmp_path), out, vocabulary=pin)
    assert r.returncode != 0
    assert "not valid JSON" in r.stderr
    assert not out.exists(), "a rejected pin must not leave a half-written archive"


# ═══ independent verification ═══════════════════════════════════════════════════
#
# Everything above states the work order's claims. Everything below tries to break
# them: a consumer that is allowed to know NOTHING but the manifest, the boundaries
# of the row-partition and the OOV counters, the `None`-not-`1.0` discipline at
# every place it is echoed, the cost the observer actually carries across windows,
# and a check that the wall-clock guard would bite if it had to.


# -- the acceptance criterion, strictly -----------------------------------------

#: What a declared container means — and nothing else does. A container the
#: manifest declares and this table does not name is an unrecognized shape: the
#: consumer rejects it rather than reading it as "probably a list". This is the
#: one grounding a manifest cannot remove, so it is made explicit and checked,
#: not left as a silent `else` branch (CONVENTIONS §3).
_CONTAINER_READERS = {
    "dict": lambda v: list(v.items()),
    "list": lambda v: [(k, 1.0) for k in v],
}

#: What a declared weight kind means. Same discipline as `_CONTAINER_READERS`: a
#: kind the manifest declares and this table does not name is unrecognized and is
#: rejected. `container` alone cannot tell a byte proportion from a raw flow
#: count, and a consumer that normalizes the wrong one has silently rescaled a
#: feature (M6).
_WEIGHT_KINDS = {
    "proportion": "already normalized per row; do not re-normalize, do not sum across rows",
    "count": "raw event counts; additive and unnormalized",
    "indicator": "presence; every weight is exactly 1.0",
}

#: Every key a manifest must carry for a consumer to encode and to audit. Absence
#: of any one of them means the manifest is insufficient, which is a failure of
#: the acceptance criterion, not of the consumer.
_REQUIRED_PER_FIELD = ("container", "weight", "vocabulary", "index", "oov_key",
                       "oov_index", "row_counts", "rows_total", "rows_present",
                       "rows_empty", "rows_absent", "mass_total", "coverage",
                       "coverage_rows", "oov", "oov_keys_truncated",
                       "vocabulary_truncated", "composite")


class _ManifestOnlyConsumer:
    """A tensor assembler that has read `manifest.json` and nothing else.

    Stricter than `_encode_from_manifest` above in exactly the ways that one
    could have smuggled in knowledge the manifest does not carry:

      * it learns *which file* the rows live in from `categorical.artifact`
        instead of importing `NODES` from the code;
      * it learns which fields are categorical, and whether each is open, pinned
        or closed, from the manifest — never from a hardcoded name, and it does
        not know that `state_mix` is the closed one;
      * it rejects a container the manifest declares but does not teach it to
        read, rather than defaulting to the list branch;
      * it *reads* where the reserved column sits instead of assuming the tail:
        the vector width comes from `oov_index`, and a manifest that placed the
        reserved column inside the key range would be rejected, not silently
        aliased onto a key's column;
      * it re-derives every count the manifest states and refuses to trust a
        manifest whose own numbers it cannot reproduce from the rows.
    """

    def __init__(self, manifest: dict):
        cat = manifest["categorical"]
        self.artifact = cat["artifact"]
        self.pinned_to = cat["pinned_to"]
        assert isinstance(self.artifact, str) and self.artifact, \
            "the manifest must name the file its map applies to"
        self.fields = {}
        for name, spec in cat["fields"].items():
            missing = [k for k in _REQUIRED_PER_FIELD if k not in spec]
            assert not missing, f"manifest field {name!r} is insufficient: missing {missing}"
            assert spec["container"] in _CONTAINER_READERS, (
                f"manifest declares container {spec['container']!r} for {name!r}; a "
                "consumer cannot encode a shape the manifest never taught it")
            assert spec["weight"] in _WEIGHT_KINDS, (
                f"manifest declares weight {spec['weight']!r} for {name!r}; a consumer "
                "cannot USE a number whose units the manifest never taught it")
            index, oov = spec["index"], spec["oov_index"]
            assert len(set(index)) == len(index), f"{name}: a repeated key has two columns"
            assert spec["oov_key"] not in index, f"{name}: the reserved key is also a column"
            assert oov >= len(index), (
                f"{name}: the reserved column {oov} lands inside the key range "
                f"(0..{len(index) - 1}) — an unknown key would alias onto a known one")
            assert sorted(spec["row_counts"]) == sorted(index), \
                f"{name}: row_counts must cover exactly the index, zero-filled"
            self.fields[name] = {
                "columns": {key: i for i, key in enumerate(index)},
                "read": _CONTAINER_READERS[spec["container"]],
                "oov": oov, "width": oov + 1, "spec": spec,
            }

    def encode(self, row: dict) -> dict:
        out = {}
        for name, f in self.fields.items():
            vector = [0.0] * f["width"]
            if row.get(name) is not None:
                for key, weight in f["read"](row[name]):
                    vector[f["columns"].get(key, f["oov"])] += float(weight)
            out[name] = vector
        return out

    def audit(self, rows: list) -> dict:
        """Re-derive the manifest's own numbers from the rows, and assert they hold.

        An independent second implementation of the counting rule the manifest
        documents — `row_counts` are rows not occurrences, `oov.rows` is rows not
        occurrences, mass is summed weight. If the sink and this disagree, one of
        them is wrong and the archive cannot be encoded from its manifest alone.
        """
        state = {name: {"counts": Counter(), "mass": Counter(), "oov_keys": Counter(),
                        "oov_mass": Counter(), "present": 0, "empty": 0, "absent": 0,
                        "oov_rows": 0, "total": 0} for name in self.fields}
        for row in rows:
            for name, f in self.fields.items():
                s = state[name]
                s["total"] += 1
                if name not in row:
                    s["absent"] += 1
                    continue
                pairs = f["read"](row[name])
                if not pairs:
                    s["empty"] += 1
                    continue
                s["present"] += 1
                saw_oov = False
                for key, weight in pairs:
                    if key in f["columns"]:
                        s["counts"][key] += 1
                        s["mass"][key] += float(weight)
                    else:
                        saw_oov = True
                        s["oov_keys"][key] += 1
                        s["oov_mass"][key] += float(weight)
                s["oov_rows"] += saw_oov
        for name, f in self.fields.items():
            spec, s = f["spec"], state[name]
            where = f"{self.artifact}:{name}"
            assert spec["rows_total"] == s["total"], where
            assert spec["rows_present"] == s["present"], where
            assert spec["rows_empty"] == s["empty"], where
            assert spec["rows_absent"] == s["absent"], where
            assert (s["absent"] + s["empty"] + s["present"]) == s["total"], \
                f"{where}: present/empty/absent do not partition the rows"
            assert spec["row_counts"] == {k: s["counts"].get(k, 0) for k in spec["index"]}, where
            # A key that found no column reaches the reserved column whatever put
            # it there — unknown to a pin, or refused by the cap. The manifest
            # splits the two causes; the consumer only sees the sum, and the two
            # must reconcile.
            spilled = (spec["oov_keys_truncated"]["occurrences"]
                       + spec["vocabulary_truncated"]["occurrences"])
            in_mass, oov_mass = sum(s["mass"].values()), sum(s["oov_mass"].values())
            reserved = spec["oov"]["mass"] + spec["vocabulary_truncated"]["mass"]
            assert reserved == pytest.approx(oov_mass, abs=1e-5), where
            assert spec["mass_total"] == pytest.approx(in_mass + oov_mass, abs=1e-5), where
            if spilled:
                # A truncated map may itemize only part of its tail — but never
                # more than it saw, and never a key that was not there.
                assert set(spec["oov"]["keys"]) <= set(s["oov_keys"]), where
                assert spec["oov"]["rows"] <= s["oov_rows"], where
            else:
                assert spec["oov"]["rows"] == s["oov_rows"], where
                assert spec["oov"]["keys"] == dict(sorted(s["oov_keys"].items())), where
            if in_mass + oov_mass:
                assert spec["coverage"] == pytest.approx(
                    in_mass / (in_mass + oov_mass), abs=1e-5), where
            else:
                assert spec["coverage"] is None, f"{where}: no mass is not full coverage"
            # The row-counted twin of coverage, re-derived independently: it is
            # None only when there were no rows to count, never merely no mass.
            if spec["rows_present"]:
                assert spec["coverage_rows"] == pytest.approx(
                    (spec["rows_present"] - s["oov_rows"]) / spec["rows_present"],
                    abs=1e-5), where
            else:
                assert spec["coverage_rows"] is None, where
        return state

    def read_archive(self, directory) -> list:
        """Stream the rows of the file the MANIFEST named — not one the code named."""
        path = Path(directory) / self.artifact
        return [json.loads(line) for line in path.read_text().splitlines()]


def test_a_manifest_only_consumer_can_encode_and_reproduce_every_number(tmp_path):
    """The acceptance criterion with nothing smuggled in (work order §4, §5).

    Field names, containers, the reserved column, and the file the map applies to
    all come out of the manifest; the consumer then reproduces every statistic the
    manifest states. A manifest that did not carry enough would fail construction.
    """
    _run(tmp_path, _windows(services=("smb", "dns", "krb"), ports=(445, 53, 88)))
    consumer = _ManifestOnlyConsumer(_manifest(tmp_path))
    rows = consumer.read_archive(tmp_path)
    assert rows, "fixture must produce rows for the audit to mean anything"
    consumer.audit(rows)
    # Open vs closed is learned, not assumed: the consumer never names a field.
    kinds = {name: f["spec"]["vocabulary"] for name, f in consumer.fields.items()}
    assert set(kinds.values()) == {"observed", "closed"}
    assert sum(k == "closed" for k in kinds.values()) == 1
    for row in rows:
        for name, vector in consumer.encode(row).items():
            assert len(vector) == consumer.fields[name]["width"]
            assert vector[consumer.fields[name]["oov"]] == 0.0, f"{name} spilled to OOV"


def test_a_manifest_only_consumer_holds_on_a_pinned_archive_that_really_spills(tmp_path):
    """The audit must be exercised where it can fail: real OOV, read off disk."""
    pin = _pin(tmp_path / "a", _windows(services=("smb",), ports=(445,)))
    _run(tmp_path / "b", _windows(services=("smb", "dns"), ports=(445, 53)),
         vocabulary=load_pinned_vocabulary(pin))
    consumer = _ManifestOnlyConsumer(_manifest(tmp_path / "b"))
    state = consumer.audit(consumer.read_archive(tmp_path / "b"))
    assert state["proto_mix"]["oov_rows"] > 0, "the audit would be vacuous without a spill"
    assert consumer.pinned_to is not None and consumer.fields["proto_mix"]["spec"][
        "vocabulary"] == "pinned"
    # An unknown key encodes into the reserved column and moves nothing else.
    known = consumer.fields["proto_mix"]["columns"]
    vector = consumer.encode(_node(proto_mix={**{k: 1.0 for k in known}, "unheard-of": 3.0}))
    assert vector["proto_mix"][consumer.fields["proto_mix"]["oov"]] == 3.0
    assert vector["proto_mix"][:len(known)] == [1.0] * len(known)


def test_the_strict_consumer_rejects_a_manifest_it_cannot_encode_from(tmp_path):
    """Proof the strictness is real: each doctored manifest must be refused."""
    _run(tmp_path, _windows())
    good = _manifest(tmp_path)

    def doctored(**patch):
        m = json.loads(json.dumps(good))
        m["categorical"]["fields"]["proto_mix"].update(patch)
        return m

    with pytest.raises(AssertionError, match="never taught it"):
        _ManifestOnlyConsumer(doctored(container="set"))
    with pytest.raises(AssertionError, match="insufficient"):
        m = json.loads(json.dumps(good))
        del m["categorical"]["fields"]["proto_mix"]["container"]
        _ManifestOnlyConsumer(m)
    with pytest.raises(AssertionError, match="lands inside the key range"):
        _ManifestOnlyConsumer(doctored(index=["a", "b"], oov_index=1,
                                       row_counts={"a": 0, "b": 0}))
    with pytest.raises(AssertionError, match="two columns"):
        _ManifestOnlyConsumer(doctored(index=["a", "a"], oov_index=2,
                                       row_counts={"a": 0}))
    with pytest.raises(AssertionError, match="reserved key is also a column"):
        _ManifestOnlyConsumer(doctored(index=[OOV_KEY], oov_index=1,
                                       row_counts={OOV_KEY: 0}))


def test_the_audit_would_catch_a_manifest_that_miscounted(tmp_path):
    """And proof the audit is not vacuous: a wrong number must be caught."""
    _run(tmp_path, _windows())
    good = _manifest(tmp_path)
    rows = [json.loads(line) for line in (tmp_path / NODES).read_text().splitlines()]
    for patch in ({"rows_present": 999}, {"mass_total": 999.0},
                  {"coverage": 1.0, "mass_total": 0.0}):
        m = json.loads(json.dumps(good))
        m["categorical"]["fields"]["proto_mix"].update(patch)
        # row_counts is keyed on the index, so patching the index alone must also
        # be caught — check the count path separately from the mass path.
        with pytest.raises(AssertionError):
            _ManifestOnlyConsumer(m).audit(rows)
    m = json.loads(json.dumps(good))
    first = m["categorical"]["fields"]["proto_mix"]["index"][0]
    m["categorical"]["fields"]["proto_mix"]["row_counts"][first] += 1
    with pytest.raises(AssertionError):
        _ManifestOnlyConsumer(m).audit(rows)


# -- pin semantics under adversarial input --------------------------------------

def test_a_pin_with_an_empty_index_makes_every_key_out_of_vocabulary(tmp_path):
    """An empty pin is representable and is NOT the same as no pin at all.

    No pin means "observe whatever is here"; an empty pin means "the agreed map
    has no columns", so every key is outside it and coverage is 0.0 — a real
    zero, not the `None` of no evidence.
    """
    pin = tmp_path / "vocab.json"
    pin.write_text(json.dumps({"fields": {"proto_mix": {"index": []}}}))
    _run(tmp_path / "b", [_window({"h": _node(proto_mix={"alpha": 1.0})})],
         vocabulary=load_pinned_vocabulary(pin))
    proto = _manifest(tmp_path / "b")["categorical"]["fields"]["proto_mix"]
    assert proto["vocabulary"] == "pinned" and proto["index"] == []
    assert proto["oov_index"] == 0 and proto["row_counts"] == {}
    assert proto["oov"] == {"rows": 1, "mass": 1.0, "keys": {"alpha": 1}}
    assert proto["coverage"] == 0.0, "0.0 is 'covered nothing'; None is 'nothing to cover'"


def test_a_partial_pin_leaves_the_unnamed_fields_observed(tmp_path):
    """`pinned_to` is set for the run; `vocabulary` is the per-field truth."""
    pin = tmp_path / "vocab.json"
    pin.write_text(json.dumps({"fields": {"proto_mix": {"index": ["alpha"]}}}))
    _run(tmp_path / "b", _windows(), vocabulary=load_pinned_vocabulary(pin))
    cat = _manifest(tmp_path / "b")["categorical"]
    assert cat["pinned_to"]["fields"] == ["proto_mix"]
    assert cat["fields"]["proto_mix"]["vocabulary"] == "pinned"
    assert cat["fields"]["listen_ports"]["vocabulary"] == "observed", \
        "a partial pin must not claim to have pinned a field it never named"


def test_an_append_extension_adds_a_column_and_moves_no_existing_one(tmp_path):
    """DESIGN_BRIEF §11's additive change, at the index map."""
    pin_a = _pin(tmp_path / "a", [_window({"h": _node(proto_mix={"alpha": 1.0,
                                                                 "zeta": 1.0})})])
    base = json.loads(pin_a.read_text())["categorical"]["fields"]["proto_mix"]["index"]
    extended = tmp_path / "extended.json"
    extended.write_text(json.dumps({"fields": {"proto_mix": {"index": base + ["omega"]}}}))
    _run(tmp_path / "b", [_window({"h": _node(proto_mix={"alpha": 1.0})})],
         vocabulary=load_pinned_vocabulary(extended))
    proto = _manifest(tmp_path / "b")["categorical"]["fields"]["proto_mix"]
    assert proto["index"] == base + ["omega"]
    assert proto["index"][:len(base)] == base, "every prior column kept its position"
    assert proto["row_counts"]["omega"] == 0, "a declared-but-unseen key is a visible zero"
    assert proto["oov_index"] == len(base) + 1, "the reserved column moved with the map"


def test_a_closed_table_pin_is_rejected_when_only_the_order_differs(tmp_path):
    """Membership is not the contract — position is. A re-ordered table is a redefinition."""
    reordered = tmp_path / "pin.json"
    reordered.write_text(json.dumps(
        {"fields": {"state_mix": {"index": sorted(FLOW_STATE_CLASSES)}}}))
    assert sorted(FLOW_STATE_CLASSES) != list(FLOW_STATE_CLASSES), "fixture must reorder"
    with pytest.raises(ValueError, match="closed table and disagrees"):
        load_pinned_vocabulary(reordered)


def test_an_unsorted_pin_is_honored_verbatim_and_still_routes_unknowns(tmp_path):
    """Sortedness is how an OBSERVED map is derived, never a property of a pin."""
    pin = tmp_path / "vocab.json"
    pin.write_text(json.dumps({"fields": {"proto_mix": {"index": ["zeta", "alpha"]}}}))
    _run(tmp_path / "b", [_window({"h": _node(proto_mix={"alpha": 0.5, "middle": 0.5})})],
         vocabulary=load_pinned_vocabulary(pin))
    proto = _manifest(tmp_path / "b")["categorical"]["fields"]["proto_mix"]
    assert proto["index"] == ["zeta", "alpha"], "never re-sorted"
    consumer = _ManifestOnlyConsumer(_manifest(tmp_path / "b"))
    assert consumer.encode(_node(proto_mix={"alpha": 1.0}))["proto_mix"] == [0.0, 1.0, 0.0]
    assert proto["oov"]["keys"] == {"middle": 1}


def test_a_pin_that_is_a_directory_is_rejected_like_any_unreadable_pin(tmp_path):
    with pytest.raises(ValueError, match="unreadable"):
        load_pinned_vocabulary(tmp_path)


# -- the row partition and the OOV counters -------------------------------------

def test_present_empty_and_absent_partition_every_row_for_every_field(tmp_path):
    """Mutually exclusive and exhaustive, or `rows_total` means nothing."""
    rows = {"absent": {"flows_out": 1},
            "empty": _node(),
            "present": _node(proto_mix={"alpha": 1.0}, listen_ports=["445/tcp"],
                             state_mix={"established": 2}),
            "half": _node(proto_mix={"beta": 1.0})}      # empty for the other two
    _run(tmp_path, [_window(rows)])
    for spec in _manifest(tmp_path)["categorical"]["fields"].values():
        assert spec["rows_total"] == 4
        assert (spec["rows_absent"] + spec["rows_empty"] + spec["rows_present"]
                == spec["rows_total"])
        assert spec["rows_absent"] == 1


def test_a_row_whose_every_key_is_unknown_is_present_and_wholly_uncovered(tmp_path):
    """The boundary the `rows_present`/OOV split has to get right."""
    pin = tmp_path / "vocab.json"
    pin.write_text(json.dumps({"fields": {"proto_mix": {"index": ["alpha"]}}}))
    _run(tmp_path / "b", [_window({"h": _node(proto_mix={"x": 0.5, "y": 0.5})})],
         vocabulary=load_pinned_vocabulary(pin))
    proto = _manifest(tmp_path / "b")["categorical"]["fields"]["proto_mix"]
    assert (proto["rows_present"], proto["rows_empty"], proto["rows_absent"]) == (1, 0, 0)
    assert proto["row_counts"] == {"alpha": 0}, "the pinned column survives, at zero"
    assert proto["oov"]["rows"] == 1 and proto["oov"]["mass"] == 1.0
    assert proto["coverage"] == 0.0 and proto["mass_total"] == 1.0


def test_oov_rows_and_oov_keys_answer_different_questions(tmp_path):
    """Rows outside the map is not occurrences outside the map — both are stated."""
    pin = tmp_path / "vocab.json"
    pin.write_text(json.dumps({"fields": {"proto_mix": {"index": ["alpha"]}}}))
    win = _window({"h1": _node(proto_mix={"alpha": 0.4, "x": 0.3, "y": 0.3}),
                   "h2": _node(proto_mix={"x": 1.0}),
                   "h3": _node(proto_mix={"alpha": 1.0})})
    _run(tmp_path / "b", [win], vocabulary=load_pinned_vocabulary(pin))
    proto = _manifest(tmp_path / "b")["categorical"]["fields"]["proto_mix"]
    assert proto["oov"]["rows"] == 2, "two of three rows carried an unknown key"
    assert proto["oov"]["keys"] == {"x": 2, "y": 1}, "and x appeared in two of them"
    assert proto["rows_present"] == 3
    assert proto["oov"]["mass"] == pytest.approx(1.6)
    assert proto["coverage"] == pytest.approx(1.4 / 3.0)


# -- `None`, never a number, at every place it is echoed (LES-006) --------------

def test_no_mass_stays_none_through_the_manifest_the_summary_and_the_cli(tmp_path):
    """`coverage` is None when there is nothing to cover, and nothing re-numbers it."""
    summary = _run(tmp_path, [_window({"h": _node(listen_ports=["445/tcp"])})])
    assert summary["vocabulary"]["proto_mix"]["coverage"] is None
    assert summary["vocabulary"]["listen_ports"]["coverage"] == 1.0
    proto = _manifest(tmp_path)["categorical"]["fields"]["proto_mix"]
    assert proto["coverage"] is None
    assert proto["composite"]["observed_mass_fraction"] is None, "same discipline, same answer"
    # And it is `null` in the bytes on disk — not 0, not 1.0, not omitted.
    text = (tmp_path / MANIFEST).read_text()
    assert '"coverage": null' in text and '"observed_mass_fraction": null' in text


def test_the_cli_echo_prints_no_coverage_rather_than_a_number(tmp_path):
    """A field the capture gave no mass must not read as fully covered on stdout."""
    zdir = tmp_path / "zeek"
    zdir.mkdir(parents=True)
    # Flows the sensor summarized with no byte counters: proto_mix is byte-weighted,
    # so it has no mass at all, while listen_ports (one weight per label) does.
    (zdir / "conn.log").write_text(
        "#separator \\x09\n"
        "#fields\tts\tid.orig_h\tid.orig_p\tid.resp_h\tid.resp_p\tproto\tservice"
        "\tduration\torig_bytes\tresp_bytes\tconn_state\torig_pkts\tresp_pkts\n"
        + "\n".join(f"{T0 + i:.6f}\t10.0.0.21\t51000\t10.0.0.5\t445\ttcp\tsmb"
                    f"\t0.2\t0\t0\tSF\t1\t1" for i in range(3)) + "\n")
    out = tmp_path / "features"
    r = _cli_features(zdir, out)
    assert r.returncode == 0, r.stderr
    assert "proto_mix: observed vocabulary, 0 key(s), coverage=n/a" in r.stdout
    assert "coverage=1.0000" in r.stdout, "the field that DID have mass still reports it"
    assert _manifest(out)["categorical"]["fields"]["proto_mix"]["coverage"] is None


# -- the cost the observer carries across windows (LES-010) ---------------------

def _port_rows(n_windows, rows_per_window, new_ports_per_row, base=49152):
    """Node rows minting never-seen-before dynamic ports — the unbounded-tail shape."""
    port = base
    for _ in range(n_windows):
        nodes = {}
        for r in range(rows_per_window):
            labels = []
            for _ in range(new_ports_per_row):
                labels.append(f"{port}/tcp")
                port += 1
            nodes[f"h{r:04d}"] = _node(listen_ports=labels)
        yield _window(nodes)


def _retained(sink) -> int:
    """Distinct keys the sink's vocabulary observer is holding, all fields.

    Two bounded maps per field: the keys that found a column, and the itemized
    out-of-vocabulary tail.
    """
    return sum(len(o.in_map) + len(o.oov) for o in sink._vocab._fields.values())


def test_observer_retention_is_a_function_of_keys_and_not_of_rows(tmp_path):
    """The claim in vocabulary.py: O(vocabulary), not O(rows).

    Ten times the rows over the same key set must cost the same, or the sink is
    accumulating per row and a 72-window capture is not the same shape as a
    one-window one.
    """
    def cost(directory, n_windows):
        sink = get_sink("jsonl")
        sink.open(str(directory), META)
        for w in range(n_windows):
            sink.write_window(_window({f"h{i}": _node(listen_ports=["445/tcp", "53/udp"])
                                       for i in range(20)}), [])
        held = _retained(sink)
        sink.close()
        return held

    one, ten = cost(tmp_path / "few", 1), cost(tmp_path / "many", 10)
    assert one > 0, "nothing was measured — the check would pass on an empty observer"
    assert one == ten


def test_a_pin_bounds_the_index_and_the_cap_bounds_the_rest(tmp_path):
    """The bound `base.py` now promises, measured where it used to fail (M1/M2).

    `listen_ports` keys are minted from the data (`"<port>/<proto>"`), so the
    "vocabulary" the observer is O(...) in is itself a function of the rows: a
    host answering on an ever-changing dynamic port adds a key per connection.
    Pinning does NOT bound that — a pin fixes the index, and every unknown key
    still wanted its own entry in the observer AND in the manifest's `oov.keys`,
    which is the artifact a consumer is told to read first. `MAX_KEYS` is what
    bounds it, and the spill counter is what keeps the bounded map honest.
    """
    pin = tmp_path / "vocab.json"
    pin.write_text(json.dumps({"fields": {"listen_ports": {"index": ["445/tcp"]}}}))
    distinct = 8 * 80    # 8 windows x 20 rows x 4 new ports each
    assert distinct > MAX_KEYS, "fixture must exceed the cap or it proves nothing"
    sink = get_sink("jsonl")
    sink.open(str(tmp_path / "b"), META, vocabulary=load_pinned_vocabulary(pin))
    for win in _port_rows(8, 20, 4):
        sink.write_window(win, [])
    held = _retained(sink)
    sink.close()

    listen = _manifest(tmp_path / "b")["categorical"]["fields"]["listen_ports"]
    assert listen["index"] == ["445/tcp"], "the pin bounds the index"
    assert len(listen["oov"]["keys"]) == MAX_KEYS, "and the cap bounds the manifest"
    assert held <= 2 * MAX_KEYS, "and the observer"
    # Truncation is DISCLOSED, never silent: the tail that did not fit is counted,
    # and the mass it carried is still inside `oov.mass` and `mass_total`.
    spill = listen["oov_keys_truncated"]
    assert spill["cap"] == MAX_KEYS
    assert spill["occurrences"] == distinct - MAX_KEYS
    assert spill["mass"] == pytest.approx(float(distinct - MAX_KEYS))
    assert listen["oov"]["mass"] == pytest.approx(float(distinct)), "no mass vanished"
    assert listen["mass_total"] == pytest.approx(float(distinct))
    assert listen["coverage"] == 0.0


# -- the wall-clock exemption, and proof the guard would bite -------------------

def test_the_composite_block_scopes_keys_to_the_index_and_mass_to_the_data(tmp_path):
    """Two scopes in one block, and the EMITTED NAMES say which is which (S3).

    Under a pin, `index_*` describes the index while `observed_*` describes
    everything seen. A composite key that fell outside the pin therefore
    contributes mass while appearing in no key list, so the block can state "no
    composite keys" and "three quarters of the mass is composite" at once. That
    used to be specified in a docstring and invisible in the manifest a consumer
    is told to read first; now every member carries its scope in its name.
    """
    pin = tmp_path / "vocab.json"
    pin.write_text(json.dumps({"fields": {"proto_mix": {"index": ["gamma"]}}}))
    _run(tmp_path / "b", [_window({"h": _node(proto_mix={"alpha,beta": 0.75,
                                                         "gamma": 0.25})})],
         vocabulary=load_pinned_vocabulary(pin))
    proto = _manifest(tmp_path / "b")["categorical"]["fields"]["proto_mix"]
    comp = proto["composite"]
    assert comp["index_keys"] == [] and comp["index_atoms_if_split"] == ["gamma"]
    assert comp["observed_rows"] == 1 and comp["observed_mass"] == 0.75
    assert comp["observed_mass_fraction"] == 0.75
    # The scoping is legible from the block alone — no member is unscoped.
    assert all(k.startswith(("index_", "observed_"))
               for k in comp if k != "separator"), sorted(comp)
    assert proto["oov"]["keys"] == {"alpha,beta": 1}, "the composite key is only visible here"


def test_the_wallclock_exemption_guard_is_not_vacuous(tmp_path, monkeypatch):
    """`test_the_vocabulary_does_not_widen_the_wallclock_exemption` must be able to fail.

    Inject a second non-reproducible value — nested inside an existing manifest
    key, which is the way one would actually slip in behind the exemption — and
    the differing-key set must widen. If it did not, the guard above would be
    passing because nothing is being compared, not because nothing drifts.
    """
    from aximprint.features import jsonl as sink_module

    # `build_stamp` (public, shared with `StoreSink` via `aximprint.provenance`
    # since WORK_ORDER_egress_sink_unification.md) is imported by name into
    # `jsonl`'s own module namespace (`from ... import build_stamp`), so
    # patching it here — not `aximprint.provenance.build_stamp` — is what
    # actually intercepts the call `close()` makes.
    nonce = iter(range(2))
    real = sink_module.build_stamp
    monkeypatch.setattr(sink_module, "build_stamp",
                        lambda: {**real(), "nonce": next(nonce)})
    wins = _windows()
    _run(tmp_path / "a", wins)
    _run(tmp_path / "b", wins)
    ma, mb = (_manifest(tmp_path / d) for d in "ab")
    differing = sorted(k for k in set(ma) | set(mb) if ma.get(k) != mb.get(k))
    assert differing == ["build", WALLCLOCK_FIELD], (
        "the guard did not notice a second non-reproducible manifest value")


def test_the_categorical_block_is_identical_across_runs_in_separate_processes(tmp_path):
    """Two runs in one process share a hash seed; two processes do not.

    The in-process guard cannot see a dict-iteration dependency, so the index map
    is compared across a real process boundary as well (LES-009).
    """
    a, b = tmp_path / "a.features", tmp_path / "b.features"
    zdir = _zeek_dir(tmp_path, ("krb", "dns"))
    assert _cli_features(zdir, a).returncode == 0
    assert _cli_features(zdir, b).returncode == 0
    assert _manifest(a)["categorical"] == _manifest(b)["categorical"]
    assert (a / NODES).read_bytes() == (b / NODES).read_bytes()


# ═══ fix round: the bound, the digest, the units ════════════════════════════════
#
# One regression per must-fix. The theme is that every claim the artifact makes
# about itself has to be one a reader can act on: a bound that is enforced rather
# than asserted, a digest that answers the question it is named for, and units
# without which a decoded number cannot be used.


# -- M2: the observer is bounded, and says so when the bound bites ---------------

def test_the_observed_index_is_capped_and_the_truncation_is_disclosed(tmp_path):
    """An UNPINNED run is the unbounded case, and the cap is what closes it.

    Without a pin every key is in-map by construction, so the index grew with the
    data: 144 k distinct dynamic-port labels produced a 144 k-column index that
    reported `coverage: 1.0` — honest-looking and useless. Now the index stops at
    the cap and the mass that found no column is subtracted from coverage, so a
    capped map can never report itself complete.
    """
    over = MAX_KEYS + 250
    win = _window({f"h{i:04d}": _node(listen_ports=[f"{1024 + i}/tcp"]) for i in range(over)})
    sink = get_sink("jsonl")
    sink.open(str(tmp_path), META)
    sink.write_window(win, [])
    held = _retained(sink)
    sink.close()

    listen = _manifest(tmp_path)["categorical"]["fields"]["listen_ports"]
    assert len(listen["index"]) == MAX_KEYS, "the index stops at the cap"
    assert listen["oov_index"] == MAX_KEYS
    assert held <= 2 * MAX_KEYS, "and so does the observer"
    cut = listen["vocabulary_truncated"]
    assert cut["cap"] == MAX_KEYS
    assert cut["occurrences"] == over - MAX_KEYS
    assert cut["rows"] == over - MAX_KEYS, "one refused key per row, so one row each"
    assert cut["mass"] == pytest.approx(float(over - MAX_KEYS))
    # The mass did not vanish and it does not count as covered.
    assert listen["mass_total"] == pytest.approx(float(over))
    assert listen["coverage"] == pytest.approx(MAX_KEYS / over)
    assert listen["coverage"] < 1.0, "a capped map reporting full coverage is the bug"
    # `1 - coverage` is exactly what a consumer will find in the reserved column.
    assert listen["oov"]["mass"] + cut["mass"] == pytest.approx(
        listen["mass_total"] * (1 - listen["coverage"]))


def test_a_pin_longer_than_the_cap_is_rejected_rather_than_truncated(tmp_path):
    """Honoring a pin in part would encode against a different map than it names."""
    pin = tmp_path / "vocab.json"
    pin.write_text(json.dumps({"fields": {"proto_mix": {
        "index": [f"svc{i:05d}" for i in range(MAX_KEYS + 1)]}}}))
    with pytest.raises(ValueError, match="over this build's cap"):
        load_pinned_vocabulary(pin)


def test_the_capped_manifest_stays_small_next_to_the_rows_it_describes(tmp_path):
    """The failure this cap exists for: a manifest larger than its own archive."""
    wide = _window({"h": _node(listen_ports=[f"{1024 + i}/tcp" for i in range(5_000)])})
    _run(tmp_path, [wide])
    manifest_bytes = (tmp_path / MANIFEST).stat().st_size
    node_bytes = (tmp_path / NODES).stat().st_size
    assert manifest_bytes < node_bytes, (
        f"manifest {manifest_bytes} B describes {node_bytes} B of rows")
    listen = _manifest(tmp_path)["categorical"]["fields"]["listen_ports"]
    assert listen["vocabulary_truncated"]["occurrences"] == 5_000 - MAX_KEYS


# -- M3: the digest that answers "the same columns?" -----------------------------

def test_two_pins_carrying_the_same_map_agree_on_map_sha256_and_differ_on_file(tmp_path):
    """The one field meant to answer "same columns?" must not fingerprint the clock.

    The documented pin target is a prior `manifest.json`, which carries the
    wall-clock block — so two archives pinned to the IDENTICAL map reported
    different digests and the question could not be answered at all.
    """
    wins = _windows()
    pin_a = _pin(tmp_path / "a", wins)
    pin_b = _pin(tmp_path / "b", wins)          # same map, second wall-clock
    va, vb = load_pinned_vocabulary(pin_a), load_pinned_vocabulary(pin_b)
    assert va.indexes == vb.indexes, "fixture must produce the identical map"
    assert va.file_digest != vb.file_digest, "different files, by wall-clock alone"
    assert va.map_digest == vb.map_digest, "the same columns must digest the same"

    # And a real change of columns must move it.
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"fields": {"proto_mix": {"index": ["alpha", "beta"]}}}))
    assert load_pinned_vocabulary(other).map_digest != va.map_digest


def test_every_archive_carries_the_digest_of_the_map_it_emitted(tmp_path):
    """Pinned or not, two archives can be compared on their columns alone."""
    wins = _windows()
    _run(tmp_path / "a", wins)
    a_map = _manifest(tmp_path / "a")["categorical"]["map_sha256"]
    # A pinned re-run of the same rows must land on the same columns...
    _run(tmp_path / "b", wins, vocabulary=load_pinned_vocabulary(tmp_path / "a" / MANIFEST))
    cat_b = _manifest(tmp_path / "b")["categorical"]
    assert cat_b["map_sha256"] == a_map
    assert cat_b["pinned_to"]["map_sha256"] == a_map, "pinned to exactly those columns"
    # ...and a run that saw different keys must not.
    _run(tmp_path / "c", _windows(services=("zzz",), ports=(9999,)))
    assert _manifest(tmp_path / "c")["categorical"]["map_sha256"] != a_map
    assert a_map == index_map_digest(
        {n: s["index"] for n, s in _manifest(tmp_path / "a")["categorical"]["fields"].items()})


# -- M4: mass is a property of the row set, to the precision published ----------

def test_per_key_mass_is_compensated_not_naively_accumulated():
    """The drift the dropped `sorted()` never addressed, at the unit that owns it."""
    from aximprint.features.vocabulary import _KeyStats

    stats = _KeyStats(MAX_KEYS)
    for w in (1e16, 1.0, -1e16):           # naive left-to-right loses the 1.0
        stats.add("alpha", w)
    assert 1e16 + 1.0 - 1e16 == 0.0, "the naive sum this replaces"
    assert stats.mass() == 1.0


def test_mass_total_does_not_move_when_the_same_rows_arrive_in_another_order(tmp_path):
    """`_mass()`'s claim, checked across rows and within a row (LES-009)."""
    weights = [round(0.1 + i * 1e-7, 9) for i in range(60)]
    fwd = _window({f"h{i:03d}": _node(proto_mix={"alpha": w, "beta": 1.0 - w})
                   for i, w in enumerate(weights)})
    rev = _window({f"h{i:03d}": _node(proto_mix={"beta": 1.0 - w, "alpha": w})
                   for i, w in reversed(list(enumerate(weights)))})
    _run(tmp_path / "fwd", [fwd])
    _run(tmp_path / "rev", [rev])
    a = _manifest(tmp_path / "fwd")["categorical"]["fields"]["proto_mix"]
    b = _manifest(tmp_path / "rev")["categorical"]["fields"]["proto_mix"]
    assert a["mass_total"] == b["mass_total"]
    assert a == b, "the whole block is a property of the row set"


# -- M5: an unexpanded --vocabulary must not produce an unpinned archive ---------

def test_an_empty_vocabulary_flag_fails_loud_instead_of_running_unpinned(tmp_path):
    """`--vocabulary $VOCAB` with `$VOCAB` unset used to exit 0 with `pinned_to: null`."""
    out = tmp_path / "features"
    r = subprocess.run([sys.executable, "-m", "aximprint.cli", "features",
                        "--source", str(_zeek_dir(tmp_path)), "--out", str(out),
                        "--vocabulary", ""], capture_output=True, text=True)
    assert r.returncode != 0, r.stdout
    assert "vocabulary pin unreadable" in r.stderr
    assert not out.exists(), "and it fails closed, before a destination exists"


def test_omitting_the_flag_still_produces_a_deliberate_unpinned_archive(tmp_path):
    """The contrast that makes the failure above meaningful."""
    out = tmp_path / "features"
    assert _cli_features(_zeek_dir(tmp_path), out).returncode == 0
    assert _manifest(out)["categorical"]["pinned_to"] is None


# -- M6: the units of a weight, declared -----------------------------------------

def test_every_categorical_field_declares_what_its_weights_mean(tmp_path):
    """Two `container: "dict"` fields, one normalized per row and one not (M6).

    On the reference capture `state_mix` summed to the run's whole flow count
    while `proto_mix` summed to ~1.0 per row. A manifest-only consumer could not
    tell whether to normalize — which is knowledge the manifest did not carry, so
    the acceptance criterion was not actually met.
    """
    win = _window({"h": _node(proto_mix={"alpha": 0.75, "beta": 0.25},
                              listen_ports=["445/tcp", "53/udp"],
                              state_mix={"established": 40})})
    _run(tmp_path, [win])
    fields = _manifest(tmp_path)["categorical"]["fields"]
    assert fields["proto_mix"]["weight"] == "proportion"
    assert fields["state_mix"]["weight"] == "count"
    assert fields["listen_ports"]["weight"] == "indicator"
    # The declaration is the only thing distinguishing them: both dicts, and the
    # masses are three orders of magnitude apart on the very same row.
    assert fields["proto_mix"]["container"] == fields["state_mix"]["container"] == "dict"
    assert fields["proto_mix"]["mass_total"] == 1.0
    assert fields["state_mix"]["mass_total"] == 40.0
    assert fields["listen_ports"]["mass_total"] == 2.0, "one per label, by declaration"
    assert all(f["weight"] in WEIGHT_KINDS for f in fields.values())


def test_a_field_declared_with_an_unknown_weight_kind_is_rejected():
    """Fail loud at declaration, not at emit: an unusable manifest never ships."""
    with pytest.raises(ValueError, match="weight must be one of"):
        CategoricalField("made_up", "dict", "bytes")
    with pytest.raises(ValueError, match="unknown container"):
        CategoricalField("made_up", "set", "count")


# -- S1: a new categorical node field is noticed, not silently untracked ---------

def test_an_undeclared_categorical_row_field_is_counted_not_ignored(tmp_path):
    """`NODE_CATEGORICAL` duplicates knowledge `windowing` owns; this is the tripwire."""
    rows = {"h1": _node(tls_versions=["TLSv13", "TLSv12"], dns_qtypes={"A": 3.0}),
            "h2": _node(tls_versions=["TLSv13"])}
    _run(tmp_path, [_window(rows)])
    cat = _manifest(tmp_path)["categorical"]
    untracked = cat["untracked_categorical_fields"]
    assert untracked["fields"] == {"dns_qtypes": 1, "tls_versions": 2}
    assert untracked["names_dropped"] == 0
    # The rows themselves were still written, and the new fields are in them —
    # this discloses a gap in the map, it does not reject the data.
    assert _manifest(tmp_path)["rows"][NODES] == 2
    assert set(cat["fields"]) == {"proto_mix", "listen_ports", "connect_ports", "state_mix", "segments"}
    written = [json.loads(line) for line in (tmp_path / NODES).read_text().splitlines()]
    assert all("tls_versions" in r for r in written)


def test_declared_and_structural_row_fields_are_not_reported_as_untracked(tmp_path):
    """The guard must be quiet on a real archive or nobody will read it.

    The per-quantile maps are dicts of string keys and numbers — the same shape a
    vocabulary has — so they are declared structural rather than left to a
    heuristic that would cry on every row.
    """
    _run(tmp_path, _windows())
    cat = _manifest(tmp_path)["categorical"]
    assert cat["untracked_categorical_fields"]["fields"] == {}
    assert NODE_CATEGORICAL.structural, "the declaration is what keeps it quiet"
    rows = [json.loads(line) for line in (tmp_path / NODES).read_text().splitlines()]
    assert any(r.get("flow_size_q") for r in rows), "fixture must carry a quantile map"


# -- S2: `coverage: null` and a real spill must not read the same ---------------

def test_a_zero_weight_spill_is_visible_even_though_mass_coverage_is_none(tmp_path):
    """`coverage` alone said "no evidence" where there was evidence of spill."""
    pin = tmp_path / "vocab.json"
    pin.write_text(json.dumps({"fields": {"proto_mix": {"index": ["alpha"]}}}))
    _run(tmp_path / "b", [_window({"h": _node(proto_mix={"zeta": 0.0})})],
         vocabulary=load_pinned_vocabulary(pin))
    proto = _manifest(tmp_path / "b")["categorical"]["fields"]["proto_mix"]
    assert proto["mass_total"] == 0.0 and proto["coverage"] is None, "no mass to weigh"
    assert proto["oov"]["rows"] == 1, "but a row really did spill"
    assert proto["coverage_rows"] == 0.0, "and the row-counted answer says so"


def test_the_two_coverage_fields_are_null_only_for_their_own_reason(tmp_path):
    """Mass-null means "nothing to weigh"; rows-null means "nothing to count"."""
    _run(tmp_path / "none", [_window({"h": _node()})])
    proto = _manifest(tmp_path / "none")["categorical"]["fields"]["proto_mix"]
    assert proto["rows_present"] == 0
    assert proto["coverage"] is None and proto["coverage_rows"] is None

    _run(tmp_path / "some", [_window({"h": _node(proto_mix={"alpha": 1.0})})])
    proto = _manifest(tmp_path / "some")["categorical"]["fields"]["proto_mix"]
    assert proto["coverage"] == 1.0 and proto["coverage_rows"] == 1.0


# -- S4/S6: one build of the section, and the artifact it belongs to ------------

def test_the_run_summary_is_projected_from_the_manifest_block_not_rebuilt(tmp_path, monkeypatch):
    """Built once, so the terminal line and the archive cannot disagree (S4)."""
    from aximprint.features import vocabulary as vocab_module

    calls = Counter()
    real = vocab_module._FieldObserver.entry

    def counted(self):
        calls[self.spec.name] += 1
        return real(self)

    monkeypatch.setattr(vocab_module._FieldObserver, "entry", counted)
    summary = _run(tmp_path, _windows())
    assert set(calls.values()) == {1}, f"each field's block was rebuilt: {dict(calls)}"

    fields = _manifest(tmp_path)["categorical"]["fields"]
    for name, v in summary["vocabulary"].items():
        assert v["coverage"] == fields[name]["coverage"]
        assert v["keys"] == len(fields[name]["index"])
        assert v["vocabulary"] == fields[name]["vocabulary"]


def test_the_artifact_name_travels_with_the_field_set(tmp_path):
    """A caller must not be able to pair one artifact's fields with another's name (S6)."""
    from aximprint.features import CategoricalSpecs, VocabularyObserver

    assert NODES == NODE_CATEGORICAL.artifact, "one string, not two that can drift"
    section = VocabularyObserver(NODE_CATEGORICAL).manifest_section()
    assert section["artifact"] == NODES
    # A different field set carries a different artifact, and cannot be told otherwise.
    other = CategoricalSpecs("edges.jsonl", (CategoricalField("proto_mix", "dict", "count"),))
    assert VocabularyObserver(other).manifest_section()["artifact"] == "edges.jsonl"
