#!/usr/bin/env bash
# Deliberately weak services for the vulnerable-fleet target (Metasploitable
# mould) — anonymous FTP + a world-readable SMB share. LAB TARGET ONLY.
# Idempotent: re-writes configs and restarts the services.
set -euo pipefail

# Anonymous-readable FTP (a classic scan finding).
cat > /etc/vsftpd.conf <<'CONF'
listen=YES
anonymous_enable=YES
local_enable=NO
write_enable=NO
anon_root=/srv/ftp
CONF
install -d -m 0755 /srv/ftp
printf 'ACME backups — internal only\n' > /srv/ftp/README.txt
systemctl enable --now vsftpd || true
systemctl restart vsftpd || true

# World-readable SMB share (no auth) — another obvious finding.
cat > /etc/samba/smb.conf <<'CONF'
[global]
   workgroup = WORKGROUP
   map to guest = Bad User
   server min protocol = NT1
[public]
   path = /srv/public
   guest ok = yes
   read only = yes
CONF
install -d -m 0777 /srv/public
printf 'shared\n' > /srv/public/notes.txt
systemctl enable --now smbd || true
systemctl restart smbd || true

echo "weak-svc staged: anon FTP (:21) + guest SMB (:445) — lab finding surface"
