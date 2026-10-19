"""Real protocol servers for a cast twin — the producer half of row 5.

`axrange/docs/WORK_ORDER_protocol_faithful_services.md` (S2/S4, and §6.1's seam). The ruling
this serves is `axrange/docs/design/13-golden-twin-fidelity.md` §8.2, verbatim: *"implement
realistic protocol clients and servers based on the observed data. avoid using synthetic data
generation where possible."*

**What was wrong, measured.** `synth/responder.py` gives every dialled role an ECHO — one
`/usr/local/lib/axr/responder.py` that binds a port list and reflects bytes. It fixed the
no-listener defect it was built for (a bare VM draws ICMP port-unreachable at every client emit,
which Zeek scores as a fabricated reverse edge) and it is precisely the *synthetic filler* §8.2's
ruling removes: `dig` gets its own query back, `curl -sk https://` fails the handshake, and a
protocol-aware probe can never pass. Its port list is also the wrong input — `listener_ports
(personas)` derives each role's bound set from *every `(port, transport)` some persona **dials** at
that role*, so `internet-sink-1` binds 88/tcp while the `web-tier` nodes bind nothing.

**The two inputs this module takes instead**, and the distinction is the whole design:

  * an OBSERVED host's declared listen set is its own **observed listen census**
    (`ir_entity.listen_ports`), never the dial set — work order §2's ruling;
  * the internet sink has no observed census — nothing observed a synthetic node — so its set is
    the persona dial set (the ports it exists to answer) plus `sink_real_services`, derived from
    a census of the EXTERNAL listener population it stands in for. That is a boundary, stated,
    not an exception smuggled in: the input is still observation, about a different population.

**Three properties every recipe here holds.**

  * **Fail loud.** Every daemon is asserted with `systemctl is-active` after a config check
    (`named-checkconf`, `nginx -t`). A server that did not start is indistinguishable downstream
    from the no-listener defect this replaces (`LES-001`'s silent-success class).
  * **Idempotent.** A re-apply rewrites config and restarts; it never stacks, and it never
    installs a package that is already present.
  * **Deterministic.** `render_serve_script` is a pure function of its arguments — byte-identical
    for the same input, which is what `scripts/verify_determinism.py`'s compiler check is for
    (`LES-002`). Every value a script cannot know at render time (its own address, its default
    gateway) is discovered IN-GUEST at run time, never guessed here.

**And the guard D8 asks for, at RENDER time rather than at runtime.** `plan_node_services`
partitions a declared listen set into (recipe-covered, echo) and refuses — loudly, with the
offending port — if a recipe would ever contend with another recipe or with the echo.
`responder-dns-resolver.sh`'s `EADDRINUSE` branch is the measured proof of why: the echo used to
race `named` for :53 and the whole failure was invisible because a *partially* bound responder
still reports `active`. A port a real daemon owns is now unreachable from the echo by
construction, not by a runtime skip.

**Not a package manager, not a substitute for `pkg:`.** `install_actions` returns the `{pkg: …}`
actions a node's recipes need so the cast can author them the supported way
(work order §7.2: packages in `pkg:`, configuration in the script, so re-deriving a zone file
from a fresh imprint stays a supported `--reapply`). The script ALSO carries a guarded install,
because a node whose `pkg:` actions never ran — the internet sink, a `--reapply`-only path — must
still come up rather than fail in a way that reads as "the recipe does not work".
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

# Same package, deliberately private-to-private: these two tables are the ONE place this project
# states the port -> protocol and emit-vocabulary -> sensor-vocabulary joins, and re-declaring
# either here would be the duplicated-fact hazard `LES-105` names — two tables ranking one
# vocabulary with nothing checking they agree (`LES-101`). Imported rather than aliased, so
# there is exactly one name for each.
from aximprint.synth.personas import _IR_PROTO_ALIASES, _PORT_PROTO
from aximprint.synth.responder import _RESPONDER_PY

#: The twin's forest, and it is a LAB CONSTANT, not an observation.
#:
#: Ruled by the user (`axrange/docs/WORK_ORDER_protocol_faithful_services.md` §10.1, **D4**):
#: *"the lab realm `corp.axr.lab`, not the observed `axellio.dom`: the forest name is `imputed`;
#: users and passwords are lab defaults marked `imputed`."* The implementation reading in the
#: same ruling is what keeps the observed half alive: *"the DC's short hostname may still follow
#: the observed `axdc01`; only the realm is replaced."*
#:
#: When the realm lands as a first-class IR fact (`docs/WORK_ORDER_reference_ingest.md` S5,
#: `ir_policy(scope='domain', parameter='realm')`), this stops being a bare `compiler` value and
#: becomes a **declared deployment transformation** — `compiler.declared_transformation`, with
#: the observed realm recorded beside it (§5.6). It is a plain compiler default today because
#: there is no observed realm at the IR boundary to record as replaced, and recording an absent
#: one would be the fabrication the class model exists to prevent.
LAB_REALM = "corp.axr.lab"

#: Sensor-vocabulary app tokens for ports `_PORT_PROTO` does not name.
#:
#: `_PORT_PROTO` is the EMIT table — it answers *"how does axRange's persona schema spell the
#: protocol a persona dials at this port"*, and a persona dials none of these. They are here
#: because the SENSOR names them and a listen census contains them, which is a different
#: question with a different (and much shorter) answer. Keyed on `(port, transport)` because
#: DHCP's two ports differ only by direction.
_EXTRA_APP_TOKENS: dict[tuple[int, str], str] = {
    (135, "tcp"): "dce_rpc",   # the Windows RPC endpoint mapper
    (67, "udp"): "dhcp",
    (68, "udp"): "dhcp",
}


def parse_port(spec: str) -> Optional[tuple[int, str]]:
    """`"53/udp"` -> `(53, "udp")`. `None` on anything this project did not mint.

    Fail-closed rather than fail-open: a malformed port-spec becomes absent, never a port 0
    entry that a recipe could then be selected for.
    """
    port, _, transport = str(spec).partition("/")
    try:
        pn = int(port)
    except (TypeError, ValueError):
        return None
    if not 0 < pn < 65536:
        return None
    transport = (transport or "tcp").lower()
    if transport not in ("tcp", "udp"):
        return None
    return pn, transport


def app_token(port: int, transport: str) -> Optional[str]:
    """The protocol a SENSOR would name on this port, in `ir_edge.proto`'s own vocabulary.

    Two vocabularies meet here and the join direction matters. `_PORT_PROTO` answers in axRange's
    PERSONA spelling (`https`, `kerberos`, `ldap`); `ir_edge.proto` answers in Zeek's (`ssl`,
    `krb`, `ldap_tcp`). `services[]` publishes ONE of them, and it is the sensor's — the work
    order's own example is `"app": "krb"`, and a consumer joining this against `edges[].proto`
    under two spellings would join to nothing, silently (`LES-143`).

    Transport-split analyzers (`ldap_tcp`/`ldap_udp`, `krb`/`krb_tcp`) resolve by suffix, so
    389/tcp is `ldap_tcp` and 389/udp is `ldap_udp`. Anything else takes the alias table's first
    token, which is declared order and therefore stable.

    `None` where no table names it — 9997/tcp (`_PORT_PROTO` maps it to the sensor's
    *unidentified* label `tcp`, which names no service), 7680, and every ephemeral socket. That
    absence is the honest answer the work order's §4 last row asks for: **observed but not
    reproduced**, never a fabricated listener.
    """
    extra = _EXTRA_APP_TOKENS.get((int(port), transport))
    if extra:
        return extra
    emit_proto = _PORT_PROTO.get(int(port))
    if emit_proto is None:
        return None
    tokens = _IR_PROTO_ALIASES.get(emit_proto)
    if not tokens:
        return None
    for token in tokens:
        if token.endswith("_" + transport):
            return token
    return tokens[0]


def edge_tokens(proto: Optional[str]) -> set[str]:
    """`"gssapi,smb,krb"` -> `{"gssapi", "smb", "krb"}`.

    Zeek joins every analyzer that fired on one connection into a single service string, so a
    composite has to be token-matched — the same rule `_IR_PROTO_ALIASES`' own comment states for
    the emit side, applied here for the observation side.
    """
    return {t.strip() for t in (proto or "").split(",") if t.strip()}


def services_for(listen_ports: Iterable[str], inbound_protos: Iterable[str]) -> list[dict]:
    """One entity's `services[]` — its observed listen census joined to the protocol actually
    spoken on edges TO it (`axrange/docs/WORK_ORDER_protocol_faithful_services.md` §6.1).

    Two evidence classes, per field, because two different things are being claimed and merging
    them would let an inference ride on an observation's class (CONVENTIONS §3, *"imputed never
    masquerades as observed"*). This mirrors `ir_entity`'s own `role`/`role_evidence_class` and
    `segment`/`segment_evidence_class` pairs rather than inventing a shape:

      * **`evidence_class`** is the LISTEN's, and it is `observed` — a listen fact is written
        only from a flow the host ACCEPTED (`windowing/windows.py`, gated on `FLOW_ACCEPTED`).
      * **`app` + `app_evidence_class`** are the protocol join. `observed` when an inbound edge
        to this host actually carries that analyzer's token; `imputed` when only the port
        convention names it. Both keys are ABSENT together when nothing names it at all —
        absent, never null, because a field with nothing behind it is worse than a missing one
        (`LES-006`).

    **No `windows_seen`**, and its absence is deliberate rather than an omission — see this
    module's companion note in `imprint_bundle.build_topology_bundle`. Nothing at the IR boundary
    carries a PER-LISTEN window count; publishing the entity's own would report a host's
    occupancy as a port's.
    """
    observed_tokens: set[str] = set()
    for proto in inbound_protos:
        observed_tokens |= edge_tokens(proto)

    out: list[dict] = []
    for spec in sorted(set(listen_ports), key=lambda s: (parse_port(s) or (0, ""), s)):
        parsed = parse_port(spec)
        if parsed is None:
            continue
        port, transport = parsed
        row = {"port": port, "proto": transport, "evidence_class": "observed"}
        token = app_token(port, transport)
        if token is not None:
            row["app"] = token
            row["app_evidence_class"] = (
                "observed" if token in observed_tokens else "imputed")
        out.append(row)
    return out


# --------------------------------------------------------------------------------------------
# The recipe table
# --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Recipe:
    """One real server, and the ports it takes responsibility for.

    `ports` is what the recipe CAN serve, not what it will: a recipe fires only on the
    intersection with a node's declared listen set, so an nginx recipe on a host that was never
    seen answering on 443 serves 80 alone.

    `unit` is the systemd unit to assert. `unit_alternatives` exists because a Debian package's
    unit name is not stable across releases (`bind9.service` became `named.service`;
    `chrony.service` is `chronyd.service` on a Red Hat-derived image) — the script resolves it
    at run time from `systemctl list-unit-files` rather than this table guessing.
    """

    name: str
    packages: tuple[str, ...]
    unit: str
    unit_alternatives: tuple[str, ...]
    ports: frozenset[tuple[int, str]]


#: Every entry is a package in **Debian main** (`axrange/docs/WORK_ORDER_protocol_faithful_
#: services.md` §4). `LES-024` ("bake fragile / external-repo tools; don't apt-install in the
#: isolated guest") targets external repos and fragile tooling; these are neither, and the
#: standing twin already installs bind9 and nginx at runtime — reliably, since the 2026-08-14
#: fix that refreshes a cloned guest's stale apt index first.
#:
#: ORDER IS THE EMISSION ORDER of the rendered script, and it is deliberate: bind9 first because
#: it is the one recipe that has to displace another daemon (`systemd-resolved` owns :53 on a
#: stock cloud image), the sshd ASSERTION last because it installs nothing and can only fail.
SERVER_RECIPES: tuple[Recipe, ...] = (
    Recipe(name="bind9", packages=("bind9", "bind9-utils"), unit="named",
           unit_alternatives=("named", "bind9"),
           ports=frozenset({(53, "udp"), (53, "tcp")})),
    # 8006 is served as REAL HTTPS and is deliberately not Proxmox (D6(a)): it *is* HTTPS, just
    # not that API, and a protocol-aware probe reads a completed handshake. `check_role_fixed_
    # point.py` already carries `hypervisor`/8006 as a permanently declared exception for the
    # role half of the same fact — no VM can serve Proxmox's own management API.
    Recipe(name="nginx", packages=("nginx", "openssl"), unit="nginx",
           unit_alternatives=("nginx",),
           ports=frozenset({(80, "tcp"), (443, "tcp"), (8006, "tcp")})),
    Recipe(name="chrony", packages=("chrony",), unit="chrony",
           unit_alternatives=("chrony", "chronyd"),
           ports=frozenset({(123, "udp")})),
    Recipe(name="isc-dhcp-server", packages=("isc-dhcp-server",), unit="isc-dhcp-server",
           unit_alternatives=("isc-dhcp-server",),
           ports=frozenset({(67, "udp")})),
    # ASSERT ONLY: sshd is running on every Debian/Ubuntu cloud image this twin clones from, so
    # installing anything here would be a no-op with a failure mode. `packages` is empty and
    # that is the whole recipe.
    Recipe(name="sshd", packages=(), unit="ssh", unit_alternatives=("ssh", "sshd"),
           ports=frozenset({(22, "tcp")})),
)


class ServiceRenderError(Exception):
    """A render-time refusal (D8 / work order §9.1 test 4).

    Raised where a port would be bound twice — by two recipes, or by a recipe and the echo. The
    point of raising at RENDER time is that the runtime symptom is invisible: a responder that
    loses a bind keeps running because its siblings are healthy, `systemctl is-active` still says
    `active`, and the fingerprint then blames the personas.
    """


@dataclass(frozen=True)
class ServicePlan:
    """What one node group serves, and by what.

    `covered` and `echo` PARTITION `declared` — asserted in `plan_node_services`, so a caller
    can rely on it rather than re-checking.
    """

    node: str
    declared: tuple[tuple[int, str], ...]
    recipes: tuple[tuple[Recipe, tuple[tuple[int, str], ...]], ...]
    echo: tuple[tuple[int, str], ...]

    @property
    def covered(self) -> tuple[tuple[int, str], ...]:
        return tuple(sorted({p for _r, ports in self.recipes for p in ports}))

    @property
    def packages(self) -> tuple[str, ...]:
        seen: list[str] = []
        for recipe, _ports in self.recipes:
            for pkg in recipe.packages:
                if pkg not in seen:
                    seen.append(pkg)
        return tuple(seen)

    def recipe_named(self, name: str) -> Optional[tuple[Recipe, tuple[tuple[int, str], ...]]]:
        for row in self.recipes:
            if row[0].name == name:
                return row
        return None


def plan_node_services(node: str, declared: Iterable[tuple[int, str]], *,
                       segment_cidr: Optional[str] = None) -> ServicePlan:
    """Partition a declared listen set into real servers and the labelled echo fallback.

    **D8(a), ruled:** the echo survives, but only on ports no real server covers, and it is
    reported as a `compiler`-class choice rather than as an observation. Removing it outright was
    the alternative and it is not free either — an unserved port draws an ICMP unreachable that
    Zeek scores as a *fabricated reverse local edge* (220 such rows from 228 dead-port emits,
    iteration 17), so silence manufactures evidence exactly as filler does.

    `segment_cidr` gates the DHCP recipe alone: a scope has to come from somewhere, and a
    segment whose prefix leaves no room for a pool is declined here rather than rendered into a
    config that fails `dhcpd -t` in the guest.
    """
    want = sorted(set(declared))
    claimed: dict[tuple[int, str], str] = {}
    recipes: list[tuple[Recipe, tuple[tuple[int, str], ...]]] = []
    for recipe in SERVER_RECIPES:
        ports = tuple(sorted(p for p in want if p in recipe.ports))
        if not ports:
            continue
        if recipe.name == "isc-dhcp-server" and dhcp_pool(segment_cidr) is None:
            # Declined, not silently dropped: the port falls through to the echo below and the
            # caller reports why. A DHCP server with no derivable scope is worse than none.
            continue
        for port in ports:
            if port in claimed:
                raise ServiceRenderError(
                    f"node {node!r}: port {port[0]}/{port[1]} is claimed by both "
                    f"{claimed[port]!r} and {recipe.name!r}. Two daemons cannot own one bind; "
                    f"fix SERVER_RECIPES rather than letting the loser fail at runtime.")
            claimed[port] = recipe.name
        recipes.append((recipe, ports))

    echo = tuple(p for p in want if p not in claimed)
    overlap = sorted(set(echo) & set(claimed))
    if overlap:
        raise ServiceRenderError(
            f"node {node!r}: the echo responder would bind {overlap}, which a real daemon on "
            f"this host already owns (D8). This is unreachable by construction — if it fires, "
            f"the partition below `plan_node_services` is broken.")
    if sorted(set(echo) | set(claimed)) != want:
        raise ServiceRenderError(
            f"node {node!r}: the recipe/echo split does not partition the declared listen set "
            f"{want} (covered={sorted(claimed)}, echo={sorted(echo)}).")
    return ServicePlan(node=node, declared=tuple(want), recipes=tuple(recipes), echo=echo)


def install_actions(plan: ServicePlan, script_path: str) -> list[dict]:
    """The `install:` actions a node needs for `plan`, in the order axRange must run them.

    Packages first as `{pkg: …}` and configuration last as `{script: …}` — work order §7.2, and
    the reason is `--update`'s mutation rule rather than tidiness: a `run-script`'s CONTENT can
    be re-applied to a standing twin (`--update --reapply`, CHANGELOG 2026-09-02) while adding a
    package to an existing node mutates `provision-app`'s persisted `install_actions` and is
    refused. Configuration that has to be re-derived from a fresh imprint therefore belongs in
    the script, and packages in `pkg:`.
    """
    return [{"pkg": pkg} for pkg in plan.packages] + [{"script": script_path}]


def dhcp_pool(cidr: Optional[str]) -> Optional[tuple[str, str, str, str]]:
    """`(network, netmask, pool_first, pool_last)` for a segment, or `None` if it has no room.

    The pool is the segment's **top 12 addresses**, below the broadcast. axRange allocates a
    twin's own hosts from the LOW end (the gateway takes `network + 1`), so a pool at the top is
    the part of the space a twin does not use — a collision would need ~240 hosts on one /24,
    which is above axRange's own per-segment reality and is reported as a caveat by the caller
    rather than silently risked.

    Declines anything wider than a /30's worth of room, and IPv6 outright: `isc-dhcp-server`'s
    v6 mode is a different daemon invocation and no observed listen census in this project has
    ever carried 547/udp.
    """
    if not cidr:
        return None
    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return None
    if net.version != 4 or net.num_addresses < 32:
        return None
    last = int(net.broadcast_address) - 1
    first = last - 11
    if first <= int(net.network_address) + 1:
        return None
    return (str(net.network_address), str(net.netmask),
            str(ipaddress.ip_address(first)), str(ipaddress.ip_address(last)))


def zone_serial(*parts: str) -> int:
    """A CONTENT-derived SOA serial, never a clock.

    Determinism is a house rule (CONVENTIONS §3) and a wall-clock serial would make every
    re-cast of one PIT a different artifact — the exact instability
    `scripts/verify_determinism.py` exists to catch. Content-derived is also the behaviour DNS
    actually wants: identical zone content keeps its serial, changed content gets a new one.
    """
    digest = hashlib.blake2s("\x00".join(parts).encode("utf-8"), digest_size=4).hexdigest()
    return int(digest, 16) % 2_000_000_000


# --------------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------------

_SHEBANG = "#!/bin/sh\n"

_PREAMBLE = r'''# Delivered as an `install: [{script: ...}]` action, so it runs through `run-script`
# (OP-APP-030) IN-GUEST as root, honouring this shebang.
set -eu
umask 022
mkdir -p /usr/local/lib/axr /var/log/axrange

# Install only what is MISSING, and refresh the index first. A cloned guest's apt index is
# stale by construction -- axRange fixed exactly this on its own `provision-app` path on
# 2026-08-14, and a `pkg:` action having already run is the common case here, which is why the
# whole body is skipped when nothing is missing.
axr_pkg() {
    _missing=""
    for _p in "$@"; do
        if ! dpkg-query -W -f='${Status}' "$_p" 2>/dev/null \
                | grep -q '^install ok installed$'; then
            _missing="$_missing $_p"
        fi
    done
    if [ -n "$_missing" ]; then
        DEBIAN_FRONTEND=noninteractive apt-get update -qq
        # shellcheck disable=SC2086
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
            --no-install-recommends $_missing
    fi
}

# A Debian package's unit name is not stable across releases (bind9 -> named, chrony ->
# chronyd on RH-derived images). Resolve it from what is actually installed rather than
# guessing at render time.
axr_unit_name() {
    for _u in "$@"; do
        if systemctl list-unit-files "$_u.service" >/dev/null 2>&1 \
                && systemctl list-unit-files "$_u.service" 2>/dev/null | grep -q "^$_u\.service"; then
            echo "$_u"; return 0
        fi
    done
    echo "$1"
}

# FAIL LOUD. A server that did not start is indistinguishable downstream from the no-listener
# defect this whole file replaces, and `systemctl is-active` is the only thing that can tell
# them apart (axRange LES-079/LES-086; never `nohup ... &`, which the guest-agent session
# reaps).
axr_unit() {
    systemctl enable "$1" >/dev/null 2>&1 || true
    systemctl restart "$1"
    sleep 1
    if ! systemctl is-active --quiet "$1"; then
        systemctl status --no-pager --full "$1" >&2 || true
        echo "axr-serve: unit '$1' did not start" >&2
        exit 1
    fi
}

axr_assert_unit() {
    for _u in "$@"; do
        if systemctl is-active --quiet "$_u"; then return 0; fi
    done
    echo "axr-serve: none of the units '$*' is active" >&2
    exit 1
}

axr_self_ip() { ip -4 -o addr show scope global | awk '{split($4,a,"/"); print a[1]; exit}'; }
axr_gateway() { ip -4 route show default | awk '{print $3; exit}'; }
'''


def _render_bind9(ports, ctx) -> str:
    """bind9: authoritative for the LAB realm, plus a forwarder to the segment gateway.

    **Names come from observation; the addresses do not, and the file says so.** The zone's
    labels are the twin's own observed short hostnames (work order §3: *"Names and structure
    come from observation. Bytes and secrets never do"*, and D4's implementation reading keeps
    the short hostname while replacing the realm). Their A records point at the resolver itself,
    because a cast cannot know a twin host's address — axRange allocates it at deploy time — and
    an A record's RDATA is payload, which §11 already puts out of row 5's scope: the rule is
    *protocol-valid*, not *equivalent*.

    **No corporate-looking inventions.** Only names bound to a LOCAL, PLACED entity in this PIT
    reach the zone; the 670 external query names the capture also carries do not, because a twin
    resolver claiming authority for `sharepoint.us` would be asserting something no observation
    supports. Everything else falls to the wildcard, which is what makes any twin-internal query
    answerable at all.
    """
    names = ctx["zone_names"]
    realm = ctx["realm"]
    serial = zone_serial(realm, *names)
    records = "\n".join(f"{n:<24} IN  A     @@IP@@" for n in names)
    return f'''
# --- bind9 -----------------------------------------------------------------
# serves: {_fmt_ports(ports)}  (packages: bind9 bind9-utils, installed above)
# systemd-resolved owns :53 on a stock cloud image, so `named` cannot bind it without
# displacing the stub first. Measured on dev45: the port-53 thread of the echo responder
# crashed on EADDRINUSE while its unit still reported healthy.
if systemctl is-active --quiet systemd-resolved 2>/dev/null; then
    systemctl disable --now systemd-resolved
fi
AXR_IP="$(axr_self_ip)"
AXR_GW="$(axr_gateway)"
rm -f /etc/resolv.conf
printf 'nameserver 127.0.0.1\\nsearch {realm}\\n' > /etc/resolv.conf

cat > /etc/bind/db.axr-lab <<'AXR_ZONE_EOF'
$TTL 300
@   IN  SOA   ns.{realm}. hostmaster.{realm}. (
        {serial} ; serial (content-derived, never a clock)
        3600       ; refresh
        600        ; retry
        604800     ; expire
        300 )      ; negative TTL
@   IN  NS    ns.{realm}.
ns                       IN  A     @@IP@@
{records}
; Every other twin-internal name. The twin's node ids are covered here rather than enumerated:
; a cast knows the names and not the addresses, so one wildcard says exactly as much as N
; records would and says it honestly.
*                        IN  A     @@IP@@
AXR_ZONE_EOF
sed -i "s/@@IP@@/$AXR_IP/g" /etc/bind/db.axr-lab

cat > /etc/bind/named.conf.local <<'AXR_LOCAL_EOF'
zone "{realm}" {{
    type master;
    file "/etc/bind/db.axr-lab";
}};
AXR_LOCAL_EOF

cat > /etc/bind/named.conf.options <<'AXR_OPT_EOF'
options {{
    directory "/var/cache/bind";
    listen-on {{ any; }};
    listen-on-v6 {{ none; }};
    allow-query {{ any; }};
    recursion yes;
    allow-recursion {{ any; }};
    forwarders {{ @@GW@@; }};
    forward first;
    dnssec-validation no;
}};
AXR_OPT_EOF
if [ -n "$AXR_GW" ]; then
    sed -i "s/@@GW@@/$AXR_GW/" /etc/bind/named.conf.options
else
    sed -i '/forwarders/d; /forward first/d' /etc/bind/named.conf.options
fi

named-checkconf
named-checkzone {realm} /etc/bind/db.axr-lab
axr_unit "$(axr_unit_name named bind9)"
'''


def _render_nginx(ports, ctx) -> str:
    """nginx on whichever of 80 / 443 / 8006 the host was observed answering.

    The certificate is ALWAYS a self-signed lab cert generated in-guest (§11, absolute: a real
    certificate, chain or key is never reproduced). Its CN follows an observed vhost where one
    reaches this compiler and the node's own name otherwise — a name may be observed, key
    material never.
    """
    cn = ctx["tls_cn"]
    vhosts = ctx.get("vhosts") or []
    blocks = []
    if (80, "tcp") in ports:
        blocks.append(
            "server {\n"
            "    listen 80 default_server;\n"
            f"    server_name {cn};\n"
            "    root /var/www/html;\n"
            "    location / { try_files $uri $uri/ /index.html; }\n"
            "}")
    for tls_port in (443, 8006):
        if (tls_port, "tcp") in ports:
            blocks.append(
                f"server {{\n"
                f"    listen {tls_port} ssl default_server;\n"
                f"    server_name {cn};\n"
                f"    ssl_certificate     /etc/ssl/axr/serve.crt;\n"
                f"    ssl_certificate_key /etc/ssl/axr/serve.key;\n"
                f"    root /var/www/html;\n"
                "    location / { try_files $uri $uri/ /index.html; }\n"
                f"}}")
    for vhost in vhosts:
        blocks.append(
            "server {\n"
            f"    listen {'443 ssl' if (443, 'tcp') in ports else '80'};\n"
            f"    server_name {vhost};\n"
            + ("    ssl_certificate     /etc/ssl/axr/serve.crt;\n"
               "    ssl_certificate_key /etc/ssl/axr/serve.key;\n"
               if (443, "tcp") in ports else "")
            + "    root /var/www/html;\n"
            "    location / { try_files $uri $uri/ /index.html; }\n"
            "}")
    conf = "\n".join(blocks)
    needs_tls = any(p in ports for p in ((443, "tcp"), (8006, "tcp")))
    tls = f'''
if [ ! -f /etc/ssl/axr/serve.crt ]; then
    mkdir -p /etc/ssl/axr
    openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \\
        -keyout /etc/ssl/axr/serve.key -out /etc/ssl/axr/serve.crt \\
        -subj "/CN={cn}" -addext "subjectAltName=DNS:{cn}" >/dev/null 2>&1
    chmod 600 /etc/ssl/axr/serve.key
fi''' if needs_tls else ""
    return f'''
# --- nginx -----------------------------------------------------------------
# serves: {_fmt_ports(ports)}  (packages: nginx openssl, installed above){tls}
mkdir -p /var/www/html
cat > /var/www/html/index.html <<'AXR_INDEX_EOF'
<!doctype html><title>{cn}</title>
<h1>{cn}</h1>
<p>axRange cast twin. Body content is synthetic by design; the protocol is not.</p>
AXR_INDEX_EOF
rm -f /etc/nginx/sites-enabled/default
cat > /etc/nginx/sites-available/axr-serve <<'AXR_NGINX_EOF'
{conf}
AXR_NGINX_EOF
ln -sfn /etc/nginx/sites-available/axr-serve /etc/nginx/sites-enabled/axr-serve
nginx -t
axr_unit "$(axr_unit_name nginx)"
'''


def _render_chrony(ports, ctx) -> str:
    """chrony serving time to the segment. `local stratum 10` makes it a usable reference with
    no upstream, which is the state an air-gapped twin segment is actually in."""
    return f'''
# --- chrony ----------------------------------------------------------------
# serves: {_fmt_ports(ports)}  (package: chrony, installed above)
AXR_CHRONY_CONF=/etc/chrony/chrony.conf
[ -f "$AXR_CHRONY_CONF" ] || AXR_CHRONY_CONF=/etc/chrony.conf
if ! grep -q '^# axr-serve' "$AXR_CHRONY_CONF" 2>/dev/null; then
    {{
        echo "# axr-serve"
        echo "local stratum 10"
        echo "allow all"
    }} >> "$AXR_CHRONY_CONF"
fi
axr_unit "$(axr_unit_name chrony chronyd)"
'''


def _render_dhcp(ports, ctx) -> str:
    """isc-dhcp-server, scope from the node group's own segment CIDR.

    The pool is the segment's top 12 addresses (`dhcp_pool`), which is the part of the space
    axRange's low-end allocator does not use. `authoritative` is deliberately NOT set: an
    authoritative server NAKs leases it does not recognise, and a twin whose hosts are statically
    addressed by cloud-init has no business doing that.
    """
    network, netmask, first, last = ctx["dhcp_pool"]
    return f'''
# --- isc-dhcp-server -------------------------------------------------------
# serves: {_fmt_ports(ports)}  (package: isc-dhcp-server, installed above)
cat > /etc/dhcp/dhcpd.conf <<'AXR_DHCPD_EOF'
default-lease-time 600;
max-lease-time 7200;
ddns-update-style none;
# NOT `authoritative`: the twin's hosts are statically addressed by cloud-init, so a server
# that NAKs unrecognised leases would be answering a question nobody asked.
subnet {network} netmask {netmask} {{
    range {first} {last};
}}
AXR_DHCPD_EOF
AXR_IFACE="$(ip -4 -o route show default | awk '{{print $5; exit}}')"
[ -n "$AXR_IFACE" ] || AXR_IFACE="$(ip -4 -o addr show scope global | awk '{{print $2; exit}}')"
printf 'INTERFACESv4="%s"\\nINTERFACESv6=""\\n' "$AXR_IFACE" > /etc/default/isc-dhcp-server
dhcpd -t -cf /etc/dhcp/dhcpd.conf
axr_unit "$(axr_unit_name isc-dhcp-server)"
'''


def _render_sshd(ports, ctx) -> str:
    return f'''
# --- sshd ------------------------------------------------------------------
# serves: {_fmt_ports(ports)}  (ASSERT ONLY -- every Debian/Ubuntu cloud image runs it)
axr_assert_unit ssh sshd
'''


def _render_echo(ports, ctx) -> str:
    """The echo, D8(a): kept, but ONLY on ports no real server covers, and labelled `compiler`.

    Unchanged in mechanism from `synth/responder.py` — the same `_RESPONDER_PY`, imported rather
    than copied — and changed entirely in scope: it is a fallback for ports this project has no
    real server for, not the twin's answer to every conversation.
    """
    args = " ".join(f"{p}/{t}" for p, t in ports)
    return f'''
# --- echo responder (evidence class: compiler) ------------------------------
# serves: {_fmt_ports(ports)}
# No real server in `SERVER_RECIPES` covers these ports. They are echoed rather than left
# silent because an unserved port draws an ICMP unreachable that Zeek scores as a FABRICATED
# reverse local edge -- silence manufactures evidence exactly as filler does (D8(a)).
cat > /usr/local/lib/axr/responder.py <<'AXR_RESPONDER_EOF'
{_RESPONDER_PY.strip()}
AXR_RESPONDER_EOF
systemctl stop axr-responder.service 2>/dev/null || true
systemd-run --unit=axr-responder --collect \\
    /usr/bin/python3 /usr/local/lib/axr/responder.py {args}
sleep 1
systemctl is-active --quiet axr-responder.service
'''


_RENDERERS = {
    "bind9": _render_bind9,
    "nginx": _render_nginx,
    "chrony": _render_chrony,
    "isc-dhcp-server": _render_dhcp,
    "sshd": _render_sshd,
}


def _fmt_ports(ports: Sequence[tuple[int, str]]) -> str:
    return ", ".join(f"{p}/{t}" for p, t in ports) or "(none)"


def render_serve_script(plan: ServicePlan, *, realm: str = LAB_REALM,
                        zone_names: Sequence[str] = (),
                        vhosts: Sequence[str] = (),
                        segment_cidr: Optional[str] = None,
                        origin: str = "") -> str:
    """The `serve-<node>.sh` a node group installs. Pure, deterministic, byte-stable.

    ONE script per NODE GROUP rather than per role, and that is the correctness fix rather than a
    file-layout preference: a role's members can have different observed listen sets (this
    source's four `dns-resolver` hosts answer on 1, 2 and 9 ports respectively), so a role-level
    script would make three of them serve six ports they were never seen serving — manufacturing
    the very listeners the work order exists to make honest. A group's own members are collapsed
    clones, so the union across them is a statement about one population rather than across
    several.
    """
    # The FIRST observed vhost becomes the certificate's CN and the default server's name; the
    # rest get their own `server_name` blocks. Sorted, so which one is "first" is a property of
    # the content rather than of the caller's iteration order (`LES-009`).
    ordered_vhosts = sorted({v for v in vhosts if v})
    ctx = {
        "realm": realm,
        "zone_names": tuple(sorted(set(zone_names))),
        "tls_cn": ordered_vhosts[0] if ordered_vhosts else f"{plan.node}.{realm}",
        "vhosts": tuple(ordered_vhosts[1:]),
        "dhcp_pool": dhcp_pool(segment_cidr),
        "segment_cidr": segment_cidr,
    }
    header = [
        "# axRange cast twin -- real protocol servers for node '%s'." % plan.node,
        "# GENERATED by aximprint (`synth/services.py`); do not hand-edit -- a re-cast",
        "# overwrites it, and an edit that lives only in the installed file is an edit the",
        "# next re-cast silently discards.",
        "#",
        "# Declared listen set (from the OBSERVED listen census, never the persona dial set --",
        "# `axrange/docs/WORK_ORDER_protocol_faithful_services.md` §2):",
        "#   %s" % _fmt_ports(plan.declared),
    ]
    if origin:
        header.append("# Origin: %s" % origin)
    header.append("#")
    for recipe, ports in plan.recipes:
        header.append("#   %-16s -> %s" % (_fmt_ports(ports), recipe.name))
    if plan.echo:
        header.append("#   %-16s -> echo responder (evidence class: compiler)"
                      % _fmt_ports(plan.echo))
    body = [_SHEBANG, "\n".join(header), "\n", _PREAMBLE]
    if plan.packages:
        # EVERY package, ONCE, and BEFORE any daemon is reconfigured. Not a tidiness choice: the
        # bind9 section displaces `systemd-resolved` and repoints `/etc/resolv.conf` at the
        # loopback, so a later section's own `apt-get` would have to resolve its mirror through a
        # `named` that has not started yet. One install up front makes the ordering hazard
        # structurally absent rather than merely unlikely, and costs one `apt-get update`
        # instead of four.
        body.append("\n# --- packages --------------------------------------------------"
                    "-------------\n"
                    "axr_pkg %s\n" % " ".join(plan.packages))
    for recipe, ports in plan.recipes:
        body.append(_RENDERERS[recipe.name](ports, ctx))
    if plan.echo:
        body.append(_render_echo(plan.echo, ctx))
    body.append("\necho \"axr-serve: %s ready (%s)\"\n" % (plan.node, _fmt_ports(plan.declared)))
    return "".join(body)


def zone_names_from_pit(pit: dict) -> list[str]:
    """The twin's own observed short hostnames — the only names that reach a bind9 zone.

    Two sources, both already at the IR boundary and neither needing a parse:

      * every LOCAL, PLACED entity whose natural key is a hostname (`hostname:axdc01`), which is
        the resolver's own minted short name;
      * every `ir_identity` DNS name bound to a local, placed entity, reduced to its leftmost
        label — which is what adds `axdc04`, a host the wire named but never keyed by hostname.

    **Everything else is refused, by a positive gate.** A name is skipped when it is bound to no
    local placed entity (670 of this source's 708 DNS rows), when any label starts with `_`
    (mDNS/SRV service names such as `_pxstats._tcp.local` are not hosts), when it ends in a
    reserved suffix (`.local`, `.arpa`), or when it carries a character a DNS label cannot. That
    is the *"never invent corporate-looking names"* rule made mechanical: the zone can only
    contain a name the capture bound to a host this twin actually deploys.
    """
    placed = {e["entity_key"] for e in pit.get("ir_entity") or []
              if e.get("locality") == "local" and e.get("segment")}
    names: set[str] = set()
    for entity in pit.get("ir_entity") or []:
        key = entity.get("entity_key") or ""
        if key in placed and key.startswith("hostname:"):
            label = key.split(":", 1)[1]
            if _is_dns_label(label):
                names.add(label)
    for row in pit.get("ir_identity") or []:
        if row.get("kind") != "dns" or row.get("entity_key") not in placed:
            continue
        ident = str(row.get("identifier") or "")
        labels = ident.split(".")
        if any(lb.startswith("_") for lb in labels) or len(labels) < 2:
            continue
        if labels[-1].lower() in ("local", "arpa", "invalid", "onion"):
            continue
        if _is_dns_label(labels[0]):
            names.add(labels[0].lower())
    return sorted(names)


def _is_dns_label(label: str) -> bool:
    if not label or len(label) > 63 or label.startswith("-") or label.endswith("-"):
        return False
    return all(ch.isalnum() or ch == "-" for ch in label)


def listen_specs(entities: Iterable[dict]) -> list[str]:
    """The union of a node group's members' observed listen sets, as `"<port>/<proto>"` specs.

    A UNION across a collapsed group, and the granularity is the honest one: axRange fans a
    group out into `count` IDENTICAL clones, so every member of one group is the same host as
    far as the twin is concerned. Union across a ROLE would not be — that is what
    `render_serve_script`'s docstring refuses.
    """
    out: set[str] = set()
    for entity in entities:
        raw = entity.get("listen_ports")
        try:
            ports = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except (TypeError, ValueError):
            ports = []
        for spec in ports:
            parsed = parse_port(spec)
            if parsed is not None:
                out.add(f"{parsed[0]}/{parsed[1]}")
    return sorted(out, key=lambda s: (parse_port(s) or (0, ""), s))


def external_listen_census(pit: dict) -> list[tuple[tuple[int, str], int]]:
    """`[((port, transport), hosts)]` — what the network's EXTERNAL peers were observed answering
    on, most common first. The internet sink's own evidence
    (`axrange/docs/WORK_ORDER_protocol_faithful_services.md` §7.4).

    The sink is a boundary rather than a scope item: 15 synthetic hosts stand in for 349
    externally-observed listeners, and *no capture observed the sink itself*, so it cannot take
    its declared set from a listen census of its own. It takes it from the census of what it
    STANDS IN FOR — which is a real observation about this network, and on this source
    overwhelmingly 443/tcp (191 hosts) and 123/udp (105).

    The host COUNT rides along because the caller needs it: a port one external host was seen
    answering is not evidence that "the internet" answers there, and `sink_real_services` uses
    the share to decide.
    """
    counts: dict[tuple[int, str], int] = {}
    for entity in pit.get("ir_entity") or []:
        if entity.get("locality") == "local":
            continue
        raw = entity.get("listen_ports")
        try:
            ports = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except (TypeError, ValueError):
            ports = []
        for spec in set(ports):
            parsed = parse_port(spec)
            if parsed is not None:
                counts[parsed] = counts.get(parsed, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


#: A port must be answered by at least this SHARE of the external listener population before the
#: sink runs a real daemon on it. On this source (349 external listeners) that is ~3.5 hosts, which
#: admits 443/tcp (191), 123/udp (105), 53/udp (11), 80/tcp (11) and 22/tcp (5) and excludes
#: 445/tcp (2) and 389/udp (1).
#:
#: A share rather than a top-N, because top-N answers the wrong question: "the twelve commonest"
#: is a bound on OUTPUT SIZE, and what the sink needs is a bound on EVIDENCE — one external host
#: seen answering somewhere is not the internet answering there. It also keeps a long single-host
#: tail from putting a DHCP server on the sink segment, which is the concrete harm.
SINK_SERVICE_MIN_SHARE = 0.01

#: The fallback when a PIT carries no external listen census at all — §7.4's own two, stated
#: there as what the sink's 349 externally-observed listeners are overwhelmingly made of.
#:
#: `compiler`-class, unavoidably and by name: nothing observed the sink, so its declared set is a
#: choice rather than a reading. What the choice is DERIVED from is observed — which is exactly
#: the distinction `evidence_class` exists to keep legible.
COMPILER_SINK_PORTS: tuple[tuple[int, str], ...] = ((443, "tcp"), (123, "udp"))


def sink_real_services(pit: dict) -> tuple[tuple[int, str], ...]:
    """Which ports the internet sink serves for REAL, derived from what it stands in for.

    Two filters, and each one is doing different work:

      * **the recipe table** — a port no `SERVER_RECIPES` entry covers cannot be served for real
        by this project at all, so admitting it would only move it from the echo to the echo;
      * **`SINK_SERVICE_MIN_SHARE`** — evidence, not size (see the constant).

    Derived per PIT rather than hardcoded, because §8.2's ruling is *"based on the observed
    data"* and this is observed data about the population the sink replaces. `COMPILER_SINK_PORTS`
    is the fallback for a PIT with no external census, not the normal path.
    """
    census = external_listen_census(pit)
    if not census:
        return COMPILER_SINK_PORTS
    # The DENOMINATOR is the external listener population — every external entity observed
    # answering on anything — not the busiest port's own count. `max(counts)` would be the
    # tempting one and it answers a different question ("a share of the commonest port"), which
    # on this source is 191 rather than 349 and moves the bar by nearly a factor of two
    # (`LES-109`: a rate's denominator decides which question it answers).
    population = sum(1 for e in pit.get("ir_entity") or []
                     if e.get("locality") != "local"
                     and (e.get("listen_ports") or "[]") not in ("[]", "", None, []))
    covered: set[tuple[int, str]] = set().union(*[r.ports for r in SERVER_RECIPES])
    chosen = tuple(sorted(
        port for port, hosts in census
        if port in covered and hosts >= population * SINK_SERVICE_MIN_SHARE))
    return chosen or COMPILER_SINK_PORTS
