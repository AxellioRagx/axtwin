"""The network IR schema, and the spec↔code agreement that keeps it honest.

`docs/WORK_ORDER_network_ir.md` build step 1. The pairing — a normative SPEC plus an executable
half plus this test — is copied from `docs/SPEC_zeek_metadata_archive.md` ↔ `_COLUMNS`, for the
same reason: a schema defined only in prose is a schema nothing checks (`LES-014`).

This is the ONE versioned schema in the project, so several tests here defend the *scope* of that
ratification rather than the schema's contents. Getting the scope wrong is `LES-001`'s failure
arriving from a new direction.
"""
import pathlib
import re

import pytest

from aximprint.ir import (
    COVERAGE_SOURCES, EVIDENCE_CLASSES, IR_TABLES, KEY_KINDS, SCHEDULE_CLASSES, SCHEMA_VERSION,
    can_read, parse_version,
)

_REPO = pathlib.Path(__file__).resolve().parent.parent
# The spec lives in the root `docs/contracts/` (the version-locked set), not in the package.
_SPEC = _REPO.parent / "docs" / "contracts" / "SPEC_network_ir.md"
_SRC = _REPO / "src" / "aximprint"


def _spec_tables() -> dict[str, list[str]]:
    """Column names per table, read out of the SPEC's own `###` sections.

    Parsed from the document rather than hand-listed here: a second hand-written copy would be a
    third thing to keep in sync, which is the problem this test exists to solve, not a second
    instance of it.
    """
    text = _SPEC.read_text()
    tables: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        if m := re.match(r"^### `(ir_\w+)`", line):
            current = m.group(1)
            tables[current] = []
        elif current and (m := re.match(r"^\| `(\w+)` \|", line)):
            tables[current].append(m.group(1))
        elif line.startswith("## ") and current:
            current = None
    return tables


# -- the agreement ---------------------------------------------------------------

def test_the_spec_declares_every_table_the_code_does():
    assert set(_spec_tables()) == set(IR_TABLES), (
        "SPEC and `ir/schema.py` disagree about which tables exist"
    )


@pytest.mark.parametrize("table", sorted(IR_TABLES))
def test_each_table_has_the_same_columns_in_the_same_order(table):
    """Order too, not just membership. §11 says "named fields, not positions", so order is not
    load-bearing at READ time — but a silent reordering between the two documents is a sign one
    was edited without the other, which is exactly what this test is watching for."""
    assert _spec_tables()[table] == list(IR_TABLES[table]), (
        f"{table}: SPEC {_spec_tables()[table]} != code {list(IR_TABLES[table])}"
    )


def test_every_table_is_keyed_on_the_pit():
    """Many PITs in one database is D2's whole point — a drift comparison is a join, not N
    file attaches. A table without `pit_id` could not participate."""
    for table, cols in IR_TABLES.items():
        assert cols[0] == "pit_id", f"{table} must lead with pit_id, got {cols[0]!r}"


# -- versioning ------------------------------------------------------------------

def test_the_spec_states_the_same_version_the_code_does():
    assert f"schema `{SCHEMA_VERSION}`" in _SPEC.read_text()


def test_the_version_is_a_column_not_only_a_constant():
    """An artifact has to state its own version to be readable by a build that did not write
    it. That is the entire difference between this and the belief store's build guard."""
    assert "schema_version" in IR_TABLES["ir_pit"]


@pytest.mark.parametrize("bad", ["1", "1.x", "", "v1.0", "1.0.0", "1..0"])
def test_a_malformed_version_is_refused_not_guessed(bad):
    """Parsed, never pattern-matched (`LES-008`): `startswith("1.")` accepts `"10.3"`."""
    with pytest.raises(ValueError):
        parse_version(bad)


def test_a_reader_accepts_any_minor_within_its_major_including_a_higher_one():
    """Looks wrong, is not: MINOR bumps are additive by definition (§11), so a newer artifact
    carries columns this reader ignores."""
    assert can_read("1.0", "1.0")
    assert can_read("1.7", "1.0"), "additive changes must not lock out an older reader"
    assert can_read("1.0", "1.7"), "nor a newer one reading an older artifact"


def test_a_reader_fails_closed_on_an_unknown_major():
    """§11: fail-closed load. A subtractive change may have removed something we depend on."""
    assert not can_read("2.0", "1.0")
    assert not can_read("1.0", "2.0")


