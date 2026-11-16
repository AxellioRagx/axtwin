#!/bin/sh
# trex-test -- the router's static routes to the emulated crowd ranges.
#
# The TRex generators source and answer for address ranges that are NOT on
# their segments (10.250.1.0/24 clients behind trex-client, 10.250.2.0/28
# servers behind trex-server), so the router needs one route per range to the
# generator's own port IP. Delivered as an `install: script` recipe on the
# router (run-script, after its configure-router) -- the same path corp-twin's
# serve-hq-mx-routes.sh uses, because the engine's Debian configure-router
# installs no static routes. `ip route replace` only needs the next hop
# on-link, not answering, so this is safe before the generators are up.
#
# Reboot-persistent through a oneshot unit; removed with the VM at teardown.
set -eu

mkdir -p /etc/axr-crowd
cat > /etc/axr-crowd/routes.sh <<'EOS'
#!/bin/sh
ip route replace 10.250.1.0/24 via 10.70.1.10
ip route replace 10.250.2.0/28 via 10.70.2.10
EOS
chmod 0755 /etc/axr-crowd/routes.sh
cat > /etc/systemd/system/axr-crowd-routes.service <<'EOU'
[Unit]
Description=axRange trex-test static routes to the emulated crowd ranges
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/etc/axr-crowd/routes.sh

[Install]
WantedBy=multi-user.target
EOU
systemctl daemon-reload >/dev/null 2>&1 || true
systemctl enable axr-crowd-routes.service >/dev/null 2>&1 || true
/etc/axr-crowd/routes.sh
ip -4 route show 10.250.1.0/24 | grep -q 'via 10.70.1.10' || { echo "axr-crowd: FAIL 10.250.1.0/24 via 10.70.1.10 not installed" >&2; exit 1; }
ip -4 route show 10.250.2.0/28 | grep -q 'via 10.70.2.10' || { echo "axr-crowd: FAIL 10.250.2.0/28 via 10.70.2.10 not installed" >&2; exit 1; }
echo "AXR_CROWD_ROUTES_OK 10.250.1.0/24 via 10.70.1.10, 10.250.2.0/28 via 10.70.2.10"
