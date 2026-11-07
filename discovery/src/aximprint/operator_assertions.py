"""The operator assertions file — `axr-assertions/1`: parse, validate, merge, re-emit.

`docs/WORK_ORDER_reference_ingest.md` §4 (D10-D18), implementing axRange
`docs/design/14-operator-assertions.md` §3 as that note refines it. **The file is the contract**
between the two repos: axRange's Console writes it (`docs/WORK_ORDER_console_canvas.md` §9 seam 3)
and hands it to `aximprint cast --assertions` by path; this module is the only thing on either
side that decides what a valid record is.

**Why this sits at the bottom of the package rather than inside `ingest/`.** Two consumers on
opposite branches of the layering need the same document: `ingest/reference/` maps it into belief
store rows (D14), and `synth/compiler.py` resolves it against a PIT at compile time (§4's last
paragraph). A module holding only the document — stdlib and `yaml`, nothing from `aximprint` —
can be imported by both without inventing a sideways edge, which is the same argument
`CONVENTIONS.md` §3 already makes for `provenance` and `catalog`. It is spelled
`operator_assertions` rather than `assertions` deliberately: the belief store's `assertions` table
is the project's *uniform fact shape* and these are one producer of rows in it, so two things named
alike would collide in prose, in a traceback, and in `docs/LESSONS.md`'s bare-filename `Scope:`
matching (CONVENTIONS §5's ruling on exactly that).

**Two failure classes, and the split is deliberate** (D15 is explicit about one half of it, and
this module states the other):

  * **A shape error is a REFUSAL** — an unknown `override.field`, `kind: route`, a key in both
    `add` and `suppress`, an authored `confidence`, a missing `note`. These are always the
    author's error, they are the same error on every PIT, and `CONVENTIONS.md` §3's fail-closed
    rule applies without qualification. Every one of them names the offending value AND the
    accepted set, because a refusal that does not say what would have been accepted just moves
    the guessing.
  * **A resolution MISS is reported, never fatal** — a key this PIT does not carry, a conduit
    endpoint that is not a live segment. The PIT moves underneath a standing file by
    construction: a re-imprint from a later capture may not see a host the operator asserted
    about last week, and refusing there would make every re-imprint an editing session. That is
    the same reasoning D17 applies to drift (flag, never demote), and the reporting lives in the
    compiler, which is the only layer that knows what a PIT contains.

**Confidence is never authored** (`14` §3): an `asserted` fact is 1.0 by convention, and a file
that could set 0.4 would let an operator hand-tune their own twin's evidence mix, which is the one
guardrail the scorecard has (`14` §2). Present in the document at all is a refusal, not a silent
drop — a silently ignored field is how a hand edit vanishes with no message.
"""
from __future__ import annotations

import datetime as _dt
import ipaddress
import json
from dataclasses import dataclass, field as _field
from pathlib import Path
from typing import Any, Optional

import yaml

#: The assertions file's own version, and the fourth versioned schema string this project
#: declares (after the network IR's, and the two 2026-09-04 cast-output exceptions). Ratified by
#: the user in axRange `docs/design/14-operator-assertions.md` §3, which is the design of record
#: for the file; `tests/test_ir_schema.py`'s closed-set guard carries it in `ratified` and would
#: have failed the suite otherwise, which is `LES-141` working as intended rather than a
#: formality.
#:
#: Deliberately NOT spelled with the IR's own constant name — that identifier is refused anywhere
#: outside `aximprint.ir` by `tests/test_ir_schema.py`, which matches the token in PROSE as well
#: as in a declaration, so even naming it in a comment here would fail the suite. The same
#: sidestep `synth/compiler.py::PROVENANCE_SCHEMA` documents.
#:
#: Declared ONCE, here, in the module that parses and emits it — the same rule the sidecar's
#: `PROVENANCE_SCHEMA` and the bundle's `BUNDLE_SCHEMA` follow (`LES-105`).
ASSERTIONS_SCHEMA = "axr-assertions/2"

#: Every schema string `parse_assertions` accepts. A `/1` file is a `/2` file that uses none of
#: the device-tree records (`docs/design/topology-layers.md` §3.2, 2026-11-07), so it parses
#: unchanged and is re-emitted as `/2`; nothing an operator wrote under `/1` is refused.
ACCEPTED_SCHEMAS: tuple[str, ...] = ("axr-assertions/1", "axr-assertions/2")

#: The closed set of `add.kind` values (D10/D13). Sorted, and sorted is load-bearing: it is the
#: first component of the canonical `add` order, so a record's position in a re-emitted file is a
#: property of this tuple rather than of the author's typing order (D15).
ADD_KINDS: tuple[str, ...] = ("conduit", "device", "host", "link", "segment", "tap")

#: `add: {kind: device}` / `override: {field: device_kind}` values. A device is a host entity
#: (keyed like one) that is wired by links rather than attached to one segment: a switch, or a
#: router discovery never saw (`docs/design/topology-layers.md` §2.2). `host` is accepted on
#: the OVERRIDE only, to turn a device back into a host.
DEVICE_KINDS: tuple[str, ...] = ("router", "switch")
#: `links[].mode` / `.state` as the twin config spells them (`twinconfig/schema.py`).
LINK_MODES: tuple[str, ...] = ("access", "trunk")
LINK_STATES: tuple[str, ...] = ("blocked", "down", "up")
#: The two shapes a `tap` key takes: a whole segment, or one device port.
TAP_KEY_PREFIXES: tuple[str, ...] = ("port:", "segment:")

#: `kind` values refused BY NAME, with the reason (D13, ruled §11 D-c(a)). A named refusal is not
#: the same as an unknown-kind refusal: `route` is a thing an operator will reasonably try, and
#: "there is no route object in a twin config at all" is the answer they need — inter-segment
#: reachability is the synthetic gateway's, conduits are how it is authored, and the only
#: route-shaped surface anywhere is the fabric slice's per-router `routing:`, which a cast never
#: emits. Accepting it as an alias for `conduit` (option (b)) would make two words mean one thing.
REFUSED_ADD_KINDS: dict[str, str] = {
    "route": ("a twin config has no route object at all — inter-segment reachability belongs to "
              "the synthetic gateway, and `kind: conduit` is how it is authored. The only "
              "route-shaped surface in axRange is the fabric slice's per-router `routing:`, "
              "which a cast never emits"),
}

