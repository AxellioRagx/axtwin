#!/bin/bash
# corp-twin -- the 9K01/9K02 L3 TOR pair (HSRP analogue: keepalived VRRP).
# Design: docs/WORK_ORDER_corp_twin.md §10 (P1).
#
# Each 9K is a Debian `kind: host` VM with two addressed legs:
#   leg 0 -- VLAN935, the transit to the MX85 (hq-mx is .1 there; the
#            planner gives THIS leg the host's one default route),
#   leg 1 -- VLAN953, the server VLAN behind the eng-8325h pair.
# The pair runs TWO VRRP instances in ONE sync group, so the VLAN935 VIP (the
# next hop hq-mx routes 10.180.34.0/25 to -- `serve-hq-mx-routes.sh`) and the
# VLAN953 VIP (the servers' default gateway, .1) always move TOGETHER: a split
# (935 VIP on one box, 953 VIP on the other) would route return traffic
# through the backup, which has no path it is not also forwarding for.
#
# **Which device am I.** `hostname -s` (cloud-init sets it to the node id);
# `AXR_VRRP_DEVICE=<id>` overrides. Interfaces are found by ADDRESS (both
# legs are addressed), never by name.
#
# **Why not VyOS.** An internal VyOS router needs a no-WAN router mode and
# VRRP inputs on `configure-router` -- locked IR_SCHEMA/OP_CATALOG changes
# (work order §12 D1). This is the implementation substitution: same wire
# behaviour (VRRPv2 adverts to 224.0.0.18, gratuitous ARP on failover), no
# contract change. Reversible: swap the image + recipe when D1 lands.
#
# **Pair health is REPORTED, not assumed.** After keepalived starts, the
# recipe waits for each instance's VRRP state (from keepalived's own `notify`
# hook) to SETTLE -- both known, agreeing, unchanged for longer than the
# master-down interval, bounded 30 s -- pings the peer on both legs (bounded;
# the peer may still be booting -- a WARN, not a failure), re-reads the
# settled state, and ARP-probes each VIP: a MASTER that gets an ARP answer
# for its own VIP from another box, or a BACKUP for whose VIP nobody answers,
# is a split pair and is warned about LOUDLY (a split sends return traffic
# through a box that is not forwarding for it). The one hard failure is a
# split sync group ON THIS BOX (one VIP held, not the other), re-checked for
# ~8 s first.
#
# Needs apt (keepalived, iputils-arping) -> it runs in the twin's `provision`
# phase, after EVERY bridge VM of its fabric component (the planner's
# forwarder ordering: an addressed forwarder waits on all address-less ones)
# and hq-mx's recipes + configure-router (the served-hosts closure).
set -eu

DEV="${AXR_VRRP_DEVICE:-$(hostname -s)}"

# ---- the pair's table -------------------------------------------------------
V935_NET=10.180.93.        # prefix match for the VLAN935 leg
V935_VIP=10.180.93.254/24  # hq-mx's next hop for 10.180.34.0/25
V953_NET=10.180.34.        # prefix match for the VLAN953 leg
V953_VIP=10.180.34.1/25    # the servers' default gateway (the segment gateway)
MX_GW=10.180.93.1          # hq-mx on VLAN935 -- the pair's default route
case "$DEV" in               # PEER_*: the other box's pinned leg addresses
    9k01) PRIO=150; PEER_935=10.180.93.3; PEER_953=10.180.34.3 ;;   # master while healthy
    9k02) PRIO=100; PEER_935=10.180.93.2; PEER_953=10.180.34.2 ;;
    *) echo "axr-vrrp[$DEV]: FAIL not a 9K pair member (set AXR_VRRP_DEVICE?)" >&2; exit 1 ;;
esac

log() { echo "axr-vrrp[$DEV]: $*"; }
die() { echo "axr-vrrp[$DEV]: FAIL $*" >&2; exit 1; }

