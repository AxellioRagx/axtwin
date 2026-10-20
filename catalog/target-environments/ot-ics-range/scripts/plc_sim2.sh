#!/usr/bin/env bash
# Simulated PLC #2 — pymodbus Modbus/TCP slave on :502 (a "conveyor line").
# Distinct process from PLC1. Idempotent. Needs WAN egress for pip.
set -euo pipefail

export PIP_BREAK_SYSTEM_PACKAGES=1
python3 -m pip install --quiet --upgrade pymodbus 2>/dev/null \
  || echo "pymodbus deferred (needs egress)"

install -d -m 0755 /opt/plc
cat > /opt/plc/plc2.py <<'PY'
#!/usr/bin/env python3
"""Conveyor-line PLC: Modbus/TCP slave. Registers model belt speed + e-stop."""
try:
    from pymodbus.server import StartTcpServer
    from pymodbus.datastore import ModbusSlaveContext, ModbusServerContext, ModbusSequentialDataBlock
except Exception:
    import time; time.sleep(1e9)
block = ModbusSequentialDataBlock(0, [75, 0, 1, 0, 0, 0, 0, 0, 0, 0])  # [speed%, estop, running,...]
ctx = ModbusServerContext(slaves=ModbusSlaveContext(hr=block), single=True)
StartTcpServer(context=ctx, address=("0.0.0.0", 502))
PY
chmod 0755 /opt/plc/plc2.py

cat > /etc/systemd/system/plc-sim2.service <<'UNIT'
[Unit]
Description=Simulated PLC #2 (pymodbus, conveyor line)
After=network.target
[Service]
ExecStart=/usr/bin/python3 /opt/plc/plc2.py
Restart=always
[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now plc-sim2.service || true
echo "PLC2 staged: Modbus/TCP :502 (conveyor line)"
