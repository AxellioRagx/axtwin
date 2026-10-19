#!/usr/bin/env bash
# Corp file server — an SMB share (lateral-movement target). Idempotent.
set -euo pipefail

cat > /etc/samba/smb.conf <<'CONF'
[global]
   workgroup = ACME
   server string = ACME File Server
   map to guest = Bad User
[corp]
   path = /srv/corp
   guest ok = yes
   read only = no
CONF

install -d -m 0775 /srv/corp
cat > /srv/corp/IT-handover.txt <<'TXT'
Onboarding notes. Default service creds rotate quarterly (TODO: enforce).
TXT

systemctl enable --now smbd || true
systemctl restart smbd || true
echo "file-srv staged: SMB share //file-srv/corp (lateral target)"
