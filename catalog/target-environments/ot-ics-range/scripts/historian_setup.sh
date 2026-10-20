#!/usr/bin/env bash
# SCADA historian — MQTT broker (mosquitto) + a tiny subscriber-to-sqlite store.
# The L3.5 broker-only data path between IT and OT. Idempotent.
set -euo pipefail

# Mosquitto MQTT broker on the standard port, listening for telemetry.
install -d -m 0755 /etc/mosquitto/conf.d
cat > /etc/mosquitto/conf.d/historian.conf <<'CONF'
listener 1883 0.0.0.0
allow_anonymous true
CONF
systemctl enable --now mosquitto || true

# Minimal historian: subscribe to plant/# and append to a sqlite table.
install -d -m 0755 /opt/historian
cat > /opt/historian/historian.py <<'PY'
#!/usr/bin/env python3
import sqlite3, subprocess, time, os
db = sqlite3.connect("/opt/historian/historian.db")
db.execute("CREATE TABLE IF NOT EXISTS samples(ts INTEGER, topic TEXT, value TEXT)")
db.commit()
# Tail mosquitto_sub if available; otherwise idle (broker still serves clients).
try:
    p = subprocess.Popen(["mosquitto_sub","-t","plant/#","-v"], stdout=subprocess.PIPE, text=True)
    for line in p.stdout:
        parts = line.strip().split(" ", 1)
        if len(parts) == 2:
            db.execute("INSERT INTO samples VALUES(?,?,?)", (int(time.time()), parts[0], parts[1]))
            db.commit()
except FileNotFoundError:
    while True: time.sleep(60)
PY
chmod 0755 /opt/historian/historian.py

cat > /etc/systemd/system/historian.service <<'UNIT'
[Unit]
Description=SCADA historian (MQTT -> sqlite)
After=mosquitto.service
[Service]
ExecStart=/usr/bin/python3 /opt/historian/historian.py
Restart=always
[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now historian.service || true
echo "historian staged: mosquitto :1883 + sqlite store (plant/#)"
