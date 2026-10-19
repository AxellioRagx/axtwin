#!/usr/bin/env bash
# Corporate file share. The finance/ dir is the seed-dataset (data overlay) target.
set -uo pipefail
install -d -m 0775 /srv/share /srv/share/finance
cat >> /etc/samba/smb.conf <<'SMB'

[corp]
  path = /srv/share
  browseable = yes
  guest ok = yes
  read only = yes
SMB
systemctl restart smbd 2>/dev/null || systemctl restart smb 2>/dev/null || true
echo "share_setup: //file-srv/corp ready"
