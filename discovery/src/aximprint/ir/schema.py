"""The network IR schema — the ONE versioned shape in this project.

`docs/SPEC_network_ir.md` is the authority; this module is its executable half, and
`tests/test_ir_schema.py` asserts the two agree. That pairing is deliberate and copied from
`docs/SPEC_zeek_metadata_archive.md` ↔ `ingest/zeek/plugin.py::_COLUMNS`: a schema defined only in
prose is a schema nothing checks (`LES-014`).

**Read `LES-001` before adding a version field to anything else.** This module carries
`SCHEMA_VERSION` because DESIGN_BRIEF §0.2 was amended 2026-08-14 by user ratification to make the
IR — and *only* the IR — a versioned schema. The event model, feature shapes, plugin contract and
belief-store schema remain incubating and carry no version at all. The discriminator, stated in
`LES-001`'s own amendment: **a schema is versioned when something outside its producer depends on
its shape.** The belief store has one in-repo consumer that moves with it; this artifact has four
on independent lifecycles (a drift comparator, a topology-diagram generator, a GNN evaluation
harness, and the twin compiler), and will be re-read long after it was written.

Build step 1 of `docs/WORK_ORDER_network_ir.md`: **the schema and nothing else.** The natural-key
derivation is step 2, materialization is step 3, compilation step 4, comparison step 5. Nothing
here reads the belief store or writes a row.
"""
from __future__ import annotations

import json

#: `MAJOR.MINOR`, and the two halves mean exactly what DESIGN_BRIEF §11's change classes say —
#: §11 became BINDING for this artifact on the same ratification, so the policy is applied here,
#: not invented:
#:
#:   * **additive** (a new column, or a new field name replacing a redefined one) -> **MINOR**.
#:     A reader written against an older MINOR of the same MAJOR stays correct: §11's "named
#:     fields, not positions" is what makes an unknown column ignorable rather than fatal.
#:   * **subtractive** (a column removed or narrowed) -> **MAJOR**. A reader may depend on it.
#:   * **semantic redefinition in place** -> **FORBIDDEN**, not a version bump at all. §11:
#:     "changed meaning gets a new field name (converts the dangerous case into add + deprecate);
#:     representation changes are redefinitions in disguise." A column whose meaning changes while
#:     its name stays is undetectable by any version check, which is precisely why the rule is a
#:     prohibition rather than a bump rule.
#:
#: **Fail-closed load** (§11): a reader refuses an unknown MAJOR rather than guessing. Refusing is
#: also why the version lives in a ROW (`ir_pit.schema_version`) and not only in this constant —
#: an artifact has to state its own version to be readable by a build that did not write it, which
#: is the entire difference between this and the belief store's build-fingerprint guard.
#:
#: **`1.3`** (`docs/WORK_ORDER_traffic_profiles.md` D8): `ir_traffic_profile` and
#: `ir_capture_profile` added — additive, MINOR, the same ceremony `1.1` (locality) and `1.2`
#: (vantage + `ir_unseen`) both followed. Not a fresh §0.2 ratification: the IR is already the
#: ratified versioned schema, and this follows its own established rules rather than extending
#: versioning to anything new (`LES-001`).
#:
#: **`1.4`** (`docs/WORK_ORDER_reference_ingest.md` D1/D2/D-a, slice S1) carries **two** changes,
#: and the second is a correction rather than an addition:
#:
#:   * `EVIDENCE_CLASSES` gains `asserted` and `imported` — a vocabulary widening on existing
#:     columns, additive by exactly the argument `UNSEEN_REASONS` already records.
#:   * `ir_entity.listen_ports`/`.dial_ports` were added on 2026-08-27 (GAP-C9) and **the version
#:     table was never bumped**, against the SPEC's own additive→MINOR rule. They are named in
#:     `1.4`'s entry rather than given a retro-dated version of their own, so no version is minted
#:     that no artifact ever carried. The consequence a reader must know: **stores written between
#:     2026-08-27 and this bump say `1.3` while already carrying those two columns**, so those two
#:     — and only those two — must be detected by column-presence, never by version. Verified on
#:     disk: six CorpAPCN IR stores written before 2026-08-27 say `1.3` and lack them; the
#:     2026-09-02 re-materialization says `1.3` and carries them.
SCHEMA_VERSION = "1.6"

