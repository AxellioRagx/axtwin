#!/bin/sh
# axRange svc-engines -- the FILE SERVER (Samba on 445) and the SNMP agent (161).
#
# HAND-WRITTEN for this twin; there is no cast recipe for a file server among
# aximprint's fourteen. Every helper below is copied BYTE-IDENTICAL from
# `twin-configs/svc-verify/scripts/serve-web-tier-10-80-72-core.sh`, so the
# fail-loud contract (`systemctl is-active` or exit 1, never `nohup ... &` --
# LES-086) and the install-only-what-is-missing contract are the cast's.
#
# **Why a file server exists in this twin at all.** The cast observed **2.2 GB
# of internal SMB** (work order §1) -- the single largest internal byte volume
# after TLS. Filler bytes at port 445 cannot carry that: SMB byte volume is a
# real client doing a real NEGOTIATE/SESSION_SETUP/TREE_CONNECT and then real
# SMB2 READs out of a real share at a real UNC path. So this host holds ~1 GiB
# of files and the file-client persona reads them repeatedly -- a 2.2 GB
# window is reached by RE-READING, never by inventing 2.2 GB of content nobody
# observed.
#
# **The files are sparse** (`fallocate`), and that is honest rather than
# convenient: a sparse file still puts every one of its bytes on the WIRE when
# it is read (SMB has no compression enabled by default, and the sink/serve
# recipes make no claim about file CONTENT anywhere -- aximprint GAP-019).
# Writing 1 GiB of /dev/urandom would cost minutes of deploy time and 1 GiB of
# real storage to produce bytes that are equally meaningless. What is real is
# the protocol and the volume.
#
# **NOT domain-joined**, deliberately -- recorded, not hidden. A Samba member
# server needs `net ads join` plus a working keytab and is a slice of its own.
# The consequence: SMB here authenticates with NTLM or as guest, never with
# Kerberos. The Kerberos-authenticated SMB path is exercised against the DC's
# SYSVOL share instead (the AD persona), where it is genuine.
#
# **NetBIOS is off** (`disable netbios = yes`, nmbd masked): a modern file
# server is SMB-over-445 only, the declared listen set is then exactly
# `445/tcp`, and 137/138 broadcast noise would otherwise sit on the tapped
# segment pretending to be content this twin authored.
#
# Delivered as an `install: [{script: ...}]` action, so it runs through
# `run-script` (OP-APP-030) IN-GUEST as root, honouring this shebang.
#
#   serve-file-server.sh                install + populate (idempotent)
#   serve-file-server.sh --healthcheck  exit 0 only when smbd + snmpd are up
set -eu
umask 022
mkdir -p /usr/local/lib/axr /var/log/axrange

