#!/usr/bin/env python3
"""Install an `aximprint cast` output into axRange's twin-config catalog under a STABLE id.

**A THIN WRAPPER since 2026-09-05.** The whole implementation moved into
`src/aximprint/synth/install.py`, which is also what `aximprint install-cast` calls — one body,
two entry points, deleted here rather than duplicated. This script survives because the fidelity
loop's own notes, runbooks and journal entries invoke it BY PATH, and because it runs from a
checkout with nothing installed beyond the package's own dependencies.

Read that module's docstring for why the install exists at all (a cast names its `twin_config.id`
after its PIT, which is right for provenance and fatal for `te deploy --update`) and for the
three-valued exit-code contract (0 installed / 1 finding / 2 usage-or-shape).

Usage: install_cast_as_twin.py <cast-dir> <twin-configs-dir> <stable-id> [--sensors A,B]
"""
import argparse
from pathlib import Path

from aximprint.synth.install import install_cast, split_sensors


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="install_cast_as_twin.py",
        description="Install an `aximprint cast` into axRange's twin-config catalog.")
    ap.add_argument("cast", type=Path)
    ap.add_argument("root", type=Path, help="the twin-configs directory to install into")
    ap.add_argument("stable", help="the stable twin id to rewrite the cast's PIT id to")
    # The one Lab fact the cast genuinely cannot observe, supplied at INSTALL time because
    # that is where the two repos meet. The compiler is right to leave `sensors:` unfilled --
    # naming a sensor it never saw is the exact fabrication `evidence_class` exists to prevent
    # -- but the installed plan is unappliable until something fills it, and "hand-edit it
    # every iteration" is precisely how `default_agents: false` got lost: an edit that lives
    # only in the installed file is an edit the next re-cast silently discards.
    #
    # Comma-separated here, `nargs="+"` on the subcommand; `split_sensors` accepts both, so the
    # two spellings are two front doors onto one rule rather than two rules.
    ap.add_argument("--sensors", default="",
                    help="comma-separated o2/external sensor name(s) to fill into every tap "
                         "binding, e.g. --sensors XPress-SW-A. Omitted leaves `sensors:` "
                         "absent, which is what the cast emits and what axRange refuses to "
                         "apply.")
    args = ap.parse_args()
    return install_cast(args.cast, args.root, args.stable, split_sensors(args.sensors))


if __name__ == "__main__":
    raise SystemExit(main())
