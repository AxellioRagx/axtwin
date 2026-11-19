#!/bin/sh
# pace-test: a real Samba file server on 445/tcp with one guest-readable share,
# `share`, holding the file the client persona fetches (`axrange-probe.bin`,
# 1 MiB, the command driver's default smb_file). Guest access is what lets the
# persona run `smbclient -N` (knobs.auth_credential: none) with no credential.
# Runs in-guest as root through run-script (OP-APP-030). Fails loud if smbd
# does not come up.
set -eu
umask 022

if ! dpkg-query -W -f='${Status}' samba 2>/dev/null | grep -q '^install ok installed$'; then
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends samba
fi

mkdir -p /srv/share
if [ ! -s /srv/share/axrange-probe.bin ]; then
    head -c 1048576 /dev/urandom > /srv/share/axrange-probe.bin
fi
chmod 0755 /srv/share
chmod 0644 /srv/share/axrange-probe.bin

cat > /etc/samba/smb.conf <<'AXR_SMB_EOF'
[global]
   workgroup = PACE
   server string = pace-test file server
   server role = standalone server
   server min protocol = SMB2_10
   map to guest = Bad User
   guest account = nobody
   disable netbios = yes
   smb ports = 445
   log file = /var/log/samba/log.%m
   max log size = 1000

[share]
   path = /srv/share
   read only = yes
   guest ok = yes
   guest only = yes
   browseable = yes
AXR_SMB_EOF
testparm -s >/dev/null

systemctl disable --now nmbd >/dev/null 2>&1 || true
systemctl enable smbd >/dev/null 2>&1 || true
systemctl restart smbd
sleep 1
if ! systemctl is-active --quiet smbd; then
    systemctl status --no-pager --full smbd >&2 || true
    echo "axr-serve: smbd did not start" >&2
    exit 1
fi
echo "axr-serve: smb ready (445/tcp, share 'share')"
