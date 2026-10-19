#!/bin/sh
# corp-twin -- the unfragmented mirror path to XPress-SW-A (capture-rate ladder, 2026-10-01;
# docs/reference/capture-rate/2026-10-01-corp-twin-ladder.md, rung R2).
#
# Run ON the PVE host (dev45) as root:   sh mirror-mtu-path.sh apply|revert|status
#
# Why: the o2 transport `o2v7dc98` is MTU 1500, so every full-size mirrored frame (1514/1518 B)
# encapsulates to 1542/1546 B and leaves the host as two outer IP fragments (the gretaps carry
# `nopmtudisc ignore-df`, LES-356). The sensor must reassemble, and small frames overtake the
# fragmented big ones -> 5-6 % of TCP segments captured out of wire order. This gives ONLY
# corp-twin's own gretaps a 1600-byte path to the sensor:
#   * the 24 corp-twin gretaps get `fwmark 0x5ca1` (changelink: same ifindex, tc mirred
#     filters untouched; the changelink re-derives the gretap MTU to 1458, so it is set back to
#     1500 every time),
#   * policy rule fwmark 0x5ca1 -> table 251, where 10.255.251.21 has `mtu 1600`,
#   * the sensor's transport leg tap104i1 gets MTU 1600 (guest ens19 stays 1500 and accepts
#     the 1546 B frames -- verified with a marked `ping -M do -s 1572`).
# Every other tunnel on o2v7dc98 (svc-engines' tapg_84e687/tapg_14167b -> .21, anything -> .10/.12)
# is unmarked and keeps the main-table 1500 path byte-for-byte.
#
# PRECONDITION on the sensor: GRO OFF on ens19 AND gre-cap (o2/tcpdump-sensor/README.md,
# "Capture offloads and the transport MTU"). With GRO on, whole outer packets get GRO-merged into
# up-to-64 KB super-frames: 24,227 pxswcapture `queue` drops in 25 s and 3.7 KB frames in the pcap
# (rung R2-try1). The fragmentation had been hiding that.
#
# Runtime state only: lost on a dev45 reboot (the twin goes with it), on a VM-104 NIC replug
# (tap104i1 MTU), and on a gretap re-CREATE (a tap destroy/deploy; a plain `tap deploy`
# re-apply adopts the netdev and its changelink keeps the fwmark). Re-run `apply` after any.
set -eu
MARK=0x5ca1; TABLE=251; PREF=2510; SENSOR=10.255.251.21; SRC=10.255.251.1; BR=o2v7dc98; LEG=tap104i1
GRETAPS="tapg_0ae0ba tapg_1b739b tapg_25a261 tapg_434500 tapg_57e392 tapg_5c1d65 tapg_6a9e1d
tapg_79a4b1 tapg_852660 tapg_9a661b tapg_a2bccf tapg_afb728 tapg_b1be78 tapg_b69f6b tapg_babd68
tapg_c51e81 tapg_c904f2 tapg_cd63db tapg_ceecb9 tapg_d9837a tapg_f203d3 tapg_f4270e tapg_f82a66
tapg_f904a2"   # tap-corp-twin-07cfcdb2-8176c5ad (`axrange tap info` lists them); re-derive after a re-create

case "${1:-status}" in
apply)
  ip link set "$LEG" mtu 1600
  ip route replace "$SENSOR/32" dev "$BR" src "$SRC" mtu 1600 table "$TABLE"
  ip rule show pref "$PREF" | grep -q . || ip rule add fwmark "$MARK" lookup "$TABLE" pref "$PREF"
  for g in $GRETAPS; do ip link set "$g" type gretap fwmark "$MARK"; ip link set "$g" mtu 1500; done
  ;;
revert)
  for g in $GRETAPS; do ip link set "$g" type gretap fwmark 0; ip link set "$g" mtu 1500; done
  ip rule del pref "$PREF" 2>/dev/null || true
  ip route flush table "$TABLE" 2>/dev/null || true
  ip link set "$LEG" mtu 1500
  ;;
status)
  ip rule show pref "$PREF"; ip route show table "$TABLE" 2>/dev/null || true
  echo "$LEG $(cat /sys/class/net/$LEG/mtu)"
  for g in $GRETAPS; do echo "$g $(ip -d link show "$g" | grep -oE 'mtu [0-9]+|fwmark [0-9a-fx]+|nopmtudisc|ignore-df' | tr '\n' ' ')"; done
  nstat -az IpFragCreates | tail -1
  ;;
*) echo "usage: $0 apply|revert|status" >&2; exit 2 ;;
esac
