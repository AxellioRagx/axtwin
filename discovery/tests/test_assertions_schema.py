"""`src/aximprint/schemas/axr-assertions-1.schema.json` — the PUBLISHED vocabulary, checked
against the parser that is actually in charge.

The schema is a statement ABOUT `operator_assertions.parse_assertions`, not a second parser:
nothing in `src/` reads it, axRange vendors a byte-identical copy, and the only thing that keeps
the two from drifting is this file. So there are three kinds of check here and they do different
work:

  1. **The closed sets agree with the constants**, imported rather than retyped. This is the
     half a consumer actually depends on — `OVERRIDE_FIELDS`, `PORT_PROTOS`, `RESOURCE_KEYS`,
     `ZONE_TYPES`, `HOST_KEY_PREFIXES`, `ADD_FIELDS`, `EVIDENCE_CLASSES`, the schema string and
     the `source:` prefix — and a re-typed copy is the duplicated-fact hazard `LES-105` names.
  2. **A corpus**: every document the parser ACCEPTS validates against the schema, and every
     document it REFUSES fails it. `_INEXPRESSIBLE` is the closed, named list of refusals no
     per-instance JSON Schema keyword can reach; each entry carries its reason, and a refusal
     fixture NOT in that list which the schema lets through fails this suite.
  3. **The file is canonical** — `json.dumps(sort_keys=True, indent=2)` plus a trailing newline,
     ASCII-safe — so a hand edit that reformats it is caught here rather than by a byte
     comparison one repo over.

`jsonschema` is not a dependency of this project and deliberately is not being made one
(`CLAUDE.md`: the dependency list is thin and adding to it is the user's call). The corpus
therefore runs through `_validate` below, a small draft-2020-12 subset evaluator, **gated by a
positive check that every keyword the schema actually uses is one it implements** — a checker
that silently ignores what it does not understand would pass everything, which is the shape of
`LES-134`'s "reports itself as passed when it did not run". When `jsonschema` IS importable (a
developer venv, or axRange's), the corpus is additionally run through it and the two must agree.
"""
import json
import re

import pytest

from aximprint.ir.schema import EVIDENCE_CLASSES
from aximprint.operator_assertions import (
    ADD_FIELDS,
    ADD_KINDS,
    ASSERTIONS_EVIDENCE_CLASS,
    ASSERTIONS_SCHEMA,
    HOST_KEY_PREFIXES,
    OVERRIDE_FIELDS,
    PORT_PROTOS,
    RESOURCE_KEYS,
    SCHEMA_FILE,
    SOURCE_PREFIX,
    ZONE_TYPES,
    AssertionsError,
    parse_assertions,
    schema_document,
    schema_text,
)

SCHEMA = schema_document()


# ---------------------------------------------------------------------------------------------
# a draft-2020-12 subset evaluator, and the gate that keeps it honest
# ---------------------------------------------------------------------------------------------

#: Keywords `_validate` implements. Anything the schema uses that is not here or in `_ANNOTATION`
#: stops the suite: a validator that skips a constraint it does not know reports every document
#: as valid, and the corpus below would be measuring nothing.
_HANDLED = frozenset({
    "$ref", "additionalProperties", "allOf", "anyOf", "const", "enum", "if", "items", "maximum",
    "minItems", "minProperties", "minimum", "not", "oneOf", "pattern", "properties",
    "propertyNames", "required", "then", "type",
})

#: Keywords that carry no constraint. `format` is here because draft 2020-12 makes it an
#: annotation by default — which is exactly why `as_of` appears in `_INEXPRESSIBLE`.
_ANNOTATION = frozenset({"$comment", "$defs", "$id", "$schema", "description", "title", "format"})

def _is_type(value, name):
    """One branch per JSON type, spelled out. A table lookup here reads shorter and gets
    `True is an int` wrong, which is the one case the `resources` block turns on."""
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "null":
        return value is None
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    if name == "string":
        return isinstance(value, str)
    raise AssertionError(f"unhandled JSON type {name!r} -- classify it, do not ignore it")


