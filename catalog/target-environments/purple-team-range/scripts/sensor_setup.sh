#!/usr/bin/env bash
# SOC sensor — Suricata + Zeek for the purple-team detection loop. Idempotent.
# Live mirrored feed + the detection-coverage scoring are the deferred OP-OVL.
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
ROLE=purple-soc-sensor
SIEM_HOST=10.20.30.20
FOCUS=phishing,lateral-smb,c2-beacon,exfil
WATCH=10.20.10.0/24,10.20.20.0/24
ENV

echo "purple sensor staged: suricata+zeek; ships to SIEM 10.20.30.20"