AXR_RESOLVER_IP=10.66.10.11
AXR_SHARE_ROOT=/srv/axr-share
AXR_WORKGROUP=CORP
# A LOCAL Samba account, deliberately NOT the domain's `alice`. This host is
# not domain-joined, so its accounts are its own; naming it `alice` would imply
# the domain identity and invite someone to try her password here. No persona
# uses it -- the file engine reads the ANONYMOUS `pub` share -- so it is used
# by the README's manual authenticated check and by this script's own check 3,
# and by no automated consumer beyond that. (A persona may never carry a
# credential literal at all: `command._resolve_client_knobs` refuses a
# secret-looking knob by name, and the real secret is adapter.conf's
# `DEFAULT_PASSWORD`, resolved at run time.)
AXR_SMB_USER=smbuser
# The lab-wide default (`adapter.conf`'s DEFAULT_PASSWORD, whose built-in value
# is this literal). Deliberately the SAME string, so the accounts here and the
# personas cannot drift: a persona that ever wants the `corp` share authors
# `auth_user: smbuser` + `auth_credential: default` and the driver resolves the
# real secret at run time (`command._resolve_client_knobs` -- a persona may not
# carry a literal). None does today: every persona reads `pub` anonymously, and
# the only consumers of this password are the README's manual check and check 3
# below. If DEFAULT_PASSWORD is overridden in adapter.conf, override it here too.
AXR_SMB_PASS='Axr4nge-Lab!'
# The SHELL account the `ssh` engine logs in as. Separate from the Samba
# account above on purpose: that one is `--disabled-password` and deliberately
# cannot ssh, while this one has no Samba presence at all.
#
# It exists because the persona's ssh session has to COMPLETE. Before
# 2026-09-21 the file-client persona ran `ssh -o BatchMode=yes
# axr@files.svc.axr.lab true` against a host where `axr` did not exist --
# `Permission denied (publickey,password)`, exit 255, every cycle: a real KEX
# at the sensor and a client that never once did the thing it claims to do
# (LES-001, measured on dev45). The engine now authenticates with the lab-wide
# password (`sshpass -f <0600 file>`, `command._build_ssh`), so the account
# has to be here and its password has to be that same lab default -- hence
# `AXR_SMB_PASS`, the one literal this file already duplicates from
# adapter.conf's DEFAULT_PASSWORD. Password login itself is already on:
# `_render_userdata` sets `ssh_pwauth: true` on every Linux guest
# (docs/WORK_ORDER_access_facts.md Part A), and check 5 below asserts it
# rather than assuming it.
AXR_SSH_USER=axr
# ~1 GiB across four files: big enough that one read is a real transfer, small
# enough that populating it is instant and the disk (60Gi) is never a factor.
AXR_BULK_COUNT=4
AXR_BULK_SIZE=256M
# `public`, and not a lab-flavoured name: `drive: command`'s `snmp` template
# takes its community from ONE of two sources (`knobs.snmp_community_source`)
# -- the literal string `public`, or the lab-wide DEFAULT_PASSWORD -- and
# never from a persona-authored literal. The mgmt persona chooses `public`
# (a password as an SNMPv2c community, in clear on the wire, would be a
# genuinely bad idea), so the agent has to answer to exactly that.
AXR_SNMP_COMMUNITY=public

# --- house helpers (copied byte-identical from the cast's recipes) -----------

axr_pkg() {
    _missing=""
    for _p in "$@"; do
        if ! dpkg-query -W -f='${Status}' "$_p" 2>/dev/null \
                | grep -q '^install ok installed$'; then
            _missing="$_missing $_p"
        fi
    done
    if [ -n "$_missing" ]; then
        DEBIAN_FRONTEND=noninteractive apt-get update -qq
        # shellcheck disable=SC2086
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
            --no-install-recommends $_missing
    fi
}

axr_unit_name() {
    for _u in "$@"; do
        if systemctl list-unit-files "$_u.service" >/dev/null 2>&1 \
                && systemctl list-unit-files "$_u.service" 2>/dev/null | grep -q "^$_u\.service"; then
            echo "$_u"; return 0
        fi
    done
    echo "$1"
}

axr_unit() {
    systemctl enable "$1" >/dev/null 2>&1 || true
    systemctl restart "$1"
    sleep 1
    if ! systemctl is-active --quiet "$1"; then
        systemctl status --no-pager --full "$1" >&2 || true
        echo "axr-serve: unit '$1' did not start" >&2
        exit 1
    fi
}

axr_die() { echo "axr-serve: $*" >&2; exit 1; }

# NOT from the cast's recipes -- new here, and the reason is at the check block
# below: every check in the first version of this file sent stderr to /dev/null
# and then reported a cause it had not observed. `axr_run` keeps the tool's own
# output in `$_out` and prints it, indented, when the tool fails.
_out=""
axr_run() {  # axr_run <what> <cmd...>  -> 0, or 1 with the tool's output shown
    _what="$1"; shift
    if _out="$("$@" 2>&1)"; then return 0; fi
    echo "axr-serve: $_what failed ($*):" >&2
    printf '%s\n' "$_out" | sed 's/^/    /' >&2
    return 1
}