#: Evidence classes an IR row may carry, **in PRECEDENCE ORDER — the tuple IS the ranking**
#: (`docs/WORK_ORDER_reference_ingest.md` D1; axRange `docs/design/14-operator-assertions.md` §2).
#: Higher (earlier) wins when two classes speak to one fact. Declaring the order as the tuple's
#: own order is deliberate: a second, separate rank table could drift from the vocabulary it
#: ranks (`LES-101`), and `evidence_rank` below is the only reader of the position.
#:
#:   * `asserted`   — an operator's hand-authored fact, through the assertions file.
#:   * `imported`   — an out-of-band point-in-time snapshot (a directory export, a host
#:                    inventory, a device config), through reference ingest.
#:   * `observed`   — the sensor saw the entity do it, first-hand.
#:   * `referenced` — the wire carried a THIRD PARTY's claim about it. **Meaning unchanged**: this
#:                    is second-hand evidence *from inside the capture* — a DNS answer, an SPN, a
#:                    redirect, an XFF header (`learners/extractors.py`, and the class behind
#:                    `ir_unseen`). It was never a name for out-of-band imports, which is why
#:                    `imported` is a separate class on the OTHER side of `observed` in trust.
#:   * `imputed`    — a learner inferred it from evidence (a role from a listen census).
#:
#: **`compiler` (renamed from `defaulted` at the same time) is still deliberately EXCLUDED**, and
#: the original reasoning is untouched by the widening: it is what a COMPILER does when a catalog
#: supplies a value discovery never observed (an `image`, a CPU count), so it is a property of
#: `twin_config`, not of the IR. **The IR carries only what was discovered**, and a value nothing
#: discovered is absent from it rather than present-and-defaulted. Keeping it out is what stops
#: the compiler's inventions from re-entering the drift comparison as though they were
#: observations — and it is also why `evidence_rank` refuses it: a sidecar-only label must never
#: be ranked against evidence. The string itself is declared once, in the producer that emits it
#: (`synth/compiler.py::COMPILER_CLASS`), and never here.
EVIDENCE_CLASSES = ("asserted", "imported", "observed", "referenced", "imputed")

#: How an entity's cross-PIT natural key was derived (D6). Declared as a closed vocabulary so a
#: key's *provenance* is legible: two PITs joining on a `hostname` key are joining on a parsed
#: computer name (`LES-107`), while two joining on `address` are making a much weaker claim that
#: the address did not change hands. A comparator may legitimately weight them differently, and it
#: cannot if the artifact does not say which it used. Step 2 populates this; step 1 declares it.
KEY_KINDS = ("hostname", "mac", "address")

#: An entity's inside/outside verdict, resolved from its per-window verdicts by
#: `windowing.windows.locality_verdict` (majority, ties to local — one rule, stated there, applied
#: at two time scales). `None` means nothing could judge it, and is NOT "external" (`LES-006`).
#:
#: **Added at `1.1`, and the reason is worth keeping: without it, `segment IS NULL` conflates two
#: different things** — an internet peer, and a local host on nothing provisionable (IPv6
#: link-local, multicast: 117 of them on the 2026-08-11 baseline). A drift comparator filtering the
#: monitored network on `segment IS NOT NULL` would silently drop the second group and would have
#: no way to notice. Found by materializing a real PIT and reading it, not by review.
LOCALITY_CLASSES = ("local", "external")

#: `ir_traffic_profile.schedule` — D3's classification, closed so `synth/compiler.py` can only
#: ever hand axRange one of the two tokens its own `_SCHEDULE_ALIASES` accepts verbatim
#: (`axrange/o2control/traffic.py`). **A claim about SHAPE, not the clock**: the capture carries
#: no timezone, so `business-hours` means "activity concentrated in some contiguous 8-hour UTC
#: band", never "active 09:00-17:00 local". `None` (omitted from the assignment entirely, never
#: emitted as a literal null) is the third, unlisted state — below D4's diurnal floor, nothing
#: classifies, and axRange's own parser already treats an absent `schedule` as "leave the
#: persona's own cadence-derived gate alone", which is the honest reading of "we don't know".
SCHEDULE_CLASSES = ("24x7", "business-hours")

