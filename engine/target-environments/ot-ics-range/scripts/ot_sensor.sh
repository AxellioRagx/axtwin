#!/usr/bin/env bash
# OT-aware SOC sensor — Suricata + Zeek tuned for OT protocols (Modbus/DNP3).
# Idempotent. The live mirrored feed + Modbus/DNP3 rule packs are the deferred
# DEFENSE OVERLAY (OP-OVL).
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
ROLE=ot-soc-sensor
FOCUS=modbus-write-anomaly,unauthorized-master,dnp3,plc-scan
WATCH=10.80.20.0/24,10.80.15.0/24
ENV

echo "OT sensor staged: suricata+zeek; focus=Modbus/DNP3 anomalies"