#: `override.field` — CLOSED, PER KIND, fail-closed (D12). Anything else is a refusal naming the
#: field, the kind it was asked of, and this set. Never a silent skip: a silently ignored
#: override is an operator's edit vanishing with no message, which is the failure mode the whole
#: save/apply loop exists to prevent.
#:
#: `image` and `resources` are here on purpose and are the interesting half. They are
#: `compiler`-class today (a catalog default, unscored by the scorecard); asserting one moves it
#: into a class that IS scored, which is exactly `14` §4's point about the first edit set.
OVERRIDE_FIELDS: dict[str, tuple[str, ...]] = {
    "host": ("device_kind", "group", "image", "resources", "role", "segment"),
    "link": ("mode", "native", "state", "vlans"),
    "segment": ("name", "zone_type"),
}

#: Content an `add` record may carry, per kind — the `override` vocabulary for that kind plus the
#: fields only an ADD can supply (a listen/dial census for a host nothing observed). One table,
#: so `add` and `override` cannot drift into two different ideas of what a host has (`LES-087`).
ADD_FIELDS: dict[str, tuple[str, ...]] = {
    "conduit": (),
    # A device add is a host add that also names what the device IS; `group` is a member
    # split of an OBSERVED group and makes no sense on a host declared into existence.
    "device": ("device_kind", "image", "resources", "role", "segment"),
    "host": tuple(sorted(f for f in OVERRIDE_FIELDS["host"] + ("dial", "listen")
                         if f not in ("device_kind", "group"))),
    "link": OVERRIDE_FIELDS["link"],
    "segment": OVERRIDE_FIELDS["segment"],
    "tap": ("sensors",),
}

#: `entity_key` prefixes (D10) — the self-describing `<key_kind>:<value>` form the IR already
#: mints (`docs/SPEC_network_ir.md`, `ir/keys.py`), never a twin-config index. A segment's key is
#: its OBSERVED CIDR and a conduit's is `"<cidrA>|<cidrB>"`, so those two are shaped rather than
#: prefixed and are detected below.
HOST_KEY_PREFIXES: tuple[str, ...] = ("address:", "hostname:", "mac:")

#: `listen`/`dial` protocols. Closed, because the port strings these become (`"389/tcp"`) are
#: joined against `ir_entity.listen_ports`, whose producer is Zeek's own `proto` column — a value
#: outside this set would never match anything and would be a typo the file silently kept.
PORT_PROTOS: tuple[str, ...] = ("icmp", "tcp", "udp")

#: `resources` keys, mirroring `catalog/roles.yaml`'s own rows (`{cpu, ram, disk}`). Closed for
#: the same reason every other vocabulary here is: axRange's `twin_config` is `extra="forbid"`, so
#: a fourth key would be refused at deploy time, one repo and several hours later.
RESOURCE_KEYS: tuple[str, ...] = ("cpu", "disk", "ram")

#: `segment.zone_type` — axRange's two SDN zone shapes (`CLAUDE.md`, "Networking is SDN-native").
#: A cast emits `simple` for every segment as a `compiler` choice ("no VLAN evidence exists at
#: this vantage"); asserting `vlan` is D11(c)'s worked example of replacing one.
ZONE_TYPES: tuple[str, ...] = ("simple", "vlan")

#: The evidence class every fact from this document carries (`ir/schema.py::EVIDENCE_CLASSES`,
#: axRange `docs/design/14-operator-assertions.md` §2's top row). A property of the CHANNEL, not
#: of a record: an assertions file is `asserted` throughout, which is exactly what lets it outrank
#: an export and the wire alike. Spelled as a constant rather than as a literal at each producer
#: because the reference plugin, the store's replace-by-source delete and the compiler's `Fact`
#: all have to agree on it (`LES-105`).
ASSERTIONS_EVIDENCE_CLASS = "asserted"

#: The confidence an asserted fact carries, by convention rather than by measurement (`14` §3).
#: Never authored (see `_reject_authored_confidence`), never anything else.
ASSERTED_CONFIDENCE = 1.0

#: The `source:` prefix (D14). An assertion's source is a PERSON, and saying so in the value is
#: what lets a scorecard's evidence mix distinguish "an operator said so" from "an export said
#: so" without a second column.
SOURCE_PREFIX = "operator:"

#: The PUBLISHED vocabulary — a JSON Schema (draft 2020-12) stating what the parser below
#: accepts, shipped as a file rather than described in prose.
#:
#: **Why a file and not a docstring.** axRange's Console *writes* this document
#: (`docs/WORK_ORDER_console_canvas.md` §9 seam 3) and its API validates one before handing it
#: back; until now the only statement of what a valid record is lived in this module's control
#: flow, which the consumer cannot import (`CLAUDE.md`: neither repo imports the other). A
#: vendorable artifact is the one shape a contract can take across that boundary — axRange keeps
#: a byte-identical copy and compares it by test, which is why the file is canonical
#: (`json.dumps(sort_keys=True, indent=2)` + a trailing newline) and why it is edited HERE and
#: copied there, never the reverse.
#:
#: **It is a statement ABOUT this parser, not a second parser.** Nothing in this module reads it
#: — `parse_assertions` is still the only thing that decides — so the two can only agree by
#: being checked against each other, which `tests/test_assertions_schema.py` does field by field
#: against the constants above rather than by eye (`LES-014`: a bound stated in prose and checked
#: by nothing). Six refusals are cross-record or arithmetic and no per-instance JSON Schema
#: keyword reaches them; they are named in the file's own `$comment` rather than left for a
#: consumer to discover by reading schema-valid as parser-accepted.
SCHEMA_FILE = Path(__file__).resolve().parent / "schemas" / "axr-assertions-2.schema.json"


def schema_text() -> str:
    """The published JSON Schema, verbatim.

    Verbatim rather than re-serialized: the bytes ARE the artifact axRange compares against, so
    a round trip through `json.loads`/`json.dumps` here would make this function's output and
    the shipped file two things that merely usually agree.
    """
    return SCHEMA_FILE.read_text(encoding="utf-8")


def schema_document() -> dict:
    """The published JSON Schema, parsed — for a caller that wants to read a `$defs` entry."""
    return json.loads(schema_text())


class AssertionsError(ValueError):
    """A shape error in an assertions file. Always names the offending value and, where there is
    one, the accepted set.

    A distinct type rather than a bare `ValueError` so a caller — the CLI, the Console's apply
    endpoint one repo over — can report an authoring mistake as an authoring mistake instead of
    as a crash, which is the difference between a message an operator can act on and a traceback.
    """


