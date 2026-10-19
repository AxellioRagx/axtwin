#!/bin/bash
# corp-twin -- ONE parameterized recipe for every L2 switch device VM (MX85's
# switch side, MS120, the access switches, the 3850, the 8325h VSX pair):
# a VLAN-aware Linux bridge with 802.1Q trunks, kernel STP and admin-down
# ports. iproute2 only -- no apt, so it needs no egress and runs in any phase.
# Design and the device/VLAN table's sources: docs/WORK_ORDER_corp_twin.md
# §5.3 / §10 (P2).
#
# **Which device am I.** `run-script` delivers one script with no arguments, so
# the device is `hostname -s` (cloud-init sets it to the node id); the table
# below is keyed by it. `AXR_L2_DEVICE=<id>` overrides (a hand re-run).
#
# **Which NIC is which leg.** The table lists a device's legs IN THE ORDER the
# twin config declares them (== PVE `netN` order, == the planner's NIC index).
# An L2 leg has no address to find it by, so NICs are ordered by their PCI
# path: PVE places net0-net5 at bus 0 slots 0x12-0x17 and net6+ behind the
# pci.1 bridge (bus 0 slot 0x1e), so sorting `/sys/class/net/*/device`'s
# resolved path IS netN order. The mapping (leg -> netN -> iface -> MAC) is
# printed on every apply: the live-verify runbook (§11 D2) compares it with
# `qm config <vmid>`. A NIC count that differs from the table is a hard error
# -- a shifted table would bridge the wrong segments together.
#
# **Loop-storm guard.** The bridge is created with STP ON (`stp_state 1`) and
# brought up BEFORE any port is enslaved, so every port enters the STP
# listening state and the redundant SFP25/SFP26 pair can never forward a
# broadcast in a loop while the topology converges. Never reorder this.
#
# **VLAN 1 is a real VLAN here** (the Meraki/Cisco native VLAN), so the
# bridge's automatic PVID 1 is switched OFF (`vlan_default_pvid 0`) and every
# membership is explicit.
#
# Table grammar, one port per line, in leg order:
#   <leg-segment>  access:<vid>                    [flags]
#   <leg-segment>  trunk:<native-vid|->:<vid,...>  [flags]
# flags: cost=<n> (STP path cost) | down (admin-down, the port stays enslaved
#        but its link is DOWN -- the hwlab2960s P17 case) | mgmt (the NIC's
#        cloud-init address moves to an SVI on the port's access VLAN -- the
#        MS120's management IP).
# Bridge line: `bridge prio=<n>` (STP bridge priority; lowest is root).
#
# **Ports carry no address, ever.** Per port: IPv6 off (no link-local, no
# SLAAC), any dhclient on it killed, and the guest's own network config for
# it (an ifupdown stanza, a cloud-init netplan entry) rewritten to "no
# address" so the next boot does not DHCP it either; cloud-init is told not to
# re-render the network config. An address left on a bridge port is a host
# on that segment the twin never declared. Asserted before OK. A rewritten
# config is checked with its own parser (`ifquery`, `netplan generate`) in
# the same apply -- an unparsable one would lose every interface at boot.
#
# **The running network daemon must LOAD the rewrite before a port is
# touched.** Rewriting the file is not enough: systemd-networkd (Debian 12
# cloud images: netplan -> networkd) keeps the .network it read at boot in
# memory, and re-applies that static address on every carrier-up -- which
# enslaving a port causes (down, master, up). So every port's config is
# rewritten FIRST (pass 1), then `netplan generate` + `networkctl reload`
# (fail loud), and only then are ports flushed and enslaved (pass 2). A
# netplan port is also marked `optional: true` so boot never waits on it.
# Measured on the corp-twin live run 1 (2026-09-26): the MS120's mgmt NIC got
# 10.180.16.15/20 back from networkd seconds after the flush, and the final
# no-address assertion failed all three attempts (docs/WORK_ORDER_corp_twin.md
# §11.D).
#
# **OK means converged.** After the ports join, the script waits (bounded,
# 45 s) until every port that is not admin-down reaches an STP end state --
# `forwarding` or `blocking`, or `disabled` with no carrier (the peer is
# down; logged) -- and dies otherwise: a switch still in
# listening/learning forwards nothing, and a host recipe gated on this one
# (the planner's forwarder edges) would race it.
#
# Re-applied at boot by `axr-l2switch.service` (the LES-013 pattern: a
# runtime-only config dies with the first reboot). Idempotent: an existing
# bridge is torn down first -- ports un-enslaved forward nothing, so the
# rebuild cannot loop either.
set -eu

