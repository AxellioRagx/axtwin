"""Router EGRESS PHASE POLICY renderer — `provision` (real egress) vs `measure`
(no real egress; the sink is the only thing off-twin).

`docs/WORK_ORDER_protocol_engines.md` §E1. Pure string generation, the same shape
as `fabric_render.py`: a small validated *egress context dict* in, in-guest shell
out. This module knows nothing about Proxmox, the IR or the planner — the adapter
(`rest.py::op_configure_router`) writes what it returns into the guest over QGA,
and `scripts/gen_egress_policy.py` writes the identical text into a twin's
`scripts/` directory as a `run-script` payload. ONE source for both paths, because
two hand-maintained copies of a containment rule set is exactly how a twin ends up
talking to the real internet while a test says it cannot.

WHY A ROUTER-SIDE PHASE AT ALL
------------------------------
Provisioning is a real apt workload (Chromium, `smbclient`, `krb5-user`, the server
packages) and needs the router's ordinary LAN→WAN masquerade. Measurement must have
NO real egress: every external destination has to land on the twin's sink, which
impersonates the observed external world. Containment is therefore a property of
the **network**, not of a render-time argv check on each client (ruling 2026-09-21,
reversing `WORK_ORDER_protocol_faithful_traffic.md` §2.2). The router is the one
box that can enforce it for every client at once, including clients that resolve
names normally and chase addresses no corpus ever saw.

DEFAULT CLOSED
--------------
`normalize_phase(None)` is `measure`, the in-guest engine falls back to `measure`
when its state file is missing or unparsable, and a failed `provision` transition
forces `measure` back on before it fails. `provision` is only ever entered when
something explicitly asks for it *and* the transition succeeds. An unknown phase
STRING (a typo) is a loud render-time error rather than a silent demotion to
`measure` — a silently-different-from-asked ruleset is the LES-001 failure mode,
and at render time we can still say so.

Residual, stated honestly: nothing here expires a `provision` phase. A twin left in
`provision` stays open until someone flips it. That is why the engine records
`phase` + `phase-since` and `--show` prints them: §6's capture-window labelling
must refuse to score a window that ran in `provision`.

THE TWO PHASES, AS RULES
------------------------
`provision` — re-assert `configure-router`'s masquerade (`axr-router-nat.sh`),
tear down the sink policy routes, leave an empty-but-present marker table so the
phase is readable, and FAIL if no LAN→WAN masquerade is actually present.

`measure` — drop everything forwarded to or from the WAN interface (counters, so
"zero WAN-forwarded packets" is directly readable), drop the router's own NEW
connections out the WAN (DHCP and already-established flows survive, so an operator
SSH session into the router does not die with the flip), and policy-route every
destination that is not internal to the sink.

DEFAULT ROUTE vs PER-PREFIX (the decision, and the trade-off)
-------------------------------------------------------------
`mode: default` (the default) puts ONE route in the policy table: `default via
<sink>`. `mode: cidrs` instead installs one route per prefix from the sink corpus
generator's `external-cidrs.txt` (~1,270 aggregated /24s, ~2,059 lossless).

Default wins for measurement because a real client chases names a corpus never saw
— a CDN one rotation away from the observed address, a QUIC alternative endpoint, a
telemetry hostname that only appears under load. Under `default` those still find a
server; under `cidrs` they fall through to the main table's default route, out the
WAN, into the DROP — a silent black hole that looks like a broken client. `cidrs`
is kept for the narrower question "did anything reach for an address the cast never
observed?", where the black hole IS the signal: nothing outside the observed set is
served, and the WAN drop counters say how much was attempted. Nothing is ever
"stolen" in either mode — the policy rules only match traffic that already arrived
on one of this router's LAN interfaces.

ROUTING vs ARP (why the router has to carry these routes at all)
----------------------------------------------------------------
The sink accepts packets addressed to ANY IP via an `iif`-scoped `local 0.0.0.0/0`
route in a table of its own (`twins/_shared/scripts/serve-sink.sh`), but it
does NOT answer ARP for foreign addresses. So "send everything to the sink" cannot
be expressed on the sink; it has to be a next-hop route on the router, which is
this module's second job.

VyOS
----
NOT implemented. `rest.py` raises loudly for a VyOS router carrying an egress
policy rather than applying half of it or no-oping (LES-001): VyOS 1.3 and 1.4
disagree about firewall syntax (`firewall name` vs `firewall ipv4 forward filter`),
and a containment rule set that silently does not apply is the single worst failure
this slice can have.
"""
from __future__ import annotations

