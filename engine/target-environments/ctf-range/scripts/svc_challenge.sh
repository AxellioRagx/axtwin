#!/usr/bin/env bash
# Service/pwn challenge — a deliberately weak network service over socat.
# Idempotent: (re)installs a systemd unit and (re)starts it.
set -euo pipefail

install -d -m 0755 /opt/chal
printf 'flag{s3rv1ce_0verfl0w_pwn3d}\n' > /opt/chal/flag.txt
chmod 0644 /opt/chal/flag.txt

# A trivial "guess the access code" line service — stands in for a pwn target.
cat > /opt/chal/service.sh <<'SH'
#!/usr/bin/env bash
echo "ACME service v0.1 — enter access code:"
read -r code
if [ "$code" = "ACME-1337" ]; then cat /opt/chal/flag.txt; else echo "denied"; fi
SH
chmod 0755 /opt/chal/service.sh

cat > /etc/systemd/system/chal-svc.service <<'UNIT'
[Unit]
Description=CTF weak service (socat)
After=network.target
[Service]
ExecStart=/usr/bin/socat TCP-LISTEN:9999,reuseaddr,fork EXEC:/opt/chal/service.sh
Restart=always
[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now chal-svc.service || true
echo "service challenge ready on :9999 (flag planted: svc-chal)"
