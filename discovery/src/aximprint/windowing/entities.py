"""Entity resolution v1: addresses -> time-bounded host entities. DESIGN_BRIEF §5.1, `Q1`.

Everything above this module speaks **addresses**; everything below it speaks **entities**, and
the synthesizer casts one twin host per entity. v0 was the identity function (`entity == ip`) with
a hardcoded `ambiguity_rate() -> 0.0`; this is the real resolver, built to
`docs/WORK_ORDER_entity_resolution.md` and its four forks, ratified as drafted:

* **D1 — only HOST-scoped evidence may merge two addresses.** A user principal is a person and a
  DNS answer is a third party's address; neither anchors a host. Union-on-any-shared-identifier
  would collapse a CDN's A records into one entity and two workstations sharing a login into one
  host, which is why evidence is CLASSIFIED (`IDENTITY_SCOPES`) before it is ever weighted.
* **D2 — causal and forward-only.** Resolution uses evidence seen so far. A merge learned in
  window 40 is emitted as a `same_entity_as` assertion (`take_merges`) and applied by the snapshot
  reader; windows 1-39 keep the ids they closed with. Window close stays a pure function of
  (events, resolver state, code build) and `Q2`'s streaming win is not re-spent on a second pass.
* **D3 — ids are minted sequentially in first-observation order** (`ent-0001`), stable within a
  run and explicitly **NOT across captures**; the roster says so in the artifact (`roster()`'s
  own `id_scope`), so nobody has to infer it.
* **D4 — an address with no host-scoped evidence resolves to itself**, exactly as v0 did. This is
  the safety property that makes the change reviewable: on a capture with no host-scoped evidence
  the output is byte-identical to v0's, so every difference is a merge with a name attached.

**The vantage that motivated the work order is not the vantage this shipped against.** That draft
measured `dhcp.log` ABSENT and concluded most identity evidence here cannot anchor a host. The
2026-08-11 baseline has 455 `dhcp.log` rows, `ntlm.log`, and Kerberos MACHINE accounts — host-
scoped evidence on three independent sources — so D4's null test is *not* vacuous here and the
resolver actually resolves. See `docs/WORK_ORDER_entity_resolution.md`'s findings section.

## Why an identifier is normalized, and why that is a parse rather than a pattern-match

The same host is named differently by different sensors: Kerberos calls it `WORKSTATION$` (the
Active Directory computer-account convention — a SAM account name with a `$` suffix), NTLM calls
it `workstation.corp.example.com` (an FQDN), DHCP calls it whatever the client put in option 12.
Left as raw strings those are three identifiers, three entities, and one host torn into three.
Measured on the baseline: **7 addresses carry BOTH an NTLM hostname and a Kerberos machine
principal**, so this is the common case here, not a corner.

So each source's own format is PARSED down to the computer name it actually contains
(`computer_name`, `kerberos_identifier`) and the result is the resolver's key, while `raw` keeps
what the sensor said, verbatim, on every emitted record. `LES-008` is the rule this obeys — the
Kerberos split is a parse of a principal's grammar (`components/realm`, primary ending `$`, or a
`host/` service principal), never a regex over an opaque blob, and a principal whose primary
parses as neither is a USER, counted as such and anchoring nothing.

## What links two identifiers (and what deliberately does not)

Only one thing: **appearing in the SAME sensor row** (`_link`). One `dhcp.log` row asserting a mac
and a host name is a direct observation that they name one host; nothing is inferred from mere
co-location in time at one address, which is exactly the inference address REUSE would poison.
That single rule is enough for every case the work order's validation names, and it needs no
"contemporaneous" horizon parameter to tune or defend.

Two identifiers in the same namespace at one address (two macs, two host names) are ALTERNATIVES,
never a link: the second supersedes the first, closing its generation, and the address yields two
entities. That is the whole point of generations — an address reused by a second host must not
become one entity with a confused history.

## Ambiguity means something now

`resolver_ambiguity` is the fraction of resolutions that landed on an address with **more than one
host-scoped binding open at that ts, belonging to different entities**, over the resolutions that
landed on an address with **at least one** open host-scoped binding — and `None` when that
denominator is zero (`LES-006`: no identity evidence resolved anything this window is not the same
finding as "resolution found no ambiguity"). The denominator is deliberately NOT all resolved flow
endpoints, which the work order proposed: under D4 the overwhelming majority of endpoints have no
host-scoped evidence at all, so that denominator would make one genuinely disputed address read as
~0.000001 — a number measuring how FEW hosts are identified rather than how ambiguous the
identified ones are, which is not the question the field's name asks (`LES-015`).

Supersession is what keeps this from firing spuriously: a new host-scoped binding closes every
open one at that address belonging to a different entity, so an address normally has exactly one.
The one thing that does NOT close is a binding whose DECLARED validity has not expired — a DHCP
lease still running. Two claims genuinely overlapping is then the only way ambiguity arises, which
makes it a real finding rather than a restatement of ordinary churn, and it is why an unexpired
lease also WINS the resolution (`_precedence`): DHCP is authoritative for address assignment.

## Bounds

Two bounds, because the two populations are unrelated (`LES-014`) — but they are **different kinds
of bound**, and conflating them was a real defect until 2026-08-14.

`MAX_HOST_BINDINGS` caps the resolver's **working state**, the map every `resolve()` consults. It
is a genuine memory bound on a hot structure, and it REFUSES past the cap: a host binding that
cannot be held cannot participate in resolution, so refusing it loses information and
`roster()["state"]["complete"]` goes false.

`NONHOST_WORKING_SET` bounds a **dedup window**, not a census. Nothing consults it; it exists so a
repeated DNS answer extends one interval instead of opening thousands. Eviction is LRU and
**lossless** — the evicted binding is emitted as a `final` record with its accumulated counters, so
the store receives it in full and `complete` stays true. What eviction costs is interval
fragmentation (one pair may appear as several records whose counts sum correctly), never data.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from aximprint.events import IdentityEvent

#: What a piece of identity evidence can ANCHOR — classified before it is weighted, which is the
#: work order's central inversion of `Q1`'s own framing. A closed vocabulary, modelled on
#: `events.FLOW_STATE_CLASSES`: a kind outside `IDENTITY_SCOPES` below is COUNTED
#: (`unscoped_identity_kinds`), never bucketed into the nearest neighbour.
HOST = "host"              # names the machine itself: a mac, a computer name. May merge (D1).
USER = "user"              # names a person: a Kerberos user principal. Recorded, never merges.
REFERENCED = "referenced"  # names a THIRD PARTY's address: a DNS answer. Recorded, never merges.

#: scope -> the `evidence_class` an assertion derived from it carries (`learners/extractors.py`).
#: This is where `referenced` finally gets a code path: it has been in the vocabulary, and in
#: DESIGN_BRIEF §6's uniform assertion shape, since v0 with **no producer at all** — every
#: assertion this project emitted was `observed`, including `bound_dns`, a claim about a host the
#: sensor never saw at that address. "Imputed never masquerades as observed" was two-thirds
#: implemented; a DNS-derived binding is now `referenced` at the source.
#:
#: `USER` maps to `observed`, not to something weaker, and the distinction is worth stating: a
#: Kerberos user principal IS observed at the address that sent the request — it is perfectly good
#: evidence, of a PERSON rather than of a machine. Scope answers "what can this anchor";
#: evidence_class answers "did we see it". They are different questions and this table is the one
#: place they meet (`LES-015`).
SCOPE_EVIDENCE_CLASS: dict[str, str] = {
    HOST: "observed",
    USER: "observed",
    REFERENCED: "referenced",
}

#: Identifier namespaces. Two identifiers can only ever be the same identifier within one of
#: these; across namespaces they are linked by co-observation (`_link`) or not at all.
NS_MAC = "mac"              # a hardware address (DHCP)
NS_HOSTNAME = "hostname"    # a computer name (DHCP option 12, NTLM, a Kerberos machine account)
NS_PRINCIPAL = "principal"  # a Kerberos user principal, verbatim
NS_DNSNAME = "dnsname"      # a queried name that resolved to this address

#: Host-scoped bindings — the resolver's WORKING state, the map every `resolve()` consults.
#: Measured on the 2026-08-11 baseline: **41 bindings, resolving to 25 entities** across DHCP,
#: NTLM and Kerberos machine accounts, so this is ~100x headroom, not a working limit. The
#: unit that can blow it up is (addresses on the monitored network) x (identifier churn per
#: address): 4,096 covers a /20 with one generation each, or a /24 churning 16 times over. Past
#: that, a vantage is seeing a network large enough that per-address interval state is the wrong
#: structure entirely and the sketch layer is the right one — a cap is the honest place to say so,
#: at roughly 1 MB of retained records.
MAX_HOST_BINDINGS = 4096

#: User- and referenced-scoped bindings — RECORDED for the store, **never consulted by
#: `resolve()`** (verified: zero references). A different population entirely from the host-scoped
#: map above, and — since 2026-08-14 — a different KIND of bound, which is why it was renamed.
#:
#: **This is a working-set bound, not a census cap.** It was `MAX_NONHOST_BINDINGS`, and it
#: REFUSED new bindings once full: first-come-first-served, so a long capture kept whatever DNS
#: names appeared in its first hours and discarded every later one. That is not a sample of a
#: capture, it is its earliest slice — and over a 41-day run it would have made the census close
#: to useless while reporting itself merely "incomplete". Worse, it capped exactly the
#: referenced-but-unseen census that `docs/WORK_ORDER_visibility_and_vantage.md` D7 names as the
#: primary input to DESIGN_BRIEF §8's visibility model.
#:
#: Now the map is a bounded **LRU dedup window** and eviction is **lossless to the store**: the
#: evicted binding is emitted as a `final` record carrying its accumulated interval and count,
#: exactly as `flush_open` does at end of run (`LES-108` — a delta artifact must flush what it
#: stops tracking, or the archive says it was seen once). A key observed again after eviction
#: opens a NEW interval, so one (address, name) pair may reach the store as several records whose
#: counts sum to the true total. That is an honest gap, visible in the data, rather than a silent
#: absence.
#:
#: The bound therefore governs **memory**, never what the artifact contains, which is the split
#: `docs/WORK_ORDER_visibility_and_vantage.md` D9 requires: filtering belongs at compile time, not
#: upstream of the IR. Measured for scale: 6,414 distinct (answer address, queried name) pairs
#: from 144,886 DNS-derived events over ~3 h, so 16,384 holds ~8 h of this vantage resident before
#: the LRU starts turning over — and turning over now costs interval fragmentation, not data.
NONHOST_WORKING_SET = 16384


@dataclass(frozen=True, slots=True)
class Identifier:
    """One name for a subject, as extracted from one sensor row.

    `value` is normalized and is what the resolver keys on; `raw` is exactly what the sensor said
    and is what every emitted record publishes as `bound_to`. Keeping both is not redundancy —
    publishing only the derived form would make the observation unrecoverable downstream, and
    keying on the raw form is what tears one host into three (module docstring).
    """
    namespace: str
    value: str
    scope: str
    raw: str

    @property
    def key(self) -> str:
        return f"{self.namespace}:{self.value}"


def computer_name(value: str) -> str:
    """A computer name in any of the three shapes this pipeline sees -> its normalized short name.

    Three transformations, each a parse of a documented convention rather than a guess at a shape
    (`LES-008`):

      * a trailing `$` is Active Directory's SAM convention for a COMPUTER account, not part of
        the name (`WORKSTATION$` and `WORKSTATION` are one host);
      * everything from the first `.` is the DNS suffix, not the host's own name — NTLM reports
        `server_dns_computer_name` as an FQDN while Kerberos and NetBIOS report the short name;
      * case is not significant in DNS or NetBIOS, and the two sources disagree about it by
        convention (NetBIOS uppercases, DNS does not), so the key is lowercased.

    Returns `""` for anything that normalizes away to nothing, which every caller treats as "this
    row carried no usable name" rather than minting an empty identifier.
    """
    name = value.strip()
    if name.endswith("$"):
        name = name[:-1]
    return name.split(".", 1)[0].lower()


def kerberos_identifier(client: str) -> Identifier | None:
    """A Kerberos `client` field -> the one identifier it carries, and what that identifier can
    anchor.

    **This is the machine-vs-user split, and it decides whether Kerberos can anchor a host at
    all.** Measured on the 2026-08-11 baseline: 86 rows name a machine account, 9 name a user, so
    both branches are live here — the work order's finding #1 ("is any Kerberos principal a
    machine account?") answers YES, and this vantage is therefore NOT one where identity evidence
    is provably an identity function.

    Parsed, not pattern-matched (`LES-008`). Zeek's `client` is the principal joined to its realm
    with `/`; RFC 4120 principals are themselves `component[/component...]`, so the LAST component
    is the realm and the rest are the principal proper. Three outcomes:

      * primary ends with `$` -> an AD computer account -> `hostname`, HOST scope;
      * primary is `host` with a following component -> a host service principal
        (`host/fqdn@REALM`) -> that component's short name, `hostname`, HOST scope;
      * anything else -> a USER principal, kept VERBATIM as the identifier.

    The user branch deliberately does not normalize case: RFC 4120 principal names are
    case-sensitive, so folding them would be inventing an equivalence the protocol does not grant.
    It costs nothing to be exact here — a user identifier anchors no entity either way (D1).

    Returns `None` when the field carries nothing parseable, so the caller mints no identifier
    rather than one keyed on an empty string.
    """
    parts = [p for p in client.split("/") if p]
    if not parts:
        return None
    components = parts[:-1] if len(parts) > 1 else parts
    primary = components[0]
    if primary.endswith("$"):
        name = computer_name(primary)
        return Identifier(NS_HOSTNAME, name, HOST, client) if name else None
    if primary.lower() == "host" and len(components) > 1:
        name = computer_name(components[1])
        return Identifier(NS_HOSTNAME, name, HOST, client) if name else None
    return Identifier(NS_PRINCIPAL, client.strip(), USER, client)


def radius_username(username: str) -> Identifier | None:
    """A RADIUS `username` -> the one identifier it carries, and what that identifier can anchor.

    **A RADIUS username is NOT a Kerberos principal and is deliberately not parsed by
    `kerberos_identifier`.** Zeek's `kerberos.client` is `component[/component...]` joined to a
    realm by `/`; a RADIUS username follows RFC 4282's NAI (`user@realm`) or Microsoft's
    down-level logon name (`DOMAIN\\user`), and 802.1X machine authentication puts an AD computer
    account through the same field. Running one grammar's parser over the other's strings is
    exactly the shape-guessing `LES-008` forbids, so this is its own parse of two documented
    conventions:

      * strip the NAI realm (everything from the LAST `@`) -- `host/pc.corp@REALM` keeps
        `host/pc.corp`, so the machine branches below still see what they need;
      * strip a down-level `DOMAIN\\` prefix;
      * then, the same AD computer-account conventions `computer_name` documents: a primary
        ending `$` is a machine, and `host/<fqdn>` is a machine service principal -- both HOST
        scope; anything else is a person, USER scope, kept verbatim.

    Returns `None` for an unparseable or empty field, so the caller mints nothing rather than an
    identifier keyed on an empty string.
    """
    raw = username.strip()
    if not raw:
        return None
    local = raw.rsplit("@", 1)[0] if "@" in raw else raw
    if "\\" in local:
        local = local.rsplit("\\", 1)[-1]
    local = local.strip()
    if not local:
        return None
    if local.endswith("$"):
        name = computer_name(local)
        return Identifier(NS_HOSTNAME, name, HOST, username) if name else None
    head, sep, rest = local.partition("/")
    if sep and head.lower() == "host" and rest:
        name = computer_name(rest)
        return Identifier(NS_HOSTNAME, name, HOST, username) if name else None
    return Identifier(NS_PRINCIPAL, local, USER, username)


def _identifiers_radius(ev: IdentityEvent) -> list[Identifier]:
    """A RADIUS row carries up to TWO identifiers of DIFFERENT scopes, and that is the point.

    `mac` names the machine (HOST); `username` names whoever authenticated, which is a person
    (USER) unless the AD conventions above say it is a machine account. Both arrive in one row,
    so `_link` joins them by direct observation exactly as it does for DHCP.

    The row's SUBJECT -- the address these identifiers are about -- is `framed_addr`, resolved in
    `ingest/zeek/plugin.py::_radius` before the event is ever built. Nothing here needs to know
    that, which is the point of doing it there: an identifier's scope and its subject are two
    different questions and they are answered in two different places.
    """
    out: list[Identifier] = []
    if ev.mac:
        out.append(Identifier(NS_MAC, ev.mac.strip().lower(), HOST, ev.mac))
    if ev.principal:
        ident = radius_username(ev.principal)
        if ident is not None:
            out.append(ident)
    return out


def _identifiers_dhcp(ev: IdentityEvent) -> list[Identifier]:
    """A DHCP row carries up to TWO identifiers, and that is the point.

    The mac and the client-supplied host name arrive in ONE row, so `_link` treats them as one
    host by direct observation — the only cross-namespace link this resolver makes, and the only
    one it needs. Without it a host known by mac to DHCP and by name to NTLM would be two entities
    with nothing able to join them.
    """
    out: list[Identifier] = []
    if ev.mac:
        out.append(Identifier(NS_MAC, ev.mac.strip().lower(), HOST, ev.mac))
    if ev.hostname:
        name = computer_name(ev.hostname)
        if name:
            out.append(Identifier(NS_HOSTNAME, name, HOST, ev.hostname))
    return out


def _identifiers_kerberos(ev: IdentityEvent) -> list[Identifier]:
    ident = kerberos_identifier(ev.principal) if ev.principal else None
    return [ident] if ident is not None else []


def _identifiers_ntlm(ev: IdentityEvent) -> list[Identifier]:
    name = computer_name(ev.hostname) if ev.hostname else ""
    return [Identifier(NS_HOSTNAME, name, HOST, ev.hostname)] if name else []


def _identifiers_dns(ev: IdentityEvent) -> list[Identifier]:
    """REFERENCED, and this is the kind D1 exists to keep out of the merge path.

    A DNS answer binds a name to a THIRD PARTY's address — the one the query resolved to, not the
    one that answered — and the mapping is many-to-many. A resolver that merged on it would fold
    every address behind one CDN name into a single entity the first time a round-robin answer
    arrived.
    """
    name = ev.hostname.strip().rstrip(".").lower() if ev.hostname else ""
    return [Identifier(NS_DNSNAME, name, REFERENCED, ev.hostname)] if name else []


def _identifiers_none(_ev: IdentityEvent) -> list[Identifier]:
    """Declared, not omitted: `known_hosts.log` is Zeek's own first-seen census and carries an
    address with NOTHING to bind it to. v0 dropped it by falling through
    `ev.mac or ev.hostname or ev.principal`, which looks identical to a kind nobody has ruled on;
    naming it here is what makes the difference readable, and it is why `known_host` does not show
    up in `unscoped_identity_kinds` (the same discipline `_SERVICE_NOT_PROJECTED` applies one
    channel over).
    """
    return []


#: `IdentityEvent.kind` -> the identifiers that kind carries. A kind ABSENT from this table
#: anchors nothing and is counted as `unscoped_identity_kinds` in the window health record — it
#: never falls through to a default extractor, which is exactly how v0's
#: `ev.mac or ev.hostname or ev.principal` silently assigned meaning to whatever showed up.
#:
#: Distinct from `windowing.windows.IDENTITY_PROJECTIONS`, which is keyed on the same field and is
#: NOT a duplicate of it: this table decides what a kind can RESOLVE, that one decides what a
#: binding is ASSERTED as. A kind can legitimately be in one and not the other, and each has its
#: own unmodelled counter for exactly that reason.
IDENTITY_SCOPES: dict[str, Callable[[IdentityEvent], list[Identifier]]] = {
    "dhcp": _identifiers_dhcp,
    "kerberos": _identifiers_kerberos,
    "ntlm": _identifiers_ntlm,
    "radius": _identifiers_radius,
    "dns": _identifiers_dns,
    "known_host": _identifiers_none,
}


@dataclass(slots=True)
class _Binding:
    """One (address, identifier) binding over a half-open interval `[from_ts, to_ts)`.

    `from_ts`/`to_ts` are the believed VALIDITY; `first_seen`/`last_seen` are the OBSERVATION
    extent. They are not the same question and are not maintained the same way — the observation
    bounds move with min/max on every refresh because the ingest reorder buffer is bounded and an
    event genuinely can arrive behind its predecessor (`LES-005`, an interval that ran backwards
    here once already), while validity moves only on evidence: `from_ts` never changes and `to_ts`
    is set exactly once, by supersession.

    `lease_seconds` (a DHCP lease) and `record_ttl` (a DNS TTL) are two fields, never one, because
    they answer two different questions: a lease bounds how long the BINDING is true, a TTL bounds
    how long the ANSWER may be cached and says nothing about the mapping's lifetime. `LES-015`
    applied to the event model — see `events.IdentityEvent`, where they used to be one field.
    """
    kind: str
    ip: str
    namespace: str
    value: str
    raw: str
    scope: str
    entity: str            # the entity as MINTED; `_canon` resolves it through later merges
    from_ts: float
    to_ts: float | None    # None = still open (never superseded)
    first_seen: float
    last_seen: float
    lease_seconds: float | None
    record_ttl: float | None
    count: int


class EntityResolver:
    """Addresses -> entities, statefully, in one forward pass over the event stream."""

    def __init__(self) -> None:
        self._host: dict[str, list[_Binding]] = {}   # address -> host-scoped bindings, in from_ts order
        self._other: dict[tuple, _Binding] = {}      # (kind, ip, ns, value) -> user/referenced binding
        self._entity_of: dict[str, str] = {}         # identifier key -> entity id as minted
        self._alias: dict[str, str] = {}             # absorbed entity -> absorbing entity (D2 merges)
        self._mint_order: dict[str, int] = {}
        self._entity_first: dict[str, float] = {}
        self._entity_last: dict[str, float] = {}
        self._next = 1
        self._host_n = 0
        self._merges: list[dict] = []
        # Per-window counters, taken and reset by `take_window_stats()` at window close.
        self._w_anchored = self._w_ambiguous = self._w_unscoped = self._w_refused = 0
        # Evictions are counted apart from refusals throughout: a refusal loses a fact, an
        # eviction only splits its interval, and summing them would report data loss that did
        # not happen.
        self._w_evicted = 0
        # Run-level totals, never reset — the roster's own numbers (`LES-014` #2: a bounded map
        # must disclose what it refused, and a refusal counted per window would be unrecoverable
        # once the windows are summed).
        self._t_anchored = self._t_ambiguous = self._t_unanchored = self._t_unaddressed = 0
        self._t_unscoped = self._t_merges = 0
        self._host_refused = self._other_evicted = 0

    # -- resolution -------------------------------------------------------------

    def resolve(self, addr: str, ts: float) -> str:
        """Address -> entity id, using evidence seen SO FAR (D2).

        The hot path in the whole system — twice per flow, 3.07 M calls on the reference capture —
        so an address with no host-scoped evidence at all costs exactly one dict miss and returns
        the address itself (D4). Only an address that has ever carried host-scoped evidence pays
        for an interval scan, and that list is one element deep in every case measured.
        """
        entity, ambiguous = self._lookup(addr, ts)
        if entity is None:
            if addr:
                self._t_unanchored += 1
                return addr
            self._t_unaddressed += 1
            return "?"
        self._w_anchored += 1
        self._t_anchored += 1
        if ambiguous:
            self._w_ambiguous += 1
            self._t_ambiguous += 1
        return entity

    def _lookup(self, addr: str, ts: float) -> tuple[str | None, bool]:
        """`(entity or None, ambiguous)` — the resolution WITHOUT counting it.

        Separate from `resolve()` on purpose: `observe()` has to know what an address resolved to
        immediately *before* it applies new evidence (to tell an anchor from a split, D2), and
        that internal probe must not enter the ambiguity denominator — it is not a flow endpoint
        anyone resolved.
        """
        bindings = self._host.get(addr)
        if bindings is None:
            return None, False
        winner: _Binding | None = None
        entity: str | None = None
        ambiguous = False
        for b in bindings:
            if not (b.from_ts <= ts and (b.to_ts is None or ts < b.to_ts)):
                continue
            candidate = self._canon(b.entity)
            if winner is None:
                winner, entity = b, candidate
            elif candidate != entity:
                # Two DIFFERENT entities claim this address at this instant. Two bindings naming
                # the SAME entity (a mac and a host name of one host) are not ambiguity — they are
                # the link working.
                ambiguous = True
                if self._precedence(b) > self._precedence(winner):
                    winner, entity = b, candidate
        return entity, ambiguous

    @staticmethod
    def _precedence(b: _Binding) -> tuple:
        """Total order over competing bindings, so the winner never depends on list order.

        A binding with a DECLARED lease outranks one without: DHCP is authoritative for address
        assignment, and a lease is a statement about the future, while a point observation is only
        a statement about an instant. After that, the most recent evidence wins, and the
        identifier itself breaks the remaining ties so two bindings can never compare equal.
        """
        return (1 if b.lease_seconds else 0, b.from_ts, b.namespace, b.value)

    def _entity_at(self, ip: str, ts: float) -> str:
        entity, _ = self._lookup(ip, ts)
        return entity if entity is not None else (ip or "?")

    def _canon(self, entity: str) -> str:
        """Follow `entity` through any merges it has been absorbed by, compressing the path.

        Bindings store the entity as MINTED and are never rewritten, which is what keeps a merge
        forward-only (D2): a window that closed before the merge keeps the id it published, and
        the `same_entity_as` assertion is what joins the two afterwards.
        """
        root = self._alias.get(entity)
        if root is None:
            return entity
        chain = [entity]
        while True:
            nxt = self._alias.get(root)
            if nxt is None:
                break
            chain.append(root)
            root = nxt
        for e in chain:
            self._alias[e] = root
        return root

    # -- observation ------------------------------------------------------------

    def observe(self, ev: IdentityEvent) -> list[dict]:
        """Fold one identity fact into the resolver; return the binding CHANGES it caused.

        Emits on CHANGE, not on observation (`Q9`, `LES-010`): a binding opening, a binding
        closing, or a merge. `last_seen` moving forward and `count` incrementing are restatement
        and return nothing — 144,886 DNS-derived observations on the 2026-08-11 baseline are
        6,414 distinct bindings, so v0's return-a-record-per-observation made `identity.jsonl`
        95.6 % restatement. The still-open bindings are published once at run end
        (`flush_open`, `change="final"`), which is where their final `last_seen`/`count` land.

        Returns a LIST, not an `Optional[dict]` as v0 did: one DHCP row legitimately carries two
        identifiers (a mac and a host name), and collapsing them to one would throw away the only
        cross-namespace link this resolver has.
        """
        extract = IDENTITY_SCOPES.get(ev.kind)
        if extract is None:
            # A kind nobody has ruled on. Counted, never given a default extractor.
            self._w_unscoped += 1
            self._t_unscoped += 1
            return []
        identifiers = extract(ev)
        if not (ev.ip and identifiers):
            return []
        changes: list[dict] = []
        for ident in identifiers:
            if ident.scope != HOST:
                changes.extend(self._observe_nonhost(ev, ident))
        host = [i for i in identifiers if i.scope == HOST]
        if host:
            changes.extend(self._observe_host(ev, host))
        return changes

    def _observe_nonhost(self, ev: IdentityEvent, ident: Identifier) -> list[dict]:
        """Fold one non-host identity fact in; return the change records it caused.

        A LIST since 2026-08-14, because one observation can now produce two records: evicting the
        least-recently-used binding to make room emits that binding's `final`, and the new one
        emits its `opened`. Returning only the second would drop the first on the floor — the
        exact `LES-108` failure the end-of-run flush exists to prevent, arriving through a
        different door.
        """
        key = (ev.kind, ev.ip, ident.namespace, ident.value)
        b = self._other.get(key)
        if b is not None:
            # Touch: move to the end so the LRU order reflects use, not arrival. Without this the
            # eviction policy is first-in-first-out, which discards the names a capture keeps
            # seeing in favour of ones it saw once.
            self._other[key] = self._other.pop(key)
            # Both ends move, because events arrive only APPROXIMATELY in ts order (the ingest
            # reorder buffer is bounded). Fixing `first_seen` at creation gives a binding whose
            # interval runs backwards the moment an out-of-order event lands (`LES-005`).
            b.first_seen = min(b.first_seen, ev.ts)
            b.last_seen = max(b.last_seen, ev.ts)
            b.count += 1
            return []
        out: list[dict] = []
        if len(self._other) >= NONHOST_WORKING_SET:
            # Evict the least-recently-used and PUBLISH it, rather than refusing the new one.
            # The store ends up with every binding; only the interval is split.
            oldest = next(iter(self._other))
            out.append(self._record(self._other.pop(oldest), "final"))
            self._other_evicted += 1
            self._w_evicted += 1
        b = self._other[key] = _Binding(
            kind=ev.kind, ip=ev.ip, namespace=ident.namespace, value=ident.value,
            raw=ident.raw, scope=ident.scope, entity="",
            from_ts=ev.ts, to_ts=None, first_seen=ev.ts, last_seen=ev.ts,
            lease_seconds=None, record_ttl=ev.record_ttl or None, count=1)
        out.append(self._record(b, "opened"))
        return out

    def _observe_host(self, ev: IdentityEvent, identifiers: list[Identifier]) -> list[dict]:
        changes: list[dict] = []
        # BEFORE any mutation: what did this address resolve to a moment ago? That is what tells
        # an ANCHOR (an unanchored address learning its identity — a merge the snapshot reader
        # must apply to already-closed windows) from a SPLIT (an anchored address changing hands —
        # genuinely two entities, and emphatically not a merge).
        prior, _ = self._lookup(ev.ip, ev.ts)
        bindings = self._host.setdefault(ev.ip, [])
        # Keyed on the EVIDENCE KIND as well as the identifier, so NTLM and Kerberos naming the
        # same host at the same address hold one binding each rather than the second silently
        # refreshing the first's counters and vanishing. Measured on the baseline: 7 addresses
        # carry both, and "confirmed by two independent sources" is a materially stronger fact
        # than "seen twice" — a binding record whose `kind` is whichever source happened to
        # arrive first would publish the weaker one as the whole story. They still resolve to ONE
        # entity (the identifier is the same), so this multiplies bindings, never entities: see
        # the supersession loop below, which skips anything already naming this entity.
        open_now = {(b.kind, b.namespace, b.value): b for b in bindings if b.to_ts is None}
        fresh = [i for i in identifiers if (ev.kind, i.namespace, i.value) not in open_now]
        if fresh and self._host_n + len(fresh) > MAX_HOST_BINDINGS:
            # Refuse the whole event's new bindings rather than a prefix of them: admitting one
            # identifier of a two-identifier row would record a link the roster cannot show.
            self._host_refused += len(fresh)
            self._w_refused += len(fresh)
            fresh = []
        admitted = [i for i in identifiers
                    if (ev.kind, i.namespace, i.value) in open_now] + fresh
        if not admitted:
            if not bindings:
                del self._host[ev.ip]      # never leave an empty list behind on the hot path
            return changes
        entity = self._link(ev, admitted)
        lease = ev.lease_seconds if ev.lease_seconds and ev.lease_seconds > 0 else None
        for ident in admitted:
            b = open_now.get((ev.kind, ident.namespace, ident.value))
            if b is not None:
                b.first_seen = min(b.first_seen, ev.ts)
                b.last_seen = max(b.last_seen, ev.ts)
                b.count += 1
                if lease is not None:
                    b.lease_seconds = lease   # a renewal restates the lease; not a change (`Q9`)
                continue
            b = _Binding(
                kind=ev.kind, ip=ev.ip, namespace=ident.namespace, value=ident.value,
                raw=ident.raw, scope=HOST, entity=entity, from_ts=ev.ts, to_ts=None,
                first_seen=ev.ts, last_seen=ev.ts, lease_seconds=lease,
                record_ttl=None, count=1)
            bindings.append(b)
            self._host_n += 1
            changes.append(self._record(b, "opened"))
        # Supersession: a host-scoped binding naming a DIFFERENT entity is now stale — this
        # address has changed hands, and leaving both open would give one address two entities
        # simultaneously rather than two generations in sequence.
        for b in bindings:
            if b.to_ts is not None or self._canon(b.entity) == entity:
                continue
            lease_to = b.from_ts + b.lease_seconds if b.lease_seconds else None
            if lease_to is not None and lease_to > ev.ts:
                continue    # declared validity has not expired: a real overlap, i.e. ambiguity
            b.to_ts = ev.ts
            changes.append(self._record(b, "closed"))
        if prior is None and entity != (ev.ip or "?"):
            # An unanchored address just learned its identity. Every window already closed
            # attributed its features to the bare address, so the two must be joinable — that is
            # what the assertion is for, and why nothing here rewrites history (D2).
            self._merge(ev, prior_entity=ev.ip, entity=entity, reason="address_anchored",
                        identifiers=admitted)
        return changes

    def _link(self, ev: IdentityEvent, identifiers: list[Identifier]) -> str:
        """Put every host-scoped identifier of ONE sensor row into one entity, and return it.

        Co-observation in a single row is the only link rule (module docstring). When two
        identifiers already carry different entities, this is where they merge: the entity minted
        FIRST absorbs the other, so a published id keeps its meaning and only the newer one is
        ever redirected.
        """
        keys = [i.key for i in identifiers]
        known = [self._canon(e) for e in
                 (self._entity_of.get(k) for k in keys) if e is not None]
        if not known:
            entity = self._mint()
        else:
            entity = min(known, key=lambda e: self._mint_order[e])
            for loser in sorted(set(known) - {entity}, key=lambda e: self._mint_order[e]):
                self._alias[loser] = entity
                self._entity_first[entity] = min(self._entity_first.get(entity, ev.ts),
                                                 self._entity_first.pop(loser, ev.ts))
                self._entity_last[entity] = max(self._entity_last.get(entity, ev.ts),
                                                self._entity_last.pop(loser, ev.ts))
                self._merge(ev, prior_entity=loser, entity=entity, reason="identifier_link",
                            identifiers=identifiers)
        for k in keys:
            self._entity_of[k] = entity
        self._entity_first[entity] = min(self._entity_first.get(entity, ev.ts), ev.ts)
        self._entity_last[entity] = max(self._entity_last.get(entity, ev.ts), ev.ts)
        return entity

    def _mint(self) -> str:
        """D3: sequential, in first-observation order, stable within a run and never across one.

        Deliberately not content-derived: an id computed from an entity's evidence would CHANGE
        the moment that entity acquired stronger evidence, breaking joins across windows inside a
        single run — the one place ids have to hold.
        """
        entity = f"ent-{self._next:04d}"
        self._mint_order[entity] = self._next
        self._next += 1
        return entity

    def _merge(self, ev: IdentityEvent, *, prior_entity: str, entity: str,
               reason: str, identifiers: list[Identifier]) -> None:
        self._t_merges += 1
        self._merges.append({
            "from_entity": prior_entity, "to_entity": entity, "reason": reason,
            "ts": ev.ts, "evidence": ev.kind,
            "identifiers": sorted(i.key for i in identifiers)})

    def _record(self, b: _Binding, change: str) -> dict:
        """One binding change, as the dict every sink downstream persists.

        A HOST binding publishes its own entity; a user- or referenced-scoped one publishes the
        entity its ADDRESS resolves to, because it anchors nothing itself — that is what makes
        `bound_dns` an assertion about a subject at all, while keeping it `referenced`.
        """
        entity = (self._canon(b.entity) if b.scope == HOST
                  else self._entity_at(b.ip, b.last_seen))
        return {"change": change, "kind": b.kind, "ip": b.ip, "bound_to": b.raw,
                "namespace": b.namespace, "identifier": f"{b.namespace}:{b.value}",
                "scope": b.scope, "entity": entity,
                "from_ts": b.from_ts, "to_ts": b.to_ts,
                "first_seen": b.first_seen, "last_seen": b.last_seen,
                "lease_seconds": b.lease_seconds, "record_ttl": b.record_ttl,
                "count": b.count}

    # -- window / run boundaries ------------------------------------------------

    def take_merges(self) -> list[dict]:
        """Merges learned since the last call, drained. Emitted as `same_entity_as` assertions."""
        merges, self._merges = self._merges, []
        return merges

    def take_window_stats(self) -> dict:
        """Per-window resolver facts for the health record, taken and reset.

        `resolver_ambiguity` is `None` — not `0.0` — when nothing this window resolved through
        host-scoped evidence at all. v0 returned a hardcoded `0.0` into all 72 window health
        records, which read as "resolution found no ambiguity" when it meant "no resolution was
        attempted": a false clean bill of health in exactly the sense `LES-006` names.
        """
        ambiguity = (self._w_ambiguous / self._w_anchored) if self._w_anchored else None
        stats = {"resolver_ambiguity": ambiguity,
                 "resolver_anchored_resolutions": self._w_anchored,
                 "unscoped_identity_kinds": self._w_unscoped,
                 "identity_bindings_refused": self._w_refused,
                 # Evictions are NOT refusals and are counted separately on purpose: a refusal
                 # means a fact never reached the store, an eviction means it reached it early
                 # and its interval was split. Summing them would report data loss that did not
                 # happen (`LES-006`, one field over).
                 "identity_bindings_evicted": self._w_evicted}
        self._w_anchored = self._w_ambiguous = self._w_unscoped = self._w_refused = 0
        self._w_evicted = 0
        return stats

    def flush_open(self) -> list[dict]:
        """Every still-open binding, once, as `change="final"` — the run's closing statement.

        Without this, a binding that opened and never closed would be published exactly once, at
        creation, carrying `count=1` and a `last_seen` equal to its `from_ts` — so change-based
        emission (`Q9`) would have bought its 95 % reduction by throwing away the observation
        extent, which is most of what a binding record is FOR. Sorted, so the rows are a function
        of content and not of dict insertion order (`LES-002`).
        """
        out = []
        for ip in sorted(self._host):
            for b in self._host[ip]:
                if b.to_ts is None:
                    out.append(self._record(b, "final"))
        for key in sorted(self._other):
            out.append(self._record(self._other[key], "final"))
        return out

    def roster(self) -> dict:
        """The run's entity namespace: every path that can mint a member of it (`LES-017`).

        A published namespace is the union of every mint path, including the ones nobody declared
        — and this one has four, of which only the first looks like an entity id. Enumerating the
        bound entities and *counting* the rest is deliberate: the bare-address path (D4) is
        unbounded by construction, so listing it here would put an uncapped, data-keyed map into
        the artifact to describe a cap (`LES-014`). The distinct set is already in `nodes.jsonl`'s
        own `entity` column; what the manifest adds is which of those values came from WHERE.

        `state.complete` is the honesty flag: false the moment either cap refused anything, so a
        truncated roster can never be read as a census.
        """
        by_entity: dict[str, dict] = {}
        for ip in sorted(self._host):
            for b in self._host[ip]:
                entity = self._canon(b.entity)
                rec = by_entity.get(entity)
                if rec is None:
                    rec = by_entity[entity] = {
                        "entity": entity, "mint_path": "bound", "scope": HOST,
                        "identifiers": set(), "addresses": set(), "evidence": set(),
                        "_generations": set(), "merged_from": set(),
                        "first_seen": b.first_seen, "last_seen": b.last_seen}
                rec["identifiers"].add(f"{b.namespace}:{b.value}")
                rec["addresses"].add(ip)
                rec["evidence"].add(b.kind)
                # Distinct (address, identifier) pairs, NOT the raw binding count: two sensors
                # confirming one identifier at one address is one generation observed twice, and
                # counting rows would inflate it into two. What this number answers is "how many
                # distinct address-identity periods does this entity span" — one for a static
                # host, two for a roam, more for a churning one.
                rec["_generations"].add((ip, b.namespace, b.value))
                rec["first_seen"] = min(rec["first_seen"], b.first_seen)
                rec["last_seen"] = max(rec["last_seen"], b.last_seen)
        for loser in sorted(self._alias, key=lambda e: self._mint_order[e]):
            rec = by_entity.get(self._canon(loser))
            if rec is not None:
                rec["merged_from"].add(loser)
        bound = []
        for _, rec in sorted(by_entity.items()):
            out = {k: (sorted(v) if isinstance(v, set) else v)
                   for k, v in rec.items() if k != "_generations"}
            out["generations"] = len(rec["_generations"])
            bound.append(out)
        # `complete` keys on HOST refusals alone since 2026-08-14. A non-host eviction publishes
        # the binding it drops (`NONHOST_WORKING_SET`), so the census stays whole and calling it
        # incomplete would be a false alarm on the commonest path in a long capture.
        refused = self._host_refused
        return {
            # D3, stated in the artifact rather than left to be inferred: these ids mean nothing
            # in another capture, and a store accumulating several runs inherits that.
            "id_scope": "run",
            "mint_paths": [
                {"path": "bound", "pattern": "ent-NNNN", "enumerated": True,
                 "entities": len(bound),
                 "note": "host-scoped evidence anchored these (D1); listed under 'bound' below"},
                {"path": "address", "pattern": "<the observed address, verbatim>",
                 "enumerated": False, "resolutions": self._t_unanchored,
                 "note": "D4 fallback: no host-scoped evidence, so the address stands for "
                         "itself. Unbounded by construction, hence counted in RESOLUTIONS "
                         "rather than enumerated here; the distinct set is nodes.jsonl's own "
                         "entity column"},
                {"path": "unaddressed", "pattern": "?", "enumerated": True,
                 "entities": 1 if self._t_unaddressed else 0,
                 "resolutions": self._t_unaddressed,
                 "note": "resolve() called with an empty address — a sensor row with no "
                         "endpoint. A member of the namespace that no sensor ever emitted"},
            ],
            "bound": bound,
            "state": {
                "complete": refused == 0,
                "host_bindings": self._host_n,
                "host_bindings_cap": MAX_HOST_BINDINGS,
                "host_bindings_refused": self._host_refused,
                "nonhost_bindings": len(self._other),
                "nonhost_working_set": NONHOST_WORKING_SET,
                "nonhost_bindings_evicted": self._other_evicted,
                "entities_minted": self._next - 1,
                "entity_merges": self._t_merges,
                "unscoped_identity_kinds": self._t_unscoped,
                "resolutions_anchored": self._t_anchored,
                "resolutions_ambiguous": self._t_ambiguous,
                "resolutions_unanchored": self._t_unanchored,
                "resolutions_unaddressed": self._t_unaddressed,
            },
        }