# --- --healthcheck -----------------------------------------------------------
if [ "${1:-}" = "--healthcheck" ]; then
    _rc=0
    for _cands in "smbd" "snmpd"; do
        _u="$(axr_unit_name $_cands)"
        if systemctl is-active --quiet "$_u"; then echo "ok   unit $_u"; else echo "FAIL unit $_u"; _rc=1; fi
    done
    if ss -lnt 2>/dev/null | awk '{print $4}' | grep -q ':445$'; then
        echo "ok   listener tcp/445"
    else
        echo "FAIL listener tcp/445"; _rc=1
    fi
    # $4, not $5: `ss -lnu` (one protocol) omits the Netid column, so $5 is the
    # PEER address and never matches a port. Same bug cost the sink's op a run
    # (2026-09-21); the tcp checks above already read $4.
    if ss -lnu 2>/dev/null | awk '{print $4}' | grep -q ':161$'; then
        echo "ok   listener udp/161"
    else
        echo "FAIL listener udp/161"; _rc=1
    fi
    # 22 is not this recipe's service (sshd is in the base image), but the ssh
    # engine dials it here and a host with no listener would read at the
    # sensor as "ssh attempted, refused" -- a shape, not a session.
    if ss -lnt 2>/dev/null | awk '{print $4}' | grep -q ':22$'; then
        echo "ok   listener tcp/22"
    else
        echo "FAIL listener tcp/22"; _rc=1
    fi
    if id "$AXR_SSH_USER" >/dev/null 2>&1; then
        echo "ok   account $AXR_SSH_USER (the ssh engine's login)"
    else
        echo "FAIL account $AXR_SSH_USER"; _rc=1
    fi
    exit "$_rc"
fi

# --- packages ---------------------------------------------------------------
# The SERVER packages do not ship the CLIENT binaries, and the self-checks at
# the bottom of this file are clients:
#   smbclient  -- its own package; `samba`/`samba-common-bin` give smbd,
#                 testparm, smbpasswd, nmblookup and NOT /usr/bin/smbclient.
#   snmp       -- ships snmpget/snmpwalk; `snmpd` is the agent only.
# Missing them is what failed this op on dev45 (2026-09-21): `smbclient: not
# found` became "anonymous SMB session was refused", a wrong answer about a
# server that was in fact fine. Both are also the packages `drive: command`
# installs on a CLIENT host (`personas/drivers/command.py`: `package=smbclient`,
# `package=snmp`), so the names agree with the engine table on purpose.
axr_pkg samba samba-common-bin smbclient snmpd snmp

# --- the shares --------------------------------------------------------------
# `pub`  -- anonymous, read-only, holds the bulk files. The file persona reads
#           it with `smbclient -N`, i.e. no credential at all, so the byte
#           volume never depends on an identity being right.
# `corp` -- authenticated (the LOCAL `smbuser`), read-write. This is the share
#           that produces a real SESSION_SETUP with a real user name on the
#           wire, which is what a detection rule about SMB auth would key on.
#           Exercised by hand (README), not by a persona.
mkdir -p "$AXR_SHARE_ROOT/pub/bulk" "$AXR_SHARE_ROOT/pub/docs" "$AXR_SHARE_ROOT/corp"

_i=1
while [ "$_i" -le "$AXR_BULK_COUNT" ]; do
    _f="$AXR_SHARE_ROOT/pub/bulk/bulk-0$_i.dat"
    [ -f "$_f" ] || fallocate -l "$AXR_BULK_SIZE" "$_f" || axr_die "cannot create $_f"
    _i=$((_i + 1))
done

# A handful of small files so a directory listing looks like a share rather
# than like a staging area, and so a short read has something to read.
for _n in readme notes policy handover; do
    _f="$AXR_SHARE_ROOT/pub/docs/$_n.txt"
    if [ ! -f "$_f" ]; then
        printf 'axRange svc-engines file server -- synthetic content.\n%s\n' \
            "The protocol is real; this text is not a claim about anything observed." > "$_f"
    fi
done

chmod -R a+rX "$AXR_SHARE_ROOT/pub"

# The authenticated share's owner. A local POSIX account is required before a
# Samba account can exist; `--disabled-password` keeps it out of SSH.
if ! id "$AXR_SMB_USER" >/dev/null 2>&1; then
    adduser --quiet --disabled-password --gecos "axRange lab user" "$AXR_SMB_USER" \
        || axr_die "cannot create POSIX user $AXR_SMB_USER"