# -- the vocabularies ------------------------------------------------------------

def test_the_compiler_class_is_not_an_ir_evidence_class():
    """The distinction this schema resolves. `compiler` (`defaulted` until 2026-09-04) is what a
    COMPILER does when a catalog supplies a value discovery never observed — a property of
    `twin_config`, not of the IR. Admitting it here would let a twin's `image:
    debian-12-cloud@v1` re-enter a drift comparison looking like evidence about the network.

    Both spellings are refused: the rename must not leave the old one quietly admissible."""
    assert "compiler" not in EVIDENCE_CLASSES
    assert "defaulted" not in EVIDENCE_CLASSES
    assert set(EVIDENCE_CLASSES) == {"asserted", "imported", "observed", "referenced", "imputed"}


def test_the_evidence_classes_are_declared_in_precedence_order():
    """D1: the tuple IS the ranking, so there is no second table to drift from it (`LES-101`).
    Pinned as the literal ORDER, not as a set — `asserted` outranking `imputed` is the whole
    content of the change, and a membership check would pass against any shuffle of it."""
    assert EVIDENCE_CLASSES == ("asserted", "imported", "observed", "referenced", "imputed")


def test_evidence_rank_is_lower_wins_and_agrees_with_the_tuple():
    from aximprint.ir import evidence_rank

    ranks = [evidence_rank(c) for c in EVIDENCE_CLASSES]
    assert ranks == sorted(ranks) == list(range(len(EVIDENCE_CLASSES)))
    assert evidence_rank("asserted") < evidence_rank("observed") < evidence_rank("imputed")


@pytest.mark.parametrize("bad", ["compiler", "defaulted", "", "OBSERVED", "hearsay"])
def test_evidence_rank_refuses_anything_outside_the_vocabulary(bad):
    """Fail closed (CONVENTIONS §3). A last-place default would silently decide that a class this
    build does not know is the weakest available — and for `compiler` specifically it would let a
    sidecar-only label compete against evidence, which is the masquerade the model forbids."""
    from aximprint.ir import evidence_rank

    with pytest.raises(ValueError, match="not an IR evidence class"):
        evidence_rank(bad)


def test_the_key_kinds_are_closed_and_include_the_weak_one():
    """`address` is deliberately present and deliberately weak: under `Q1` D4 an unanchored
    address resolves to itself, and a comparator joining two PITs on one is asserting only that
    the address did not change hands. It has to be expressible AND labelled."""
    assert set(KEY_KINDS) == {"hostname", "mac", "address"}


def test_coverage_source_is_a_vocabulary_not_a_boolean():
    """It must be able to grow. Today every row is inferred; a sensor that reports coverage
    directly must be expressible without a schema change — which, on a bool, would be a
    semantic redefinition, and §11 forbids those outright."""
    assert "inferred_from_traffic" in COVERAGE_SOURCES
    assert "sensor_reported" in COVERAGE_SOURCES


def test_schedule_classes_are_the_two_tokens_axranges_own_parser_accepts():
    """`docs/WORK_ORDER_traffic_profiles.md` (i): axRange's `_SCHEDULE_ALIASES` refuses anything
    outside this pair at their own parse time — a third value here would be silently unusable by
    the only consumer this field has."""
    assert set(SCHEDULE_CLASSES) == {"24x7", "business-hours"}


def test_every_vocabulary_the_spec_names_is_declared_in_code():
    spec = _SPEC.read_text()
    for value in (*EVIDENCE_CLASSES, *KEY_KINDS, *COVERAGE_SOURCES, *SCHEDULE_CLASSES):
        assert f"`{value}`" in spec, f"{value!r} is in code but never named in the SPEC"


# -- the scope of the ratification, which is the thing most likely to be got wrong

def test_no_other_package_has_acquired_a_version_field():
    """`LES-001` is amended, not repealed. The IR is the ONE ratified schema; a `SCHEMA_VERSION`
    appearing anywhere else means the exception leaked, which is the failure mode the amendment
    explicitly warns about."""
    offenders = [
        p.relative_to(_SRC).as_posix()
        for p in _SRC.rglob("*.py")
        if "SCHEMA_VERSION" in p.read_text() and p.parts[-2] != "ir"
    ]
    assert not offenders, (
        f"{offenders} declare a schema version. Only `aximprint.ir` may "
        "(DESIGN_BRIEF §0.2 as amended 2026-08-14; `LES-001`)"
    )