@dataclass(frozen=True, slots=True)
class AddRecord:
    """One `add:` record: an entity the vantage did not supply, declared into existence.

    `fields` is the content this add carries beyond its own identity — a host's `role`/`segment`/
    `listen`, a segment's `name`/`zone_type` — validated against `ADD_FIELDS[kind]` and stored as
    an ordinary dict whose keys are only ever iterated in `sorted()` order. Not a tuple of pairs:
    `resources` is a mapping and the shape would stop being readable to buy a hashability nothing
    here needs.
    """

    kind: str
    key: str
    note: str
    fields: dict[str, Any] = _field(default_factory=dict)

    @property
    def merge_key(self) -> tuple[str, str]:
        """D15: `add` merges on `(kind, key)`."""
        return (self.kind, self.key)


@dataclass(frozen=True, slots=True)
class OverrideRecord:
    """One `override:` record: a field of a discovered entity, replaced by a hand-authored value.

    `kind` is DERIVED from the key's shape, never authored — a `10.80.31.0/24` key is a segment
    and an `address:…` key is a host, and asking the author to restate that would introduce a
    second spelling of one fact and therefore a way for the two to disagree.
    """

    key: str
    field: str
    value: Any
    note: str
    kind: str

    @property
    def merge_key(self) -> tuple[str, str]:
        """D15: `override` merges on `(key, field)`."""
        return (self.key, self.field)


@dataclass(frozen=True, slots=True)
class SuppressRecord:
    """One `suppress:` record: an entity that is out of scope for this twin.

    Host keys only in v1 (`14` §4's ruled first edit set is "suppress a host"). A segment key is
    refused rather than accepted: removing a segment that still carries placed hosts is a
    topology edit whose consequences — every node's `net:`, every conduit touching it — are not a
    suppression, and quietly doing half of it would produce a twin config axRange's expander
    rejects at deploy time.
    """

    key: str
    note: str

    @property
    def merge_key(self) -> str:
        return self.key


@dataclass(frozen=True, slots=True)
class MarkRecord:
    """One `confirm:` or `doubt:` record (`docs/design/topology-layers.md` §3.2): the operator
    pins a discovered value as `asserted` (its fields unchanged), or marks it `unconfirmed`.
    Keyed like an override -- a host key, a segment CIDR or a link key -- and carrying nothing
    but the key and the note, because it changes provenance, never content."""

    key: str
    note: str
    kind: str

    @property
    def merge_key(self) -> str:
        return self.key


@dataclass(frozen=True, slots=True)
class AssertionSet:
    """A parsed, validated, canonically-ordered assertions file.

    **Frozen, and its three record lists are tuples in canonical order** — that is D16 as a data
    structure rather than as a discipline. Assertions are read once, here, into a value the
    compiler indexes into by key; nothing downstream ever iterates the file's own order, so two
    casts of one PIT with one file cannot differ because a record moved up the page (`LES-002`,
    `LES-009`).

    `as_of` is kept BOTH as the string the operator wrote and as epoch seconds. The string is what
    a re-emitted file carries (a save must not reformat a timestamp it did not change); the epoch
    is the time bound the belief store's `window_start` column takes (D14).
    """

    schema: str
    imprint: Optional[str]
    as_of: str
    as_of_epoch: float
    source: str
    adds: tuple[AddRecord, ...] = ()
    overrides: tuple[OverrideRecord, ...] = ()
    suppress: tuple[SuppressRecord, ...] = ()
    confirm: tuple[MarkRecord, ...] = ()
    doubt: tuple[MarkRecord, ...] = ()
    #: Where it was read from, for messages and for the sidecar's `assertions` block. `None` for
    #: a set built in memory (a test, or the Console's own PUT body before it is written).
    path: Optional[str] = None

    def __len__(self) -> int:
        return (len(self.adds) + len(self.overrides) + len(self.suppress)
                + len(self.confirm) + len(self.doubt))

    @property
    def counts(self) -> dict[str, int]:
        return {"add": len(self.adds), "override": len(self.overrides),
                "suppress": len(self.suppress), "confirm": len(self.confirm),
                "doubt": len(self.doubt)}


# -- parsing --------------------------------------------------------------------------------


def _require_mapping(obj, what: str) -> dict:
    if not isinstance(obj, dict):
        raise AssertionsError(f"{what} must be a mapping, got {type(obj).__name__}")
    return obj


def _require_note(record: dict, what: str) -> str:
    """`14` §3: *"Every record carries a `note`."*

    Enforced rather than encouraged, and it is not bureaucracy: an assertion outranks every class
    of evidence in the store, so the one thing a reader of a twin six months later needs is why
    somebody overruled the wire. A record with no note is an unattributable override.
    """
    note = record.get("note")
    if not isinstance(note, str) or not note.strip():
        raise AssertionsError(
            f"{what}: every assertions record needs a non-empty `note`. An assertion outranks "
            f"every class of evidence in the store, so the reason it was made is the only thing "
            f"that makes it reviewable later")
    return note.strip()


def _reject_authored_confidence(record: dict, what: str) -> None:
    if "confidence" in record:
        raise AssertionsError(
            f"{what}: `confidence` is never authored — an asserted fact is 1.0 by convention "
            f"(axRange `docs/design/14-operator-assertions.md` §3). A file that could set it "
            f"would let an operator tune the evidence mix that is the scorecard's only guardrail "
            f"against a twin certifying its own inputs")


def _unknown_keys(record: dict, allowed: tuple[str, ...], what: str) -> None:
    extra = sorted(set(record) - set(allowed))
    if extra:
        raise AssertionsError(
            f"{what}: unknown key(s) {extra}; accepted: {sorted(allowed)}. Fail-closed rather "
            f"than ignored (`docs/CONVENTIONS.md` §3): a silently dropped key is an operator's "
            f"edit vanishing with no message")


def _cidr(value, what: str) -> str:
    """A CIDR key, parsed rather than pattern-matched (`LES-008`).

    `strict=True`: `10.80.31.5/24` is refused and names itself, because a key that is not the
    network address would never join against `ir_segment.cidr` and would silently assert about
    nothing.
    """
    if not isinstance(value, str):
        raise AssertionsError(f"{what}: expected a CIDR string, got {type(value).__name__}")
    try:
        net = ipaddress.ip_network(value, strict=True)
    except ValueError as exc:
        raise AssertionsError(
            f"{what}: {value!r} is not a network CIDR ({exc}). A segment's key is its OBSERVED "
            f"CIDR (D10) and must be the network address, or it joins against nothing") from None
    return str(net)