#: Where a segment's coverage figure came from (D4a). A **vocabulary, not a boolean**, because it
#: has to be able to grow: today Zeek reports no per-segment loss so every row is inferred, but a
#: sensor that reports it must be expressible without a schema change (which under §11 would be a
#: semantic redefinition of a bool, i.e. forbidden). `inferred_from_traffic` means "we saw traffic
#: from this segment in N of M windows and are treating that as coverage" — a proxy, and it must
#: never be read as a measurement (`LES-006`).
COVERAGE_SOURCES = ("inferred_from_traffic", "sensor_reported")

#: Why a host the vantage KNOWS EXISTS was never observed in a flow (`ir_unseen`, D7). A closed
#: vocabulary rather than free text, and deliberately extensible: DESIGN_BRIEF §8 names four
#: sources of referenced-but-unseen hosts — DNS answers, SPNs, redirects and XFF — and only the
#: first has a producer today (2,418 addresses on the 2026-08-11 baseline, sitting in the belief
#: store and dropped by the IR until `1.2`). The others land here as new values, additively,
#: because a `reason` a consumer does not recognize is ignorable while a redefined one is not.
#:
#: `referenced_by_dns` means: some host asked, a resolver answered with this address, and nothing
#: in any flow ever came from or went to it *at this vantage*. That last clause is the whole point
#: — on a near-full-visibility tap the census is mostly the internet (9 of 2,418 inside the estate
#: on that baseline), and on a partial vantage the same census IS the blind spot.
UNSEEN_REASONS = ("referenced_by_dns",)

#: The axis a merged PIT was combined along (D4/D10). **Explicit, never inferred**, because the two
#: axes compute coverage and volume differently and a single untyped union would get one of them
#: wrong: across VANTAGES the same window is seen twice, so coverage takes the best and edge
#: volumes take the max; across TIME the windows are disjoint, so coverage and volumes accumulate.
#: `NULL` in `ir_pit.merge_axis` means the PIT was materialized from a belief store rather than
#: merged, which is a third state and not a default.
MERGE_AXES = ("vantage", "time")

#: The DDL. Keyed on `pit_id` throughout so many PITs live in one database and a drift comparison
#: is a join rather than N file attaches (D2).
#:
#: **Nullability is semantic here and is specified per column in the SPEC.** The `LES-006`
#: discipline is load-bearing for this artifact more than for any other in the project: a NULL
#: `role` means no role was inferred, a NULL `capture_loss_pct_max` means nothing audited it, and
#: a comparator that reads either as a zero will report drift that did not happen.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS ir_pit (
  pit_id VARCHAR, source VARCHAR, as_of DOUBLE,
  observed_from DOUBLE, observed_to DOUBLE, windows INTEGER,
  schema_version VARCHAR,
  aximprint_version VARCHAR, code_fingerprint VARCHAR, git_commit VARCHAR,
  capture_loss_pct_max DOUBLE,
  entities INTEGER, segments INTEGER,
  vantage VARCHAR, network VARCHAR, vantages JSON,
  merge_axis VARCHAR, merged_from JSON,
  -- `1.5`: the operator-facing label, distinct from the identity. Defaults to `pit_id` at bake
  -- time and is the ONE column a PIT allows to change afterwards (`IRStore.rename_pit`): a name
  -- is how a person refers to a snapshot, not evidence about the network. LAST, deliberately:
  -- a `1.4` store gains it by `ALTER TABLE ADD COLUMN`, which appends, and `write_pit` inserts
  -- by position.
  name VARCHAR,
  -- `1.6`: the `pit_id` this PIT was copied from (`IRStore.copy_pit`, the operator's save-as);
  -- NULL for a PIT baked from a belief store. Lineage, never identity: the copy has its own id.
  derived_from VARCHAR);
