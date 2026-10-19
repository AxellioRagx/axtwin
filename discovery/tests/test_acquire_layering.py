"""Enforces CONVENTIONS §3's `acquire` boundary as code, not prose: "`acquire` must not
import `events`" (nor `windowing`, `learners`, `store`, `sinks`, `features`, `synth`) —
"the CLI wires the two [halves] and neither imports the other." `LES-014`'s point
generalizes past the entry it names ("a bound stated in a comment is not a bound"): the
same is true of an import-direction rule stated only in a doc. Nothing but a check like
this stops a future edit from adding `from aximprint import windowing` to
`acquire/harvest/pipeline.py` for a moment's convenience, and nothing would notice —
until the day `acquire`'s axapi/Zeek backend is swapped for a line-rate Xpress one
(DESIGN_BRIEF §0.1 as amended, CHANGELOG 2026-08-10) and the swap turns out not to be the
clean "new `acquire` path plus a new parser table" the boundary promised.

Static AST analysis, not a runtime import check: a violation hidden inside a function
body is invisible to "import the module and see what `sys.modules` picked up" unless
something in the test run happens to CALL that function, which no test here is obliged
to do. `ast.walk` descends into every function/class/branch/try body, not only
`module.body`'s top level, so a lazily-imported violation is caught exactly the same as
a module-level one.
"""
from __future__ import annotations

import ast
import pathlib

# `tests/` -> repo root -> `src/`. Everything below is path arithmetic, no I/O, so the
# resolver helpers can be exercised directly against a synthetic path + source string
# without touching disk (see the `_resolved_imports_from_source` unit tests below).
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SRC_ROOT = _REPO_ROOT / "src"
_ACQUIRE_ROOT = _SRC_ROOT / "aximprint" / "acquire"
_AXIMPRINT_ROOT = _SRC_ROOT / "aximprint"

# CONVENTIONS §3 / DESIGN_BRIEF §0.1 (amended 2026-08-10): every layer `acquire` must
# never reach into. Matched as `aximprint.<root>` (or a submodule of it) — a bare
# top-level package name, not a substring, so an unrelated module that merely happens to
# share one of these short names could never false-positive (there is no such collision
# in this codebase today; the check is written to be correct regardless).
_FORBIDDEN_FOR_ACQUIRE = frozenset({
    "events", "windowing", "learners", "store", "sinks", "features", "synth",
    # `catalog` (2026-08-14, `docs/WORK_ORDER_synth_v1.md` phase 2) is FORBIDDEN rather than
    # exempt, and the choice is worth stating because it does not follow from cycle-avoidance.
    # Like `provenance`, it imports nothing from `aximprint`, so importing it could never
    # create an upward edge — the mechanical test `provenance`'s exemption rests on. It is
    # forbidden on MEANING instead: `catalog/roles.yaml` is twin content (axRange image ids,
    # persona names, CPU sizings), which is the far end of the pipeline, and the rule this
    # guard enforces is not merely "no cycles" but DESIGN_BRIEF §0.1's — `acquire` produces
    # sensor artifacts and knows nothing about what the pipeline does with them. An `acquire`
    # module reading twin-role content would satisfy the letter of the layering rule and
    # violate the whole reason it exists.
    "catalog",
    # `ir` (2026-08-14, `docs/WORK_ORDER_network_ir.md` build step 1) — the point-in-time network
    # representation, the far end of the pipeline from acquisition. Forbidden on the same grounds
    # as every entry above it and unremarkable as a classification; noted only because `ir/schema.py`
    # currently imports nothing at all, so a cycle-based test would let it through exactly as it
    # would `catalog`.
    "ir",
    # `operator_assertions` (2026-09-05, `docs/WORK_ORDER_reference_ingest.md` S2) — a top-level
    # MODULE, like `provenance` and `vantage`, and unlike both of those it is FORBIDDEN, on
    # `catalog`'s reasoning rather than on cycles. It imports nothing from `aximprint`, so it
    # could never create an upward edge; what it holds is twin content — the closed sets of
    # overridable twin fields, axRange's SDN zone types, a `resources` shape mirroring
    # `catalog/roles.yaml`. An `acquire` module reading those would satisfy the letter of the
    # layering rule and defeat the reason it exists (DESIGN_BRIEF §0.1: acquire produces sensor
    # artifacts and knows nothing about what the pipeline does with them).
    "operator_assertions",
})