def _host_key(value, what: str) -> str:
    if isinstance(value, str) and ("#" in value or "|" in value):
        raise AssertionsError(
            f"{what}: {value!r} is not a host entity key -- '#' and '|' are the link and port "
            f"separators (`<host key>#<port>|<host key>#<port>`)")
    if not isinstance(value, str) or not any(value.startswith(p) for p in HOST_KEY_PREFIXES):
        raise AssertionsError(
            f"{what}: {value!r} is not a host entity key. Keys are the stable, self-describing "
            f"`<key_kind>:<value>` form the IR mints (D10): "
            f"{', '.join(p + '…' for p in HOST_KEY_PREFIXES)} — never a twin-config index")
    if not value.split(":", 1)[1].strip():
        raise AssertionsError(f"{what}: {value!r} carries an empty key value")
    return value


def _port_end(value, what: str) -> tuple[str, str]:
    """`"<hostKey>#<port>"` -> `(hostKey, port)`; refused when either half is missing."""
    if not isinstance(value, str) or value.count("#") != 1:
        raise AssertionsError(
            f"{what}: {value!r} is not a device port -- a port end is `<host key>#<port>` "
            f"(`mac:aa:bb:cc:dd:ee:ff#P14`), one `#`")
    host, port = value.split("#", 1)
    host = _host_key(host, what)
    if not port.strip() or "|" in port:
        raise AssertionsError(f"{what}: {value!r} names no port after '#'")
    return host, port.strip()


def link_key(a: str, b: str) -> str:
    """The canonical link key for two port ends -- sorted, `|`-joined, like `conduit_key`."""
    return "|".join(sorted((a, b)))


def _link_key(value, what: str) -> str:
    if not isinstance(value, str) or value.count("|") != 1:
        raise AssertionsError(
            f"{what}: a link key is two device ports joined by '|' "
            f"(\"<host key>#<port>|<host key>#<port>\"), got {value!r}")
    left, right = value.split("|")
    (host_a, port_a), (host_b, port_b) = _port_end(left, what), _port_end(right, what)
    if host_a == host_b:
        raise AssertionsError(
            f"{what}: a link joins two DIFFERENT devices; both ends are {host_a!r}")
    return link_key(f"{host_a}#{port_a}", f"{host_b}#{port_b}")


def _tap_key(value, what: str) -> str:
    if not isinstance(value, str) or not any(value.startswith(p) for p in TAP_KEY_PREFIXES):
        raise AssertionsError(
            f"{what}: {value!r} is not a tap key. A tap sits on a whole segment "
            f"(`segment:<cidr>`) or on one device port (`port:<host key>#<port>`)")
    prefix, rest = value.split(":", 1)
    if prefix == "segment":
        return f"segment:{_cidr(rest, what)}"
    host, port = _port_end(rest, what)
    return f"port:{host}#{port}"


def _key_kind(key: str) -> Optional[str]:
    """`"address:10.0.0.1"` -> `host`; `"10.0.0.0/24"` -> `segment`; `"a|b"` -> `conduit`;
    `"a#p|b#q"` -> `link`; `"segment:…"` / `"port:…"` -> `tap`.

    `None` for anything else, so a caller refuses with its own message rather than this function
    guessing a kind for a malformed key.
    """
    # A link key STARTS with a host key (`mac:…#P1|…`), so the link shape is tested first.
    if "|" in key and "#" in key:
        return "link"
    if any(key.startswith(p) for p in HOST_KEY_PREFIXES):
        return "host"
    if any(key.startswith(p) for p in TAP_KEY_PREFIXES):
        return "tap"
    if "|" in key:
        return "conduit"
    try:
        ipaddress.ip_network(key, strict=True)
    except ValueError:
        return None
    return "segment"


def conduit_key(a: str, b: str) -> str:
    """The canonical conduit key for a segment pair — sorted, `|`-joined.

    Sorted here rather than trusted from the file, and matching `synth/compiler.py`'s own pair
    ordering exactly (D10): the compiler derives its conduits from
    `tuple(sorted((seg_a, seg_b)))`, so a key ordered the other way would describe the identical
    conduit under a second name, and the two would merge as different records.
    """
    return "|".join(sorted((a, b)))


def _parse_add(raw, index: int) -> AddRecord:
    what = f"add[{index}]"
    record = _require_mapping(raw, what)
    _reject_authored_confidence(record, what)
    kind = record.get("kind")
    if kind in REFUSED_ADD_KINDS:
        raise AssertionsError(
            f"{what}: `kind: {kind}` is refused in {ASSERTIONS_SCHEMA} — "
            f"{REFUSED_ADD_KINDS[kind]}. Accepted kinds: {list(ADD_KINDS)}")
    if kind not in ADD_KINDS:
        raise AssertionsError(
            f"{what}: unknown `kind: {kind!r}`; accepted: {list(ADD_KINDS)}")
    _unknown_keys(record, ("kind", "key", "note") + ADD_FIELDS[kind], what)
    note = _require_note(record, what)
    raw_key = record.get("key")
    if kind == "segment":
        key = _cidr(raw_key, f"{what}.key")
    elif kind in ("host", "device"):
        key = _host_key(raw_key, f"{what}.key")
    elif kind == "link":
        key = _link_key(raw_key, f"{what}.key")
    elif kind == "tap":
        key = _tap_key(raw_key, f"{what}.key")
    else:
        if not isinstance(raw_key, str) or raw_key.count("|") != 1:
            raise AssertionsError(
                f"{what}.key: a conduit key is exactly two CIDRs joined by '|' "
                f"(\"<cidrA>|<cidrB>\"), got {raw_key!r}")
        left, right = (_cidr(part, f"{what}.key") for part in raw_key.split("|"))
        if left == right:
            raise AssertionsError(
                f"{what}.key: a conduit joins two DIFFERENT segments; both sides are {left!r}")
        key = conduit_key(left, right)
    fields = {name: _validate_field(kind, name, record[name], f"{what}.{name}")
              for name in ADD_FIELDS[kind] if name in record}
    if kind == "device" and "device_kind" not in fields:
        raise AssertionsError(
            f"{what}: a device add names what the device is -- `device_kind: switch` or "
            f"`device_kind: router`")
    if kind == "link":
        _check_link_fields(fields, what)
    return AddRecord(kind=kind, key=key, note=note, fields=fields)


def _check_link_fields(fields: dict, what: str) -> None:
    """A link add says what it carries (`docs/design/topology-layers.md` §2.3): `mode` is
    required; a trunk names `vlans` and/or `native`; an access link names exactly one segment in
    `vlans` and no `native`."""
    mode = fields.get("mode")
    if mode is None:
        raise AssertionsError(f"{what}: a link add names its `mode` (access | trunk)")
    if mode == "access":
        if fields.get("native") is not None:
            raise AssertionsError(f"{what}: `native` is a trunk's field")
        if len(fields.get("vlans") or []) != 1:
            raise AssertionsError(
                f"{what}: an access link carries exactly one segment in `vlans`")
    elif not fields.get("vlans") and fields.get("native") is None:
        raise AssertionsError(f"{what}: a trunk names the segments it carries (`vlans`, `native`)")


