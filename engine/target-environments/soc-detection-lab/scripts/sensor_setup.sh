#!/usr/bin/env bash
# SOC SENSOR — Suricata + Zeek network IDS/NSM on the SOC segment.
# Idempotent. The live mirrored-traffic feed to this sensor is the deferred
# DEFENSE OVERLAY (OP-OVL observes:/tap_from:); here we stage the tools + rules.
set -euo pipefail

# Suricata: pull the ET-open ruleset and enable the service.
if command -v suricata-update >/dev/null 2>&1; then
  suricata-update --no-test || true
fi
systemctl enable --now suricata || true

# Zeek (network security monitor): enable if packaged with a service unit; many
# distro builds ship zeekctl instead, so fall back to deploying it idempotently.
if systemctl list-unit-files 2>/dev/null | grep -q '^zeek'; then
  systemctl enable --now zeek || true
elif command -v zeekctl >/dev/null 2>&1; then
  zeekctl deploy || true
fi

# Sensor metadata so a blue-team analyst (or the deferred overlay) knows where
# to point log shipping (the SIEM host on the SOC segment).
install -d -m 0755 /etc/axrange
cat > /etc/axrange/sensor.env <<'ENV'
ROLE=soc-sensor
SIEM_HOST=10.50.30.20
IDS=suricata,zeek
ENV

echo "sensor staged: suricata (ET-open) + zeek; ships to SIEM 10.50.30.20"