def _module_files(root: pathlib.Path, *, exclude_dirs: frozenset[pathlib.Path] = frozenset(),
                  exclude_files: frozenset[pathlib.Path] = frozenset()) -> list[pathlib.Path]:
    assert root.is_dir(), f"expected {root} to exist"
    files = sorted(
        p for p in root.rglob("*.py")
        if "__pycache__" not in p.parts
        and p not in exclude_files
        and not any(d in p.parents for d in exclude_dirs)
    )
    assert files, f"no .py files found under {root} (after exclusions) — the check below would pass vacuously"
    return files


def _package_of(path: pathlib.Path) -> str:
    """The dotted package name a **level-1** relative import inside `path` resolves
    against. A regular module's package is its own dotted name minus its own last
    component; `__init__.py`'s package is its own directory chain, i.e. the *same*
    strip — `.with_suffix("")` always leaves the file's basename (`__init__`, for a
    package marker, or the real module name otherwise) as the trailing component, and
    both cases want exactly that one component dropped.
    """
    rel = path.relative_to(_SRC_ROOT)
    parts = list(rel.with_suffix("").parts)[:-1]
    return ".".join(parts)


def _resolved_imports_from_source(source: str, path: pathlib.Path) -> list[tuple[int, str]]:
    """Every dotted module path `source` (the text of `path`, real or synthetic) can
    reach via `import`/`from ... import`, anywhere in the file, plus the one dynamic-
    import escape hatch (`importlib.import_module`/`__import__` with a literal string
    target — unused anywhere in `acquire` today, checked, but a checker that only
    understands `import` statements would silently miss it if that ever changed).

    Returns `(lineno, dotted_path)` pairs. For `from X import a, b` this yields BOTH `X`
    itself and `X.a`, `X.b`: nothing here executes the import, so `from aximprint import
    events` (which reaches the forbidden `aximprint.events` exactly as much as `from
    aximprint.events import Flow` does) is indistinguishable at the AST level from `from
    aximprint import some_function_defined_in___init__`. Checking both readings makes a
    false positive possible in principle (there is no such name collision among the
    forbidden roots today) but a false negative on an import-direction boundary is the
    far more expensive mistake.
    """
    tree = ast.parse(source, filename=str(path))
    resolved: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                resolved.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module  # `from X import ...` — module is never None here
            else:
                pkg_parts = _package_of(path).split(".") if _package_of(path) else []
                strip = node.level - 1  # level=1 -> this package; level=N -> N-1 more up
                base_parts = pkg_parts[: len(pkg_parts) - strip] if strip else pkg_parts
                base = ".".join(base_parts) if base_parts else None
                if node.module:
                    base = f"{base}.{node.module}" if base else node.module
            if base:
                resolved.append((node.lineno, base))
                for alias in node.names:
                    resolved.append((node.lineno, f"{base}.{alias.name}"))
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name in ("import_module", "__import__") and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    resolved.append((node.lineno, arg.value))
    return resolved


def _resolved_imports(path: pathlib.Path) -> list[tuple[int, str]]:
    return _resolved_imports_from_source(path.read_text(), path)


def _forbidden_root(dotted: str, forbidden_roots: frozenset[str]) -> str | None:
    """The violated root name, or `None`. `aximprint.<root>` and any submodule of it
    (`aximprint.windowing.windows`) are equally forbidden — importing one function from
    three levels deep is not a smaller violation than importing the package.
    """
    parts = dotted.split(".")
    if len(parts) >= 2 and parts[0] == "aximprint" and parts[1] in forbidden_roots:
        return parts[1]
    return None


def _violations(root: pathlib.Path, forbidden_roots: frozenset[str], *,
                exclude_dirs: frozenset[pathlib.Path] = frozenset(),
                exclude_files: frozenset[pathlib.Path] = frozenset()) -> list[str]:
    out: list[str] = []
    for path in _module_files(root, exclude_dirs=exclude_dirs, exclude_files=exclude_files):
        for lineno, dotted in _resolved_imports(path):
            hit = _forbidden_root(dotted, forbidden_roots)
            if hit is not None:
                rel = path.relative_to(_REPO_ROOT)
                out.append(f"{rel}:{lineno}: imports {dotted!r} (forbidden root: {hit!r})")
    return sorted(out)


# --- the enforcement gate --------------------------------------------------------------


def test_acquire_imports_nothing_from_the_discovery_pipeline():
    """CONVENTIONS §3: `acquire` must not import `events`, `windowing`, `learners`,
    `store`, `sinks`, `features`, or `synth` — scope is `src/aximprint/acquire` (the
    production import graph a future backend swap depends on), not `tests/acquire`
    (xtract's own test tree, moved verbatim; independently confirmed clean by inspection,
    but not this test's job to keep clean — the CLI's own layering, not its tests', is
    what CONVENTIONS §3 states).
    """
    violations = _violations(_ACQUIRE_ROOT, _FORBIDDEN_FOR_ACQUIRE)
    assert not violations, (
        "acquire must not import the discovery pipeline (CONVENTIONS §3) — "
        f"found {len(violations)} violation(s):\n  " + "\n  ".join(violations)
    )


