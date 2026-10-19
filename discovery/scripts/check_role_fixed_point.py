#!/usr/bin/env python3
"""Is each catalog role a FIXED POINT of the round trip?  role -> persona -> traffic -> role.

`docs/JOURNAL_corpapcn_fidelity.md` GAP-C8. aximprint infers a role from one signature and
then assigns a persona that emits a different one. Where the two disagree, a deployed twin node
can never be re-inferred as the role it was built to be — the twin cannot describe itself, and
anything downstream that keys on roles (detection rules, inventory, segmentation) is testing a
network whose labels do not survive observation.

Only a closed loop exposes this: a cast alone looks correct, and a deploy alone looks correct.

The check, per catalog row:
  * `requires` (ports the role must ANSWER on) must be served by something the row installs,
    since a persona emits, it does not listen.
  * `dials` (ports the role must ORIGINATE to) must appear in its persona's own emit ports.
  * A row with NEITHER is vacuous -- nothing to re-serve, nothing to re-emit, so every check
    passes trivially. Those are excluded from the count and named, when declared in `VACUOUS`;
    an undeclared one is a FINDING. The catalog's `default:` role (`unroled-endpoint`) is the
    only such row today, and it is included in the run precisely so the exclusion is visible.

Needs axRange importable for the persona catalog, and FAILS LOUD (exit 2, distinct from the 1 a
non-fixed-point row earns) when it is not: a check that could not run must never read as a check
that passed. It used to print `skip:` and exit 0 in that case -- a vacuous green, and in this
project's own venv it took that path EVERY time, because axRange's persona catalog pulls in
`ruamel`, which aximprint deliberately does not carry (no runtime dependency for a script;
`roles.yaml`'s own note). Run it under an interpreter that can import axRange -- its own venv
can:

    /home/rocky/axRange/.venv/bin/python scripts/check_role_fixed_point.py

`AXRANGE_ROOT` overrides where the axRange checkout is looked for (default `/home/rocky/axRange`).
An optional argument names the catalog to check (default: this repo's `roles.yaml`, anchored on
the script's own location so it runs from any working directory).

    exit 0   every row is a fixed point
    exit 1   at least one row is not -- a FINDING, reported per port
    exit 2   the check could not look: axRange not importable, a persona it does not ship, an
             unreadable catalog. Never conflated with a finding.
"""
import os
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
#: Anchored on this file, never on the cwd: a cwd-relative path raised FileNotFoundError from any
#: other directory -- a traceback and exit 1, the code reserved for a real finding.
CATALOG = REPO / "src" / "aximprint" / "catalog" / "roles.yaml"

# what a package is expected to listen on, for the `requires` half
SERVES = {
    "bind9": {"53/udp", "53/tcp"}, "nginx": {"80/tcp", "443/tcp"},
    "samba": {"445/tcp"}, "postfix": {"25/tcp", "587/tcp"}, "squid": {"3128/tcp"},
    "postgresql": {"5432/tcp"}, "prometheus": {"9090/tcp"},
}

# `install` actions are not only `pkg:` -- axRange also takes `role:`, which stands a whole
# service up. Modelling only packages made an AD promotion look like it served nothing.
ROLE_SERVES = {
    "adds": {"88/tcp", "389/tcp", "445/tcp", "636/tcp", "53/udp", "464/tcp"},
}

# Ports the IMAGE provides with no install action at all. Without this the check cries wolf on
# every role keyed to an OS service -- sshd is on every Linux cloud image, and RDP/Delivery
# Optimization ship with Windows. A checker that reports known-good rows as failures gets
# ignored, which is worse than not having one.
OS_PROVIDED = {"22/tcp", "3389/tcp", "3389/udp", "7680/tcp"}