CREATE TABLE IF NOT EXISTS ir_entity (
  pit_id VARCHAR, entity_key VARCHAR, key_kind VARCHAR, source_entity VARCHAR,
  locality VARCHAR,
  segment VARCHAR, segment_confidence DOUBLE, segment_evidence_class VARCHAR,
  role VARCHAR, role_confidence DOUBLE, role_evidence_class VARCHAR,
  -- The port evidence the role was inferred FROM, kept beside the inference (GAP-C9). JSON
  -- arrays of "<port>/<proto>". `observed`, unlike `role` itself. Persona synthesis reads
  -- these: a persona generated to drive a twin node must emit what the source was seen doing,
  -- or the node cannot exhibit the role it was built for.
  listen_ports VARCHAR, dial_ports VARCHAR,
  windows_seen INTEGER, first_seen DOUBLE, last_seen DOUBLE,
  addresses JSON, identifiers JSON, vantages JSON);
CREATE TABLE IF NOT EXISTS ir_unseen (
  pit_id VARCHAR, address VARCHAR, locality VARCHAR, segment VARCHAR,
  reason VARCHAR, evidence_class VARCHAR, identifiers JSON,
  observations BIGINT, first_ts DOUBLE, last_ts DOUBLE);
CREATE TABLE IF NOT EXISTS ir_segment (
  pit_id VARCHAR, cidr VARCHAR, name VARCHAR, entities INTEGER,
  windows_present INTEGER, windows_total INTEGER, coverage_source VARCHAR);
CREATE TABLE IF NOT EXISTS ir_edge (
  pit_id VARCHAR, src_key VARCHAR, dst_key VARCHAR, proto VARCHAR,
  flows BIGINT, bytes BIGINT, first_ts DOUBLE, last_ts DOUBLE,
  evidence_class VARCHAR);
CREATE TABLE IF NOT EXISTS ir_identity (
  pit_id VARCHAR, entity_key VARCHAR, kind VARCHAR, namespace VARCHAR,
  identifier VARCHAR, confidence DOUBLE, evidence_class VARCHAR,
  from_ts DOUBLE, to_ts DOUBLE);
CREATE TABLE IF NOT EXISTS ir_traffic_profile (
  pit_id VARCHAR, entity_key VARCHAR, windows_active INTEGER, windows_total INTEGER,
  hod_flows JSON, dow_flows JSON, schedule VARCHAR, schedule_evidence_class VARCHAR,
  flows_per_window_q JSON, bytes_per_window_q JSON,
  burstiness_p50 DOUBLE, client_ratio_p50 DOUBLE, interarrival_p50 DOUBLE,
  evidence_class VARCHAR);
CREATE TABLE IF NOT EXISTS ir_capture_profile (
  pit_id VARCHAR, parameter VARCHAR, stat VARCHAR, value DOUBLE, unit VARCHAR,
  windows INTEGER, evidence_class VARCHAR);
