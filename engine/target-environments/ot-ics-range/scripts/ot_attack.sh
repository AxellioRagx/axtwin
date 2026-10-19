#!/usr/bin/env bash
# OT attacker — stage Modbus/ICS attack tooling (red team, IT->OT pivot).
# apt scanners install via provision-app; this adds the pymodbus client + a
# Modbus probe helper. Idempotent. Needs conduit router WAN egress for pip/git.
set -euo pipefail

export PIP_BREAK_SYSTEM_PACKAGES=1
python3 -m pip install --quiet --upgrade pymodbus 2>/dev/null \
  || echo "pymodbus deferred (needs egress)"

# smod-style Modbus recon helper for the deferred attack chain (OP-ACT).
install -d -m 0755 /opt/ot-attack
cat > /opt/ot-attack/modbus_probe.py <<'PY'
#!/usr/bin/env python3
"""Probe a Modbus/TCP slave: read holding registers 0..9. Deferred OP-ACT uses
this as the recon step before register manipulation."""
import sys
try:
    from pymodbus.client import ModbusTcpClient
except Exception:
    sys.exit("pymodbus not installed (needs egress)")
host = sys.argv[1] if len(sys.argv) > 1 else "10.80.20.30"
c = ModbusTcpClient(host, port=502)
if c.connect():
    rr = c.read_holding_registers(0, 10, unit=1)
    print(host, "registers:", getattr(rr, "registers", rr))
    c.close()
PY
chmod 0755 /opt/ot-attack/modbus_probe.py

# OT target crib (Modbus/TCP :502) for the deferred attack chain.
install -d -m 0755 /etc/axrange
cat > /etc/axrange/ot_targets.txt <<'TXT'
PLC1=10.80.20.30:502   PLC2=10.80.20.31:502   HMI=10.80.20.40
HISTORIAN=10.80.15.10:1883(mqtt)
TXT

echo "OT attacker staged: nmap (pkg) + pymodbus client + modbus_probe.py"
