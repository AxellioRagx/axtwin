#!/usr/bin/env bash
# SOC SENSOR — Suricata + Zeek tuned for AD protocol traffic (Kerberos/LDAP/SMB).
# Idempotent. The live mirrored feed is the deferred DEFENSE OVERLAY (OP-OVL).
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

# AD detection focus notes (what the deferred Sigma/Wazuh rules will key on).
install -d -m 0755 /etc/axrange
cat > /etc/axrange/sensor.env <<'ENV'
ROLE=ad-soc-sensor
SIEM_HOST=10.60.10.51
FOCUS=kerberoasting,llmnr-poisoning,dcsync,smb-lateral,ldap-recon
ENV

echo "AD sensor staged: suricata+zeek; focus=kerberos/ldap/smb anomalies"