"""

#: Table -> ordered column names, parsed from `_SCHEMA` so the two can never disagree. This is
#: what `tests/test_ir_schema.py` checks the SPEC against, and what a reader uses to detect an
#: additive difference (a column it does not know) from a subtractive one (a column it needs and
#: the artifact lacks) — §11's two change classes, made checkable.
def _strip_sql_comments(ddl: str) -> str:
    """Drop `--` comments before the column parse below.

    Not cosmetic. This parser splits the DDL body on commas, so a comment containing one turns
    its own prose into COLUMN NAMES and displaces the real column that followed it. That is
    exactly what happened when `listen_ports`/`dial_ports` were added with an explanatory
    comment (`docs/JOURNAL_corpapcn_fidelity.md` GAP-C9): `IR_TABLES["ir_entity"]` came back
    carrying `--`, `kept` and `unlike` as columns, `listen_ports` vanished, and the insert failed
    with a column/value count mismatch that pointed nowhere near the cause.

    A schema file that cannot be commented is a schema file nobody explains.
    """
    return "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())


def _columns_from_ddl(ddl: str) -> dict[str, tuple[str, ...]]:
    tables: dict[str, tuple[str, ...]] = {}
    for stmt in _strip_sql_comments(ddl).strip().split(";"):
        stmt = stmt.strip()
        if not stmt:
            continue
        head, _, body = stmt.partition("(")
        name = head.split()[-1]
        cols = []
        for part in body.rstrip(")").split(","):
            tokens = part.split()
            if tokens:
                cols.append(tokens[0])
        tables[name] = tuple(cols)
    return tables


IR_TABLES: dict[str, tuple[str, ...]] = _columns_from_ddl(_SCHEMA)


def vantage_set(pit_row) -> "frozenset[str] | None":
    """Which tap(s) a PIT sees through — `None` when nothing recorded any.

    Two columns answer this and they are not redundant: `vantage` is the single tap of a
    materialized PIT, and `vantages` is the set a MERGED one combines (D4). A merged PIT leaves
    `vantage` NULL rather than picking one of its members, because "this PIT is tap A" and "this
    PIT contains tap A among others" are different claims and a comparator must not read the second
    as the first. This function is the one place that resolves the pair, so the rule lives once.

    **`None` is not the empty set.** Unknown vantage means two PITs cannot be asserted to share a
    view *or* to differ in one; an empty set would say they see through no taps at all, which is
    never true of a PIT that has entities in it (`LES-006`).
    """
    raw = pit_row.get("vantages")
    if raw:
        values = json.loads(raw) if isinstance(raw, str) else raw
        return frozenset(values) if values else None
    single = pit_row.get("vantage")
    return frozenset([single]) if single else None


def evidence_rank(evidence_class: str) -> int:
    """Precedence position of an IR evidence class — **lower wins**.

    `EVIDENCE_CLASSES` is declared in precedence order, so this is `index()` and nothing else
    (D1). That is the point: a second table mapping class → rank could drift from the vocabulary
    it ranks, and nobody would notice until two classes disagreed about one fact
    (`LES-101`).

    **Refuses anything outside the tuple, `compiler` included.** A sidecar-only label has no rank
    because it never competes: a compiler choice is what happens when nothing spoke, and a
    declared deployment transformation is applied *after* resolution rather than inside it
    (axRange `docs/design/14-operator-assertions.md` §2). Ranking either one would be the
    masquerade the whole model exists to prevent, so it is a loud refusal rather than a
    last-place default (CONVENTIONS §3).
    """
    try:
        return EVIDENCE_CLASSES.index(evidence_class)
    except ValueError:
        raise ValueError(
            f"{evidence_class!r} is not an IR evidence class and has no precedence rank; "
            f"expected one of {list(EVIDENCE_CLASSES)}. A compiler label ('compiler') is "
            f"sidecar-only and never competes — see EVIDENCE_CLASSES."
        ) from None


def parse_version(version: str) -> tuple[int, int]:
    """`"1.0"` -> `(1, 0)`. Raises `ValueError` on anything else.

    Parsed, never pattern-matched (`LES-008`): a reader deciding compatibility from
    `version.startswith("1.")` accepts `"10.3"` and refuses nothing.
    """
    major, _, minor = version.partition(".")
    if not major.isdigit() or not minor.isdigit():
        raise ValueError(f"schema version must be 'MAJOR.MINOR', got {version!r}")
    return int(major), int(minor)


def can_read(artifact_version: str, reader_version: str = SCHEMA_VERSION) -> bool:
    """May a reader at `reader_version` read an artifact at `artifact_version`?

    **Fail closed** (§11): a differing MAJOR is refused outright rather than attempted. Within one
    MAJOR, a reader may read any MINOR — **including a HIGHER one**, which is the half that looks
    wrong and is not: MINOR bumps are additive by definition, so a newer artifact carries columns
    this reader does not know and will ignore, and §11's "named fields, not positions" is exactly
    the property that makes ignoring them safe.

    This function decides only whether the *shape* is readable. Whether the artifact is
    *comparable* to another one is a different question, answered by observation extent and
    coverage (D3/D4a), and a reader must not conflate them.
    """
    a_major, _ = parse_version(artifact_version)
    r_major, _ = parse_version(reader_version)
    return a_major == r_major