def _validate(schema, value, path="$"):
    """Errors for `value` against `schema`. Empty list means valid."""
    errs = []
    if "$ref" in schema:
        target = schema["$ref"].removeprefix("#/$defs/")
        return _validate(SCHEMA["$defs"][target], value, path)
    if "type" in schema:
        names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(value, n) for n in names):
            errs.append(f"{path}: not of type {names}")
            return errs
    if "const" in schema and value != schema["const"]:
        errs.append(f"{path}: != const {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {value!r} not in enum {schema['enum']}")
    if "pattern" in schema and isinstance(value, str) and not re.search(schema["pattern"], value):
        errs.append(f"{path}: {value!r} does not match pattern")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errs.append(f"{path}: < minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errs.append(f"{path}: > maximum")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errs.append(f"{path}: fewer than minItems")
        if "items" in schema:
            for i, item in enumerate(value):
                errs += _validate(schema["items"], item, f"{path}[{i}]")
    if isinstance(value, dict):
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            errs.append(f"{path}: fewer than minProperties")
        for name in schema.get("required", ()):
            if name not in value:
                errs.append(f"{path}: missing required {name!r}")
        props = schema.get("properties", {})
        for name, sub in value.items():
            if "propertyNames" in schema:
                errs += _validate(schema["propertyNames"], name, f"{path}.<name {name!r}>")
            if name in props:
                errs += _validate(props[name], sub, f"{path}.{name}")
            elif "additionalProperties" in schema:
                extra = schema["additionalProperties"]
                if extra is False:
                    errs.append(f"{path}: additional property {name!r}")
                elif extra is not True:
                    errs += _validate(extra, sub, f"{path}.{name}")
    for branch in schema.get("allOf", ()):
        errs += _validate(branch, value, path)
    if "anyOf" in schema and not any(not _validate(b, value, path) for b in schema["anyOf"]):
        errs.append(f"{path}: matched no anyOf branch")
    if "oneOf" in schema:
        hits = sum(1 for b in schema["oneOf"] if not _validate(b, value, path))
        if hits != 1:
            errs.append(f"{path}: matched {hits} oneOf branches, expected exactly 1")
    if "not" in schema and not _validate(schema["not"], value, path):
        errs.append(f"{path}: matched a `not` schema")
    if "if" in schema and "then" in schema and not _validate(schema["if"], value, path):
        errs += _validate(schema["then"], value, path)
    return errs


def _keywords(node, in_named_map=False):
    """Every KEYWORD in the schema — never a property name, never an `enum` member."""
    out = set()
    if isinstance(node, dict):
        for key, sub in node.items():
            if in_named_map:
                out |= _keywords(sub)
                continue
            out.add(key)
            if key in ("properties", "$defs"):
                out |= _keywords(sub, in_named_map=True)
            elif key in ("oneOf", "allOf", "anyOf"):
                for branch in sub:
                    out |= _keywords(branch)
            elif key in ("items", "then", "if", "additionalProperties", "propertyNames", "not"):
                out |= _keywords(sub)
    return out


def test_the_evaluator_implements_every_keyword_the_schema_actually_uses():
    """The gate on the gate. A subset evaluator that skips an unknown keyword validates
    everything, so the corpus below would pass against a schema that constrains nothing --
    `LES-134`'s "reports itself as passed when it did not run", one layer in."""
    used = _keywords(SCHEMA)
    assert not (used - _HANDLED - _ANNOTATION), (
        f"the schema uses keyword(s) {sorted(used - _HANDLED - _ANNOTATION)} that `_validate` "
        f"neither implements nor declares annotation-only. Implement them or classify them; "
        f"do not let them be silently ignored")
    assert not (_HANDLED & _ANNOTATION)
    # And it must genuinely refuse: an evaluator whose every branch is dead passes the line
    # above too.
    assert _validate(SCHEMA, {"schema": "nope"}), "the evaluator accepted an obviously bad doc"


# ---------------------------------------------------------------------------------------------
# 1. the closed sets, against the constants rather than against a retyped copy
# ---------------------------------------------------------------------------------------------

def test_the_schema_string_is_the_constant_not_a_second_copy_of_it():
    assert SCHEMA["properties"]["schema"]["const"] == ASSERTIONS_SCHEMA
    assert SCHEMA["title"] == ASSERTIONS_SCHEMA


def test_the_document_keys_and_which_are_required_match_the_parser():
    assert set(SCHEMA["properties"]) == {
        "schema", "imprint", "as_of", "source", "add", "override", "suppress"}
    assert SCHEMA["additionalProperties"] is False
    assert sorted(SCHEMA["required"]) == ["as_of", "schema", "source"], (
        "`imprint` is optional and the three record blocks are optional; the parser requires "
        "exactly these three")


def test_add_kinds_and_each_kinds_closed_key_set_match_ADD_FIELDS():
    branches = {b["properties"]["kind"]["const"]: b for b in SCHEMA["$defs"]["addRecord"]["oneOf"]}
    assert sorted(branches) == sorted(ADD_KINDS)
    for kind, branch in branches.items():
        assert set(branch["properties"]) == {"kind", "key", "note"} | set(ADD_FIELDS[kind]), kind
        assert branch["additionalProperties"] is False
        assert sorted(branch["required"]) == ["key", "kind", "note"], kind


def test_override_fields_are_closed_PER_KIND_exactly_as_the_parser_has_them():
    branches = SCHEMA["$defs"]["overrideRecord"]["oneOf"]
    got = {}
    for branch in branches:
        kind = "host" if branch["properties"]["key"]["$ref"].endswith("hostKey") else "segment"
        got[kind] = tuple(branch["properties"]["field"]["enum"])
        assert set(branch["properties"]) == {"key", "field", "value", "note"}
        assert branch["additionalProperties"] is False
        assert sorted(branch["required"]) == ["field", "key", "note", "value"]
    assert got == {k: tuple(v) for k, v in OVERRIDE_FIELDS.items()}


def test_suppress_takes_a_host_key_and_a_note_and_nothing_else():
    rec = SCHEMA["$defs"]["suppressRecord"]
    assert set(rec["properties"]) == {"key", "note"}
    assert rec["properties"]["key"]["$ref"].endswith("hostKey"), (
        "host keys ONLY -- a segment key is refused, because removing a segment that still "
        "carries placed hosts is a topology edit rather than a suppression")
    assert sorted(rec["required"]) == ["key", "note"]
    assert rec["additionalProperties"] is False


def test_note_is_required_on_every_record_kind_and_may_not_be_blank():
    """Not a stylistic preference: an assertion outranks every class of evidence in the store,
    so an unattributable one is the thing the mandatory note prevents."""
    for branch in SCHEMA["$defs"]["addRecord"]["oneOf"]:
        assert "note" in branch["required"]
    for branch in SCHEMA["$defs"]["overrideRecord"]["oneOf"]:
        assert "note" in branch["required"]
    assert "note" in SCHEMA["$defs"]["suppressRecord"]["required"]
    assert _validate(SCHEMA["$defs"]["note"], "   "), "a whitespace-only note must fail"
    assert not _validate(SCHEMA["$defs"]["note"], "why")


def test_confidence_is_not_authorable_anywhere():
    """Never mentioned as a property, and every record closes its key set, so the file cannot
    carry one. That is the guardrail the scorecard's evidence mix rests on."""
    assert "confidence" not in json.dumps(SCHEMA["$defs"]["addRecord"]["oneOf"])
    for kind in ("addRecord", "overrideRecord"):
        for branch in SCHEMA["$defs"][kind]["oneOf"]:
            assert "confidence" not in branch["properties"]


def test_the_small_vocabularies_are_the_constants():
    assert SCHEMA["$defs"]["portList"]["items"]["properties"]["proto"]["enum"] == list(PORT_PROTOS)
    assert SCHEMA["$defs"]["zoneType"]["enum"] == list(ZONE_TYPES)
    assert SCHEMA["$defs"]["resources"]["propertyNames"]["enum"] == list(RESOURCE_KEYS)
    assert SCHEMA["$defs"]["evidenceClass"]["enum"] == list(EVIDENCE_CLASSES)
    assert EVIDENCE_CLASSES[0] == ASSERTIONS_EVIDENCE_CLASS, (
        "the published order IS the precedence order, and this file's own class is its top")


def test_the_host_key_pattern_names_exactly_the_three_prefixes_the_IR_mints():
    pattern = SCHEMA["$defs"]["hostKey"]["pattern"]
    for prefix in HOST_KEY_PREFIXES:
        assert not _validate(SCHEMA["$defs"]["hostKey"], f"{prefix}x"), prefix
    assert re.match(r"\^\(\?:([a-z|]+)\):", pattern), pattern
    assert set(re.match(r"\^\(\?:([a-z|]+)\):", pattern).group(1).split("|")) == {
        p.rstrip(":") for p in HOST_KEY_PREFIXES}


def test_the_source_pattern_accepts_both_spellings_the_parser_canonicalizes():
    src = SCHEMA["properties"]["source"]
    for good in (f"{SOURCE_PREFIX} raghu", f"{SOURCE_PREFIX}raghu", "OPERATOR:raghu",
                 f"  {SOURCE_PREFIX}  raghu  "):
        assert not _validate(src, good), good
    for bad in ("raghu", f"{SOURCE_PREFIX}", f"{SOURCE_PREFIX}   ", "export:ldif"):
        assert _validate(src, bad), bad


def test_the_port_range_is_1_to_65535_and_a_boolean_is_not_a_port():
    item = SCHEMA["$defs"]["portList"]["items"]
    assert not _validate(item, {"port": 1, "proto": "tcp"})
    assert not _validate(item, {"port": 65535, "proto": "udp"})
    for bad in ({"port": 0, "proto": "tcp"}, {"port": 65536, "proto": "tcp"},
                {"port": True, "proto": "tcp"}, {"port": "88", "proto": "tcp"},
                {"port": 88, "proto": "sctp"}, {"port": 88}, {"proto": "tcp"},
                {"port": 88, "proto": "tcp", "why": 1}):
        assert _validate(item, bad), bad


# ---------------------------------------------------------------------------------------------
# 2. the corpus: what the parser accepts must validate, what it refuses must not
# ---------------------------------------------------------------------------------------------

def _doc(**kw):
    base = {"schema": ASSERTIONS_SCHEMA, "as_of": "2026-09-04T18:00:00Z",
            "source": "operator:raghu"}
    base.update(kw)
    return base


_HOST = "address:10.80.31.9"
_SEG = "10.80.31.0/24"
_SEG2 = "10.80.32.0/24"

#: Documents the parser ACCEPTS. Every one must validate against the schema; a narrower schema
#: would make a legal file look illegal at the Console, which is worse than a loose one.
ACCEPTED = {
    "minimal": _doc(),
    "empty blocks": _doc(add=[], override=[], suppress=[]),
    "imprint set": _doc(imprint="corpapcn-golden"),
    "imprint explicitly null": _doc(imprint=None),
    "source spaced": _doc(source="operator: raghu"),
    "source uppercase": _doc(source="OPERATOR:raghu"),
    "as_of offset": _doc(as_of="2026-09-04T18:00:00+00:00"),
    "as_of naive": _doc(as_of="2026-09-04T18:00:00"),
    "as_of date only": _doc(as_of="2026-09-04"),
    "add host, every field": _doc(add=[{
        "kind": "host", "key": _HOST, "note": "a host the vantage never saw",
        "role": "file-server", "image": "debian-12-cloud@v1",
        "resources": {"cpu": 2, "ram": "4096", "disk": 20}, "segment": _SEG,
        "listen": [{"port": 445, "proto": "tcp"}],
        "dial": [{"port": 53, "proto": "udp"}, {"port": 1, "proto": "icmp"}]}]),
    "add host, hostname key": _doc(add=[
        {"kind": "host", "key": "hostname:axdc01", "note": "n"}]),
    "add host, mac key": _doc(add=[
        {"kind": "host", "key": "mac:aa:bb:cc:dd:ee:ff", "note": "n"}]),
    "add segment": _doc(add=[
        {"kind": "segment", "key": _SEG, "name": "seg-lab", "zone_type": "vlan", "note": "n"}]),
    "add conduit": _doc(add=[
        {"kind": "conduit", "key": f"{_SEG}|{_SEG2}", "note": "n"}]),
    "add conduit, reversed order": _doc(add=[
        {"kind": "conduit", "key": f"{_SEG2}|{_SEG}", "note": "n"}]),
    "add segment, bare address key": _doc(add=[
        {"kind": "segment", "key": "10.80.31.9", "note": "a /32"}]),
    "override host role": _doc(override=[
        {"key": _HOST, "field": "role", "value": "domain-controller", "note": "n"}]),
    "override host image": _doc(override=[
        {"key": _HOST, "field": "image", "value": "windows-server-2022@v1", "note": "n"}]),
    "override host resources": _doc(override=[
        {"key": _HOST, "field": "resources", "value": {"cpu": 4}, "note": "n"}]),
    "override host segment": _doc(override=[
        {"key": _HOST, "field": "segment", "value": _SEG, "note": "n"}]),
    "override segment zone_type": _doc(override=[
        {"key": _SEG, "field": "zone_type", "value": "vlan", "note": "n"}]),
    "override segment name": _doc(override=[
        {"key": _SEG, "field": "name", "value": "seg-core", "note": "n"}]),
    "suppress a host": _doc(suppress=[{"key": _HOST, "note": "out of scope"}]),
    "ipv6 segment": _doc(add=[{"kind": "segment", "key": "2001:db8::/32", "note": "n"}]),
}

#: Documents the parser REFUSES. Every one must FAIL the schema, except the six in
#: `_INEXPRESSIBLE` below.
REFUSED = {
    "unknown top-level key": _doc(comment="hi"),
    "wrong schema": _doc(schema="axr-assertions/2"),
    "no schema": {k: v for k, v in _doc().items() if k != "schema"},
    "no as_of": {k: v for k, v in _doc().items() if k != "as_of"},
    "no source": {k: v for k, v in _doc().items() if k != "source"},
    "source without prefix": _doc(source="raghu"),
    "source with empty name": _doc(source="operator:   "),
    "source is an export": _doc(source="export:ldif"),
    "imprint empty": _doc(imprint="  "),
    "add is not a list": _doc(add={"kind": "host"}),
    "add is null": _doc(add=None),
    "override is not a list": _doc(override="none"),
    "add unknown kind": _doc(add=[{"kind": "router", "key": _HOST, "note": "n"}]),
    "add kind route": _doc(add=[{"kind": "route", "key": _HOST, "note": "n"}]),
    "add no kind": _doc(add=[{"key": _HOST, "note": "n"}]),
    "add no note": _doc(add=[{"kind": "host", "key": _HOST}]),
    "add blank note": _doc(add=[{"kind": "host", "key": _HOST, "note": "  "}]),
    "add authored confidence": _doc(add=[
        {"kind": "host", "key": _HOST, "note": "n", "confidence": 0.4}]),
    "add unknown field": _doc(add=[
        {"kind": "host", "key": _HOST, "note": "n", "purpose": "dmz"}]),
    "add host with a segment-only field": _doc(add=[
        {"kind": "host", "key": _HOST, "note": "n", "zone_type": "vlan"}]),
    "add host, bad key prefix": _doc(add=[{"kind": "host", "key": "host:x", "note": "n"}]),
    "add host, empty key value": _doc(add=[{"kind": "host", "key": "address: ", "note": "n"}]),
    "add host, cidr key": _doc(add=[{"kind": "host", "key": _SEG, "note": "n"}]),
    "add segment, host key": _doc(add=[{"kind": "segment", "key": _HOST, "note": "n"}]),
    "add segment, junk key": _doc(add=[{"kind": "segment", "key": "core", "note": "n"}]),
    "add segment, bad zone_type": _doc(add=[
        {"kind": "segment", "key": _SEG, "zone_type": "ovs", "note": "n"}]),
    "add segment, name with a space": _doc(add=[
        {"kind": "segment", "key": _SEG, "name": "seg core", "note": "n"}]),
    "add conduit, one cidr": _doc(add=[{"kind": "conduit", "key": _SEG, "note": "n"}]),
    "add conduit, three cidrs": _doc(add=[
        {"kind": "conduit", "key": f"{_SEG}|{_SEG2}|10.80.33.0/24", "note": "n"}]),
    "add host, empty role": _doc(add=[
        {"kind": "host", "key": _HOST, "note": "n", "role": "  "}]),
    "add host, unknown resource key": _doc(add=[
        {"kind": "host", "key": _HOST, "note": "n", "resources": {"gpu": 1}}]),
    "add host, empty resources": _doc(add=[
        {"kind": "host", "key": _HOST, "note": "n", "resources": {}}]),
    "add host, boolean resource": _doc(add=[
        {"kind": "host", "key": _HOST, "note": "n", "resources": {"cpu": True}}]),
    "add host, empty listen": _doc(add=[
        {"kind": "host", "key": _HOST, "note": "n", "listen": []}]),
    "add host, bad proto": _doc(add=[
        {"kind": "host", "key": _HOST, "note": "n",
         "listen": [{"port": 88, "proto": "sctp"}]}]),
    "add host, port 0": _doc(add=[
        {"kind": "host", "key": _HOST, "note": "n", "listen": [{"port": 0, "proto": "tcp"}]}]),
    "add host, port 65536": _doc(add=[
        {"kind": "host", "key": _HOST, "note": "n",
         "listen": [{"port": 65536, "proto": "tcp"}]}]),
    "add host, listen entry extra key": _doc(add=[
        {"kind": "host", "key": _HOST, "note": "n",
         "listen": [{"port": 88, "proto": "tcp", "why": "x"}]}]),
    "override unknown field": _doc(override=[
        {"key": _HOST, "field": "purpose", "value": "dmz", "note": "n"}]),
    "override host with a segment field": _doc(override=[
        {"key": _HOST, "field": "zone_type", "value": "vlan", "note": "n"}]),
    "override segment with a host field": _doc(override=[
        {"key": _SEG, "field": "role", "value": "web-tier", "note": "n"}]),
    "override no value": _doc(override=[{"key": _HOST, "field": "role", "note": "n"}]),
    "override no note": _doc(override=[{"key": _HOST, "field": "role", "value": "x"}]),
    "override conduit key": _doc(override=[
        {"key": f"{_SEG}|{_SEG2}", "field": "name", "value": "x", "note": "n"}]),
    "override non-string key": _doc(override=[
        {"key": 7, "field": "role", "value": "x", "note": "n"}]),
    "override empty role": _doc(override=[
        {"key": _HOST, "field": "role", "value": "", "note": "n"}]),
    "override null value": _doc(override=[
        {"key": _HOST, "field": "role", "value": None, "note": "n"}]),
    "override extra key": _doc(override=[
        {"key": _HOST, "field": "role", "value": "x", "note": "n", "confidence": 1.0}]),
    "override segment name with a space": _doc(override=[
        {"key": _SEG, "field": "name", "value": "a b", "note": "n"}]),
    "suppress a segment": _doc(suppress=[{"key": _SEG, "note": "n"}]),
    "suppress no note": _doc(suppress=[{"key": _HOST}]),
    "suppress extra key": _doc(suppress=[{"key": _HOST, "note": "n", "field": "role"}]),
    "record is not a mapping": _doc(suppress=["address:10.0.0.1"]),
    # -- the six the schema cannot express; see `_INEXPRESSIBLE` --
    "non-strict cidr": _doc(add=[{"kind": "segment", "key": "10.80.31.5/24", "note": "n"}]),
    "self conduit": _doc(add=[{"kind": "conduit", "key": f"{_SEG}|{_SEG}", "note": "n"}]),
    "as_of unparseable": _doc(as_of="last tuesday"),
    "duplicate merge key": _doc(suppress=[{"key": _HOST, "note": "a"},
                                          {"key": _HOST, "note": "b"}]),
    "added and suppressed": _doc(add=[{"kind": "host", "key": _HOST, "note": "a"}],
                                 suppress=[{"key": _HOST, "note": "b"}]),
    "add field and override collide": _doc(
        add=[{"kind": "host", "key": _HOST, "note": "a", "role": "web-tier"}],
        override=[{"key": _HOST, "field": "role", "value": "file-server", "note": "b"}]),
}

#: The CLOSED list of parser refusals no per-instance JSON Schema keyword can reach, each with
#: the reason. A refusal fixture outside this list that the schema lets through fails the suite;
#: an entry here that the schema DOES catch fails it too, because a carve-out that is no longer
#: needed is a claim the file's own `$comment` makes and would then be wrong (`LES-139`).
_INEXPRESSIBLE = {
    "non-strict cidr": "arithmetic on host bits; `ipaddress.ip_network(strict=True)` decides",
    "self conduit": "requires comparing two halves of one string",
    "as_of unparseable": "`format` is annotation-only in draft 2020-12",
    "duplicate merge key": "keyed uniqueness over a projection of array items",
    "added and suppressed": "a relation between two different arrays",
    "add field and override collide": "a relation between two different arrays",
}


def _parser_accepts(doc):
    try:
        parse_assertions(doc)
        return True
    except AssertionsError:
        return False


@pytest.mark.parametrize("label", sorted(ACCEPTED))
def test_every_document_the_parser_accepts_validates_against_the_schema(label):
    doc = ACCEPTED[label]
    assert _parser_accepts(doc), f"fixture {label!r} is not actually accepted by the parser"
    assert not _validate(SCHEMA, doc), (
        f"the schema is NARROWER than the parser for {label!r}: {_validate(SCHEMA, doc)}")


@pytest.mark.parametrize("label", sorted(REFUSED))
def test_every_document_the_parser_refuses_fails_the_schema_or_is_a_named_carve_out(label):
    doc = REFUSED[label]
    assert not _parser_accepts(doc), f"fixture {label!r} is not actually refused by the parser"
    errs = _validate(SCHEMA, doc)
    if label in _INEXPRESSIBLE:
        assert not errs, (
            f"{label!r} is listed as inexpressible ({_INEXPRESSIBLE[label]}) but the schema "
            f"caught it: {errs}. Remove the carve-out and the sentence about it in the file's "
            f"own $comment")
    else:
        assert errs, (
            f"the schema is WIDER than the parser for {label!r} and it is not a named "
            f"carve-out. Either tighten the schema or add it to `_INEXPRESSIBLE` and to the "
            f"file's $comment, with a reason")


def test_the_carve_out_list_is_covered_by_the_files_own_comment():
    """The consumer reads the FILE, not this test. Six is a count the `$comment` states, so if
    the list grows here and the prose does not, the artifact starts under-claiming."""
    assert len(_INEXPRESSIBLE) == 6
    assert "SIX REFUSALS THIS SCHEMA CANNOT EXPRESS" in SCHEMA["$comment"]
    assert set(_INEXPRESSIBLE) <= set(REFUSED)


# ---------------------------------------------------------------------------------------------
# 3. the artifact itself
# ---------------------------------------------------------------------------------------------

def test_the_file_is_canonical_so_a_byte_comparison_one_repo_over_is_meaningful():
    text = schema_text()
    assert text == json.dumps(json.loads(text), sort_keys=True, indent=2) + "\n", (
        "sorted keys, 2-space indent, one trailing newline -- axRange vendors a byte-identical "
        "copy and compares it, so a hand edit that reformats breaks that comparison")
    assert text.isascii(), (
        "`json.dumps` defaults to ensure_ascii=True, so a non-ASCII character would ship as a "
        "\\u escape; keep the prose ASCII rather than half-escaped")
    assert SCHEMA_FILE.name == "axr-assertions-1.schema.json"
    assert SCHEMA["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_the_cli_prints_the_file_verbatim():
    """`aximprint assertions schema > copy.json` must produce the vendored copy exactly -- a
    trailing newline added by `print` would make the two differ by one byte."""
    import contextlib
    import io

    from aximprint.cli import main

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["assertions", "schema"])
    assert rc == 0
    assert buf.getvalue() == schema_text()


def test_the_corpus_has_teeth_weakening_the_schema_must_break_it():
    """Mutation proof. Every "must fail the schema" assertion above is only worth the evaluator
    noticing a weakened schema; drop the root's closed key set and the fixture that depends on
    it must start passing, which is what this asserts by watching it happen."""
    weakened = {**SCHEMA}
    weakened.pop("additionalProperties")
    assert _validate(SCHEMA, REFUSED["unknown top-level key"]), "the strict schema must refuse"
    assert not _validate(weakened, REFUSED["unknown top-level key"]), (
        "the weakened schema must accept -- if it does not, this test is measuring something "
        "other than the constraint it names")


def test_against_real_jsonschema_when_it_happens_to_be_importable():
    """Not a dependency, and deliberately not becoming one. When it IS present -- a developer
    venv, axRange's -- the subset evaluator's verdicts must match a real one's, which is what
    keeps `_validate` from quietly becoming its own dialect."""
    jsonschema = pytest.importorskip("jsonschema")
    validator = jsonschema.Draft202012Validator(SCHEMA)
    for label, doc in {**ACCEPTED}.items():
        assert validator.is_valid(doc), f"{label}: real validator disagrees (accepted set)"
    for label, doc in REFUSED.items():
        expected = label in _INEXPRESSIBLE
        assert validator.is_valid(doc) is expected, f"{label}: real validator disagrees"
