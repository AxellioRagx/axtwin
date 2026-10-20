#!/bin/sh
# corp-twin -- hq-mx's static route to the server VLAN behind the 9K pair.
# Design: docs/WORK_ORDER_corp_twin.md §10 (P1).
#
# VLAN953 (10.180.34.0/25) is routed by the 9K01/9K02 VRRP pair, not by the
# MX85, so hq-mx needs ONE static route: the /25 via the pair's VLAN935 VIP.
# Delivered as an `install: script` recipe on the router (run-script, ordered
# after its own configure-router) because the engine's Debian configure-router
# installs no static routes and OP-HOST-050 (add-static-route) has no handler
# -- the implementation substitution recorded in §12. `ip route replace` only
# needs the next hop to be ON-LINK (hq-mx's own leg on mx-vlan935), not
# answering, so this is safe to apply before the pair's keepalived is up.
#
# Reboot-persistent through a oneshot unit (the LES-013 pattern). The egress
# phase engine is unaffected: its policy rules match on the LAN interface the
# traffic ARRIVED on (mx-vlan935 is one), and its `suppress_prefixlength 0`
# rule keeps this /25 resolving through the main table.
set -eu

DST=10.180.34.0/25
VIA=10.180.93.254

mkdir -p /etc/axr-hq-mx
cat > /etc/axr-hq-mx/routes.sh <<EOS
#!/bin/sh
ip route replace $DST via $VIA
EOS
chmod 0755 /etc/axr-hq-mx/routes.sh
cat > /etc/systemd/system/axr-hq-mx-routes.service <<'EOU'
[Unit]
Description=axRange corp-twin hq-mx static routes (VLAN953 via the 9K VIP)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/etc/axr-hq-mx/routes.sh

[Install]
WantedBy=multi-user.target
EOU
systemctl daemon-reload >/dev/null 2>&1 || true
systemctl enable axr-hq-mx-routes.service >/dev/null 2>&1 || true
/etc/axr-hq-mx/routes.sh
ip -4 route show "$DST" | grep -q "via $VIA" || { echo "axr-hq-mx: FAIL route $DST via $VIA not installed" >&2; exit 1; }
echo "AXR_HQ_MX_ROUTES_OK $DST via $VIA"