iface_for() {
    ip -4 -o addr show scope global | awk -v p="$1" 'index($4, p) == 1 { print $2; exit }'
}
IF935=$(iface_for "$V935_NET"); IF953=$(iface_for "$V953_NET")
[ -n "$IF935" ] || die "no interface with a $V935_NET* address (the VLAN935 leg)"
[ -n "$IF953" ] || die "no interface with a $V953_NET* address (the VLAN953 leg)"

# ---- forwarding (persistent) --------------------------------------------------
cat > /etc/sysctl.d/99-axr-vrrp-l3.conf <<'EOS'
net.ipv4.ip_forward = 1
# Loose reverse-path filtering: a VIP move is asymmetric for a moment.
net.ipv4.conf.all.rp_filter = 2
net.ipv4.conf.default.rp_filter = 2
EOS
sysctl -q -p /etc/sysctl.d/99-axr-vrrp-l3.conf

# Default via hq-mx on VLAN935 -- never via our own VLAN953 VIP. cloud-init's
# ipconfig0 already set it (the planner's first-leg gateway rule); re-assert,
# and persist through a boot unit in case a lease/renderer ever drops it.
ip route replace default via "$MX_GW" dev "$IF935"
mkdir -p /etc/axr-vrrp
cat > /etc/axr-vrrp/route.sh <<EOS
#!/bin/sh
ip route replace default via $MX_GW dev $IF935
EOS
chmod 0755 /etc/axr-vrrp/route.sh
cat > /etc/systemd/system/axr-vrrp-route.service <<'EOU'
[Unit]
Description=axRange corp-twin 9K default route via hq-mx
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/etc/axr-vrrp/route.sh

[Install]
WantedBy=multi-user.target
EOU
systemctl daemon-reload >/dev/null 2>&1 || true
systemctl enable axr-vrrp-route.service >/dev/null 2>&1 || true

# ---- keepalived -----------------------------------------------------------
need=""
for pkg in keepalived iputils-arping; do
    dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q '^install ok installed$' ||
        need="$need $pkg"
done
if [ -n "$need" ]; then
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    # shellcheck disable=SC2086
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $need
fi

# keepalived's notify hook: one file per instance holding its current state,
# so this recipe (and an operator) can read it without parsing the journal.
cat > /etc/axr-vrrp/notify.sh <<'EON'
#!/bin/sh
# $1 = INSTANCE|GROUP, $2 = name, $3 = MASTER|BACKUP|FAULT|STOP
mkdir -p /run/axr-vrrp
echo "$3" > "/run/axr-vrrp/$2.state"
EON
chmod 0755 /etc/axr-vrrp/notify.sh
rm -f /run/axr-vrrp/V935.state /run/axr-vrrp/V953.state

cat > /etc/keepalived/keepalived.conf <<EOC
# GENERATED by corp-twin serve-vrrp-l3.sh ($DEV) -- re-run the recipe, do not hand-edit.
global_defs {
    router_id $DEV
    enable_script_security
    script_user root
}

vrrp_sync_group CORP_9K {
    group {
        V935
        V953
    }
}

vrrp_instance V935 {
    state BACKUP
    interface $IF935
    virtual_router_id 35
    priority $PRIO
    advert_int 1
    virtual_ipaddress {
        $V935_VIP dev $IF935
    }
    notify /etc/axr-vrrp/notify.sh
}

vrrp_instance V953 {
    state BACKUP
    interface $IF953
    virtual_router_id 53
    priority $PRIO
    advert_int 1
    virtual_ipaddress {
        $V953_VIP dev $IF953
    }
    notify /etc/axr-vrrp/notify.sh
}
EOC

systemctl enable keepalived >/dev/null 2>&1 || true
systemctl restart keepalived
systemctl is-active --quiet keepalived || { systemctl status --no-pager keepalived >&2 || true; die "keepalived did not start"; }