DEV="${AXR_L2_DEVICE:-$(hostname -s)}"
BR=br0

axr_table() {
    case "$1" in
    # ---- the MX85's switch side: one untagged port per VLAN to the MX85's
    #      L3 engine (hq-mx), and the SFP25/SFP26 uplinks to the MS120.
    #      STP ROOT (the real MX85 is the root of this tree).
    mx-sw) cat <<'EOT'
bridge prio=4096
mx-vlan1     access:1
mx-vlan20    access:20
mx-vlan32    access:32
mx-vlan40    access:40
mx-vlan48    access:48
mx-vlan52    access:52
mx-vlan64    access:64
mx-vlan192   access:192
mx-vlan880   access:880
mx-vlan1010  access:1010
mx-vlan935   access:935
sfp25        trunk:1:20,32,40,48,52,64,192,880,1010,935  cost=100
sfp26        trunk:1:20,32,40,48,52,64,192,880,1010,935  cost=200
EOT
    ;;
    # ---- MS120-24P "Telstar-Switch": core L2. SFP26 costs more, so it is the
    #      port STP BLOCKS here (the real SFP26 is STP-blocked). Management IP
    #      10.180.16.15 lives on a VLAN20 SVI (conflict C2). P17 to the
    #      hwlab2960s is administratively DOWN, as in the diagram.
    ms120) cat <<'EOT'
bridge prio=8192
vlan20            access:20    mgmt
sfp25             trunk:1:20,32,40,48,52,64,192,880,1010,935  cost=100
sfp26             trunk:1:20,32,40,48,52,64,192,880,1010,935  cost=200
t-ms120-sgs5240   trunk:1:64
t-ms120-axeprod   trunk:1:48,52
t-ms120-cos       trunk:1:40
t-ms120-labsw     trunk:1:192
t-ms120-3850      trunk:1:935
p17-hwlab2960s    access:32    down
vlan1             access:1
EOT
    ;;
    sgs-5240) cat <<'EOT'
bridge prio=32768
t-ms120-sgs5240   trunk:1:64
vlan64            access:64
EOT
    ;;
    axeprodsw) cat <<'EOT'
bridge prio=32768
t-ms120-axeprod   trunk:1:48,52
vlan48            access:48
vlan52            access:52
EOT
    ;;
    cos-sw-r32e01) cat <<'EOT'
bridge prio=32768
t-ms120-cos       trunk:1:40
vlan40            access:40
EOT
    ;;
    labswitch) cat <<'EOT'
bridge prio=32768
t-ms120-labsw     trunk:1:192
vlan192           access:192
EOT
    ;;
    hwlab2960s) cat <<'EOT'
bridge prio=32768
p17-hwlab2960s    access:32
vlan32            access:32
EOT
    ;;
    # ---- Catalyst 3850: distribution for the TOR pair, one link to each 9K
    #      (the vPC analogue -- two access links, not an MLAG bundle).
    sw-3850) cat <<'EOT'
bridge prio=16384
t-ms120-3850      trunk:1:935
x3850-9k01        access:935
x3850-9k02        access:935
EOT
    ;;
    # ---- eng-8325h VSX pair (L2 for VLAN953): the ISL joins them; the
    #      servers hang off the left member.
    8325h-l) cat <<'EOT'
bridge prio=16384
x9k01-8325l       access:953
isl-8325          access:953
vlan953           access:953
EOT
    ;;
    8325h-r) cat <<'EOT'