# Roles the fixed-point question CANNOT BE ASKED OF, with the reason -- distinct from `DECLARED`
# below, which names rows that CAN be asked and are known unrealisable.
#
# A row with no `requires:` and no `dials:` has nothing to re-serve and nothing to re-emit, so
# every set difference below is empty and it would be counted as a fixed point. That is a VACUOUS
# green -- `LES-125`'s ANY-gate failure exactly: a guard whose trigger condition is stronger than
# its defect population turns an unchecked row into a checked-looking one. Such a row is therefore
# excluded from the count and REPORTED by name, and a row that reaches it WITHOUT being declared
# here is a finding, not a pass.
#
# `unroled-endpoint` is the catalog's `default:` role (`docs/WORK_ORDER_deployment_scope.md` D4):
# what a placed host carries when no evidence rule matched. It is unreachable from `match_role` by
# construction -- it is not under `roles:` -- and it has no persona, so the round trip
# role -> persona -> traffic -> role has no leg to walk at all.
VACUOUS = {
    "unroled-endpoint":
        "no `requires:`/`dials:` and no persona -- the role a host gets when NOTHING matched, so "
        "there is no evidence to re-serve and no traffic to re-infer it from (D4)",
}

# Rows that a twin genuinely CANNOT realise, with the reason. Declared rather than silently
# tolerated: a detection rule may legitimately outlive anything we can build, and the honest
# record is "known unrealisable", not a passing check.
DECLARED = {
    ("hypervisor", "8006/tcp"):
        "no VM can serve Proxmox's own management API; the rule recognises real estates",
    # REMOVED 2026-09-05: the two `domain-controller` rows (88/tcp, 389/tcp).
    #
    # They were declared because the catalog's `install:` was `[]`, so nothing served the two
    # ports the role is INFERRED from -- the twin could not describe itself. `roles.yaml` now
    # carries `install: [{role: adds}]` again (`axrange/docs/WORK_ORDER_protocol_faithful_
    # services.md` D1(a)+D4, Authority: user), `ROLE_SERVES["adds"]` already models what an AD
    # promotion serves, and the round trip closes on its own evidence rather than on an
    # exception. Deleted rather than left with a corrected reason: an exception that no longer
    # describes anything is the vacuous-green shape this whole script exists to refuse.
    #
    # The 2026-09-04 finding that preceded this stands and is recorded at the `domain-controller`
    # row in `src/aximprint/catalog/roles.yaml`: the realm IS on the wire, the blocker was never
    # the evidence. What actually unblocked the row is a RULING -- the twin's forest is the lab
    # realm, so no observed name has to be reproduced at all.
    ("monitoring-collector", "9997/tcp"):
        "Splunk-estate signature; the 9090 row covers the Prometheus estate this twin builds",
}


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if len(args) > 1:
        print(f"usage: {Path(sys.argv[0]).name} [roles.yaml]", file=sys.stderr)
        return 2
    catalog = Path(args[0]) if args else CATALOG
    root = os.environ.get("AXRANGE_ROOT", "/home/rocky/axRange")
    sys.path.insert(0, root)
    try:
        from axrange.personas import get as get_persona
    except Exception as exc:
        # Loud, on stderr, non-zero, and NOT the exit code a real finding uses. The former
        # `skip: ...` / exit 0 was a gate that reported green without having checked anything
        # (`LES-014`: a rule nobody checks is not a rule), and nothing distinguished "24 fixed
        # points" from "could not look".
        print(f"error: axRange persona catalog not importable from {root} ({exc}). This check "
              f"needs an interpreter that can import axRange (its own .venv can; set "
              f"AXRANGE_ROOT to point elsewhere) -- refusing to report a result it could not "
              f"compute.", file=sys.stderr)
        return 2

    try:
        doc = yaml.safe_load(catalog.read_text())
    except (OSError, yaml.YAMLError) as exc:
        print(f"error: cannot read {catalog} ({exc}) -- refusing to report a result it could "
              f"not compute.", file=sys.stderr)
        return 2
    rows = doc.get("roles") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        print(f"error: {catalog} has no top-level `roles:` list -- refusing to report a result "
              f"it could not compute.", file=sys.stderr)
        return 2
    # The `default:` block declares a ROLE too (`docs/WORK_ORDER_deployment_scope.md` D4), and
    # every placed host that matched nothing carries it into the twin. It is checked here rather
    # than left out of the run: a role the twin actually deploys and this script never looks at
    # would make "N catalog rows, N are fixed points" a narrower claim than it reads as. It has
    # no evidence, so it lands in `VACUOUS` below -- reported, never counted.
    default_block = doc.get("default") if isinstance(doc, dict) else None
    if isinstance(default_block, dict) and default_block.get("role"):
        rows = list(rows) + [{k: v for k, v in default_block.items()
                              if k in {"role", "persona", "install"}}]
    bad, unknown, vacuous, undeclared_vacuous = [], [], [], []
    for r in rows:
        role, pname = r["role"], r.get("persona")
        req, dials = set(r.get("requires") or []), set(r.get("dials") or [])
        # NO EVIDENCE -> the question cannot be asked. Checked FIRST, before the persona lookup:
        # every difference below would be empty and the row would score as a fixed point, which
        # is the vacuous green this branch exists to refuse (`LES-125`).
        if not req and not dials:
            (vacuous if role in VACUOUS else undeclared_vacuous).append(role)
            continue
        served, emitted, drive = set(), set(), None
        for act in (r.get("install") or []):
            served |= SERVES.get(act.get("pkg"), set())
            served |= ROLE_SERVES.get(act.get("role"), set())
        if pname:
            try:
                p = get_persona(pname)
            except Exception:
                p = None
            if p is None:
                # A persona axRange does not ship is a could-not-look, not a finding and not a
                # crash: this used to raise AttributeError on `p.drive` -- a traceback and
                # exit 1. Collected, so one run names every such row.
                unknown.append((role, pname))
                continue
            drive = p.drive
            for e in p.emits:
                for port in e.ports:
                    emitted.add(f"{port}/{e.effective_transport()}")
        missing_serve = req - served - OS_PROVIDED
        missing_dial = dials - emitted
        missing_serve = {m for m in missing_serve if (role, m) not in DECLARED}
        missing_dial = {m for m in missing_dial if (role, m) not in DECLARED}
        if missing_serve or missing_dial:
            bad.append((role, pname, drive, sorted(missing_serve), sorted(missing_dial)))

    if unknown:
        named = ", ".join(f"{role!r} -> {pname!r}" for role, pname in unknown)
        print(f"error: {len(unknown)} catalog row(s) name a persona axRange's catalog does not "
              f"ship ({named}) -- this check cannot say whether those rows are fixed points; "
              f"refusing to report a result it could not compute.", file=sys.stderr)
        return 2

    # `checked` is the DENOMINATOR the headline reports against, and it excludes the DECLARED
    # vacuous rows rather than counting them as passes. "24 catalog rows, 24 are fixed points"
    # over a set containing a row nothing could be asked of is the false-clean-bill-of-health
    # shape. An UNDECLARED vacuous row stays in the denominator and out of the numerator, so the
    # headline can never read clean while a finding is printed under it (`LES-133`).
    checked = len(rows) - len(vacuous)
    print(f"{checked} catalog rows, {checked - len(bad) - len(undeclared_vacuous)} are fixed "
          f"points" + (f" ({len(vacuous)} skipped, not counted)" if vacuous else "") + "\n")
    for role in sorted(vacuous):
        print(f"  {role:<22} SKIPPED, not a fixed point either way: {VACUOUS[role]}")
    if vacuous:
        print()
    for role, pname, drive, ms, md in bad:
        print(f"  {role:<22} persona={str(pname):<22} drive={str(drive)}")
        if ms:
            print(f"      NOT SERVED by its install: {ms}")
        if md:
            print(f"      NOT EMITTED by its persona: {md}")
    if undeclared_vacuous:
        # A FINDING, not a skip and not a pass. A row carrying neither `requires:` nor `dials:`
        # cannot be a fixed point -- there is nothing for the round trip to reproduce -- so
        # letting it through silently is the vacuous green `VACUOUS` exists to name. Declare it
        # there with a reason, or give the row evidence.
        for role in sorted(undeclared_vacuous):
            print(f"  {role:<22} NO EVIDENCE: neither `requires:` nor `dials:`, so the "
                  f"fixed-point question cannot be asked. Declare it in VACUOUS with a reason, "
                  f"or give the row evidence.")
    return 1 if (bad or undeclared_vacuous) else 0


if __name__ == "__main__":
    raise SystemExit(main())