def test_the_discovery_pipeline_does_not_import_acquire_back():
    """The other half of "neither imports the other": no non-CLI module under
    `src/aximprint` may import `aximprint.acquire`. Two exclusions from the walk, both
    structural (pruned before a single import is even inspected, not filtered afterward
    by matching on the formatted violation string — `LES-008`/`LES-030`'s "test the
    property, not a proxy for its shape"): the whole `acquire/` subtree itself (every
    file in it legitimately imports its own `aximprint.acquire.*` siblings — that is not
    a "the pipeline reaches into acquire" violation, it is acquire reaching into itself),
    and `cli.py`, the one sanctioned wiring point (build step 5). Excluding by directory
    rather than by a hand-maintained "modules that must not import acquire" list also
    means a brand new top-level `aximprint` package added later is covered automatically,
    with no parallel list to remember to update.
    """
    violations = _violations(_AXIMPRINT_ROOT, frozenset({"acquire"}),
                             exclude_dirs=frozenset({_ACQUIRE_ROOT}),
                             exclude_files=frozenset({_AXIMPRINT_ROOT / "cli.py"}))
    assert not violations, (
        "no non-CLI module may import aximprint.acquire (CONVENTIONS §3, \"neither "
        f"imports the other\") — found {len(violations)} violation(s):\n  " +
        "\n  ".join(violations)
    )


def test_forbidden_roots_are_exactly_conventions_mds_list_no_more_no_less():
    """A regression guard on the guard: if a new top-level `aximprint` package OR module
    appears (or one is retired), this fails loudly instead of `_FORBIDDEN_FOR_ACQUIRE`
    silently going stale — `LES-027`'s "a hardcoded allowlist must be updated in the same
    change it is made relevant" shape, applied here to this file's own list. (Not, as an
    earlier version of this docstring said, to `inject_lessons.py`'s `_MODULES` — that
    hardcoded allowlist was LES-027's own subject in 2026-08-06 but has since been
    replaced by `repo_dir_names()`, a set derived from the tree itself; the analogous
    failure mode LES-027 names is what this assertion generalizes, not a still-live
    twin of that specific list.)

    Packages (directories with `__init__.py`) are not the whole set of things `acquire`
    could import: `src/aximprint/provenance.py` is a top-level MODULE, invisible to a
    directory-only scan, and a future sibling (e.g. a shared `aximprint/resolver.py`)
    would be equally invisible and equally able to slip past this guard undetected.
    Folded in alongside the directory scan so both shapes are covered by one check.
    """
    actual_packages = {
        p.name for p in _AXIMPRINT_ROOT.iterdir()
        if p.is_dir() and (p / "__init__.py").is_file() and p.name != "acquire"
    }
    actual_packages |= {p.stem for p in _AXIMPRINT_ROOT.glob("*.py")} - {"__init__", "cli"}
    # `ingest` is `acquire`'s CLI-level sibling, not a package `acquire` is forbidden from
    # importing FROM (nothing stops `ingest`-adjacent code from one day reading an
    # `acquire`-produced artifact off disk) — exempt here from `_FORBIDDEN_FOR_ACQUIRE`,
    # and separately still covered by `test_the_discovery_pipeline_does_not_import_acquire_back`
    # for the REVERSE direction (that test excludes only `acquire/` itself and `cli.py`,
    # so `ingest` is checked there like every other non-CLI package, no list needed).
    #
    # `provenance` is a top-level MODULE (see the docstring above), not a pipeline layer:
    # its own docstring states it has "no dependency on any pipeline layer... so this
    # module sits below every layer and may be imported from any of them without creating
    # an upward edge" — the same shape as `ingest`'s exemption, for a module instead of a
    # package. `acquire` does not import it today (grep-verified), but nothing forbids it.
    #
    # `vantage` (2026-08-17, `docs/WORK_ORDER_visibility_and_vantage.md` build step 1) is the same
    # shape as `provenance` and is the first module `acquire` actually DOES import. It defines one
    # thing: the format of the vantage manifest `harvest` writes beside the cold tier and the zeek
    # plugin reads back. That file is a **sensor artifact on disk**, which is precisely what
    # `acquire` produces, so writing it creates no dependency on the discovery pipeline — and the
    # module imports nothing from `aximprint`, so it cannot create an upward edge either. The
    # alternative (each side spelling the same JSON by hand, to avoid an import) is `LES-005`'s
    # failure with extra steps: a format is a contract with two ends.
    known_not_forbidden_for_acquire = {"ingest", "provenance", "vantage"}
    unaccounted = actual_packages - _FORBIDDEN_FOR_ACQUIRE - known_not_forbidden_for_acquire
    assert not unaccounted, (
        f"new top-level aximprint package/module(s) {sorted(unaccounted)} are classified "
        "neither forbidden-for-acquire nor explicitly exempt — decide which, and update "
        "_FORBIDDEN_FOR_ACQUIRE or known_not_forbidden_for_acquire in this file"
    )
    assert _FORBIDDEN_FOR_ACQUIRE <= actual_packages, (
        f"_FORBIDDEN_FOR_ACQUIRE names {sorted(_FORBIDDEN_FOR_ACQUIRE - actual_packages)}, "
        "which no longer exist under src/aximprint/ — this list has gone stale"
    )