def _parse_override(raw, index: int) -> OverrideRecord:
    what = f"override[{index}]"
    record = _require_mapping(raw, what)
    _reject_authored_confidence(record, what)
    _unknown_keys(record, ("key", "field", "value", "note"), what)
    note = _require_note(record, what)
    key = record.get("key")
    if not isinstance(key, str):
        raise AssertionsError(f"{what}.key: expected a string, got {type(key).__name__}")
    kind = _key_kind(key)
    if kind is None or kind in ("conduit", "tap"):
        raise AssertionsError(
            f"{what}.key: {key!r} names neither a host, a segment nor a link. A host key is "
            f"{', '.join(p + '…' for p in HOST_KEY_PREFIXES)}, a segment key is its observed "
            f"CIDR (D10) and a link key is two device ports joined by '|'; a conduit and a tap "
            f"have no overridable field")
    if kind == "segment":
        key = _cidr(key, f"{what}.key")
    elif kind == "link":
        key = _link_key(key, f"{what}.key")
    field = record.get("field")
    if field not in OVERRIDE_FIELDS[kind]:
        raise AssertionsError(
            f"{what}: `field: {field!r}` is not overridable on a {kind}; accepted for a {kind}: "
            f"{list(OVERRIDE_FIELDS[kind])} (D12 — the set is CLOSED, per kind, and an "
            f"unrecognized field is refused rather than skipped)")
    if "value" not in record:
        raise AssertionsError(f"{what}: an override needs a `value`")
    value = _validate_field(kind, field, record["value"], f"{what}.value")
    return OverrideRecord(key=key, field=field, value=value, note=note, kind=kind)


def _parse_suppress(raw, index: int) -> SuppressRecord:
    what = f"suppress[{index}]"
    record = _require_mapping(raw, what)
    _reject_authored_confidence(record, what)
    _unknown_keys(record, ("key", "note"), what)
    note = _require_note(record, what)
    key = record.get("key")
    kind = _key_kind(key) if isinstance(key, str) else None
    if kind == "segment":
        raise AssertionsError(
            f"{what}.key: {key!r} is a segment. Suppressing a SEGMENT is not in "
            f"{ASSERTIONS_SCHEMA}: removing one that still carries placed hosts would leave "
            f"every node's `net:` and every conduit touching it dangling, which is a topology "
            f"edit rather than a suppression. Suppress its hosts, or do not emit the segment")
    return SuppressRecord(key=_host_key(key, f"{what}.key"), note=note)


def _parse_mark(raw, index: int, block: str) -> MarkRecord:
    """One `confirm:` / `doubt:` record: a key and a note, nothing else."""
    what = f"{block}[{index}]"
    record = _require_mapping(raw, what)
    _reject_authored_confidence(record, what)
    _unknown_keys(record, ("key", "note"), what)
    note = _require_note(record, what)
    key = record.get("key")
    kind = _key_kind(key) if isinstance(key, str) else None
    if kind is None or kind in ("conduit", "tap"):
        raise AssertionsError(
            f"{what}.key: {key!r} names neither a host, a segment nor a link -- those are the "
            f"things a {block} can mark")
    if kind == "segment":
        key = _cidr(key, f"{what}.key")
    elif kind == "link":
        key = _link_key(key, f"{what}.key")
    else:
        key = _host_key(key, f"{what}.key")
    return MarkRecord(key=key, note=note, kind=kind)


def _validate_field(kind: str, name: str, value, what: str):
    """One record field's own shape. Returns the canonical value.

    Every branch is a closed vocabulary or a parse, never a regex over a shape (`LES-008`), and
    every refusal names the accepted set. The `listen`/`dial` branch is the one that
    CONVERTS: `[{port: 88, proto: tcp}]` becomes `["88/tcp"]`, which is the spelling
    `ir_entity.listen_ports` already carries — done here rather than in the compiler so the
    authored shape and the joined shape are related in exactly one place.
    """
    if name == "role":
        if not isinstance(value, str) or not value.strip():
            raise AssertionsError(f"{what}: a role is a non-empty string, got {value!r}")
        return value.strip()
    if name == "image":
        if not isinstance(value, str) or not value.strip():
            raise AssertionsError(f"{what}: an image is a non-empty string, got {value!r}")
        return value.strip()
    if name == "segment":
        return _cidr(value, what)
    if name == "resources":
        block = _require_mapping(value, what)
        extra = sorted(set(block) - set(RESOURCE_KEYS))
        if extra:
            raise AssertionsError(
                f"{what}: unknown resource key(s) {extra}; accepted: {list(RESOURCE_KEYS)}. "
                f"axRange's `twin_config` is `extra=\"forbid\"`, so a fourth key is refused at "
                f"deploy time, one repo and several hours later")
        if not block:
            raise AssertionsError(f"{what}: an empty `resources` block asserts nothing")
        for rk, rv in sorted(block.items()):
            if not isinstance(rv, (str, int)) or isinstance(rv, bool):
                raise AssertionsError(
                    f"{what}.{rk}: expected a string or an integer, got {type(rv).__name__}")
        return {k: block[k] for k in sorted(block)}
    if name == "device_kind":
        accepted = DEVICE_KINDS + (("host",) if kind == "host" else ())
        if value not in accepted:
            raise AssertionsError(
                f"{what}: unknown device kind {value!r}; accepted: {list(accepted)}")
        return value
    if name == "group":
        if not isinstance(value, str) or not value.strip() or any(c.isspace() for c in value):
            raise AssertionsError(
                f"{what}: a group name is a non-empty identifier with no whitespace -- it "
                f"becomes the node group's name -- got {value!r}")
        return value
    if name == "mode":
        if value not in LINK_MODES:
            raise AssertionsError(f"{what}: unknown link mode {value!r}; accepted: {list(LINK_MODES)}")
        return value
    if name == "state":
        if value not in LINK_STATES:
            raise AssertionsError(
                f"{what}: unknown link state {value!r}; accepted: {list(LINK_STATES)}")
        return value
    if name == "native":
        return _cidr(value, what)
    if name == "vlans":
        if not isinstance(value, list) or not value:
            raise AssertionsError(f"{what}: expected a non-empty list of segment CIDRs, got {value!r}")
        cidrs = [_cidr(v, f"{what}[{i}]") for i, v in enumerate(value)]
        return sorted(set(cidrs))
    if name == "sensors":
        if (not isinstance(value, list) or not value
                or not all(isinstance(v, str) and v.strip() for v in value)):
            raise AssertionsError(
                f"{what}: expected a non-empty list of sensor names, got {value!r}")
        return sorted({v.strip() for v in value})
    if name == "zone_type":
        if value not in ZONE_TYPES:
            raise AssertionsError(
                f"{what}: unknown zone type {value!r}; accepted: {list(ZONE_TYPES)}")
        return value
    if name == "name":
        if not isinstance(value, str) or not value.strip() or any(c.isspace() for c in value):
            raise AssertionsError(
                f"{what}: a segment name is a non-empty identifier with no whitespace — it is "
                f"what a node's `net:` references — got {value!r}")
        return value
    if name in ("listen", "dial"):
        if not isinstance(value, list) or not value:
            raise AssertionsError(
                f"{what}: expected a non-empty list of {{port, proto}} entries, got {value!r}")
        ports = set()
        for i, entry in enumerate(value):
            entry = _require_mapping(entry, f"{what}[{i}]")
            _unknown_keys(entry, ("port", "proto"), f"{what}[{i}]")
            port, proto = entry.get("port"), entry.get("proto")
            if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
                raise AssertionsError(
                    f"{what}[{i}].port: expected an integer in 1..65535, got {port!r}")
            if proto not in PORT_PROTOS:
                raise AssertionsError(
                    f"{what}[{i}].proto: unknown protocol {proto!r}; accepted: "
                    f"{list(PORT_PROTOS)}. These become `\"<port>/<proto>\"` strings joined "
                    f"against `ir_entity.{name}_ports`, whose producer is Zeek's own `proto` "
                    f"column, so a value outside this set would match nothing")
            ports.add(f"{port}/{proto}")
        return sorted(ports)
    # Unreachable: `name` always comes from `ADD_FIELDS`/`OVERRIDE_FIELDS`, both closed. Kept as
    # a positive gate rather than an implicit `return value` — a field added to one of those
    # tables and not here would otherwise pass through unvalidated, which is the exact shape of
    # "a table keyed by a vocabulary that grew in one place" (`LES-087`).
    raise AssertionsError(f"{what}: no validator is declared for field {name!r}")