import ipaddress
from typing import Any, Mapping

# The two in-guest constants are the ENGINE's (one definition, shared with
# `o2control/containment.py`'s live read); re-exported here so the render
# and its tests keep reading them from this module.
from axrange.planner.egress import EGRESS_ENGINE, EGRESS_OK_MARKER  # noqa: F401

# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------

EGRESS_PHASES: tuple[str, ...] = ("provision", "measure")

#: Fail-safe. A twin that was never told a phase is CLOSED.
DEFAULT_EGRESS_PHASE = "measure"

EGRESS_MODES: tuple[str, ...] = ("default", "cidrs")
DEFAULT_EGRESS_MODE = "default"

#: In-guest paths. Stable and well-known on purpose — a flip, a healthcheck and a
#: boot-time re-apply all address them by name.
EGRESS_DIR = "/etc/axr-egress"
EGRESS_CONF = f"{EGRESS_DIR}/egress.conf"
EGRESS_STATE = f"{EGRESS_DIR}/phase"
EGRESS_CIDRS = f"{EGRESS_DIR}/external-cidrs.txt"
EGRESS_INSTALLER = "/usr/local/sbin/axr-egress-install.sh"
EGRESS_UNIT = "/etc/systemd/system/axr-egress-phase.service"

#: Policy-routing table + `ip rule` priorities. 210 is unused by PVE, by the sink
#: (which uses 100 for its own any-IP table) and by the fabric slice.
EGRESS_TABLE = 210

#: The probe destination the in-guest assertion uses in `default` mode: TEST-NET-3
#: (RFC 5737), which no cast can ever have observed. Proving THIS address routes to
#: the sink is exactly the "an unobserved external address must still find a
#: server" guarantee, checked without sending a packet.
EGRESS_PROBE_DST = "203.0.113.9"

#: Marker the adapter greps for in the installer's output.


class EgressRenderError(ValueError):
    """A malformed egress policy context — raised at render time, never applied."""


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def normalize_phase(phase: Any) -> str:
    """`None`/empty → `measure` (default CLOSED). A non-empty unknown value is a
    loud error: a typo must not quietly become a different policy."""
    if phase is None:
        return DEFAULT_EGRESS_PHASE
    text = str(phase).strip().lower()
    if not text:
        return DEFAULT_EGRESS_PHASE
    if text not in EGRESS_PHASES:
        raise EgressRenderError(
            f"egress phase {phase!r} is not one of {list(EGRESS_PHASES)} "
            "(omit it entirely to get the default-closed `measure`)"
        )
    return text


def normalize_egress(spec: Any) -> dict[str, Any] | None:
    """Validate an egress policy context. `None` in → `None` out (the router keeps
    today's permissive behavior and NO policy is installed — the 8 shipped twin
    configs are unaffected). Anything else must name a sink."""
    if spec is None:
        return None
    if not isinstance(spec, Mapping):
        raise EgressRenderError(f"egress policy must be a mapping, got {type(spec).__name__}")

    phase = normalize_phase(spec.get("phase"))

    sink_raw = str(spec.get("sink_ip") or "").strip()
    if not sink_raw:
        # Required in BOTH phases: a provision-phase router with no sink recorded
        # cannot later be flipped to measure without re-running the op, and the
        # flip is the moment we least want to discover a missing field.
        raise EgressRenderError(
            "egress policy needs `sink_ip` (the twin sink's address on a LAN "
            "segment) — it is recorded in both phases so a flip to `measure` "
            "needs no new input"
        )
    try:
        sink_ip = str(ipaddress.IPv4Address(sink_raw))
    except ValueError as exc:
        raise EgressRenderError(f"egress `sink_ip` {sink_raw!r} is not an IPv4 address") from exc

    mode = str(spec.get("mode") or DEFAULT_EGRESS_MODE).strip().lower()
    if mode not in EGRESS_MODES:
        raise EgressRenderError(f"egress `mode` {mode!r} is not one of {list(EGRESS_MODES)}")

    cidrs_in = spec.get("cidrs") or []
    if isinstance(cidrs_in, str):
        cidrs_in = [line for line in cidrs_in.splitlines() if line.strip()]
    cidrs: list[str] = []
    for entry in cidrs_in:
        text = str(entry).strip()
        if not text or text.startswith("#"):
            continue
        try:
            cidrs.append(str(ipaddress.ip_network(text, strict=False)))
        except ValueError as exc:
            raise EgressRenderError(f"egress `cidrs` entry {text!r} is not a CIDR") from exc
    if mode == "cidrs" and not cidrs:
        raise EgressRenderError(
            "egress `mode: cidrs` needs a non-empty `cidrs` list "
            "(scripts/gen_sink_corpus.py writes external-cidrs.txt)"
        )

    return {"phase": phase, "sink_ip": sink_ip, "mode": mode, "cidrs": cidrs}