# ---- per-instance state ------------------------------------------------------
# Settled = both instances known, AGREEING (the sync group moves them
# together), and unchanged for longer than VRRP's master-down interval
# (3 x advert_int + skew ~= 3.6 s -> > 4 s): a BACKUP read inside that window
# may still be about to become MASTER. Bounded at 30 s; not settling is a WARN
# here (the peer may still be booting) -- the sync-group check below is the
# hard gate.
state_of() { cat "/run/axr-vrrp/$1.state" 2>/dev/null || echo "?"; }
settle() {  # sets s935/s953; 0 = settled
    local deadline=$((SECONDS + 30)) prev="" since=$SECONDS cur
    while :; do
        s935=$(state_of V935); s953=$(state_of V953); cur="$s935/$s953"
        if [ "$cur" != "$prev" ]; then prev=$cur; since=$SECONDS; fi
        case "$cur" in
            *\?*) ;;
            *) [ "$s935" != "$s953" ] || [ $((SECONDS - since)) -le 4 ] || return 0 ;;
        esac
        [ "$SECONDS" -lt "$deadline" ] || return 1
        sleep 1
    done
}
settle || log "WARN VRRP state did not settle within 30 s (V935=$s935 V953=$s953)"
log "keepalived state: V935=$s935 V953=$s953"
[ "$s935" != FAULT ] && [ "$s953" != FAULT ] || log "WARN a VRRP instance is in FAULT"

# ---- the peer: reachable on both legs? (bounded; it may still be booting) --
for peer in "$PEER_935" "$PEER_953"; do
    ok=0
    for _ in $(seq 1 10); do
        if ping -c 1 -W 1 "$peer" >/dev/null 2>&1; then ok=1; break; fi
        sleep 1
    done
    if [ "$ok" = 1 ]; then
        log "peer $peer reachable"
    else
        log "WARN peer $peer unreachable after ~20 s -- VRRP adverts cannot be exchanged with it; if it is up, BOTH boxes will go MASTER"
    fi
done

# The peer answering can flip the election (its adverts arrive only now):
# re-read the settled state before judging the pair on it.
settle || log "WARN VRRP state did not settle within 30 s after the peer check (V935=$s935 V953=$s953)"
log "keepalived state after the peer check: V935=$s935 V953=$s953"

# ---- split-pair check: ARP for each VIP from this box ----------------------
# MASTER: nobody else may answer for our VIP (`arping -D` exits non-zero on a
# reply -- the kernel never answers its own probe). BACKUP: somebody must.
for pair in "$IF935 ${V935_VIP%/*} $s935" "$IF953 ${V953_VIP%/*} $s953"; do
    set -- $pair
    if [ "$3" = MASTER ]; then
        if ! arping -D -q -c 2 -w 3 -I "$1" "$2" >/dev/null 2>&1; then
            log "WARN !!! SPLIT PAIR: this box is MASTER for $2 and ANOTHER box answers ARP for it too -- both 9Ks are MASTER; check VRRP adverts (224.0.0.18) between them !!!"
        fi
    elif [ "$3" = BACKUP ]; then
        if ! arping -q -c 2 -w 3 -I "$1" "$2" >/dev/null 2>&1; then
            log "WARN !!! nobody answers ARP for $2 while this box is BACKUP -- the VIP is held by no one !!!"
        fi
    fi
done

# The sync group moves both VIPs in one transition, but not atomically: a
# read between the two `ip addr add`s sees one. Re-check a few times first.
for try in 1 2 3 4 5; do
    has935=$(ip -4 -o addr show dev "$IF935" | grep -c " ${V935_VIP%/*}/" || true)
    has953=$(ip -4 -o addr show dev "$IF953" | grep -c " ${V953_VIP%/*}/" || true)
    [ "$has935" != "$has953" ] || break
    [ "$try" = 5 ] || sleep 2
done
log "if935=$IF935 if953=$IF953 prio=$PRIO holds: 935-vip=$has935 953-vip=$has953"
if [ "$has935" != "$has953" ]; then
    die "the sync group is split on this box (935-vip=$has935, 953-vip=$has953, after ~8 s)"
fi
echo "AXR_VRRP_OK device=$DEV prio=$PRIO master=$has935"
