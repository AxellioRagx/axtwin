"""`scripts/check_role_fixed_point.py` -- the round-trip self-check must fail LOUD when it
cannot run, and its three exit codes must mean three different things.

It used to print `skip: ... not importable` and exit 0 whenever axRange's persona catalog
could not be imported -- which, in this project's own venv, was every time (axRange pulls in
`ruamel`, deliberately not a dependency here). A gate that reports green without having
checked anything is worse than no gate (`LES-014`: a rule nobody checks is not a rule), and
the work order that relies on this script (`docs/WORK_ORDER_proto_weight_rows.md` §6, "must
not gain a new non-fixed-point row") would have been satisfied by a skip.

Hermetic: axRange is stood in for by a stub package planted under `AXRANGE_ROOT`, shaped
exactly as the script consumes it, and the catalog under test is a planted file -- so nothing
here depends on which interpreter runs the suite, what it has installed, or the shipped
catalog's content.

    exit 0   every row is a fixed point
    exit 1   at least one row is not (a FINDING)
    exit 2   the check could not look: axRange not importable, a persona it does not ship,
             an unreadable catalog
"""
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "check_role_fixed_point.py"

# A stand-in for `axrange.personas`, shaped exactly as the script consumes it: `get(name)`
# returns an object with `.drive` and `.emits`, each emit with `.ports` and
# `.effective_transport()`, and None for a name it does not ship. `"*"` is a wildcard: any
# name gets that persona (for a run against the shipped catalog).
_STUB = '''\
_CATALOG = {catalog!r}


class _Emit:
    def __init__(self, ports, transport):
        self.ports, self._transport = ports, transport

    def effective_transport(self):
        return self._transport


class _Persona:
    drive = "scripted"

    def __init__(self, emits):
        self.emits = [_Emit(ports, transport) for ports, transport in emits]


def get(name):
    emits = _CATALOG.get(name, _CATALOG.get("*"))
    return None if emits is None else _Persona(emits)
'''


def _plant_axrange(root: Path, personas: dict) -> Path:
    """`personas`: `{name: [([ports], transport), ...]}`; `"*"` matches any name."""
    pkg = root / "axrange" / "personas"
    pkg.mkdir(parents=True)
    (root / "axrange" / "__init__.py").write_text("")
    (pkg / "__init__.py").write_text(_STUB.format(catalog=personas))
    return root


def _plant_catalog(path: Path, rows: str) -> Path:
    path.write_text("roles:\n" + textwrap.dedent(rows))
    return path


def _run(root: Path, *args, cwd: Path = _REPO):
    return subprocess.run([sys.executable, str(_SCRIPT), *map(str, args)],
                          capture_output=True, text=True, check=False, cwd=str(cwd),
                          env={**os.environ, "AXRANGE_ROOT": str(root)})


# One listen-and-dial row: `samba` serves the 445 it requires, and its persona must emit 88.
_ONE_ROW = """\
  - role: r
    persona: p
    requires: ["445/tcp"]
    dials: ["88/tcp"]
    install: [{pkg: samba}]
"""


def test_a_catalog_whose_every_row_is_a_fixed_point_exits_zero(tmp_path):
    root = _plant_axrange(tmp_path, {"p": [([88], "tcp")]})
    r = _run(root, _plant_catalog(tmp_path / "roles.yaml", _ONE_ROW))
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "1 catalog rows, 1 are fixed points" in r.stdout
    assert r.stderr == ""


def test_a_row_that_is_not_a_fixed_point_exits_one_and_names_both_halves(tmp_path):
    """Exit 1 is a FINDING: the row's install does not serve what it requires, or its persona
    does not emit what it dials -- the two halves of the round trip, each reported by port."""
    root = _plant_axrange(tmp_path, {"p": [([443], "tcp")]})          # emits 443, never 88
    catalog = _plant_catalog(tmp_path / "roles.yaml",
                             _ONE_ROW.replace('["445/tcp"]', '["5432/tcp"]'))  # samba: no 5432
    r = _run(root, catalog)
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "1 catalog rows, 0 are fixed points" in r.stdout
    assert "NOT SERVED by its install: ['5432/tcp']" in r.stdout
    assert "NOT EMITTED by its persona: ['88/tcp']" in r.stdout


def test_a_persona_axrange_does_not_ship_is_a_loud_could_not_look_not_a_crash(tmp_path):
    """`get()` returning None used to raise AttributeError on `.drive` -- a traceback and exit
    1, the code reserved for a real finding. A persona the other project does not ship means
    the row cannot be checked at all, which is a different fact from a row that failed."""
    root = _plant_axrange(tmp_path, {"p": [([88], "tcp")]})           # ships `p`, not `ghost`
    r = _run(root, _plant_catalog(tmp_path / "roles.yaml",
                                  _ONE_ROW.replace("persona: p", "persona: ghost")))
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "error:" in r.stderr and "'ghost'" in r.stderr
    assert "Traceback" not in r.stderr
    assert "fixed points" not in r.stdout, "must not report a result it could not compute"


