#!/usr/bin/env bash
# Simulated PLC #1 — pymodbus Modbus/TCP slave on :502 (a "pump station").
# Idempotent: (re)writes the server + systemd unit and (re)starts it.
# Needs conduit router WAN egress for the pymodbus pip install.
set -euo pipefail

export PIP_BREAK_SYSTEM_PACKAGES=1
python3 -m pip install --quiet --upgrade pymodbus 2>/dev/null \
  || echo "pymodbus deferred (needs egress)"

install -d -m 0755 /opt/plc
cat > /opt/plc/plc.py <<'PY'
#!/usr/bin/env python3
"""Pump-station PLC: Modbus/TCP slave, holding registers model a tank level +
pump state. The deferred OP-ACT attack writes these to disrupt the process."""
try:
    from pymodbus.server import StartTcpServer
    from pymodbus.datastore import ModbusSlaveContext, ModbusServerContext, ModbusSequentialDataBlock
except Exception:
    import time; time.sleep(1e9)
block = ModbusSequentialDataBlock(0, [50, 1, 0, 0, 0, 0, 0, 0, 0, 0])  # [level%, pump_on, ...]
ctx = ModbusServerContext(slaves=ModbusSlaveContext(hr=block), single=True)
StartTcpServer(context=ctx, address=("0.0.0.0", 502))
PY
chmod 0755 /opt/plc/plc.py

cat > /etc/systemd/system/plc-sim.service <<'UNIT'
[Unit]
Description=Simulated PLC (pymodbus, pump station)
After=network.target
[Service]
ExecStart=/usr/bin/python3 /opt/plc/plc.py
Restart=always
[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now plc-sim.service || true
echo "PLC1 staged: Modbus/TCP :502 (pump station)"