# ---------------------------------------------------------------------------
# The in-guest engine
# ---------------------------------------------------------------------------

# Constant on purpose: every per-twin fact is DATA in $EGRESS_CONF, so the engine
# itself is byte-identical on every router and a diff between two twins is a diff
# of their data. Written with `_ENGINE_TEMPLATE.format(...)` over a handful of
# path/number constants only — no per-twin interpolation, so a `{` in shell would
# be a problem; there is none (the awk bodies use `$(...)`-free forms deliberately).
_ENGINE_TEMPLATE = r"""#!/bin/sh
# axRange router EGRESS PHASE POLICY -- generated by axrange/adapter_proxmox/
# egress_render.py. Do not hand-edit; edit the renderer and re-apply.
#
#   axr-egress-phase.sh                 apply the persisted phase (boot / re-apply)
#   axr-egress-phase.sh provision       real egress: masquerade LAN->WAN, as always
#   axr-egress-phase.sh measure         NO real egress: the sink is the only way off-twin
#   axr-egress-phase.sh --healthcheck   exit 0 only when the LIVE state matches the phase
#   axr-egress-phase.sh --show          phase, when it was entered, sink, mode
#
# DEFAULT CLOSED. No state file, an unreadable one or a garbage value all mean
# `measure`. A failed `provision` transition forces `measure` back on and exits
# non-zero. `measure` writes its state BEFORE applying, `provision` only AFTER a
# successful apply -- so every failure mode lands closed.
set -eu

AXR_DIR={dir}
AXR_CONF={conf}
AXR_STATE={state}
AXR_SINCE={state}-since
AXR_CIDRS={cidrs}
AXR_WAN_CACHE=$AXR_DIR/wan
AXR_TABLE={table}
AXR_PRIO_SINK_SELF=999
AXR_PRIO_MAIN=1000
AXR_PRIO_TABLE=1001
AXR_PROBE_DST={probe}

AXR_EGRESS_SINK=""
AXR_EGRESS_MODE=default
AXR_EGRESS_PHASE=""
if [ -r "$AXR_CONF" ]; then . "$AXR_CONF"; fi

axr_die() {{ echo "axr-egress: $*" >&2; exit 1; }}

# --- interfaces --------------------------------------------------------------
# The WAN is whichever interface carries the default route. It is CACHED on every
# successful detection: in `measure` the router's own egress is dropped, a DHCP
# lease can lapse and take the default route with it, and a boot-time re-apply
# that could not name the WAN would otherwise leave the twin OPEN.
axr_wan() {{
    _w=$(ip -4 route show default 2>/dev/null | awk '/default/ {{ print $5; exit }}')
    if [ -n "$_w" ]; then
        printf '%s\n' "$_w" > "$AXR_WAN_CACHE" 2>/dev/null || true
        printf '%s\n' "$_w"
        return 0
    fi
    if [ -r "$AXR_WAN_CACHE" ]; then cat "$AXR_WAN_CACHE"; fi
}}

# Every non-loopback interface with a global v4 address that is not the WAN.
axr_lan_ifaces() {{
    _w=$(axr_wan)
    ip -4 -o addr show scope global 2>/dev/null | awk '{{ print $2 }}' | sort -u |
    while read -r _i; do
        if [ "$_i" != "$_w" ]; then printf '%s\n' "$_i"; fi
    done
}}

# A host address ON a LAN leg's connected prefix that is NOT one of this router's
# own addresses and NOT the sink: the source the measure assertion pretends a LAN
# client used. Deterministic -- the lowest usable host that qualifies (a /31's
# both ends count, a /32 has none and prints nothing). The sink is excluded
# because rule 999 (`from <sink> lookup main`) would legitimately bypass table
# {table} for it.
axr_probe_src() {{
    _own=$(ip -4 -o addr show 2>/dev/null | awk '{{ split($4, a, "/"); printf "%s ", a[1] }}')
    ip -4 -o addr show dev "$1" scope global 2>/dev/null |
        awk -v own="$_own" -v sink="$AXR_EGRESS_SINK" '
            function toint(s,  p) {{ split(s, p, "."); return ((p[1] * 256 + p[2]) * 256 + p[3]) * 256 + p[4] }}
            function tostr(n) {{
                return sprintf("%d.%d.%d.%d", int(n / 16777216) % 256, int(n / 65536) % 256,
                               int(n / 256) % 256, n % 256)
            }}
            BEGIN {{ k = split(own, o, " "); for (i = 1; i <= k; i++) mine[o[i]] = 1; mine[sink] = 1 }}
            {{
                split($4, a, "/"); len = a[2] + 0
                if (len >= 32) next
                size = 2 ^ (32 - len); ip = toint(a[1]); net = ip - (ip % size)
                lo = net + 1; hi = net + size - 2
                if (len == 31) {{ lo = net; hi = net + 1 }}
                for (c = lo; c <= hi && c < lo + 256; c++) {{
                    s = tostr(c)
                    if (!(s in mine)) {{ print s; exit }}
                }}
            }}'
}}

# Which interface the sink is reachable on. Resolved from the FIB rather than
# configured, so a re-addressed segment cannot leave a stale `dev` behind.
axr_sink_dev() {{
    ip -4 route get "$AXR_EGRESS_SINK" 2>/dev/null |
        awk '{{ for (i = 1; i <= NF; i++) if ($i == "dev") {{ print $(i + 1); exit }} }}'
}}

axr_masq_present() {{
    if command -v nft >/dev/null 2>&1; then
        if nft list table ip axrnat 2>/dev/null | grep -q masquerade; then return 0; fi
    fi
    if command -v iptables >/dev/null 2>&1; then
        if iptables -t nat -S POSTROUTING 2>/dev/null | grep -q MASQUERADE; then return 0; fi
    fi
    return 1
}}

# --- firewall ----------------------------------------------------------------
# nft preferred, iptables as the fallback -- exactly the pair `configure-router`'s
# axr-router-nat.sh already chooses between, because the base debian-12-cloud image
# is not guaranteed to carry `nft` and apt is not reachable in `measure`.
#
# Both rule sets are rebuilt from scratch on every apply (nft: add-then-delete-then-
# define in ONE atomic -f batch; iptables: a private chain that is flushed and
# re-filled), so a re-apply is idempotent and never accumulates duplicates.

axr_fw_measure() {{
    _w=$1
    if command -v nft >/dev/null 2>&1; then
        nft -f - <<AXR_NFT_EOF
add table ip axregress
delete table ip axregress
table ip axregress {{
	chain forward {{
		type filter hook forward priority -5; policy accept;
		oifname "$_w" counter drop comment "axr-egress-measure-wan-out-drop"
		iifname "$_w" counter drop comment "axr-egress-measure-wan-in-drop"
	}}
	chain output {{
		type filter hook output priority -5; policy accept;
		oifname "$_w" udp dport 67 counter accept
		oifname "$_w" udp dport 68 counter accept
		oifname "$_w" ct state established,related counter accept
		oifname "$_w" counter drop comment "axr-egress-measure-wan-self-drop"
	}}
}}
AXR_NFT_EOF
        return 0
    fi
    command -v iptables >/dev/null 2>&1 ||
        axr_die "measure: neither nft nor iptables is present -- refusing to claim containment"
    iptables -N AXR_EGRESS_F 2>/dev/null || true
    iptables -F AXR_EGRESS_F
    iptables -C FORWARD -j AXR_EGRESS_F 2>/dev/null || iptables -I FORWARD 1 -j AXR_EGRESS_F
    iptables -A AXR_EGRESS_F -o "$_w" -j DROP
    iptables -A AXR_EGRESS_F -i "$_w" -j DROP
    iptables -N AXR_EGRESS_O 2>/dev/null || true
    iptables -F AXR_EGRESS_O
    iptables -C OUTPUT -j AXR_EGRESS_O 2>/dev/null || iptables -I OUTPUT 1 -j AXR_EGRESS_O
    iptables -A AXR_EGRESS_O -o "$_w" -p udp --dport 67 -j ACCEPT
    iptables -A AXR_EGRESS_O -o "$_w" -p udp --dport 68 -j ACCEPT
    iptables -A AXR_EGRESS_O -o "$_w" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
    iptables -A AXR_EGRESS_O -o "$_w" -j DROP
}}

# `provision` leaves an empty-but-PRESENT marker table rather than deleting
# everything: "the policy ran and chose to allow" and "nothing ever ran" must not
# look the same to a reader or to --healthcheck.
axr_fw_provision() {{
    if command -v nft >/dev/null 2>&1; then
        nft -f - <<AXR_NFT_EOF
add table ip axregress
delete table ip axregress
table ip axregress {{
	chain forward {{
		type filter hook forward priority -5; policy accept;
		counter comment "axr-egress-provision-allow"
	}}
}}
AXR_NFT_EOF
    fi
    if command -v iptables >/dev/null 2>&1; then
        iptables -N AXR_EGRESS_F 2>/dev/null || true
        iptables -F AXR_EGRESS_F
        iptables -N AXR_EGRESS_O 2>/dev/null || true
        iptables -F AXR_EGRESS_O
    fi
}}

axr_fw_measure_present() {{
    _w=$1
    if command -v nft >/dev/null 2>&1; then
        if nft list table ip axregress 2>/dev/null | grep -q axr-egress-measure-wan-out-drop; then
            return 0
        fi
    fi
    if command -v iptables >/dev/null 2>&1; then
        if iptables -S AXR_EGRESS_F 2>/dev/null | grep -q -- "-o $_w -j DROP"; then return 0; fi
    fi
    return 1
}}

# --- policy routing ----------------------------------------------------------
# THE INTEGRATION POINT THE SINK CANNOT SOLVE ITSELF. The sink accepts any
# destination that arrives on its LAN NIC, but it does not answer ARP for foreign
# addresses, so "everything external goes to the sink" has to be a NEXT-HOP ROUTE
# here.
#
# Three rules per LAN interface, in priority order:
#   999  from <sink>            -> main   : the sink's own traffic is never bounced
#                                           back at the sink (no routing loop).
#   1000 iif <lan> lookup main suppress_prefixlength 0
#                                        : internal destinations keep resolving
#                                          normally -- connected segments, static
#                                          routes, everything EXCEPT main's default
#                                          route, which is what we are replacing.
#   1001 iif <lan> lookup {table}        : whatever is left is external -> sink.
#
# Without the suppress rule at 1000, table {table}'s default route would swallow
# inter-segment LAN traffic too and the twin would route east-west through the sink.
axr_routes_clear() {{
    while ip rule del priority $AXR_PRIO_SINK_SELF 2>/dev/null; do :; done
    while ip rule del priority $AXR_PRIO_MAIN 2>/dev/null; do :; done
    while ip rule del priority $AXR_PRIO_TABLE 2>/dev/null; do :; done
    ip route flush table $AXR_TABLE 2>/dev/null || true
}}

axr_routes_measure() {{
    [ -n "$AXR_EGRESS_SINK" ] || axr_die "measure: no sink configured (AXR_EGRESS_SINK in $AXR_CONF)"
    _dev=$(axr_sink_dev)
    [ -n "$_dev" ] || axr_die "measure: sink $AXR_EGRESS_SINK is not reachable from this router"
    _w=$(axr_wan)
    if [ -n "$_w" ] && [ "$_dev" = "$_w" ]; then
        axr_die "measure: sink $AXR_EGRESS_SINK resolves out the WAN interface ($_w) -- the sink must sit on a twin LAN segment"
    fi
    axr_routes_clear
    if [ "$AXR_EGRESS_MODE" = cidrs ]; then
        [ -s "$AXR_CIDRS" ] || axr_die "measure: mode=cidrs but $AXR_CIDRS is missing or empty"
        # Narrow mode: only the observed external set reaches the sink. Anything
        # else falls through to main's default route and into the WAN DROP -- that
        # black hole IS the signal here, and it is why `default` is the default.
        grep -v '^#' "$AXR_CIDRS" | grep '[0-9]' | while read -r _c; do
            ip route replace "$_c" via "$AXR_EGRESS_SINK" dev "$_dev" table $AXR_TABLE
        done
    else
        # Wide (default): one route covers every observed address AND every
        # address no corpus ever saw. A real browser chases names the cast never
        # recorded; under this route they still find a server.
        ip route replace default via "$AXR_EGRESS_SINK" dev "$_dev" table $AXR_TABLE
    fi
    ip rule add from "$AXR_EGRESS_SINK" priority $AXR_PRIO_SINK_SELF lookup main
    for _if in $(axr_lan_ifaces); do
        ip rule add iif "$_if" priority $AXR_PRIO_MAIN lookup main suppress_prefixlength 0
        ip rule add iif "$_if" priority $AXR_PRIO_TABLE lookup $AXR_TABLE
    done
}}

# The destination the in-guest assertion probes. In `default` mode that is
# TEST-NET-3 -- an address NO cast observed -- so the assertion literally proves
# the unobserved-address guarantee. In `cidrs` mode only the listed set is served,
# so it probes the first listed prefix instead.
axr_probe_dst() {{
    if [ "$AXR_EGRESS_MODE" = cidrs ] && [ -s "$AXR_CIDRS" ]; then
        grep -v '^#' "$AXR_CIDRS" | grep -m1 '[0-9]' | awk -F/ '{{ print $1 }}'
    else
        printf '%s\n' "$AXR_PROBE_DST"
    fi
}}

# --- assertions (the --healthcheck body, reused by apply) --------------------
axr_assert_measure() {{
    _rc=0
    _w=$(axr_wan)
    if [ -n "$_w" ] && axr_fw_measure_present "$_w"; then
        echo "ok   wan drop on $_w"
    else
        echo "FAIL wan drop (real egress is NOT blocked)"; _rc=1
    fi
    if ip rule show 2>/dev/null | grep -q "lookup $AXR_TABLE"; then
        echo "ok   sink policy rule"
    else
        echo "FAIL sink policy rule (nothing steers external traffic at the sink)"; _rc=1
    fi
    if ip route show table $AXR_TABLE 2>/dev/null | grep -q '[0-9]'; then
        echo "ok   sink route table $AXR_TABLE"
    else
        echo "FAIL sink route table $AXR_TABLE is empty"; _rc=1
    fi
    # The load-bearing one: a FIB lookup as if FORWARDING an unobserved external
    # destination that arrived on a LAN leg. No packet is sent; the kernel answers
    # with the next hop it WOULD use, and it must be the sink -- on EVERY LAN leg,
    # not a sample: one leg missing its rules is one segment with real egress.
    # The source is a NON-local host address on that leg (axr_probe_src). The
    # router's OWN address is a martian source on input -- the kernel answers
    # `from <local addr> iif <leg>` with EINVAL whatever the policy is, so probing
    # with it failed on every router (corp-twin devlab live run 2).
    _dst=$(axr_probe_dst)
    _n=0
    for _lan in $(axr_lan_ifaces); do
        _src=$(axr_probe_src "$_lan")
        if [ -z "$_src" ]; then
            echo "skip $_lan: no non-local host address to probe from (a /32 leg)"
            continue
        fi
        _n=$((_n + 1))
        if ip route get "$_dst" iif "$_lan" from "$_src" 2>&1 | grep -q "via $AXR_EGRESS_SINK"; then
            echo "ok   $_dst from $_src iif $_lan -> sink $AXR_EGRESS_SINK"
        else
            echo "FAIL $_dst from $_src iif $_lan does NOT route to the sink $AXR_EGRESS_SINK"; _rc=1
        fi
    done
    if [ "$_n" -eq 0 ]; then
        echo "FAIL no LAN interface with an address to probe from"; _rc=1
    fi
    # Negative proof, best effort: the router's own packet out the WAN must not
    # get through. Reported, never silently skipped.
    if command -v ping >/dev/null 2>&1; then
        if ping -c 1 -W 2 "$AXR_PROBE_DST" >/dev/null 2>&1; then
            echo "FAIL router reached $AXR_PROBE_DST -- real egress is alive"; _rc=1
        else
            echo "ok   router cannot reach $AXR_PROBE_DST"
        fi
    else
        echo "skip router egress probe (no ping installed)"
    fi
    return $_rc
}}

axr_assert_provision() {{
    _rc=0
    _w=$(axr_wan)
    if [ -n "$_w" ]; then
        echo "ok   wan $_w"
    else
        echo "FAIL no WAN default route"; _rc=1
    fi
    if axr_masq_present; then
        echo "ok   LAN->WAN masquerade"
    else
        echo "FAIL no LAN->WAN masquerade (apt will not work) -- configure-router must have run first"; _rc=1
    fi
    if [ -n "$_w" ] && axr_fw_measure_present "$_w"; then
        echo "FAIL measure-phase WAN drop is still installed"; _rc=1
    else
        echo "ok   no wan drop"
    fi
    if ip rule show 2>/dev/null | grep -q "lookup $AXR_TABLE"; then
        echo "FAIL sink policy rule is still installed"; _rc=1
    else
        echo "ok   no sink policy rule"
    fi
    return $_rc
}}

# --- apply -------------------------------------------------------------------
axr_apply_measure() {{
    _w=$(axr_wan)
    [ -n "$_w" ] || axr_die "measure: cannot identify the WAN interface (no default route, no cached name)"
    # DROP FIRST, routes second: if anything below fails we are CLOSED, never open.
    axr_fw_measure "$_w"
    axr_routes_measure
    axr_assert_measure || axr_die "measure: applied, but the live state does not assert"
}}

axr_apply_provision() {{
    # Re-assert configure-router's own NAT (idempotent) before clearing anything:
    # a reboot in `measure` can have left it unapplied.
    if [ -x /usr/local/sbin/axr-router-nat.sh ]; then /usr/local/sbin/axr-router-nat.sh; fi
    axr_routes_clear
    axr_fw_provision
    axr_assert_provision || axr_die "provision: masquerade egress did not come back"
}}

# `phase-since` is "when this phase was ENTERED", not "when it was last applied":
# a boot-time re-apply of the same phase must not move it, because that timestamp
# is what a capture window is labelled against (a `provision`-phase window is
# uncontained and must never be scored).
axr_record() {{
    mkdir -p "$AXR_DIR"
    if _cur=$(axr_persisted_phase) && [ "$_cur" = "$1" ] && [ -s "$AXR_SINCE" ]; then
        return 0
    fi
    printf '%s\n' "$1" > "$AXR_STATE"
    date -u +%Y-%m-%dT%H:%M:%SZ > "$AXR_SINCE" 2>/dev/null || true
}}

axr_persisted_phase() {{
    [ -r "$AXR_STATE" ] || return 1
    read -r _p < "$AXR_STATE" || return 1
    case "$_p" in
        provision|measure) printf '%s\n' "$_p" ;;
        *) return 1 ;;
    esac
}}

# Precedence: argv, then the persisted state (a flip survives a reboot), then the
# conf's initial phase, then DEFAULT CLOSED.
axr_resolve_phase() {{
    if [ -n "${{1:-}}" ]; then printf '%s\n' "$1"; return 0; fi
    if _p=$(axr_persisted_phase); then printf '%s\n' "$_p"; return 0; fi
    case "${{AXR_EGRESS_PHASE:-}}" in
        provision|measure) printf '%s\n' "$AXR_EGRESS_PHASE" ;;
        *) printf '%s\n' "measure" ;;
    esac
}}

AXR_CMD="${{1:-}}"

case "$AXR_CMD" in
    --show)
        _p=$(axr_resolve_phase "")
        _since=""
        if [ -r "$AXR_SINCE" ]; then read -r _since < "$AXR_SINCE" || true; fi
        echo "phase=$_p since=${{_since:-unknown}} sink=${{AXR_EGRESS_SINK:-none}} mode=$AXR_EGRESS_MODE"
        exit 0
        ;;
    --healthcheck)
        _p=$(axr_resolve_phase "")
        echo "phase=$_p"
        if [ "$_p" = provision ]; then
            # Subshell: an assertion's `exit` must not skip the summary line.
            if ( axr_assert_provision ); then exit 0; else exit 1; fi
        fi
        if ( axr_assert_measure ); then exit 0; else exit 1; fi
        ;;
    provision)
        # Only phase that can OPEN egress, so it is the only one that records
        # itself AFTER the fact. A failed transition is forced back to `measure`.
        if ( axr_apply_provision ); then
            axr_record provision
            echo "{ok} phase=provision"
        else
            axr_record measure
            ( axr_apply_measure ) || true
            axr_die "provision transition failed -- forced back to measure"
        fi
        ;;
    measure|"")
        _p=$(axr_resolve_phase "$AXR_CMD")
        if [ "$_p" = provision ]; then
            # A bare re-apply (boot) of a persisted `provision`.
            if ( axr_apply_provision ); then
                echo "{ok} phase=provision"
            else
                axr_record measure
                ( axr_apply_measure ) || true
                axr_die "provision re-apply failed -- forced back to measure"
            fi
        else
            axr_record measure
            axr_apply_measure
            echo "{ok} phase=measure"
        fi
        ;;
    *)
        axr_die "usage: axr-egress-phase.sh [provision|measure|--healthcheck|--show]"
        ;;
esac
"""