bridge prio=20480
x9k02-8325r       access:953
isl-8325          access:953
EOT
    ;;
    *) return 1 ;;
    esac
}

log() { echo "axr-l2switch[$DEV]: $*"; }
die() { echo "axr-l2switch[$DEV]: FAIL $*" >&2; exit 1; }

TABLE=$(axr_table "$DEV") || die "no port table for device '$DEV' (set AXR_L2_DEVICE?)"

# NICs in netN order (see the header): every non-loopback, non-virtual netdev
# with a PCI device, sorted by the device's resolved sysfs path.
mapfile -t NICS < <(
    for d in /sys/class/net/*; do
        [ -e "$d/device" ] || continue
        printf '%s %s\n' "$(readlink -f "$d/device")" "$(basename "$d")"
    done | sort | awk '{ print $2 }'
)
mapfile -t PORTS < <(printf '%s\n' "$TABLE" | awk 'NF && $1 != "bridge"')
[ "${#NICS[@]}" -eq "${#PORTS[@]}" ] ||
    die "the VM has ${#NICS[@]} NICs (${NICS[*]}) but the table lists ${#PORTS[@]} ports"

PRIO=$(printf '%s\n' "$TABLE" | awk '$1 == "bridge" { for (i = 2; i <= NF; i++) if ($i ~ /^prio=/) { sub(/^prio=/, "", $i); print $i } }')
PRIO=${PRIO:-32768}

# The management address (a `mgmt` port's cloud-init address) and default
# route are read BEFORE any port is enslaved -- enslaving flushes them.
MGMT_ADDR=""; MGMT_VID=""; MGMT_GW=""
for i in "${!PORTS[@]}"; do
    set -- ${PORTS[$i]}
    case " $* " in *" mgmt "*)
        nic=${NICS[$i]}
        MGMT_ADDR=$(ip -4 -o addr show dev "$nic" scope global | awk '{ print $4; exit }')
        MGMT_GW=$(ip -4 route show default dev "$nic" | awk '{ print $3; exit }')
        MGMT_VID=${2#access:}
        # A persisted SVI from an earlier apply already holds it.
        if [ -z "$MGMT_ADDR" ] && [ -r /etc/axr-l2switch/mgmt ]; then
            read -r MGMT_ADDR MGMT_GW < /etc/axr-l2switch/mgmt || true
        fi
        ;;
    esac
done

# ---- the guest's own network config must not address a port on next boot --
# ifupdown (Debian cloud images: /etc/network/interfaces{,.d/*}) and netplan
# (cloud-init's 50-cloud-init.yaml): rewrite the port's entry to "no address".
# python3 is present wherever cloud-init is. Idempotent. ifupdown has no
# end-of-line comments (`iface X inet manual # ...` is "too many parameters"
# and takes the whole file down at boot), so the marker is its own line.
# AXR_L2_ROOT prefixes every path (tests only; unset on a guest).
AXR_IFACE_MARK="# axr-l2switch: bridge port"
axr_neutralise_port_config() {  # <nic> <mac>
    AXR_IFACE_MARK="$AXR_IFACE_MARK" python3 - "$1" "$2" <<'EOPY'
import glob, os, re, sys
nic, mac = sys.argv[1], sys.argv[2].lower()
root, mark = os.environ.get("AXR_L2_ROOT", ""), os.environ["AXR_IFACE_MARK"]
changed = []
# ifupdown: `iface <nic> inet|inet6 <method>` + its indented options -> a bare
# `iface <nic> <fam> manual`, the marker on the line before it.
for path in [root + "/etc/network/interfaces"] + \
        sorted(glob.glob(root + "/etc/network/interfaces.d/*")):
    if not os.path.isfile(path):
        continue
    out, skip, dirty = [], False, False
    for line in open(path).read().splitlines(keepends=True):
        m = re.match(r"\s*iface\s+(\S+)\s+(inet6?)\s+(\S+)", line)
        if m:
            skip = m.group(1) == nic
            if skip:
                new = f"iface {nic} {m.group(2)} manual\n"
                dirty |= new != line
                if not (out and out[-1].strip() == mark):
                    out.append(mark + "\n")
                    dirty = True
                out.append(new)
                continue
        elif skip and line[:1] in (" ", "\t") and line.strip():
            if not line.lstrip().startswith("#"):
                dirty = True
                out.append("#axr-l2switch# " + line.lstrip())
                continue
        elif line.strip():
            skip = False
        out.append(line)
    if dirty:
        open(path, "w").write("".join(out))
        changed.append(path)
# netplan: the ethernets entry matching this NIC (by key, set-name or MAC).
try:
    import yaml
except ImportError:
    yaml = None
if yaml:
    # YAML 1.1 reads an unquoted all-digit MAC (52:54:00:00:00:01) as a
    # base-60 INT, and re-dumping the file would then write every such MAC
    # -- this port's or ANOTHER NIC's -- back as a number netplan rejects.
    # A MAC-shaped scalar resolves to a string first.
    class _Loader(yaml.SafeLoader):
        pass
    _mac = re.compile(r"^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}$")
    _Loader.add_implicit_resolver("tag:yaml.org,2002:str", _mac, list("0123456789abcdefABCDEF"))
    for _ch in "0123456789abcdefABCDEF":
        _rs = _Loader.yaml_implicit_resolvers[_ch]
        _rs.insert(0, _rs.pop())
for path in sorted(glob.glob(root + "/etc/netplan/*.yaml")) if yaml else []:
    doc = yaml.load(open(path), Loader=_Loader) or {}
    eths = (doc.get("network") or {}).get("ethernets") or {}
    dirty = False
    for key, cfg in eths.items():
        cfg = cfg or {}
        match = cfg.get("match") or {}
        if nic not in (key, cfg.get("set-name")) and \
                str(match.get("macaddress", "")).lower() != mac:
            continue
        for k in ("addresses", "gateway4", "gateway6", "routes", "nameservers"):
            dirty |= cfg.pop(k, None) is not None
        for k in ("dhcp4", "dhcp6", "accept-ra"):
            dirty |= cfg.get(k) is not False
            cfg[k] = False
        if cfg.get("link-local") != []:
            cfg["link-local"], dirty = [], True
        # RequiredForOnline=no: an address-less port never reaches networkd's
        # "configured", and wait-online would stall the boot on it.
        if cfg.get("optional") is not True:
            cfg["optional"], dirty = True, True
        eths[key] = cfg
    if dirty:
        open(path, "w").write(yaml.safe_dump(doc, default_flow_style=False))
        os.chmod(path, 0o600)
        changed.append(path)
print(" ".join(changed))
EOPY
}

# Validate a rewrite with the tool's own parser, in the same apply: a config
# the boot-time networking cannot parse loses every interface, mgmt included.
axr_check_port_config() {  # <changed paths>
    case " $* " in *" /etc/network/"*)
        if command -v ifquery >/dev/null 2>&1; then
            ifquery -a --list >/dev/null ||
                die "ifupdown cannot parse the rewritten config ($*)"
        elif command -v ifup >/dev/null 2>&1; then
            ifup --no-act -a >/dev/null ||
                die "ifupdown cannot parse the rewritten config ($*)"
        fi
        ;;
    esac
    case " $* " in *" /etc/netplan/"*)
        if command -v netplan >/dev/null 2>&1; then
            netplan generate || die "netplan cannot parse the rewritten config ($*)"
        fi
        ;;
    esac
}

# Make the RUNNING network daemon load the rewritten configs (see the header):
# netplan renders /run/systemd/network, networkd re-reads it. Unconditional
# (cheap, idempotent): an earlier apply may have rewritten the file and died
# before the reload, leaving networkd on the boot-time config.
axr_reload_network_daemon() {
    if systemctl is-active --quiet systemd-networkd 2>/dev/null; then
        if command -v netplan >/dev/null 2>&1; then
            netplan generate || die "netplan generate failed"
        fi
        networkctl reload ||
            die "networkctl reload failed -- networkd would re-address a bridge port on carrier-up"
        log "systemd-networkd reloaded (ports' no-address config is live)"
    fi
}

# ---- teardown of a previous apply (idempotent re-run / boot re-apply) ------
if ip link show "$BR" >/dev/null 2>&1; then
    log "removing the previous $BR"
    ip link set "$BR" down || true
    ip link del "$BR"
fi

# ---- the bridge: STP ON and UP before any port joins (loop-storm guard) ----
ip link add "$BR" type bridge stp_state 1 vlan_filtering 1 vlan_default_pvid 0 \
    priority "$PRIO" forward_delay 400 hello_time 200 max_age 2000
ip link set "$BR" up
[ "$(cat /sys/class/net/$BR/bridge/stp_state)" != 0 ] || die "STP did not enable on $BR"

# ---- pass 1: every port's own network config -> no address, then LOADED ----
# Before any port is flushed or enslaved (see the header): a daemon still on
# the boot-time config would put the address straight back on carrier-up.
for i in "${!PORTS[@]}"; do
    set -- ${PORTS[$i]}
    nic=${NICS[$i]}
    mac=$(cat "/sys/class/net/$nic/address")
    fixed=$(axr_neutralise_port_config "$nic" "$mac") ||
        die "port $1 ($nic): could not neutralise its network config"
    if [ -n "$fixed" ]; then
        log "  $nic: network config set to no-address in $fixed"
        axr_check_port_config $fixed
    fi
done
axr_reload_network_daemon

# ---- pass 2: flush + enslave --------------------------------------------------
for i in "${!PORTS[@]}"; do
    set -- ${PORTS[$i]}
    seg=$1; mode=$2; shift 2
    nic=${NICS[$i]}
    mac=$(cat "/sys/class/net/$nic/address")
    cost=""; down=0; DOWN[$i]=0
    for f in "$@"; do
        case "$f" in
            cost=*) cost=${f#cost=} ;;
            down) down=1; DOWN[$i]=1 ;;
            mgmt) ;;
            *) die "port $seg: unknown flag '$f'" ;;
        esac
    done
    log "net$i $nic $mac -> $seg $mode${cost:+ cost=$cost}$([ $down = 1 ] && echo ' ADMIN-DOWN')"
    # No address on a port, now or after a reboot: kill a lease client
    # BEFORE the flush (it would re-add), drop IPv6 (link-local, SLAAC). The
    # guest's own config for the NIC was rewritten and loaded in pass 1.
    pkill -f "dhclient.*\b$nic\b" || true
    sysctl -qw "net.ipv6.conf.$nic.disable_ipv6=1"
    ip -4 addr flush dev "$nic" || true
    ip link set "$nic" down
    ip link set "$nic" master "$BR"
    [ -z "$cost" ] || ip link set "$nic" type bridge_slave cost "$cost"
    case "$mode" in
        access:*)
            bridge vlan add dev "$nic" vid "${mode#access:}" pvid untagged
            ;;
        trunk:*)
            rest=${mode#trunk:}; native=${rest%%:*}; tagged=${rest#*:}
            [ "$native" = "-" ] || bridge vlan add dev "$nic" vid "$native" pvid untagged
            for v in ${tagged//,/ }; do bridge vlan add dev "$nic" vid "$v"; done
            ;;
        *) die "port $seg: bad mode '$mode'" ;;
    esac
    if [ "$down" = 1 ]; then
        ip link set "$nic" down
    else
        ip link set "$nic" up
    fi
done

# ---- management SVI -------------------------------------------------------
if [ -n "$MGMT_ADDR" ]; then
    bridge vlan add dev "$BR" vid "$MGMT_VID" self
    ip link add link "$BR" name "$BR.$MGMT_VID" type vlan id "$MGMT_VID"
    ip addr add "$MGMT_ADDR" dev "$BR.$MGMT_VID"
    ip link set "$BR.$MGMT_VID" up
    [ -z "$MGMT_GW" ] || ip route replace default via "$MGMT_GW" dev "$BR.$MGMT_VID"
    mkdir -p /etc/axr-l2switch
    printf '%s %s\n' "$MGMT_ADDR" "$MGMT_GW" > /etc/axr-l2switch/mgmt
    log "mgmt $MGMT_ADDR on $BR.$MGMT_VID${MGMT_GW:+ via $MGMT_GW}"
fi

# ---- boot persistence -----------------------------------------------------
self=$(readlink -f "$0" 2>/dev/null || true)
if [ -n "$self" ] && [ -f "$self" ] && [ "$self" != /usr/local/sbin/axr-l2switch.sh ]; then
    install -m 0755 "$self" /usr/local/sbin/axr-l2switch.sh
    mkdir -p /etc/axr-l2switch
    printf 'AXR_L2_DEVICE=%s\n' "$DEV" > /etc/axr-l2switch/device.env
    cat > /etc/systemd/system/axr-l2switch.service <<'EOU'
[Unit]
Description=axRange corp-twin L2 switch (VLAN-aware bridge, STP)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
EnvironmentFile=/etc/axr-l2switch/device.env
ExecStart=/usr/local/sbin/axr-l2switch.sh

[Install]
WantedBy=multi-user.target
EOU
    systemctl daemon-reload >/dev/null 2>&1 || true
    systemctl enable axr-l2switch.service >/dev/null 2>&1 || true
fi

# Persist the per-port IPv6-off (the boot re-apply sets it again anyway, but
# this closes the window between the NIC appearing and the service running)
# and stop cloud-init re-rendering the network config it wrote on first boot.
: > /etc/sysctl.d/90-axr-l2switch.conf
for nic in "${NICS[@]}"; do
    echo "net.ipv6.conf.$nic.disable_ipv6 = 1" >> /etc/sysctl.d/90-axr-l2switch.conf
done
if [ -d /etc/cloud/cloud.cfg.d ]; then
    echo "network: {config: disabled}" > /etc/cloud/cloud.cfg.d/99-axr-l2switch-network.cfg
fi

# ---- STP convergence: every live port forwarding or blocking, bounded -----
# A port with no carrier (its peer VM not up yet, or its peer's port
# admin-down) sits `disabled`: that is settled, not pending -- logged once.
deadline=$((SECONDS + 45)); nocarrier=""
while :; do
    pending=""
    for i in "${!PORTS[@]}"; do
        [ "${DOWN[$i]}" = 1 ] && continue
        nic=${NICS[$i]}
        st=$(bridge link show dev "$nic" 2>/dev/null | sed -n 's/.* state \([a-z]*\).*/\1/p')
        case "$st" in
            forwarding|blocking) ;;
            disabled)
                if [ "$(cat "/sys/class/net/$nic/carrier" 2>/dev/null)" = 0 ]; then
                    case " $nocarrier " in *" $nic "*) ;; *)
                        nocarrier="$nocarrier $nic"
                        log "  $nic: no carrier (peer down) -- STP state disabled, accepted" ;;
                    esac
                else
                    pending="$pending $nic=$st"
                fi
                ;;
            *) pending="$pending $nic=${st:-?}" ;;
        esac
    done
    [ -n "$pending" ] || break
    [ "$SECONDS" -lt "$deadline" ] ||
        die "STP did not converge within 45 s (ports not forwarding/blocking:$pending)"
    sleep 1
done

# ---- no port carries an address (either family) ----------------------------
for nic in "${NICS[@]}"; do
    addrs=$(ip -o addr show dev "$nic" | awk '{ print $4 }' | tr '\n' ' ')
    [ -z "${addrs// /}" ] ||
        die "bridge port $nic still has an address: $addrs (re-added by the network daemon? networkctl status $nic)"
done

log "bridge $BR prio=$PRIO stp=on ports=${#PORTS[@]} converged"
bridge -d link show 2>/dev/null | sed "s/^/axr-l2switch[$DEV]:   /" || true
echo "AXR_L2SWITCH_OK device=$DEV ports=${#PORTS[@]}"
