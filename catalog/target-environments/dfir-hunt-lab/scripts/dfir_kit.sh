#!/usr/bin/env bash
# DFIR analyst workstation — the OSS forensics kit (FLARE/Redline equivalent on
# Linux) + a hunt playbook keyed to the planted victim artifacts. Idempotent.
# Needs gw router WAN egress for the pip install.
set -euo pipefail

export PIP_BREAK_SYSTEM_PACKAGES=1
# Volatility3 (memory forensics) — tshark/sleuthkit/yara come via provision-app.
python3 -m pip install --quiet --upgrade volatility3 2>/dev/null \
  || echo "volatility3 deferred (needs egress)"

install -d -m 0755 /opt/dfir
# Hunt playbook: where to look + the IOCs the deferred OP-ACT compromise leaves.
cat > /opt/dfir/HUNT_PLAYBOOK.md <<'MD'
# DFIR hunt playbook — victim 10.30.10.10

Evidence sources:
  - PCAP from the capture sensor (victim is tapped) -> tshark / zeek-offline
  - Host triage (disk image / live)                 -> sleuthkit, yara, find
  - Memory image (if captured)                      -> volatility3

Hunt checklist:
  1. Network: beacons to 185.234.219.45:443 (jittered)   [pcap / zeek conn.log]
  2. Persistence: systemd timer 'sysupdate.timer'        [systemctl list-timers]
  3. Dropper: /tmp/.cache.bin (YARA AXRANGE_SIM_MALWARE)  [yara rule below]
  4. Hidden dir: /opt/.sysupdate (agent.sh, config.ini)  [find / -name '.*' -type d]
  5. Log IOCs: auth.log 'AXRANGE-SIM' root-from-C2 + sudo www-data->bash
MD

# A starter YARA rule that matches the planted dropper marker.
cat > /opt/dfir/sim_dropper.yar <<'YAR'
rule axrange_sim_dropper {
  strings: $m = "AXRANGE_SIM_MALWARE_DO_NOT_DETONATE"
  condition: $m
}
YAR

# Known-bad IOC list for the hunt stack to match against.
cat > /opt/dfir/iocs.txt <<'TXT'
185.234.219.45      # C2 ip
/opt/.sysupdate     # persistence dir
sysupdate.timer     # persistence unit
AXRANGE_SIM_MALWARE_DO_NOT_DETONATE   # dropper marker
TXT

echo "DFIR kit staged: tshark/sleuthkit/yara (pkg) + volatility3 + hunt playbook"