def test_the_two_cast_output_schemas_are_declared_once_and_are_not_ir_versions():
    """The 2026-09-04 exceptions, pinned so they stay narrow (`docs/WORK_ORDER_reference_ingest.md`
    D6/D-b, D-e; Authority: user). **Three since S2**, the third being the assertions file's own
    `axr-assertions/2` — an INPUT rather than a cast output, and versioned for the same reason:
    axRange's Console writes it on an independent lifecycle (`docs/WORK_ORDER_console_canvas.md`
    §9 seam 3), so neither side can tell a schema change from a parser bug without it.

    Two CAST OUTPUTS declare their own version — the provenance sidecar and the topology
    export — because axRange reads both on an independent lifecycle, which is `LES-001`'s own
    amended discriminator. That is a real widening of the project's versioning surface and is
    worth a guard rather than a promise, so this checks the three things that keep it narrow:
    each string is declared exactly once, in the module that emits it; neither is the IR's; and
    the IR's own version is unaffected by either.

    The test above catches a `SCHEMA_VERSION` leaking out of `aximprint.ir` — it cannot see these,
    because they are deliberately spelled differently, which is exactly why they need their own.
    """
    from aximprint.operator_assertions import ASSERTIONS_SCHEMA
    from aximprint.synth.compiler import PROVENANCE_SCHEMA
    from aximprint.synth.imprint_bundle import BUNDLE_SCHEMA

    assert PROVENANCE_SCHEMA == "axr-cast-provenance/1"
    assert ASSERTIONS_SCHEMA == "axr-assertions/2"
    assert BUNDLE_SCHEMA.startswith("axr-topology/")
    assert SCHEMA_VERSION not in (PROVENANCE_SCHEMA, BUNDLE_SCHEMA, ASSERTIONS_SCHEMA)

    # Declared once each, in the producer. A second literal in PRODUCTION code is the
    # duplicated-fact hazard that lets a producer and a consumer drift while both look right
    # (`LES-105`) — `scripts/verify_determinism.py` imports `PROVENANCE_SCHEMA` rather than
    # re-typing it, and this is what keeps that true. Matched in EITHER quote style, since a
    # single-quoted copy drifts exactly as easily as a double-quoted one.
    #
    # `tests/` is swept for the closed-set check below but deliberately NOT here: a test that
    # writes the literal is PINNING the contract, which is the opposite of duplicating it — the
    # assertion two lines up is itself such a pin, and a rule that forbade it would forbid
    # checking the value at all.
    for literal, owner in ((PROVENANCE_SCHEMA, "src/aximprint/synth/compiler.py"),
                           (ASSERTIONS_SCHEMA, "src/aximprint/operator_assertions.py"),
                           (BUNDLE_SCHEMA, "src/aximprint/synth/imprint_bundle.py")):
        holders = sorted(
            f.relative_to(_REPO).as_posix()
            for f in (*_SRC.rglob("*.py"), *(_REPO / "scripts").rglob("*.py"))
            if f'"{literal}"' in f.read_text() or f"'{literal}'" in f.read_text())
        assert holders == [owner], f"{literal} is declared in {holders}, expected only {owner}"


#: Version-shaped literals assigned to an ALL-CAPS `*_SCHEMA`/`*_VERSION` name — the shape every
#: versioned schema string in this repo has taken so far.
_VERSIONED_LITERAL = re.compile(
    r"^(?P<name>[A-Z][A-Z0-9_]*(?:_SCHEMA|_VERSION))\s*(?::[^=]+)?=\s*[\"'](?P<value>[^\"']*"
    r"(?:/\d+|\d+\.\d+))[\"']", re.MULTILINE)