def _parse_as_of(value) -> tuple[str, float]:
    """The file's own time bound (D14) — the string as written, plus epoch seconds.

    Parsed, not regex-matched (`LES-008`). PyYAML resolves an unquoted RFC-3339 timestamp into a
    `datetime` before this function ever sees a string, so both shapes are accepted and both
    canonicalize to the same ISO-8601 `Z` spelling on re-emit; a naive datetime is read as UTC and
    said to be, rather than silently taking the writer's local zone.
    """
    if isinstance(value, _dt.datetime):
        stamp = value if value.tzinfo else value.replace(tzinfo=_dt.timezone.utc)
    elif isinstance(value, str):
        text = value.strip()
        try:
            stamp = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            raise AssertionsError(
                f"as_of: {value!r} is not an ISO-8601 timestamp (e.g. "
                f"2026-09-04T18:00:00Z). It is this snapshot's TIME BOUND and becomes the "
                f"`window_start` of every row the reference ingest writes") from None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=_dt.timezone.utc)
    else:
        raise AssertionsError(
            f"as_of: expected an ISO-8601 timestamp, got {type(value).__name__}")
    stamp = stamp.astimezone(_dt.timezone.utc)
    return (stamp.strftime("%Y-%m-%dT%H:%M:%SZ"), stamp.timestamp())


def _cross_record_checks(adds, overrides, suppress, confirm=(), doubt=()) -> None:
    """The three refusals that are about a PAIR of records rather than about either one.

    D15 rules the first: *"a key in both `add` and `suppress` is a **refusal**, not a precedence
    puzzle."* The other two are the same argument applied where it also holds — a document that
    declares one field twice has no honest tiebreak, and picking by position is the row-order
    dependence `LES-009` forbids one layer down.
    """
    for label, records in (("add", adds), ("override", overrides), ("suppress", suppress),
                           ("confirm", confirm), ("doubt", doubt)):
        seen: dict = {}
        for record in records:
            if record.merge_key in seen:
                raise AssertionsError(
                    f"{label}: {record.merge_key!r} is declared twice in one file. A save "
                    f"REPLACES a same-key record across files (D15); inside one file a duplicate "
                    f"is a contradiction with no honest tiebreak")
            seen[record.merge_key] = record

    suppressed = {r.key for r in suppress}
    both = sorted({r.key for r in adds} & suppressed)
    if both:
        raise AssertionsError(
            f"key(s) {both} appear in both `add` and `suppress`. That is a refusal, not a "
            f"precedence puzzle (D15): the file says the entity should exist and should not, and "
            f"nothing outside the file can decide which was meant")

    marked_both = sorted({r.key for r in confirm} & {r.key for r in doubt})
    if marked_both:
        raise AssertionsError(
            f"key(s) {marked_both} appear in both `confirm` and `doubt`. The file says the "
            f"entity is settled and is not, and nothing outside the file can decide which")

    add_fields = {(r.key, name) for r in adds for name in r.fields}
    clash = sorted(add_fields & {r.merge_key for r in overrides})
    if clash:
        raise AssertionsError(
            f"{clash} are declared by BOTH an `add` record's own field and an `override`. One "
            f"field, two hand-authored values, one class — there is no precedence between two "
            f"`asserted` readings, so this is refused rather than ordered. Drop one")


