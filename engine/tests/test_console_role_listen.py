"""The Console's per-role default listen table (`o2/console/src/model/
roleListen.generated.ts`) against the persona library it is generated from.

The vendored-with-drift-test pattern (`tests/test_api_console.py`'s
`real-segment-rosters.json` check): the table is a CHECKED-IN generated file the
SPA bundles, so a persona edit that is not followed by a regeneration must fail
here rather than leave the Console prefilling a listen set the catalog no longer
implies. The generator is a `scripts/` file, loaded with
`spec_from_file_location` (LES-029).

Regenerate on drift::

    .venv/bin/python scripts/gen_role_listen.py
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from axrange.personas.registry import load_catalog_dir
from axrange.personas.schema import DRIVE_KINDS, RESERVED_PEER_ROLES

REPO = Path(__file__).resolve().parent.parent
GENERATOR = REPO / "scripts" / "gen_role_listen.py"
TABLE = REPO / "o2" / "console" / "src" / "model" / "roleListen.generated.ts"
CATALOG = REPO / "axrange" / "personas" / "catalog"


def _generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_role_listen", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE exec: the generator's `@dataclass` resolves its string
    # annotations through `sys.modules[cls.__module__]`, which is `None` for a
    # module `spec_from_file_location` has not put there yet.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


def test_the_console_role_listen_table_matches_the_persona_catalog():
    gen = _generator()
    # The same order `main()` runs in: the caveat's role list is checked against
    # the real catalog before anything is rendered from it.
    gen.check_dialed_caveat()
    expected = gen.render(gen.build_table())
    assert TABLE.read_text(encoding="utf-8") == expected, (
        f"{TABLE.relative_to(REPO)} has drifted from axrange/personas/catalog -- regenerate it with "
        "`.venv/bin/python scripts/gen_role_listen.py`"
    )


def _parse_table(text: str) -> dict[str, set[tuple[int, str]]]:
    import re

    table: dict[str, set[tuple[int, str]]] = {}
    for role, body in re.findall(r'^  "([^"]+)": \[(.*)\],$', text, flags=re.M):
        table[role] = {
            (int(port), proto)
            for port, proto in re.findall(r'\{ port: (\d+), proto: "(tcp|udp)" \}', body)
        }
    assert table, "no role rows parsed from the generated table"
    return table


def _generated(gen: ModuleType) -> dict[str, set[tuple[int, str]]]:
    return {
        role: {(int(t.split("/")[0]), t.split("/")[1]) for t in entries}
        for role, entries in gen.build_table().by_role.items()
    }


def test_every_entry_is_an_emit_the_catalog_names_on_the_schemas_own_transport():
    """Read back the CHECKED-IN table and account for it from the catalog emit
    by emit -- not by re-running the generator (LES-187). WHO listens depends on
    the persona's drive: a `real-service` persona's own host serves its emits (so
    they are filed under its id, whatever their peer_role), while any other drive
    dials its emit's peer_role (so they are filed under that peer). Every port is
    carried over the transport the persona schema resolves
    (`Emit.effective_transport()`).

    RESTATED for review S7: the `loadgen-*` personas are excluded -- they exist
    to characterize capture throughput, not to model an environment, and their
    netflow emits say nothing about what a real host listens on."""
    table = _parse_table(TABLE.read_text(encoding="utf-8"))
    # The rule reads the drive as a closed set: if a SIXTH drive appears, this
    # test is the place to decide which side it listens on. `browser`
    # (2026-09-21) was decided here: it DIALS (it is a client engine, like
    # `scripted`/`command`), so its emit's `peer_role` is the listener.
    assert set(DRIVE_KINDS) == {
        "scripted", "replay", "real-service", "command", "browser",
    }

    expected: dict[str, set[tuple[int, str]]] = {}
    excluded = 0
    for persona in load_catalog_dir(CATALOG).values():
        if persona.id.startswith("loadgen-"):
            excluded += 1
            continue
        for emit in persona.emits:
            if emit.proto == "icmp" or not emit.ports:
                continue
            if persona.drive == "real-service":
                listener = persona.id
            else:
                if not emit.peer_role or emit.peer_role in RESERVED_PEER_ROLES:
                    continue
                listener = emit.peer_role
            for port in emit.ports:
                expected.setdefault(listener, set()).add((port, emit.effective_transport()))
    assert table == expected
    # The exclusion is real content, not an empty rule: eight personas.
    assert excluded == 8

    # The transport really is DERIVED, not the literal proto: a dns emit that
    # pins no transport runs over udp, a kerberos one over tcp.
    assert (53, "udp") in table["dns-resolver"]
    assert (88, "tcp") in table["domain-controller"]
    # The reserved tokens never become a role row.
    assert not set(table) & set(RESERVED_PEER_ROLES)
    # Nor do the two roles the load generators were the ONLY source of.
    assert "gw" not in table and "loadgen" not in table


#: What the `domain-controller` persona (`drive: real-service`) SERVES to its
#: workstation clients, read off `axrange/personas/catalog/domain-controller.yaml`
#: by hand on 2026-09-15 and pinned as a literal, so neither the generator nor
#: the catalog loader can supply its own expectation.
_DC_SERVES = {
    (53, "udp"), (88, "tcp"), (464, "tcp"), (389, "tcp"), (636, "tcp"),
    (3268, "tcp"), (3269, "tcp"), (445, "tcp"), (135, "tcp"), (123, "udp"),
}

#: What NON-real-service, NON-load-generator personas actually dial ON a
#: `corp-workstation`, pinned the same way: `backup-server` (smb 445),
#: `scanner-benign` (22/80/443/445/3389/5985) and `xseg-mgmt-poll` (9100).
#: If a catalog edit moves this set, restate it here with the persona that moved
#: it -- this literal is the test.
#:
#: RESTATED for review S7: 9995-9998/udp are gone with the `loadgen-*` personas
#: that were their only source.
_WORKSTATION_LISTENS = {
    (22, "tcp"), (80, "tcp"), (443, "tcp"), (445, "tcp"), (3389, "tcp"),
    (5985, "tcp"), (9100, "tcp"),
}

#: The roles whose entry holds a port the role DIALS rather than serves --
#: rule (b)'s blind spot (review M1). Read off the catalog by hand on 2026-09-15
#: and pinned as a literal, so the generator cannot supply its own expectation.
_DIALED_UNDER_THE_SERVER = {
    "web-tier": (3306, "tcp"),            # its own backend call to db-tier
    "db-replica": (5432, "tcp"),          # replication it PULLS from db-tier
    "protocol-gateway": (502, "tcp"),     # its southbound poll of plc
    "egress-proxy": (53, "udp"),          # its upstream resolver call
    "monitoring-collector": (9100, "tcp"),  # scraped ON corp-workstation
}


def test_a_real_service_persona_listens_on_what_it_serves_and_its_clients_do_not():
    """The drive-aware rule on the two roles it was corrected for, in the
    generator's output AND in the checked-in file the SPA bundles. The first cut
    read every persona as a dialer, so the DC's SERVED ports landed on
    `corp-workstation` (its clients) and `domain-controller` lost 53/123/3268/3269."""
    gen = _generator()
    for where, table in (("generator", _generated(gen)), ("checked-in table", _parse_table(TABLE.read_text(encoding="utf-8")))):
        dc = table["domain-controller"]
        ws = table["corp-workstation"]
        assert _DC_SERVES <= dc, f"{where}: domain-controller is missing {sorted(_DC_SERVES - dc)}"
        # The DC-only services never land on its clients...
        for served in [(53, "udp"), (88, "tcp"), (389, "tcp"), (135, "tcp"), (123, "udp")]:
            assert served not in ws, f"{where}: corp-workstation lists the DC's {served}"
        # ...and the workstation keeps exactly what dialing personas reach it on
        # (445 among them: backup-server pulls over SMB).
        assert ws == _WORKSTATION_LISTENS, f"{where}: corp-workstation is {sorted(ws)}"


def test_the_load_generator_personas_are_left_out_of_the_table(tmp_path):
    """S7: `loadgen-*` is characterization scaffolding, and its netflow emits
    had been the sole reason `corp-workstation` and `domain-controller` carried
    9995-9998/udp and the roles `gw`/`loadgen` existed at all. Proven on a
    purpose-built catalog, so the assertion is about the RULE, not the content."""
    (tmp_path / "loadgen-x.yaml").write_text(
        "persona:\n"
        "  id: loadgen-x\n"
        "  os_family: linux\n"
        "  drive: scripted\n"
        "  emits:\n"
        "    - { proto: netflow, ports: [9995], peer_role: corp-workstation }\n",
        encoding="utf-8",
    )
    (tmp_path / "real.yaml").write_text(
        "persona:\n"
        "  id: backup-server\n"
        "  os_family: linux\n"
        "  drive: scripted\n"
        "  emits:\n"
        "    - { proto: smb, ports: [445], peer_role: corp-workstation }\n",
        encoding="utf-8",
    )
    gen = _generator()
    table = gen.build_table(tmp_path)
    assert table.by_role == {"corp-workstation": {"445/tcp"}}
    assert table.skipped == {"excluded persona (loadgen-x: load-generator scaffolding)": 1}
    # The generated header SAYS so -- the exclusion is not a silent filter.
    rendered = gen.render(table)
    assert "Load-generator personas (`loadgen-*`) are EXCLUDED" in rendered
    # ...and the real catalog's own header carries the same sentence.
    assert "Load-generator personas (`loadgen-*`) are EXCLUDED" in TABLE.read_text(encoding="utf-8")


def test_the_caveat_names_every_role_whose_default_holds_a_port_it_dials():
    """M1: rule (b) files a `real-service` persona's emits under its own id, and
    the catalog does not say which of them the host SERVES. The roles where that
    bites are named -- in the generator, in the generated header, and in the
    `ROLES_WITH_DIALED_EMITS` the Console reads to say so where it offers the
    prefill. Guarded, too: a role named here that is no longer a real-service
    persona fails the generator loudly rather than leaving a stale caveat."""
    gen = _generator()
    assert set(gen._DIALED_BY_A_SERVING_PERSONA) == set(_DIALED_UNDER_THE_SERVER)
    table = _parse_table(TABLE.read_text(encoding="utf-8"))
    catalog = load_catalog_dir(CATALOG)
    for role, port in _DIALED_UNDER_THE_SERVER.items():
        # Each one really is a real-service persona whose own id is the key...
        assert catalog[role].drive == "real-service", role
        # ...and the dialled port really is in the default the Console offers.
        assert port in table[role], f"{role} no longer carries the dialled {port}"

    text = TABLE.read_text(encoding="utf-8")
    assert "(b) CANNOT TELL A SERVED PORT FROM A DIALLED ONE" in text
    for role in _DIALED_UNDER_THE_SERVER:
        assert f" *   - `{role}`:" in text, f"{role} is not named in the generated header"
    # The TypeScript the Console imports, in the order it is emitted.
    listed = text.split("export const ROLES_WITH_DIALED_EMITS")[1]
    assert [line.strip().strip('",') for line in listed.splitlines() if line.startswith("  \"")] == sorted(
        _DIALED_UNDER_THE_SERVER
    )


def test_the_caveats_role_list_is_guarded_against_a_catalog_rename(tmp_path):
    """The list is hand-kept (nothing in the catalog marks an emit's direction),
    so it must fail LOUD when it stops describing the catalog -- otherwise the
    Console goes on naming a role no persona serves."""
    import pytest

    (tmp_path / "real.yaml").write_text(
        "persona:\n"
        "  id: backup-server\n"
        "  os_family: linux\n"
        "  drive: scripted\n"
        "  emits:\n"
        "    - { proto: smb, ports: [445], peer_role: corp-workstation }\n",
        encoding="utf-8",
    )
    gen = _generator()
    with pytest.raises(SystemExit) as raised:
        gen.check_dialed_caveat(tmp_path)
    assert "is not a real-service persona" in str(raised.value)
    # It names the FIRST role that stopped describing the catalog, and why it
    # was on the list -- enough to re-check the YAML without reading the script.
    assert "db-replica" in str(raised.value)
    assert "replication it PULLS from db-tier" in str(raised.value)
    # ...and on the real catalog it passes, so the guard is not vacuous.
    gen.check_dialed_caveat()


def test_the_skip_rules_on_a_catalog_built_to_hit_each(tmp_path):
    """One emit per rule, so a rule that stops firing shows up as a row that
    should not exist (or a missing one) rather than as nothing at all. A DIALING
    persona and a SERVING one side by side, because the peer_role rules apply to
    one side only."""
    (tmp_path / "probe.yaml").write_text(
        "persona:\n"
        "  id: probe\n"
        "  os_family: linux\n"
        "  drive: scripted\n"
        "  emits:\n"
        "    - { proto: https, ports: [443], peer_role: internet }\n"
        "    - { proto: ssdp, ports: [1900], peer_role: multicast }\n"
        "    - { proto: icmp, ports: [7], peer_role: web-tier }\n"
        "    - { proto: ntp, ports: [123], peer_role: web-tier }\n"
        "    - { proto: dns, ports: [53], peer_role: web-tier, transport: tcp }\n"
        "    - { proto: http, ports: [], peer_role: web-tier }\n"
        "    - { proto: http, ports: [80, 80, 8080], peer_role: web-tier }\n",
        encoding="utf-8",
    )
    (tmp_path / "server.yaml").write_text(
        "persona:\n"
        "  id: web-tier\n"
        "  os_family: linux\n"
        "  drive: real-service\n"
        "  emits:\n"
        # Served to internet clients: a reserved CLIENT, and still a listen port.
        "    - { proto: https, ports: [443], peer_role: internet }\n"
        # Served to a named client: filed under the SERVER, never the client.
        "    - { proto: http, ports: [8443], peer_role: corp-workstation }\n"
        "    - { proto: icmp, ports: [0], peer_role: corp-workstation }\n",
        encoding="utf-8",
    )
    gen = _generator()
    table = gen.build_table(tmp_path)
    assert table.by_role == {"web-tier": {"123/udp", "53/tcp", "80/tcp", "8080/tcp", "443/tcp", "8443/tcp"}}
    assert "corp-workstation" not in table.by_role
    assert table.skipped == {
        "reserved peer token (internet)": 1,
        "reserved peer token (multicast)": 1,
        "portless protocol (icmp)": 2,
        "no ports": 1,
    }
    # `build_table` is a pure function of the catalog it is HANDED: the caveat's
    # guard belongs to `check_dialed_caveat`, which runs against the real
    # catalog only, so a probe like this one is never asked to carry five roles.
    assert "web-tier" in gen._DIALED_BY_A_SERVING_PERSONA
    rendered = gen.render(table)
    # The compiler's canonical port order -- the "<port>/<proto>" STRING sorted,
    # so "123/udp" < "443/tcp" < "53/tcp" < "80/tcp" < "8080/tcp" < "8443/tcp".
    assert (
        '  "web-tier": [{ port: 123, proto: "udp" }, { port: 443, proto: "tcp" }, '
        '{ port: 53, proto: "tcp" }, { port: 80, proto: "tcp" }, { port: 8080, proto: "tcp" }, '
        '{ port: 8443, proto: "tcp" }],'
    ) in rendered.splitlines()