def test_no_UNRATIFIED_versioned_schema_string_appears_outside_the_ir():
    """A CLOSED SET, not an allow-list of the two exceptions already known.

    The test above pins `PROVENANCE_SCHEMA` and `BUNDLE_SCHEMA` by name, which tests yesterday.
    This one discovers version-shaped literals structurally and asserts the discovered set is
    exactly the ratified one — which tests tomorrow. The distinction is not academic: the very
    next slice of `docs/WORK_ORDER_reference_ingest.md` introduces `axr-assertions/2` (§4), and
    under a name-based guard it would fail nothing at all.

    **Why this guard has to exist in this shape.** `CONVENTIONS.md` §3 says `ir` is *"the one
    package permitted to declare a schema version"* and cites this file as the enforcement — but
    `test_no_other_package_has_acquired_a_version_field` scans for the IDENTIFIER
    `SCHEMA_VERSION`, so once two versioned strings landed under deliberately different
    spellings (2026-09-04, Authority: user), that test enforced a spelling rather than the rule
    it is cited for. A bound stated in prose and checked by nothing is `LES-014`'s failure; a
    guard narrowed by the exception it was granted is the same failure arriving politely.

    Ratifying a schema is the user's call (`CONVENTIONS.md` §1.2), so a new string appearing
    here should stop the suite and force that call, not slip through.
    """
    from aximprint.operator_assertions import ASSERTIONS_SCHEMA
    from aximprint.synth.compiler import PROVENANCE_SCHEMA
    from aximprint.synth.imprint_bundle import BUNDLE_SCHEMA

    # `ASSERTIONS_SCHEMA` joined 2026-09-05 (S2). It is the case this guard was WRITTEN for — its
    # own docstring named `axr-assertions/2` as the string the previous, name-based test would
    # have missed — and it is ratified by the user in axRange
    # `docs/design/14-operator-assertions.md` §3, which is the design of record for the file.
    ratified = {ASSERTIONS_SCHEMA, PROVENANCE_SCHEMA, BUNDLE_SCHEMA, SCHEMA_VERSION}
    found: dict[str, str] = {}
    for path in (*_SRC.rglob("*.py"), *(_REPO / "scripts").rglob("*.py")):
        for m in _VERSIONED_LITERAL.finditer(path.read_text()):
            found[m.group("value")] = f"{path.relative_to(_REPO).as_posix()}:{m.group('name')}"
    unratified = {v: where for v, where in found.items() if v not in ratified}
    assert not unratified, (
        f"a versioned schema string appeared outside the ratified set: {unratified}. "
        f"Ratifying a schema is the USER's call (`CONVENTIONS.md` §1.2, `LES-001`) — add it to "
        f"`ratified` here only once that call has been made and recorded in the CHANGELOG."
    )
    # And the ratified ones must actually be discoverable by this sweep, or it is checking
    # nothing: a guard whose detector cannot see its own known cases is a guard in name only.
    assert ratified <= set(found), (
        f"the sweep no longer finds {sorted(ratified - set(found))} — its pattern has drifted "
        f"from how these constants are declared, so it would not catch a new one either")


def test_the_ir_schema_module_imports_nothing_from_aximprint():
    """Build step 1 is the schema and nothing else. The materializer (step 3) will import
    `store`; until it does, this module having no aximprint imports is what proves the step was
    kept to its scope rather than quietly growing into the next one."""
    src = (_SRC / "ir" / "schema.py").read_text()
    assert "from aximprint" not in src and "import aximprint" not in src


# -- schema 1.2's own vocabularies (`docs/WORK_ORDER_visibility_and_vantage.md`) ----

def test_the_merge_axes_are_a_closed_pair_and_neither_is_a_default():
    """Across taps the same window is seen twice; across spans it is seen once. A merge that
    picked an axis for a caller who did not think about it would compute coverage and volume
    wrongly for one of them, silently."""
    from aximprint.ir import MERGE_AXES
    assert set(MERGE_AXES) == {"vantage", "time"}


def test_unseen_reasons_is_a_growable_vocabulary_with_one_producer_today():
    """DESIGN_BRIEF §8 names four sources of referenced-but-unseen hosts — DNS answers, SPNs,
    redirects, XFF. Only the first has a producer, and the others must land here additively
    rather than by redefining this one (§11: redefinition is forbidden, not versioned)."""
    from aximprint.ir import UNSEEN_REASONS
    assert UNSEEN_REASONS == ("referenced_by_dns",)


def test_the_vantage_pair_resolves_to_one_set_and_unknown_is_not_the_empty_set():
    """`vantage` is the single tap of a materialized PIT; `vantages` is the set a merged one
    combines. `None` means nothing recorded a tap — which is not "sees through no taps"."""
    from aximprint.ir import vantage_set
    assert vantage_set({"vantage": "tap-a", "vantages": None}) == frozenset({"tap-a"})
    assert vantage_set({"vantage": None, "vantages": '["tap-a", "tap-b"]'}) == \
        frozenset({"tap-a", "tap-b"})
    assert vantage_set({"vantage": None, "vantages": None}) is None