def parse_assertions(doc, *, path: Optional[str] = None) -> AssertionSet:
    """Validate a loaded YAML document into a frozen, canonically-ordered `AssertionSet`.

    Every refusal raises `AssertionsError` naming the offending value; there is no partial
    acceptance and no "skipped N unreadable records" mode, because a file that half-applied would
    produce a twin the operator did not author and could not tell from one they did.
    """
    doc = _require_mapping(doc, "assertions document")
    _unknown_keys(doc, ("schema", "imprint", "as_of", "source", "add", "override", "suppress",
                        "confirm", "doubt"),
                  "assertions document")
    schema = doc.get("schema")
    if schema not in ACCEPTED_SCHEMAS:
        raise AssertionsError(
            f"schema: expected one of {list(ACCEPTED_SCHEMAS)}, got {schema!r}. Fail closed on "
            f"an unknown schema rather than guessing which records it meant")
    if "as_of" not in doc:
        raise AssertionsError(
            "as_of: required — it is the snapshot's time bound, and an import with no time bound "
            "cannot be compared with anything else (`docs/WORK_ORDER_reference_ingest.md` §6)")
    as_of, as_of_epoch = _parse_as_of(doc["as_of"])
    source = doc.get("source")
    if not isinstance(source, str) or not source.strip().lower().startswith(SOURCE_PREFIX):
        raise AssertionsError(
            f"source: expected {SOURCE_PREFIX!r} followed by a name, got {source!r}. An "
            f"assertion's source is a PERSON, and saying so in the value is what lets a "
            f"scorecard's evidence mix tell 'an operator said so' from 'an export said so'")
    # `"operator: raghu"` and `"operator:raghu"` canonicalize to one spelling. Both appear in the
    # design note's own worked examples, and two spellings of one source would split a scorecard's
    # evidence mix and this module's own replace-by-source ingest into two.
    name = source.strip()[len(SOURCE_PREFIX):].strip()
    if not name:
        raise AssertionsError(f"source: {source!r} names no operator after {SOURCE_PREFIX!r}")
    source = SOURCE_PREFIX + name

    imprint = doc.get("imprint")
    if imprint is not None and (not isinstance(imprint, str) or not imprint.strip()):
        raise AssertionsError(f"imprint: expected a non-empty string, got {imprint!r}")

    for block in ("add", "override", "suppress", "confirm", "doubt"):
        if block in doc and not isinstance(doc[block], list):
            raise AssertionsError(
                f"{block}: expected a list of records, got {type(doc[block]).__name__}")
    adds = [_parse_add(r, i) for i, r in enumerate(doc.get("add") or [])]
    overrides = [_parse_override(r, i) for i, r in enumerate(doc.get("override") or [])]
    suppress = [_parse_suppress(r, i) for i, r in enumerate(doc.get("suppress") or [])]
    confirm = [_parse_mark(r, i, "confirm") for i, r in enumerate(doc.get("confirm") or [])]
    doubt = [_parse_mark(r, i, "doubt") for i, r in enumerate(doc.get("doubt") or [])]
    _cross_record_checks(adds, overrides, suppress, confirm, doubt)
    # A `/1` document is read as a `/2` one: the set it parses into carries the CURRENT schema
    # string, so a re-emit (a merge, the Console's save) writes `/2` and the file migrates on
    # its first save with nothing else changed.
    return AssertionSet(
        schema=ASSERTIONS_SCHEMA, imprint=imprint.strip() if isinstance(imprint, str) else None,
        as_of=as_of, as_of_epoch=as_of_epoch, source=source,
        adds=tuple(sorted(adds, key=lambda r: r.merge_key)),
        overrides=tuple(sorted(overrides, key=lambda r: r.merge_key)),
        suppress=tuple(sorted(suppress, key=lambda r: r.merge_key)),
        confirm=tuple(sorted(confirm, key=lambda r: r.merge_key)),
        doubt=tuple(sorted(doubt, key=lambda r: r.merge_key)),
        path=path)


def load_assertions(path) -> AssertionSet:
    """Read and validate one assertions file. `AssertionsError` on anything malformed.

    `yaml.safe_load`, never `load`: this file arrives from another repo's API endpoint and from an
    operator's editor, and a document that can construct Python objects is not a document.
    """
    text = open(path, "r", encoding="utf-8").read()
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise AssertionsError(f"{path}: not valid YAML ({exc})") from None
    if doc is None:
        raise AssertionsError(
            f"{path}: is empty. An assertions file always carries at least `schema`, `as_of` and "
            f"`source`; an absent file and an empty one are different states and only the first "
            f"means 'no assertions'")
    return parse_assertions(doc, path=str(path))


# -- merge and re-emit (D15) ----------------------------------------------------------------


def merge_assertions(base: AssertionSet, incoming: AssertionSet) -> AssertionSet:
    """D15: fold one save into the standing file.

    Keyed `(kind, key)` for `add`/`suppress`… — note the ruling says `(kind, key)` for `add` and
    the bare key for `suppress`, which is what `merge_key` returns on each record type. **A save
    REPLACES a same-key record and appends otherwise**, `as_of` is rewritten to the incoming
    save's, and the result is re-validated: a base that adds a key an incoming save suppresses is
    a refusal, exactly as it would be inside one file, and discovering that only at the next
    compile would be a worse place to find it.

    **Deletion is expressed by the whole file, not by a record**, and that is a real limit rather
    than an omission: an incoming save carries the records that should stand, so removing one
    means the writer sends a file without it — and a writer that only ever sends the record it
    just edited can never delete. axRange's Console holds the whole document in the editor and
    PUTs it back (`docs/WORK_ORDER_console_canvas.md` S1.3), so the shape it writes is the shape
    this merges; a partial-save writer would need a `remove:` block, which is not in
    `axr-assertions/1`.
    """
    merged_adds = {r.merge_key: r for r in base.adds}
    merged_adds.update({r.merge_key: r for r in incoming.adds})
    merged_over = {r.merge_key: r for r in base.overrides}
    merged_over.update({r.merge_key: r for r in incoming.overrides})
    merged_sup = {r.merge_key: r for r in base.suppress}
    merged_sup.update({r.merge_key: r for r in incoming.suppress})
    merged_conf = {r.merge_key: r for r in base.confirm}
    merged_conf.update({r.merge_key: r for r in incoming.confirm})
    merged_doubt = {r.merge_key: r for r in base.doubt}
    merged_doubt.update({r.merge_key: r for r in incoming.doubt})

    adds = sorted(merged_adds.values(), key=lambda r: r.merge_key)
    overrides = sorted(merged_over.values(), key=lambda r: r.merge_key)
    suppress = sorted(merged_sup.values(), key=lambda r: r.merge_key)
    confirm = sorted(merged_conf.values(), key=lambda r: r.merge_key)
    doubt = sorted(merged_doubt.values(), key=lambda r: r.merge_key)
    _cross_record_checks(adds, overrides, suppress, confirm, doubt)
    return AssertionSet(
        schema=ASSERTIONS_SCHEMA,
        imprint=incoming.imprint if incoming.imprint is not None else base.imprint,
        as_of=incoming.as_of, as_of_epoch=incoming.as_of_epoch, source=incoming.source,
        adds=tuple(adds), overrides=tuple(overrides), suppress=tuple(suppress),
        confirm=tuple(confirm), doubt=tuple(doubt),
        path=base.path)