fi
chown -R "$AXR_SMB_USER":"$AXR_SMB_USER" "$AXR_SHARE_ROOT/corp"
chmod 0770 "$AXR_SHARE_ROOT/corp"

# Idempotent: `smbpasswd -a` re-sets an existing account's password rather than
# failing, so a re-run converges.
printf '%s\n%s\n' "$AXR_SMB_PASS" "$AXR_SMB_PASS" | smbpasswd -s -a "$AXR_SMB_USER" >/dev/null \
    || axr_die "cannot set the Samba password for $AXR_SMB_USER"
smbpasswd -e "$AXR_SMB_USER" >/dev/null 2>&1 || true

# --- the ssh engine's account ------------------------------------------------
# A real POSIX account with a real shell (the persona runs `true` over the
# session, which needs one) and the lab password. `chpasswd` is idempotent, so
# a re-run converges rather than failing.
if ! id "$AXR_SSH_USER" >/dev/null 2>&1; then
    adduser --quiet --disabled-password --gecos "axRange lab shell user" \
        --shell /bin/bash "$AXR_SSH_USER" \
        || axr_die "cannot create POSIX user $AXR_SSH_USER (the ssh engine logs in as it)"
fi
printf '%s:%s\n' "$AXR_SSH_USER" "$AXR_SMB_PASS" | chpasswd \
    || axr_die "cannot set the password for $AXR_SSH_USER -- the ssh engine
      authenticates with the lab-wide DEFAULT_PASSWORD and has no other credential"

cat > /etc/samba/smb.conf <<AXR_SMB_EOF
# axRange svc-engines -- written by serve-file-server.sh; do not hand-edit.
[global]
   workgroup = $AXR_WORKGROUP
   server string = axRange twin file server
   security = user
   # Anonymous access lands on the guest account instead of being refused, so
   # \`smbclient -N\` gets a COMPLETED session rather than an NT_STATUS error
   # -- the difference between "SMB classified and completed" and "SMB reset",
   # which is the whole acceptance criterion of the engine round.
   map to guest = Bad User
   guest account = nobody
   # \`restrict anonymous = 0\` (the default, pinned): 1/2 would refuse the
   # NULL session that \`smbclient -N\` opens before it ever reaches a share.
   restrict anonymous = 0
   usershare allow guests = yes
   # Signing and encryption are both AUTO/OFF deliberately, and both for the
   # same reason: a guest session has no session key, so a server that REQUIRES
   # either one refuses anonymous access outright (NT_STATUS_ACCESS_DENIED at
   # session setup -- indistinguishable, from the client, from a wrong password).
   # \`smb encrypt = off\` also keeps SMB READABLE at the sensor, which is this
   # twin's whole point: an encrypted SMB3 flow is \`smb\` in the eve.json and
   # nothing else, and the 2.2 GB of internal SMB would then prove less than it
   # looks like it does.
   server signing = auto
   client signing = auto
   smb encrypt = off
   # SMB2 floor: SMB1 is off in every modern client anyway, and a dialect
   # downgrade would make the capture unrepresentative of what a real 2025
   # client negotiates.
   server min protocol = SMB2
   client min protocol = SMB2
   # 445 only -- see the header. nmbd is masked below.
   disable netbios = yes
   smb ports = 445
   log file = /var/log/samba/log.%m
   max log size = 1000
   load printers = no
   printing = bsd
   printcap name = /dev/null
   disable spoolss = yes

[pub]
   comment = Public bulk share (anonymous, read-only)
   path = $AXR_SHARE_ROOT/pub
   browseable = yes
   read only = yes
   # \`guest ok\` and \`public\` are the same parameter; one spelling only.
   # \`guest only\` lands EVERY session on this share as the guest account, so a
   # client that presents a credential this host does not know still reads the
   # bulk files instead of being refused -- the byte volume is never allowed to
   # depend on an identity being right.
   guest ok = yes
   guest only = yes

[corp]
   comment = Corporate share (authenticated)
   path = $AXR_SHARE_ROOT/corp
   browseable = yes
   read only = no
   guest ok = no
   valid users = $AXR_SMB_USER
AXR_SMB_EOF

