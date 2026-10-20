#!/usr/bin/env bash
# SOC sensor — Suricata + Zeek so the CTF can be run blue-team (detect solves).
# Idempotent. Live mirrored feed is the deferred DEFENSE OVERLAY (OP-OVL).
set -euo pipefail

if command -v suricata-update >/dev/null 2>&1; then
  suricata-update --no-test || true
fi
systemctl enable --now suricata || true

if systemctl list-unit-files 2>/dev/null | grep -q '^zeek'; then
  systemctl enable --now zeek || true
elif command -v zeekctl >/dev/null 2>&1; then
  zeekctl deploy || true
fi

install -d -m 0755 /etc/axrange
cat > /etc/axrange/sensor.env <<'ENV'
ROLE=ctf-sensor
FOCUS=web-exploit,brute-force,service-pwn
ENV

echo "CTF sensor staged: suricata+zeek (blue-team play)"
