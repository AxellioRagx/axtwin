#!/bin/sh
# trex-test -- the router's static routes to the emulated ranges, and the
# internal/external boundary rule on its inet leg.
#
# The TRex generators source and answer for address ranges that are NOT on
# their segments, so the router needs one route per range to the generator's
# own port IP:
#   10.250.1.0/24    crowd clients        via trex-client 10.70.1.10  (internal)
#   10.250.2.0/28    file servers         via trex-server 10.70.2.10  (internal)
#   198.51.100.0/24  external web         via trex-ext    192.0.2.10  (external)
#   203.0.113.0/29   external resolvers   via trex-ext    192.0.2.10  (external)
# Delivered as an `install: script` recipe on the router (run-script, after
# its configure-router) -- the same path corp-twin's serve-hq-mx-routes.sh
# uses, because the engine's Debian configure-router installs no static
# routes. `ip route replace` only needs the next hop on-link, not answering,
# so this is safe before the generators are up.
#
# THE BOUNDARY: the leg on 192.0.2.0/24 (inet) is the twin's external edge.
# An nftables forward rule drops any NEW connection arriving on it; replies to
# connections opened from inside (ct state established/related) pass. It is
# its own table (`inet axr_boundary`), so it composes with whatever
# configure-router installed and is read back below.
#
# Reboot-persistent through a oneshot unit; removed with the VM at teardown.
set -eu

mkdir -p /etc/axr-crowd
cat > /etc/axr-crowd/routes.sh <<'EOS'
#!/bin/sh
set -eu
ip route replace 10.250.1.0/24 via 10.70.1.10
ip route replace 10.250.2.0/28 via 10.70.2.10
ip route replace 198.51.100.0/24 via 192.0.2.10
ip route replace 203.0.113.0/29 via 192.0.2.10
ext=$(ip -o -4 addr show | awk '$4 ~ /^192\.0\.2\.1\// {print $2; exit}')
[ -n "$ext" ] || { echo "axr-crowd: no interface holds 192.0.2.1 (the inet leg)" >&2; exit 1; }
nft delete table inet axr_boundary 2>/dev/null || true
nft -f - <<EON
table inet axr_boundary {
  chain forward {
    type filter hook forward priority -10; policy accept;
    iifname "$ext" ct state established,related accept
    iifname "$ext" ct state new counter drop comment "external may not open inward"
  }
}
EON
EOS
chmod 0755 /etc/axr-crowd/routes.sh
command -v nft >/dev/null 2>&1 || { apt-get install -y -q nftables >/dev/null 2>&1 || true; }
command -v nft >/dev/null 2>&1 || { echo "axr-crowd: FAIL nft is not installed" >&2; exit 1; }
cat > /etc/systemd/system/axr-crowd-routes.service <<'EOU'
[Unit]
Description=axRange trex-test routes to the emulated ranges and the inet boundary rule
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
for r in "10.250.1.0/24 via 10.70.1.10" "10.250.2.0/28 via 10.70.2.10" \
         "198.51.100.0/24 via 192.0.2.10" "203.0.113.0/29 via 192.0.2.10"; do
  set -- $r
  ip -4 route show "$1" | grep -q "via $3" || { echo "axr-crowd: FAIL $r not installed" >&2; exit 1; }
done
nft list chain inet axr_boundary forward | grep -q 'ct state new' || {
  echo "axr-crowd: FAIL the inet boundary rule is not installed" >&2; exit 1; }
echo "AXR_CROWD_ROUTES_OK 4 routes; boundary: new connections from inet dropped"