axr_run "testparm on the rendered /etc/samba/smb.conf" testparm -s \
    || axr_die "smb.conf was rejected by testparm (its complaint is above) --
      a parameter name or value this Samba does not accept"

# nmbd would bind 137/138 and advertise this host on the tapped segment; the
# declared listen set says 445 only, and a service the twin did not declare is
# noise a reader has to explain away.
systemctl disable --now nmbd >/dev/null 2>&1 || true
systemctl mask nmbd >/dev/null 2>&1 || true
axr_unit "$(axr_unit_name smbd)"

# --- SNMP --------------------------------------------------------------------
# Debian's snmpd ships bound to 127.0.0.1, which would make the mgmt persona's
# `snmpget` time out at a host that looks up. Bind the wildcard and give it a
# read-only community. Read-only and lab-named on purpose: this is a monitored
# NAS, not a writable device.
cat > /etc/snmp/snmpd.conf <<AXR_SNMP_EOF
# axRange svc-engines -- written by serve-file-server.sh; do not hand-edit.
agentaddress udp:161
rocommunity $AXR_SNMP_COMMUNITY default
sysLocation axRange svc-engines twin
sysContact  axrange-lab
sysServices 72
AXR_SNMP_EOF
axr_unit "$(axr_unit_name snmpd)"

# --- prove it, in-guest ------------------------------------------------------
# A share that exists in smb.conf but cannot be opened is the silent failure
# these checks exist to turn into a failed op (LES-001).
#
# Each check KEEPS the tool's own output (`axr_run`, above) and prints it when
# it fails. The first version of this file sent every check's stderr to
# /dev/null and reported "anonymous SMB session to 127.0.0.1 was refused" --
# while the actual message was `smbclient: not found`. A check that names the
# wrong cause is worse than no check: it sends the next person to smb.conf,
# which was never wrong.

# 1. The anonymous session + share open -- what the file-client persona does
#    (`auth_credential: none` renders `smbclient -N`). Opening the share, not
#    `smbclient -N -L` (a browse), is the authoritative check: with SMB1 off
#    `-L` also tries a NetBIOS workgroup listing it cannot do here
#    (`disable netbios = yes`), so its exit code says as much about SMB1 as
#    about guest access. Open what the persona opens.
axr_run "anonymous SMB to //127.0.0.1/pub" \
    smbclient -N -m SMB3 //127.0.0.1/pub -c 'ls' \
    || axr_die "anonymous SMB to //127.0.0.1/pub was refused -- look at, in order:
      (a) is smbclient installed at all (this script installs the 'smbclient' package);
      (b) /etc/samba/smb.conf: 'security = user' + 'map to guest = Bad User' +
          'guest account = nobody' + 'restrict anonymous = 0', and on [pub]
          'guest ok = yes' -- a guest session cannot be signed or encrypted, so
          'server signing = mandatory' or 'smb encrypt = required' refuse it;
      (c) filesystem: $AXR_SHARE_ROOT/pub must be traversable+readable by 'nobody';
      (d) /var/log/samba/log.* for the NT_STATUS the server actually returned"

# 2. The exact path the persona reads (knobs.smb_share=pub, smb_file=bulk/bulk-01.dat).
#    Grep the LISTING rather than trusting smbclient's exit code for a missing
#    file: a share that opens but is empty would otherwise pass.
axr_run "listing the 'pub' bulk directory" \
    smbclient -N -m SMB3 //127.0.0.1/pub -c 'ls bulk\*' \
    || axr_die "the anonymous 'pub' share did not open its bulk directory"
printf '%s\n' "$_out" | grep -q 'bulk-01\.dat' \
    || axr_die "'pub' opened but bulk-01.dat is missing -- the file-client persona
      reads exactly //<files>/pub/bulk/bulk-01.dat (knobs.smb_file); check the
      fallocate loop above and $AXR_SHARE_ROOT/pub/bulk"

# 3. The authenticated share. Checked because the README claims it works, and
#    because an smbpasswd account that silently failed to be created is exactly
#    the kind of thing that is discovered by hand, weeks later. The credential
#    goes in a 0600 authfile and never on a command line -- the same split
#    `drive: command`'s smb template uses (`-A <authfile>`, never `-U u%p`).
_authfile="$(mktemp)"
chmod 0600 "$_authfile"
printf 'username=%s\npassword=%s\ndomain=%s\n' \
    "$AXR_SMB_USER" "$AXR_SMB_PASS" "$AXR_WORKGROUP" > "$_authfile"