# --- unit tests of the resolver itself (synthetic sources, no filesystem) --------------
#
# The enforcement gate above proves the CURRENT tree is clean; these prove the CHECKER
# would actually catch the shapes a violation could take, independent of what happens to
# be true of the tree on any given day.

_FAKE_ACQUIRE_MODULE = _ACQUIRE_ROOT / "client.py"          # package: aximprint.acquire
_FAKE_ACQUIRE_SUBMODULE = _ACQUIRE_ROOT / "harvest" / "pipeline.py"  # package: ...acquire.harvest


def _hits(source: str, path: pathlib.Path = _FAKE_ACQUIRE_MODULE) -> list[str]:
    return [dotted for _, dotted in _resolved_imports_from_source(source, path)
            if _forbidden_root(dotted, _FORBIDDEN_FOR_ACQUIRE) is not None]


def test_resolver_catches_a_module_level_from_import():
    assert _hits("from aximprint.events import Flow\n") == ["aximprint.events", "aximprint.events.Flow"]


def test_resolver_catches_a_bare_import_of_a_submodule():
    assert _hits("import aximprint.windowing.windows\n") == ["aximprint.windowing.windows"]


def test_resolver_catches_from_aximprint_import_root_package_by_name():
    """`from aximprint import windowing` reaches `aximprint.windowing` exactly like `from
    aximprint.windowing import X` does — the AST alone cannot tell "windowing" apart from
    an ordinary name defined in `aximprint/__init__.py`, so both readings are checked.
    """
    assert "aximprint.windowing" in _hits("from aximprint import windowing\n")


def test_resolver_catches_an_import_nested_inside_a_function_body():
    """The whole reason this is an AST walk and not a module-header scan: a violation
    guarded behind a lazy/optional import must be exactly as visible as one at the top
    of the file.
    """
    source = (
        "def _lazy():\n"
        "    if True:\n"
        "        from aximprint.store import BeliefStore\n"
        "        return BeliefStore\n"
    )
    assert "aximprint.store" in _hits(source)


def test_resolver_catches_a_relative_import_that_escapes_the_acquire_package():
    """`from .. import store` inside `aximprint/acquire/harvest/pipeline.py` (package
    `aximprint.acquire.harvest`, level=2 -> strip one component -> `aximprint.acquire`)
    would reach for `aximprint.acquire.store`, not the forbidden `aximprint.store` — a
    relative import can only ever resolve to something inside `acquire` itself here,
    which is the point: this test asserts the (correct) NON-match, so a future edit that
    accidentally makes `_package_of` resolve two levels too far cannot silently start
    reporting false negatives.
    """
    assert _hits("from .. import store\n", path=_FAKE_ACQUIRE_SUBMODULE) == []
    assert _resolved_imports_from_source("from .. import store\n", _FAKE_ACQUIRE_SUBMODULE) == [
        (1, "aximprint.acquire"),
        (1, "aximprint.acquire.store"),
    ]


def test_resolver_catches_the_importlib_dynamic_escape_hatch():
    assert "aximprint.events" in _hits('importlib.import_module("aximprint.events")\n')
    assert "aximprint.windowing" in _hits('__import__("aximprint.windowing")\n')


def test_resolver_does_not_false_positive_on_ordinary_acquire_and_stdlib_imports():
    source = (
        "from __future__ import annotations\n"
        "import sys\n"
        "import requests\n"
        "from pathlib import Path\n"
        "from aximprint.acquire import catalog, manifest\n"
        "from aximprint.acquire.errors import XtractError\n"
        "import aximprint\n"
    )
    assert _hits(source) == []