def to_document(aset: AssertionSet) -> dict:
    """The plain document an `AssertionSet` re-emits as — canonical order, declared key order.

    Records are emitted with their keys in a FIXED order (`kind`, `key`, the content fields
    sorted, then `note`), not in whatever order the author typed. Together with the canonical
    record order that is what makes D15's *"a no-op save is byte-identical"* true rather than
    likely: the bytes are a function of the record content alone.

    **The limit that comes with canonicalizing: YAML comments do not survive a save.** D15's
    byte-identity claim holds for a document this function wrote — save it again and the bytes
    match — and not for a hand-commented file on its FIRST save, which is canonicalized and loses
    its comments. Stated rather than fixed: a comment-preserving round trip needs a round-tripping
    YAML parser (`ruamel`), which is a new runtime dependency for a repo that deliberately has
    two, and every record already carries a mandatory `note` that DOES survive. An author who
    wants their reasoning kept should put it in the `note`, which is also where a reader of the
    twin will look for it.
    """
    def _field_value(name: str, value):
        """The value as the FILE spells it, which is not always as the record holds it.

        `listen`/`dial` are parsed from `[{port, proto}]` into the `"<port>/<proto>"` strings the
        IR joins against, and re-emitting the parsed form would produce a document this module's
        own parser refuses — a round trip that does not round-trip, which a merge discovers the
        first time an operator saves a file containing one (found exactly that way).
        """
        if name in ("listen", "dial"):
            return [{"port": int(v.split("/")[0]), "proto": v.split("/")[1]} for v in value]
        return value

    doc: dict = {"schema": aset.schema}
    if aset.imprint:
        doc["imprint"] = aset.imprint
    doc["as_of"] = aset.as_of
    doc["source"] = aset.source
    if aset.adds:
        doc["add"] = [
            {"kind": r.kind, "key": r.key,
             **{name: _field_value(name, r.fields[name]) for name in sorted(r.fields)},
             "note": r.note}
            for r in aset.adds]
    if aset.overrides:
        doc["override"] = [
            {"key": r.key, "field": r.field, "value": r.value, "note": r.note}
            for r in aset.overrides]
    if aset.suppress:
        doc["suppress"] = [{"key": r.key, "note": r.note} for r in aset.suppress]
    if aset.confirm:
        doc["confirm"] = [{"key": r.key, "note": r.note} for r in aset.confirm]
    if aset.doubt:
        doc["doubt"] = [{"key": r.key, "note": r.note} for r in aset.doubt]
    return doc


def dump_assertions(aset: AssertionSet) -> str:
    """Byte-stable YAML for an `AssertionSet`.

    `sort_keys=False` so `to_document`'s declared key order survives — the same reason
    `synth/compiler.py::to_yaml` sets it, and the reason a re-emit does not reorder `schema`
    under `add`.
    """
    return yaml.safe_dump(to_document(aset), sort_keys=False, default_flow_style=False,
                          allow_unicode=True)


# -- the belief-store projection (D14) ------------------------------------------------------

#: Which assertion facts have a predicate the belief store's own readers already consume, and
#: what each becomes. **Deliberately partial**, and the gap is reported rather than papered over:
#: `snapshot()` reads `in_segment`, `has_role`, `listens_on` and `dials_port` and nothing else, so
#: those four round-trip into a materialized PIT while `image`, `resources`, `zone_type`, `name`,
#: conduits and suppressions have no store consumer at all. Minting predicates for them would put
#: rows in the store that nothing reads, which is `LES-101`'s "documentation in a data costume" —
#: the assertions FILE stays their authority, and `cast --assertions` is what applies them.
STORE_PREDICATES: dict[str, str] = {
    "segment": "in_segment",
    "role": "has_role",
    "listen": "listens_on",
    "dial": "dials_port",
}


def store_rows(aset: AssertionSet) -> tuple[list[dict], dict[str, int]]:
    """Project an `AssertionSet` into belief-store `assertions` rows (D14).

    Returns `(rows, uncarried)` — the rows, and a count per record kind of what has no store
    predicate. **No belief-store column changes**: `(window_start, subject, predicate, object,
    evidence_class, confidence, source)` already carries all of it, with the file's `as_of` as the
    time bound and `confidence` 1.0 by convention.

    **What this CANNOT do, stated here because it is the difference between the two doors an
    assertion can come through.** A row is a fact ABOUT a subject; it does not mint one. An
    entity reaches a materialized PIT through `node_features` (`BeliefStore.snapshot`'s
    `entities`), so an `add: host` for an address the capture never saw writes rows that join to
    nothing and the host does not appear in the PIT. The cast path (`cast --assertions`) is what
    adds hosts, segments and conduits to a twin; this path is what makes an override of a
    DISCOVERED entity's role or segment survive re-materialization. Both are wanted, they are not
    substitutes, and the counts returned here say which records took which door.
    """
    rows: list[dict] = []
    uncarried: dict[str, int] = {}

    def note(kind: str) -> None:
        uncarried[kind] = uncarried.get(kind, 0) + 1

    def emit(subject: str, field: str, value) -> None:
        predicate = STORE_PREDICATES[field]
        for obj in (value if isinstance(value, list) else [value]):
            rows.append({"window_start": aset.as_of_epoch, "subject": subject,
                         "predicate": predicate, "object": str(obj),
                         "evidence_class": ASSERTIONS_EVIDENCE_CLASS,
                         "confidence": ASSERTED_CONFIDENCE,
                         "source": aset.source})

    for record in aset.adds:
        if record.kind != "host":
            note(f"add:{record.kind}")
            continue
        carried = False
        for name in sorted(record.fields):
            if name in STORE_PREDICATES:
                emit(record.key, name, record.fields[name])
                carried = True
        if not carried:
            note("add:host")
    for record in aset.overrides:
        if record.kind == "host" and record.field in STORE_PREDICATES:
            emit(record.key, record.field, record.value)
        else:
            note(f"override:{record.kind}.{record.field}")
    for _record in aset.suppress:
        note("suppress")
    for _record in aset.confirm:
        note("confirm")
    for _record in aset.doubt:
        note("doubt")
    # Sorted at the persistence boundary so the rows a store receives depend on their content
    # rather than on the order three loops happened to run in (`LES-009`).
    rows.sort(key=lambda r: (r["subject"], r["predicate"], r["object"]))
    return rows, dict(sorted(uncarried.items()))


def canonical(value) -> str:
    """A stable string for any assertion value — used as a grouping-key component in the cast.

    `sort_keys=True` because a `resources` mapping is the one value shape whose Python dict order
    comes from the file, and a group key that varied with it would rename a node group between two
    compiles of one file (`LES-002`).
    """
    return json.dumps(value, sort_keys=True, default=str)