_rc=0
axr_run "authenticated SMB to //127.0.0.1/corp as $AXR_SMB_USER" \
    smbclient -A "$_authfile" -m SMB3 //127.0.0.1/corp -c 'ls' || _rc=$?
rm -f "$_authfile"
[ "$_rc" -eq 0 ] || axr_die "the 'corp' share refused $AXR_SMB_USER -- the POSIX
      user exists but its SAMBA account may not: re-check the 'smbpasswd -a/-e'
      pair above, and 'valid users' in [corp]"

# 4. SNMP, with the community the mgmt persona uses (knobs.snmp_community_source).
# NUMERIC OID, deliberately. Debian's `snmp` package ships no MIB files (they are
# non-free, in snmp-mibs-downloader), so a symbolic name fails with "Unknown
# Object Identifier (Sub-id not found: (top) -> sysDescr)" against a perfectly
# healthy agent -- measured on this twin 2026-09-21. .1.3.6.1.2.1.1.1.0 IS
# sysDescr.0 and needs no MIB. The persona's own snmp emit is numeric for the
# same reason.
axr_run "snmpget .1.3.6.1.2.1.1.1.0 (sysDescr.0) on 127.0.0.1" \
    snmpget -v2c -c "$AXR_SNMP_COMMUNITY" -r 2 -t 2 127.0.0.1 .1.3.6.1.2.1.1.1.0 \
    || axr_die "snmpd did not answer sysDescr.0 -- check 'rocommunity
      $AXR_SNMP_COMMUNITY' and 'agentaddress udp:161' in /etc/snmp/snmpd.conf
      (the 'snmp' package provides snmpget; 'snmpd' alone does not)"

# 5. The ssh engine's login path, both halves, asserted rather than assumed --
#    the account exists AND sshd will accept a password for it. Checked here
#    because the failure is otherwise invisible until a persona window is over
#    and the log says `Permission denied (publickey,password)`, which names
#    neither half. `sshd -T` prints the EFFECTIVE config (drop-ins included),
#    which is the only honest source: cloud-init writes
#    `PasswordAuthentication yes` into /etc/ssh/sshd_config.d/, and a Debian
#    cloud image ships a drop-in that says no.
id "$AXR_SSH_USER" >/dev/null 2>&1 \
    || axr_die "POSIX user $AXR_SSH_USER does not exist after adduser -- the ssh
      engine would fail every firing with 'Permission denied'"
_pwauth="$(sshd -T 2>/dev/null | awk '$1 == "passwordauthentication" {print $2}' || true)"
[ "$_pwauth" = "yes" ] || axr_die "sshd's effective PasswordAuthentication is
      '${_pwauth:-unknown}', not 'yes' -- the ssh engine authenticates
      $AXR_SSH_USER by password (sshpass; there is no key in this twin). Look
      at /etc/ssh/sshd_config.d/*.conf; cloud-init's ssh_pwauth writes the
      'yes' this expects (docs/WORK_ORDER_access_facts.md Part A)"

# --- E3: this host resolves through the twin resolver ------------------------
if systemctl is-active --quiet systemd-resolved 2>/dev/null; then
    systemctl disable --now systemd-resolved
fi
rm -f /etc/resolv.conf
printf 'nameserver %s\nsearch svc.axr.lab corp.axr.lab\n' "$AXR_RESOLVER_IP" > /etc/resolv.conf

AXR_BYTES="$(du -sh --apparent-size "$AXR_SHARE_ROOT/pub/bulk" 2>/dev/null | awk '{print $1}')"
echo "axr-serve: file server ready (445/tcp, shares pub+corp, ${AXR_BYTES:-?} of bulk files)"
echo "axr-serve: snmpd ready (161/udp, community $AXR_SNMP_COMMUNITY)"
echo "axr-serve: ssh login ready (22/tcp, account $AXR_SSH_USER, password auth)"