def test_an_unimportable_persona_catalog_is_a_loud_nonzero_exit_not_a_skip(tmp_path):
    # A stub `axrange` that refuses to import, planted where the script looks FIRST, so the
    # outcome is the same under every interpreter whatever it happens to have installed.
    (tmp_path / "axrange").mkdir()
    (tmp_path / "axrange" / "__init__.py").write_text(
        'raise ImportError("stub: no persona catalog here")\n')
    r = _run(tmp_path)
    # 2, not 1: a check that could not run is a different fact from a row that failed it.
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "not importable" in r.stderr and str(tmp_path) in r.stderr
    assert "fixed points" not in r.stdout, "must not report a result it could not compute"
    assert "skip" not in (r.stdout + r.stderr).lower()


def test_an_unreadable_catalog_is_a_could_not_look_not_a_finding(tmp_path):
    root = _plant_axrange(tmp_path, {"*": []})
    r = _run(root, tmp_path / "does-not-exist.yaml")
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "error:" in r.stderr and "Traceback" not in r.stderr
    assert "fixed points" not in r.stdout


def test_the_shipped_catalog_is_found_from_any_working_directory(tmp_path):
    """The default catalog path used to be cwd-relative, so running the script from anywhere
    but the repo root raised FileNotFoundError -- exit 1, in the code reserved for a finding.
    Anchored on the script's own location now. Run from a foreign cwd with no argument, under
    a wildcard persona that emits nothing: the only thing asserted about the OUTCOME is that
    it is a report (0 or 1), never a traceback -- the shipped catalog's content is
    `test_role_catalog.py`'s business, not this file's."""
    root = _plant_axrange(tmp_path, {"*": []})
    r = _run(root, cwd=tmp_path)
    assert r.returncode in (0, 1), (r.stdout, r.stderr)
    assert "Traceback" not in r.stderr
    assert re.search(r"\d+ catalog rows, \d+ are fixed points", r.stdout), r.stdout


# -- a row with NO evidence: skipped by name, or a finding --------------------------------
#
# `docs/WORK_ORDER_deployment_scope.md` §5. The catalog's `default:` role
# (`unroled-endpoint`) has no `requires:` and no `dials:`, so every set difference the checker
# computes is empty and it would score as a fixed point. That is a VACUOUS green — `LES-125`'s
# ANY-gate failure, where a guard whose trigger condition is stronger than its defect
# population turns an unchecked row into a checked-looking one.

_NO_EVIDENCE = """\
  - role: unroled-endpoint
    persona: null
    requires: []
    dials: []
    install: []
"""


def test_a_declared_no_evidence_row_is_skipped_by_name_and_not_counted(tmp_path):
    root = _plant_axrange(tmp_path, {"p": [([88], "tcp")]})
    r = _run(root, _plant_catalog(tmp_path / "roles.yaml", _ONE_ROW + _NO_EVIDENCE))
    assert r.returncode == 0, (r.stdout, r.stderr)
    # ONE checked row out of two declared: the vacuous row leaves the denominator, so the
    # headline can never claim to have checked something it could not ask about.
    assert "1 catalog rows, 1 are fixed points (1 skipped, not counted)" in r.stdout
    assert "unroled-endpoint" in r.stdout and "SKIPPED" in r.stdout


def test_an_UNDECLARED_no_evidence_row_is_a_finding_not_a_pass(tmp_path):
    """The mutation half (`LES-031`): remove the row's name from `VACUOUS` and the check must
    FAIL. A skip list that can be emptied without anything going red is not a gate."""
    root = _plant_axrange(tmp_path, {"p": [([88], "tcp")]})
    catalog = _plant_catalog(tmp_path / "roles.yaml",
                             _ONE_ROW + _NO_EVIDENCE.replace("unroled-endpoint", "mystery-role"))
    r = _run(root, catalog)
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "mystery-role" in r.stdout and "NO EVIDENCE" in r.stdout
    # And the headline must not read clean while a finding is printed under it (`LES-133`).
    assert "2 catalog rows, 1 are fixed points" in r.stdout


def test_the_default_blocks_role_is_included_in_the_run(tmp_path):
    """It is a role the twin actually deploys, so a checker that never looked at it would make
    "N catalog rows, N are fixed points" a narrower claim than it reads as."""
    root = _plant_axrange(tmp_path, {"p": [([88], "tcp")]})
    catalog = tmp_path / "roles.yaml"
    catalog.write_text(
        "default:\n  role: unroled-endpoint\n  image: img@v1\n  install: []\n"
        "roles:\n" + textwrap.dedent(_ONE_ROW))
    r = _run(root, catalog)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "unroled-endpoint" in r.stdout and "SKIPPED" in r.stdout
    assert "1 catalog rows, 1 are fixed points (1 skipped, not counted)" in r.stdout
