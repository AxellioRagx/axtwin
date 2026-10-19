"""Loader and matcher for `roles.yaml` (`docs/WORK_ORDER_synth_v1.md` phase 2).

Validation is the reason this is code rather than a bare `yaml.safe_load`. A catalog read
without checking is a silent-default trap wearing a data file's extension: a typo'd `requires`
key yields a rule that matches everything, a misspelled `confidenc` yields a rule with no
confidence at all, and neither raises. Every field is checked on load and an unrecognized key
is REFUSED (CONVENTIONS §3, positive gates) — the same closed-vocabulary posture axRange's own
`twin_config` schema takes with `extra="forbid"`, and for the same reason: this file's whole
job is to be edited by hand.

Loaded once at import and frozen. The catalog is run-invariant content, and a table that could
be re-read mid-run is a table whose contents could differ between two windows of one capture
(`LES-002`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

_CATALOG_PATH = Path(__file__).with_name("roles.yaml")

#: Every key a `roles[]` entry may carry. A row with anything else is a typo or a field
#: somebody added without a consumer, and both are refused rather than ignored.
_ROW_KEYS = frozenset({
    "role", "persona", "persona_cadence", "requires", "dials", "confidence",
    "measured_on_baseline", "image", "resources", "install",
})
#: Of those, the ones a row cannot omit. `persona` is deliberately NOT here: `null` is a real,
#: meaningful value (a role axRange ships no persona for — `dns-resolver`), so requiring the
#: KEY while allowing a null VALUE is what keeps "no persona exists" distinct from "nobody
#: filled this in" (`LES-006`).
_REQUIRED_KEYS = frozenset({
    "role", "persona", "persona_cadence", "requires", "confidence", "measured_on_baseline",
    "image",
})

#: axRange's own cadence vocabulary, as seconds of capture a persona needs before it can have been
#: observed at all (D12). `24x7` is 0 because a continuous emitter shows up immediately; `diurnal`
#: is a day because a daily cycle cannot be seen in less than one.
_NAMED_CADENCES = {"24x7": 0, "diurnal": 86_400}
#: `cyclic:`/`burst:` durations. Parsed, never pattern-matched (`LES-008`): a cadence spelled
#: `cyclic:2H` or `every 2h` must fail to LOAD rather than quietly compare as zero and disable
#: the warning it exists to raise.
_CADENCE_UNITS = {"s": 1, "m": 60, "h": 3_600, "d": 86_400}
_CADENCE_RE = re.compile(r"^(?:cyclic|burst):(\d+)([smhd])$")


class CatalogError(Exception):
    """A catalog file that cannot be trusted. Raised at import: fail closed, loudly, before
    any window is processed, rather than at the first row that happens to need the bad rule."""


@dataclass(frozen=True)
class RoleRule:
    """One catalog row: the evidence, the role, and the twin content that role implies."""

    role: str
    persona: Optional[str]
    #: The persona's slowest emit cadence, in axRange's own spelling. `None` iff `persona` is.
    persona_cadence: Optional[str]
    #: That cadence in seconds — how long a capture must be for the persona to have been
    #: observable at all (D12). `None` where there is no persona, `0` for a `24x7` one.
    min_capture_seconds: Optional[int]
    requires: frozenset[str]
    #: Ports the host must have DIALLED as originator (`docs/JOURNAL_imprint_loop.md` GAP-L5).
    #: Empty for an ordinary listen-based row. A row may carry `requires`, `dials`, or both;
    #: both sides are subset tests and both must hold.
    dials: frozenset[str]
    confidence: float
    measured_on_baseline: bool
    image: str
    resources: dict
    install: tuple

    def matches(self, listen_ports, connect_ports=()) -> bool:
        """True when every required port was observed. Subset, never equality — a real
        domain controller serves a dozen ports and no fixed signature survives one.

        `dials` is the same test against the ports the host ORIGINATED to, and it is what lets
        a pure client — which accepts nothing and is therefore invisible to a listen census —
        carry a role at all (GAP-L5). A row with an empty `dials` is unaffected: the empty set
        is a subset of anything.
        """
        return (self.requires <= set(listen_ports)
                and self.dials <= set(connect_ports))


def _port(value, where: str) -> str:
    """A `<port>/<proto>` label, in the exact spelling `windows._close` publishes.

    Parsed, not shape-matched (`LES-008`): a rule written `"445"` or `"445/TCP"` would simply
    never match anything, and a rule that silently never fires is worse than one that fails to
    load — it looks like the evidence was absent.
    """
    if not isinstance(value, str) or value.count("/") != 1:
        raise CatalogError(f"{where}: port must be '<port>/<proto>', got {value!r}")
    port, proto = value.split("/")
    if not port.isdigit():
        raise CatalogError(f"{where}: port must be numeric, got {value!r}")
    if proto != proto.lower() or not proto.isalpha():
        raise CatalogError(f"{where}: proto must be lowercase letters, got {value!r}")
    return value


def _cadence_seconds(value, where: str) -> Optional[int]:
    """`"diurnal"` -> 86400, `"cyclic:15m"` -> 900, `None` -> `None`. Anything else raises.

    Refusing an unrecognized spelling is the point (CONVENTIONS §3, positive gates). A cadence
    this build cannot parse would otherwise become "0 seconds needed", silently disabling D12's
    warning for exactly the persona whose cadence somebody just wrote in a new form.
    """
    if value is None:
        return None
    text = str(value)
    if text in _NAMED_CADENCES:
        return _NAMED_CADENCES[text]
    match = _CADENCE_RE.match(text)
    if not match:
        raise CatalogError(
            f"{where}: `persona_cadence` must be one of {sorted(_NAMED_CADENCES)} or "
            f"'cyclic:<n><s|m|h|d>' / 'burst:<n><s|m|h|d>', got {value!r}")
    return int(match.group(1)) * _CADENCE_UNITS[match.group(2)]


def _load(path: Path = _CATALOG_PATH) -> tuple[RoleRule, ...]:
    try:
        raw = yaml.safe_load(path.read_text())
    except OSError as exc:
        raise CatalogError(f"{path}: unreadable ({exc})") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("roles"), list):
        raise CatalogError(f"{path}: expected a top-level `roles:` list")
    rules = []
    for i, row in enumerate(raw["roles"]):
        where = f"{path}: roles[{i}]"
        if not isinstance(row, dict):
            raise CatalogError(f"{where}: not a mapping")
        if unknown := set(row) - _ROW_KEYS:
            raise CatalogError(f"{where}: unrecognized key(s) {sorted(unknown)}")
        if missing := _REQUIRED_KEYS - set(row):
            raise CatalogError(f"{where}: missing required key(s) {sorted(missing)}")
        requires = row["requires"]
        dials = row.get("dials") or []
        if not isinstance(requires, list):
            raise CatalogError(f"{where}: `requires` must be a list")
        if not isinstance(dials, list):
            raise CatalogError(f"{where}: `dials` must be a list")
        # One of the two must carry evidence. A row with neither matches EVERY entity — the
        # exact silent-default trap this loader exists to refuse (a rule that fires on
        # everything reads downstream as "the evidence was there", which it never was).
        if not requires and not dials:
            raise CatalogError(
                f"{where}: a row must declare evidence — `requires` (ports it ANSWERS on), "
                "`dials` (ports it CONNECTS to), or both; both empty matches every host")
        conf = row["confidence"]
        if not isinstance(conf, (int, float)) or isinstance(conf, bool) or not 0 < conf <= 1:
            raise CatalogError(f"{where}: `confidence` must be a number in (0, 1], got {conf!r}")
        if not isinstance(row["measured_on_baseline"], bool):
            raise CatalogError(f"{where}: `measured_on_baseline` must be a bool")
        # `persona_cadence` is null exactly when `persona` is: a role with no persona has no
        # cadence to compare a capture against, and a cadence for a persona that does not exist
        # would be a number about nothing. Checked rather than assumed, because the pair is
        # hand-edited in two places in one row.
        if (row["persona"] is None) != (row["persona_cadence"] is None):
            raise CatalogError(
                f"{where}: `persona` and `persona_cadence` must be null together "
                f"(persona={row['persona']!r}, persona_cadence={row['persona_cadence']!r})")
        rules.append(RoleRule(
            role=str(row["role"]),
            persona=None if row["persona"] is None else str(row["persona"]),
            persona_cadence=(None if row["persona_cadence"] is None
                             else str(row["persona_cadence"])),
            min_capture_seconds=_cadence_seconds(row["persona_cadence"], where),
            requires=frozenset(_port(p, where) for p in requires),
            dials=frozenset(_port(p, where) for p in dials),
            confidence=float(conf),
            measured_on_baseline=row["measured_on_baseline"],
            image=str(row["image"]),
            resources=dict(row.get("resources") or {}),
            install=tuple(row.get("install") or ()),
        ))
    # MOST SPECIFIC FIRST. `sorted` is stable, so rows demanding the same number of ports keep
    # the order the file declares — which is what lets the author break a tie deliberately and
    # what keeps the result independent of anything but the file's own bytes (`LES-002`).
    # Listen evidence outranks dial evidence, always: a host that ANSWERS on a port is that
    # service, whereas a host that dials one is merely its client, and every server is also
    # somebody's client. Sorting on `-len(requires)` first puts every dial-only row (0
    # requires) after every listen-bearing row, so a DC can never be demoted to "a host that
    # talks to a DC". Within each band, more evidence still wins.
    return tuple(sorted(rules, key=lambda r: (-len(r.requires), -len(r.dials))))


@dataclass(frozen=True)
class SyntheticRole:
    """Content for a role the COMPILER invents, with no evidence behind it.

    Deliberately not a `RoleRule`: a rule is an evidence statement (`requires`/`dials`/
    `confidence`/`measured_on_baseline`) and there is no evidence here to state. It carries
    only the fields a `twin_config` node needs, plus the two a compiler asks every role for --
    `persona` and `min_capture_seconds`, both permanently `None`, so `_rule_for`'s callers can
    treat it exactly like a catalog row without a type test. A synthetic role never emits: it
    exists to be TALKED TO.

    It is unreachable from `match_role` by construction, because it is not in `ROLE_CATALOG`.
    """

    role: str
    image: str
    resources: dict
    install: tuple
    #: Always None. A synthetic node runs no persona, which also means no cadence and so no
    #: D12 "the capture was too short to have seen this" warning to raise about it.
    persona: Optional[str] = None
    persona_cadence: Optional[str] = None
    min_capture_seconds: Optional[int] = None


def _load_synthetic(path: Path = _CATALOG_PATH) -> dict:
    """The `synthetic:` block — content for roles the compiler invents.

    Validated with the same closed vocabulary as `default:`, and ABSENT is legal: a catalog
    that invents no roles is a complete catalog, not a broken one.
    """
    raw = yaml.safe_load(path.read_text()) or {}
    block = raw.get("synthetic") or {}
    if not isinstance(block, dict):
        raise CatalogError(f"{path}: `synthetic:` must be a mapping of role -> content")
    out: dict[str, SyntheticRole] = {}
    for role, content in block.items():
        where = f"{path}: synthetic[{role!r}]"
        if not isinstance(content, dict):
            raise CatalogError(f"{where}: not a mapping")
        if unknown := set(content) - {"image", "resources", "install"}:
            raise CatalogError(f"{where}: unrecognized key(s) {sorted(unknown)}")
        if not content.get("image"):
            raise CatalogError(f"{where}: must declare an `image` — a node cannot omit one")
        # A synthetic role that ALSO appears as an inference rule would be inferrable from a
        # real host's ports, which is precisely what holding it out of `roles:` prevents.
        # Refused rather than resolved by precedence, because either precedence is a surprise.
        if any(r["role"] == role for r in raw.get("roles") or []):
            raise CatalogError(
                f"{where}: {role!r} is also an inference rule under `roles:`. A synthetic role "
                f"must not be inferrable from observed evidence — pick a different name.")
        out[str(role)] = SyntheticRole(
            role=str(role), image=str(content["image"]),
            resources=dict(content.get("resources") or {}),
            install=tuple(content.get("install") or ()))
    return out


def _load_default(path: Path = _CATALOG_PATH) -> dict:
    """The `default:` block — the ROLE and the content a node with no matching rule gets.

    Validated with the same closed vocabulary as a role row, minus the evidence half: a default
    has no `requires`/`confidence` because nothing inferred it. Refused if absent, because a
    compiler that reached this path with nothing to use would have to invent an image.

    `role` is REQUIRED (`docs/WORK_ORDER_deployment_scope.md` D4): since the cast emits every
    placed host, a role-less population needs a name to group and to deploy under. It must not
    collide with an inference rule, for the same reason a `synthetic:` role must not — a role
    that both "nothing matched" and "this evidence matched" can produce is two populations under
    one name, and `match_role` must be structurally unable to return it.
    """
    raw = yaml.safe_load(path.read_text()) or {}
    block = raw.get("default")
    if not isinstance(block, dict):
        raise CatalogError(f"{path}: expected a top-level `default:` mapping")
    if unknown := set(block) - {"role", "image", "resources", "install"}:
        raise CatalogError(f"{path}: default has unrecognized key(s) {sorted(unknown)}")
    if not block.get("image"):
        raise CatalogError(f"{path}: default must declare an `image` — a node cannot omit one")
    if not block.get("role"):
        raise CatalogError(
            f"{path}: default must declare a `role` — every placed host is emitted, and a host "
            f"that matched no rule still needs a name to group and deploy under (D4)")
    role = str(block["role"])
    if any(r["role"] == role for r in raw.get("roles") or []):
        raise CatalogError(
            f"{path}: default role {role!r} is also an inference rule under `roles:`. The "
            f"role a host gets when NOTHING matched must not be one some evidence can name — "
            f"pick a different name.")
    # And not a SYNTHETIC role either, which is the sharper of the two collisions: the compiler
    # decides whether a node group is compiler-class by asking `role in SYNTHETIC_ROLES`, so a
    # default role sharing that name would make every role-less host's group take the sink's
    # naming, its `count` (parsed out of the role name) and its `observed: 0` accounting.
    if role in (raw.get("synthetic") or {}):
        raise CatalogError(
            f"{path}: default role {role!r} is also a `synthetic:` role. The compiler tells "
            f"compiler-class groups from observed ones by that name — pick a different one.")
    return {"role": role, "image": str(block["image"]),
            "resources": dict(block.get("resources") or {}),
            "install": tuple(block.get("install") or ())}


#: The catalog, ordered most-specific-first. Frozen at import.
ROLE_CATALOG: tuple[RoleRule, ...] = _load()

_DEFAULT_BLOCK: dict = _load_default()

#: The role a placed host carries when no evidence rule matched (D4). Deliberately NOT
#: `generic-endpoint`, which is an inferred role with a traffic-generating persona: a host that
#: matched nothing did not match that either. An image rule and no persona — see `roles.yaml`.
UNROLED_ROLE: str = _DEFAULT_BLOCK["role"]

#: Content for a node no rule matched. 178 of 197 placed hosts on the real baseline. Content
#: ONLY — the role it confers is `UNROLED_ROLE`, kept separate so a caller cannot splice a
#: `role` key into a node body by copying this dict.
DEFAULT_NODE: dict = {k: v for k, v in _DEFAULT_BLOCK.items() if k != "role"}

#: `{role: SyntheticRole}` — content for roles the COMPILER invents, which no evidence names
#: and `match_role` can never return. Today: the `internet-sink-*` family (GAP-C17).
SYNTHETIC_ROLES: dict[str, SyntheticRole] = _load_synthetic()


def match_role(listen_ports, connect_ports=()) -> Optional[RoleRule]:
    """The most specific rule whose required ports were all observed, or None.

    **None is a supported, expected outcome, not a failure.** `twin_config`'s `role:` is
    `Optional`, so a host whose ports name no role reaches the twin without one, and axRange's
    own `roles.yaml` can supply it later. Measured on the 2026-08-11 baseline: 29 entities have
    listen evidence and roughly two thirds match a rule — inventing a role for the rest would
    trade a declared gap for a fabricated fact, which is the one thing this project's
    provenance discipline exists to prevent.
    """
    for rule in ROLE_CATALOG:
        if rule.matches(listen_ports, connect_ports):
            return rule
    return None