_UNIT_TEMPLATE = """[Unit]
Description=axRange router egress phase policy ({engine})
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart={engine}

[Install]
WantedBy=multi-user.target
"""


def render_engine() -> str:
    """The `/usr/local/sbin/axr-egress-phase.sh` body. Constant — per-twin facts
    live in the conf file, so this is byte-identical on every router."""
    return _ENGINE_TEMPLATE.format(
        dir=EGRESS_DIR,
        conf=EGRESS_CONF,
        state=EGRESS_STATE,
        cidrs=EGRESS_CIDRS,
        table=EGRESS_TABLE,
        probe=EGRESS_PROBE_DST,
        ok=EGRESS_OK_MARKER,
    )


def render_conf(egress: Mapping[str, Any]) -> str:
    """`/etc/axr-egress/egress.conf` — the per-twin data the engine sources."""
    ctx = normalize_egress(egress)
    assert ctx is not None  # normalize_egress only returns None for a None spec
    return (
        "# axRange router egress phase policy -- generated, do not hand-edit.\n"
        "# Read by /usr/local/sbin/axr-egress-phase.sh. AXR_EGRESS_PHASE is only\n"
        "# the INITIAL phase; once a phase has been set, /etc/axr-egress/phase wins.\n"
        f"AXR_EGRESS_SINK={ctx['sink_ip']}\n"
        f"AXR_EGRESS_MODE={ctx['mode']}\n"
        f"AXR_EGRESS_PHASE={ctx['phase']}\n"
    )


