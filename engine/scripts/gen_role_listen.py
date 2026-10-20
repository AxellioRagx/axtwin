#!/usr/bin/env python3
"""Generate the Console's per-role default listen set from the persona library.

    .venv/bin/python scripts/gen_role_listen.py            # write the table
    .venv/bin/python scripts/gen_role_listen.py --check    # exit 1 on drift

The Console's add-segment form can generate hosts on a fresh segment
(`docs/WORK_ORDER_console_canvas.md` §3 S5.1.c), and it prefills each generated
host's `listen` set from the ROLE it is given. The persona library
(`axrange/personas/catalog/*.yaml`) is the one place this repo says which ports
a role listens on -- but WHO listens depends on the persona's `drive`
(`docs/design/07-traffic-personas.md`, "Five drivers"):

  - `scripted`, `command`, `browser`, `replay`: the persona DIALS. Its emit's
    `peer_role` is the LISTENER, on the emit's ports.
  - `real-service`: the persona "rides an already-installed real daemon on the
    host". Its host is the LISTENER, and the emit's `peer_role` is the CLIENT
    (`domain-controller` lists kerberos 88 to `corp-workstation` because the
    DC's daemon SERVES it to workstations).

So for a role R the default listen set is the union of

  (a) {port, transport} of every emit whose `peer_role` is R, from a persona
      whose `drive` is NOT `real-service`; and
  (b) {port, transport} of EVERY emit of the `real-service` persona whose `id`
      is R, whatever that emit's `peer_role` (the client) is -- ASSUMING a
      real-service persona's id is the role it serves, which holds for every
      one in the catalog today.

with the transport taken from the persona schema's OWN rule -- `Emit.
effective_transport()` (an explicit `transport:`, else `default_transport(proto)`)
-- never restated here. (Drive-aware since 2026-09-15: the first cut read (a)
for every drive, which filed a real-service persona's SERVED ports under its
clients -- `corp-workstation` "listened" on 53/88/389/135/123.)

**Rule (b) rests on an assumption the catalog does not always keep, and the
table SAYS SO rather than guessing.** A `real-service` persona's emit list is
not split into "what my daemon serves" and "what my host dials" -- so a port the
role DIALS is filed under the role itself. `_DIALED_BY_A_SERVING_PERSONA` below
names every catalog role that holds such a port today, the generated header
repeats the names, and the Console repeats the caveat where it offers the
prefill (`o2/console/src/views/ElementActions.tsx`). No heuristic separates the
two here: the honest fix is in the CATALOG (an emit direction, or a served-port
list per persona), and until it lands a prefill is a starting point the operator
prunes, not a claim about the host.

**Load-generator personas are EXCLUDED** (`_EXCLUDED_ID_PREFIXES`). The
`loadgen-*` catalog entries exist to characterize capture throughput
(CHANGELOG 2026-08-14, the max-capture-rate ramp): their netflow emits say
nothing about what a real host listens on, and left in they gave every
`corp-workstation` and `domain-controller` four 999x/udp ports and invented two
roles (`gw`, `loadgen`) whose whole default was scaffolding.

**A checked-in generated table, not an API route** (an implementation call taken
for S5.1.c): no API publishes personas, the catalog is curated static content
loaded at import time, and a table the SPA bundles needs no request to prefill a
form. `tests/test_console_role_listen.py` regenerates it and fails on drift, the
same vendored-with-drift-test pattern as `o2/console/test/fixtures/
real-segment-rosters.json`.

Only `axrange/personas/catalog/` is read -- never `AXRANGE_PERSONAS_DIR`'s extra
directories, which hold one cast's capture-derived personas and would make the
checked-in table depend on the environment it was generated in.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from axrange.personas.registry import load_catalog_dir  # noqa: E402
from axrange.personas.schema import RESERVED_PEER_ROLES, Emit  # noqa: E402

CATALOG_DIR = REPO / "src" / "axrange" / "personas" / "catalog"
OUTPUT = REPO / "o2" / "console" / "src" / "model" / "roleListen.generated.ts"

#: The protocols a listen entry can carry (`axr-assertions/1` `$defs.portList`
#: allows icmp/tcp/udp; a TRANSPORT is only ever tcp or udp).
_LISTEN_TRANSPORTS = ("tcp", "udp")

#: A proto that carries no port at all. An ICMP emit's `ports` (if any) are not
#: a port it is dialled on -- `drivers/command.py`'s icmp template ignores them
#: (`uses_port=False`) -- and the schema's derived transport for it would read
#: "tcp", which is exactly the wrong claim to prefill as a listen entry.
_PORTLESS_PROTOS = frozenset({"icmp"})

#: The one drive whose persona's own host is the LISTENER (see the module note).
#: Named once, beside the rule that reads it; `axrange.personas.schema.
#: DRIVE_KINDS` is the closed set it belongs to.
_SERVING_DRIVE = "real-service"

#: Persona id prefixes whose emits never enter the table (module note, S7).
_EXCLUDED_ID_PREFIXES = ("loadgen-",)

#: Every role whose `real-service` persona holds at least one emit the host
#: DIALS rather than serves, so rule (b) files a dialled port under the role
#: itself. Hand-kept (the catalog carries no emit direction to derive it from --
#: see the module note) and GUARDED by `check_dialed_caveat` below, which fails
#: loud if an id here is not a real-service persona of the catalog, so a rename
#: or a removal cannot leave the caveat naming a role that no longer exists.
#: Each entry names the emit that makes it true, so a reader can check it
#: against the YAML.
_DIALED_BY_A_SERVING_PERSONA: dict[str, str] = {
    "db-replica": "its whole emit list is replication it PULLS from db-tier (3306, 5432)",
    "egress-proxy": "443 and 53/udp northbound to `internet` are the proxy's own upstream calls",
    "monitoring-collector": "9100 and 161 are SCRAPED on corp-workstation; only 514 and 2055 arrive",
    "protocol-gateway": "502 to plc and 4840 to historian are the gateway's southbound polls",
    "web-tier": "3306 to db-tier is its own backend call, not a port the web tier answers",
}


@dataclass
class Table:
    """The generated table and the account of what it skipped."""

    by_role: dict[str, set[str]] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, why: str) -> None:
        self.skipped[why] = self.skipped.get(why, 0) + 1


def _skip_reason(emit: Emit, *, serving: bool) -> str | None:
    """Why `emit` contributes no listen entry, or `None`.

    The `peer_role` checks apply only to a DIALING persona's emit, where the
    peer_role is the key -- a reserved token (`internet`, `egress`, ...) names no
    role that could listen. On a SERVING persona the peer_role is only the
    client and is not read: a `dmz-web` daemon serving `internet` clients on 443
    still listens on 443.
    """
    if not serving:
        if not emit.peer_role:
            return "no peer_role"
        if emit.peer_role in RESERVED_PEER_ROLES:
            return f"reserved peer token ({emit.peer_role})"
    if emit.proto in _PORTLESS_PROTOS:
        return f"portless protocol ({emit.proto})"
    if emit.effective_transport() not in _LISTEN_TRANSPORTS:
        return f"transport {emit.effective_transport()!r} is not tcp/udp"
    if not emit.ports:
        return "no ports"
    return None


def check_dialed_caveat(catalog_dir: Path = CATALOG_DIR) -> None:
    """Fail loud if `_DIALED_BY_A_SERVING_PERSONA` no longer describes the
    catalog -- a rename or a removal must not leave the Console naming a role
    nothing serves.

    Applied to the REAL catalog only (`main`, and the drift test): `build_table`
    stays a pure function of whatever catalog it is handed, so a probe catalog
    built to exercise one skip rule is not required to carry all five roles.
    """
    catalog = load_catalog_dir(catalog_dir)
    for role, why in sorted(_DIALED_BY_A_SERVING_PERSONA.items()):
        persona = catalog.get(role)
        if persona is None or persona.drive != _SERVING_DRIVE:
            raise SystemExit(
                f"_DIALED_BY_A_SERVING_PERSONA names {role!r} ({why}), which is not a "
                f"{_SERVING_DRIVE} persona of {catalog_dir} any more -- re-check the "
                "catalog and update the caveat, never the guard"
            )


def build_table(catalog_dir: Path = CATALOG_DIR) -> Table:
    table = Table()
    catalog = load_catalog_dir(catalog_dir)
    for pid, persona in sorted(catalog.items()):
        if pid.startswith(_EXCLUDED_ID_PREFIXES):
            table.skip(f"excluded persona ({pid}: load-generator scaffolding)")
            continue
        serving = persona.drive == _SERVING_DRIVE
        for emit in persona.emits:
            why = _skip_reason(emit, serving=serving)
            if why is not None:
                table.skip(why)
                continue
            # (b) a serving persona's host listens -- its id is the role;
            # (a) a dialing persona's peer is the listener.
            listener = persona.id if serving else emit.peer_role
            ports = table.by_role.setdefault(listener, set())
            for port in emit.ports:
                ports.add(f"{port}/{emit.effective_transport()}")
    return table


def render(table: Table) -> str:
    roles = sorted(table.by_role)
    skipped = "\n".join(
        f" *   - {count} x {why}" for why, count in sorted(table.skipped.items())
    ) or " *   - nothing"
    dialed = [
        f" *   - `{role}`: {why}" for role, why in sorted(_DIALED_BY_A_SERVING_PERSONA.items())
    ]
    lines = [
        "/**",
        " * GENERATED by `scripts/gen_role_listen.py` from `axrange/personas/catalog/*.yaml`.",
        " * Do not edit by hand: run `.venv/bin/python scripts/gen_role_listen.py`.",
        " * `tests/test_console_role_listen.py` regenerates it and fails on drift.",
        " *",
        " * Each role's DEFAULT LISTEN SET for a host the Console generates on a fresh",
        " * segment (`docs/WORK_ORDER_console_canvas.md` §3 S5.1.c). Who LISTENS depends",
        " * on the persona's `drive` (`docs/design/07-traffic-personas.md`): a",
        " * `scripted`/`command`/`replay` persona DIALS its emit's `peer_role`, while a",
        " * `real-service` persona's own host SERVES the emit and its `peer_role` is",
        " * the client. So for a role R, the union of",
        " *   (a) every emit whose `peer_role` is R, from a persona that is NOT",
        " *       `real-service`; and",
        " *   (b) every emit of the `real-service` persona whose `id` is R, whatever",
        " *       its `peer_role` -- ASSUMING a real-service persona's id is the role",
        " *       it serves (true of every one in the catalog today);",
        " * each as `{port, proto}` per port the emit names, `proto` being the emit's",
        " * TRANSPORT as the persona schema resolves it (`Emit.effective_transport()`:",
        " * an explicit `transport:`, else `default_transport(proto)`).",
        " *",
        " * **(b) CANNOT TELL A SERVED PORT FROM A DIALLED ONE.** A real-service",
        " * persona's emits are not split into what its daemon answers and what its",
        " * host calls out to, so a port the role DIALS lands in its own entry. These",
        " * roles hold such a port today (`ROLES_WITH_DIALED_EMITS` below, so the UI",
        " * can name them where it offers the prefill):",
        *dialed,
        " * A prefill is therefore a starting point to prune, never a claim that the",
        " * host serves the port -- the fix belongs in the catalog (an emit direction),",
        " * not in a heuristic here.",
        " *",
        " * Load-generator personas (`loadgen-*`) are EXCLUDED: they characterize",
        " * capture throughput, so their netflow emits describe no real host's listen",
        " * set (they had given every workstation and DC four 999x/udp ports, and",
        " * invented the roles `gw` and `loadgen` out of scaffolding alone).",
        " *",
        " * Emits that cannot contribute a `{port, proto: tcp|udp}` listen entry are",
        " * skipped: an ICMP emit (ICMP carries no port, whatever transport the schema",
        " * would derive for it), an emit whose transport is not tcp/udp or that names",
        " * no ports, and -- on the DIALING side only, where the peer_role is the key --",
        " * an emit with no `peer_role` or a reserved peer token (`internet`, `egress`,",
        " * `broadcast`, `multicast`: not a role that listens). On the serving side the",
        " * peer_role is only the client, so a daemon serving `internet` still listens.",
        " * In this catalog:",
        skipped,
        " *",
        " * A key from (b) is a persona id, which a twin may never publish as a role",
        " * (`db-replica` is named by no persona's `peer_role`): harmless, since the",
        " * table is only read by the role an operator types. A role with no entry has",
        " * NO default -- the form prefills an empty set and says that no persona in",
        " * the library serves the role. Entries are in the compiler's canonical port",
        " * order (the `\"<port>/<proto>\"` string, sorted); roles are sorted by name.",
        " */",
        "export interface RoleListenEntry {",
        "  readonly port: number;",
        '  readonly proto: "tcp" | "udp";',
        "}",
        "",
        "export const ROLE_LISTEN_DEFAULTS: Readonly<Record<string, readonly RoleListenEntry[]>> = {",
    ]
    for role in roles:
        entries = []
        # The COMPILER's canonical port order: the "<port>/<proto>" string
        # itself, sorted (aximprint `operator_assertions.py`: `sorted({f"{port}/
        # {proto}"})`) -- also the order the topology export's own `listen_ports`
        # arrive in. The Console's `canonicalPorts` applies the same rule, so a
        # prefilled set is written to the file exactly as the form shows it.
        for text in sorted(table.by_role[role]):
            port, proto = text.split("/")
            entries.append(f'{{ port: {int(port)}, proto: "{proto}" }}')
        lines.append(f'  {json.dumps(role)}: [{", ".join(entries)}],')
    lines.append("};")
    lines.extend(
        [
            "",
            "/**",
            " * The roles whose entry above holds at least one port the role DIALS rather",
            " * than serves -- rule (b)'s blind spot, named so the form that offers the",
            " * prefill can name them too (it must not read as a listen claim). See the",
            " * header: the honest fix is an emit direction in the persona catalog.",
            " */",
            "export const ROLES_WITH_DIALED_EMITS: readonly string[] = [",
        ]
    )
    for role in sorted(_DIALED_BY_A_SERVING_PERSONA):
        lines.append(f"  {json.dumps(role)},")
    lines.append("];")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if the checked-in table has drifted")
    args = parser.parse_args(argv)
    check_dialed_caveat()
    text = render(build_table())
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.is_file() else ""
        if current != text:
            print(f"{OUTPUT.relative_to(REPO)} has drifted from the persona catalog -- regenerate it", file=sys.stderr)
            return 1
        return 0
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(REPO)} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
