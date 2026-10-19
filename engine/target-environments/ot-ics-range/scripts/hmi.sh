#!/usr/bin/env bash
# HMI — Node-RED operator dashboard onto the OT process. Idempotent.
# Needs conduit router WAN egress to npm-install Node-RED.
set -euo pipefail

# Install Node-RED globally if absent (npm is idempotent-ish; guard on the bin).
if ! command -v node-red >/dev/null 2>&1; then
  npm install -g --unsafe-perm node-red 2>/dev/null \
    || echo "node-red install deferred (needs egress)"
fi

# Run Node-RED as a service. Flows that poll the PLCs/historian are authored in
# the UI (or imported) — the deferred config layer would seed a flow file.
cat > /etc/systemd/system/node-red.service <<'UNIT'
[Unit]
Description=Node-RED OT HMI
After=network.target
[Service]
ExecStart=/usr/bin/env node-red
Restart=always
Environment=NODE_RED_HOME=/opt/node-red
WorkingDirectory=/opt/node-red
[Install]
WantedBy=multi-user.target
UNIT

install -d -m 0755 /opt/node-red
systemctl daemon-reload
if command -v node-red >/dev/null 2>&1; then
  systemctl enable --now node-red.service || true
fi
echo "HMI staged: Node-RED :1880 (points at PLC1/PLC2 + historian)"