def render_cidrs(egress: Mapping[str, Any]) -> str:
    """`/etc/axr-egress/external-cidrs.txt` — the narrow mode's prefix list."""
    ctx = normalize_egress(egress)
    assert ctx is not None
    header = (
        "# axRange router egress policy -- the external prefix set, one CIDR per\n"
        "# line (scripts/gen_sink_corpus.py). Only read in `mode: cidrs`.\n"
    )
    return header + "".join(f"{c}\n" for c in ctx["cidrs"])


def render_install(egress: Mapping[str, Any], *, shebang: bool = True) -> str:
    """One self-contained in-guest script: lay down the engine + conf (+ the
    prefix list in `cidrs` mode) + the boot unit, then apply the phase.

    Idempotent by construction — every write is a full overwrite, `systemctl
    enable` is a no-op when already enabled, and the engine rebuilds its whole
    rule set on each apply. Re-running it is the supported way to re-assert.

    Used by BOTH delivery paths: `op_configure_router` writes it over QGA, and
    `scripts/gen_egress_policy.py` writes it into a twin's `scripts/` directory as
    an `install: [{script: ...}]` payload (existing IR only — no schema change).
    """
    ctx = normalize_egress(egress)
    if ctx is None:
        raise EgressRenderError("render_install needs an egress policy, got None")

    parts: list[str] = []
    if shebang:
        parts.append("#!/bin/sh")
    parts.append(
        "# axRange router egress phase policy INSTALLER -- generated by\n"
        "# axrange/adapter_proxmox/egress_render.py. Do not hand-edit.\n"
        f"# phase={ctx['phase']} sink={ctx['sink_ip']} mode={ctx['mode']}\n"
        "set -eu\n"
        "umask 022"
    )
    parts.append(f"mkdir -p {EGRESS_DIR}")
    parts.append(
        f"cat > {EGRESS_ENGINE} <<'AXR_EGRESS_ENGINE_EOF'\n"
        f"{render_engine()}"
        "AXR_EGRESS_ENGINE_EOF"
    )
    parts.append(f"chmod 0755 {EGRESS_ENGINE}")
    parts.append(
        f"cat > {EGRESS_CONF} <<'AXR_EGRESS_CONF_EOF'\n"
        f"{render_conf(ctx)}"
        "AXR_EGRESS_CONF_EOF"
    )
    if ctx["mode"] == "cidrs":
        parts.append(
            f"cat > {EGRESS_CIDRS} <<'AXR_EGRESS_CIDRS_EOF'\n"
            f"{render_cidrs(ctx)}"
            "AXR_EGRESS_CIDRS_EOF"
        )
    parts.append(
        f"cat > {EGRESS_UNIT} <<'AXR_EGRESS_UNIT_EOF'\n"
        f"{_UNIT_TEMPLATE.format(engine=EGRESS_ENGINE)}"
        "AXR_EGRESS_UNIT_EOF"
    )
    # Reboot-persistent, LES-013's shape: the phase is runtime state (nft rules, ip
    # rules, a routing table), so it has to be re-applied at every boot or a
    # `measure` twin reboots itself back onto the real internet.
    parts.append("systemctl daemon-reload >/dev/null 2>&1 || true")
    parts.append("systemctl enable axr-egress-phase.service >/dev/null 2>&1 || true")
    parts.append(f"{EGRESS_ENGINE} {ctx['phase']}")
    return "\n".join(parts) + "\n"


def phase_set_command(phase: Any) -> str:
    """The in-guest command that flips a standing router's phase (for whoever owns
    the flip verb — an `o2control` playout or a `run-script`). Validates first, so
    a typo never reaches the guest."""
    return f"{EGRESS_ENGINE} {normalize_phase(phase)}"


def healthcheck_command() -> str:
    """The in-guest assertion command: exit 0 only when the LIVE router state
    matches the persisted phase."""
    return f"{EGRESS_ENGINE} --healthcheck"
