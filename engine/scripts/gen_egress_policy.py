#!/usr/bin/env python3
"""Generate a twin router's EGRESS PHASE POLICY payload -- the `run-script` half of E1.

    .venv/bin/python scripts/gen_egress_policy.py \\
        --sink 10.20.0.9 \\
        --out twins/<twin>/scripts

**What this is for.** `docs/WORK_ORDER_protocol_engines.md` §E1 gives the twin two
egress phases: `provision` (real WAN masquerade, because Chromium, `smbclient`,
`krb5-user` and the server packages are all apt pulls) and `measure` (NO real
egress -- every external destination is policy-routed to the twin's sink, which
impersonates the observed external world). The switch lives on the router.

There are two ways to reach it and they render the SAME text
(`axrange/adapter_proxmox/egress_render.py` is the single source):

  1. **The twin's own `egress:` declaration** -- `twin_config.egress: {phase,
     sink_ip?, mode?}` (GAP-101) expands to the IR's top-level `egress:` block,
     which `pass1_emit` attaches to EVERY `kind: router` node as `OP-HOST-090
     configure-router`'s `egress` input; the adapter writes and runs the
     installer over QGA during the deploy, and `sink_ip` is resolved from the
     twin's own `role: sink` node when omitted. **This is the normal path now**
     -- authoring the posture where the twin is authored is the whole point of
     GAP-101, and nothing has to be pushed by hand afterwards.
  2. **this script** -- writes `install-egress-policy.sh` into a twin's `scripts/`
     directory as an ordinary `run-script` payload. Still useful, and not
     superseded: it is how you install or re-install the phase engine on a
     STANDING twin that was deployed before the declaration existed (whose
     `configure-router` op row is already `completed` and will not re-run,
     LES-096), and how a hand-built or non-axRange router gets the same rule
     set. Shape:

         - id: gw
           kind: router
           install:
             - script: scripts/install-egress-policy.sh

     Note `pass1_emit._classify_install` allows exactly ONE `script:` action per
     app, which is why the installer is self-contained (engine + data + boot unit
     + apply in one file) rather than a shared engine plus a data payload.

**Flipping a standing twin.** The installer leaves
`/usr/local/sbin/axr-egress-phase.sh` behind:

    axr-egress-phase.sh measure        # close: sink-only egress
    axr-egress-phase.sh provision      # open: masquerade to WAN
    axr-egress-phase.sh --healthcheck  # exit 0 only if the LIVE state matches
    axr-egress-phase.sh --show         # phase, when it was entered, sink, mode

**Default vs per-prefix routing.** `--mode default` (the default) sends everything
not internal to the sink with ONE route -- observed addresses and, just as
importantly, the addresses no cast ever observed, which a real browser will chase.
`--mode cidrs --cidrs external-cidrs.txt` instead installs one route per observed
prefix (~1,270 aggregated /24s from `scripts/gen_sink_corpus.py`): precise, and
deliberately a black hole for anything unobserved.

Same inputs in, byte-identical file out.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from axrange.adapter_proxmox.egress_render import (  # noqa: E402
    DEFAULT_EGRESS_PHASE,
    EGRESS_MODES,
    EGRESS_PHASES,
    EgressRenderError,
    render_install,
)

INSTALLER_NAME = "install-egress-policy.sh"


def build_installer(
    *,
    sink: str,
    phase: str,
    mode: str,
    cidrs: list[str] | None,
) -> str:
    return render_install(
        {"phase": phase, "sink_ip": sink, "mode": mode, "cidrs": cidrs or []}
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--sink", required=True,
        help="the twin sink's IPv4 address on a LAN segment (the next hop for "
             "every external destination in `measure`)",
    )
    parser.add_argument(
        "--out", required=True, type=Path,
        help="directory to write install-egress-policy.sh into "
             "(a twin's scripts/ directory)",
    )
    parser.add_argument(
        "--phase", default="provision", choices=list(EGRESS_PHASES),
        help="the phase the installer applies. Defaults to `provision` because "
             "an INSTALL runs during a deploy, and a deploy needs apt; the "
             "engine's own fallback when nothing says otherwise is "
             f"`{DEFAULT_EGRESS_PHASE}` (default closed). Flip to measure with "
             "`axr-egress-phase.sh measure` once provisioning is done.",
    )
    parser.add_argument(
        "--mode", default="default", choices=list(EGRESS_MODES),
        help="`default`: one route, every external destination reaches the sink "
             "(observed or not). `cidrs`: only the listed prefixes reach it.",
    )
    parser.add_argument(
        "--cidrs", type=Path, default=None,
        help="external-cidrs.txt from scripts/gen_sink_corpus.py (required for "
             "--mode cidrs)",
    )
    parser.add_argument("--dry-run", action="store_true", help="print, write nothing")
    args = parser.parse_args(argv)

    cidrs: list[str] = []
    if args.cidrs is not None:
        cidrs = [
            line.strip()
            for line in args.cidrs.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    if args.mode == "cidrs" and not cidrs:
        parser.error("--mode cidrs needs a non-empty --cidrs file")

    try:
        script = build_installer(
            sink=args.sink, phase=args.phase, mode=args.mode, cidrs=cidrs
        )
    except EgressRenderError as exc:
        parser.error(str(exc))
        return 2  # pragma: no cover — parser.error exits

    if args.dry_run:
        sys.stdout.write(script)
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / INSTALLER_NAME
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)
    print(
        f"wrote {path} (phase={args.phase} sink={args.sink} mode={args.mode}"
        + (f" prefixes={len(cidrs)}" if args.mode == "cidrs" else "")
        + ")"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
