#!/bin/sh
# axRange svc-engines -- the TWIN RESOLVER (work order §5 E3, "twin name service").
#
# HAND-WRITTEN for this twin. It is NOT one of aximprint's generated
# `serve-*.sh` recipes and must not be mistaken for one -- but every helper
# below (`axr_pkg`, `axr_unit_name`, `axr_unit`, `axr_self_ip`) is copied
# BYTE-IDENTICAL from `twin-configs/svc-verify/scripts/serve-dns-resolver-10-80-
# 64-core.sh`, so the fail-loud and install-only-what-is-missing behaviour are
# exactly the cast's.
#
# **Why not the cast recipe verbatim** (the twin config's `resolver` node says
# this at length; the short form):
#   1. it answers EVERY name, including `*`, with the RESOLVER'S OWN address --
#      which would send `\\files.svc.axr.lab\corp` and `cifs/dc.corp.axr.lab`
#      at bind9 instead of at the file server and the DC;
#   2. it forwards to the ROUTER, which is not a resolver (everything outside
#      the zone SERVFAILs);
#   3. it is authoritative for `corp.axr.lab`, which here is the AD forest the
#      DC owns -- two authorities for one zone makes every result unreadable.
#
# THE CHAIN THIS FILE IMPLEMENTS (hop 2 of 3):
#
#   Linux hosts ---------> THIS HOST (10.66.10.11)
#   Windows member ------> the DC (set by the join op) ---> forwarder = THIS HOST
#   THIS HOST:
#       svc.axr.lab ..................... AUTHORITATIVE (the zone below)
#       corp.axr.lab / _msdcs.corp... ... FORWARD ONLY -> the DC  (10.66.10.21)
#       everything else ................. FORWARD ONLY -> the SINK (10.66.30.10)
#
# **Nothing ever reaches a real public resolver.** There is no `1.1.1.1`
# anywhere, `recursion` is on but `forward only` (never `forward first`) means
# bind9 may not fall back to the root hints, and `dnssec-validation no` keeps a
# lab-signed world from being refused. If the sink is down, external names FAIL
# -- loudly and locally -- rather than leaking to the internet. That is the
# design: containment is a property of where the packets can GO.
#
# **The one thing only a cluster settles.** The sink is an AUTHORITATIVE root
# server (`recursion no`, RA=0), not a recursive resolver, and `forward only`
# normally expects a recursive forwarder. bind9 accepts an authoritative
# NOERROR/NXDOMAIN answer for the qname, which is what the sink always returns
# (its root zone covers every corpus name and NXDOMAINs the rest), so this is
# expected to work -- but if README step 3's external probe SERVFAILs while the
# same query direct to the sink succeeds, that is the cause, and the fix is one
# stanza: replace the `forwarders`/`forward only` pair in named.conf.options
# with
#     zone "." { type static-stub; server-addresses { 10.66.30.10; }; };
# in named.conf.local, which makes bind9 treat the sink as the root server it
# actually is. Recorded here rather than guessed at on the cluster.
#
# Delivered as an `install: [{script: ...}]` action, so it runs through
# `run-script` (OP-APP-030) IN-GUEST as root, honouring this shebang.
set -eu
umask 022
mkdir -p /usr/local/lib/axr /var/log/axrange

# The twin's pinned addresses. Hardcoded, and they MUST agree with
# `svc-engines.yaml` -- every host IP in this twin is pinned precisely so the
# name service, the certificates and the persona knobs can name each other
# literally. A mismatch here is a chain that answers the wrong host, which is
# worse than a chain that does not answer at all.
AXR_DC_IP=10.66.10.21
AXR_SINK_IP=10.66.30.10
AXR_WEB_IP=10.66.10.12
AXR_FILES_IP=10.66.10.13
AXR_CLIENT_IP=10.66.20.11
AXR_GW_SVC_IP=10.66.10.1
AXR_DOMAIN=corp.axr.lab
AXR_ZONE=svc.axr.lab

# --- house helpers (copied byte-identical from the cast's recipes) -----------

# Install only what is MISSING, and refresh the index first. A cloned guest's apt index is
# stale by construction -- axRange fixed exactly this on its own `provision-app` path on
# 2026-08-14, and a `pkg:` action having already run is the common case here, which is why the
# whole body is skipped when nothing is missing.
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

# A Debian package's unit name is not stable across releases (bind9 -> named, chrony ->
# chronyd on RH-derived images). Resolve it from what is actually installed rather than
# guessing at render time.
axr_unit_name() {
    for _u in "$@"; do
        if systemctl list-unit-files "$_u.service" >/dev/null 2>&1 \
                && systemctl list-unit-files "$_u.service" 2>/dev/null | grep -q "^$_u\.service"; then
            echo "$_u"; return 0
        fi
    done
    echo "$1"
}

# FAIL LOUD. A server that did not start is indistinguishable downstream from the no-listener
# defect this whole file replaces, and `systemctl is-active` is the only thing that can tell
# them apart (axRange LES-079/LES-086; never `nohup ... &`, which the guest-agent session
# reaps).
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

axr_self_ip() { ip -4 -o addr show scope global | awk '{split($4,a,"/"); print a[1]; exit}'; }

axr_die() { echo "axr-serve: $*" >&2; exit 1; }

# --- --healthcheck -----------------------------------------------------------
# Every long-running component exposes one (CLAUDE.md, "Health/metrics are
# first-class"). Exit 0 only when named is up AND all three answer paths work.
if [ "${1:-}" = "--healthcheck" ]; then
    _rc=0
    _u="$(axr_unit_name named bind9)"
    if systemctl is-active --quiet "$_u"; then echo "ok   unit $_u"; else echo "FAIL unit $_u"; _rc=1; fi
    if dig +short +time=2 +tries=1 @127.0.0.1 "web.$AXR_ZONE" A | grep -q "$AXR_WEB_IP"; then
        echo "ok   authoritative zone $AXR_ZONE"
    else
        echo "FAIL authoritative zone $AXR_ZONE"; _rc=1
    fi
    if dig +time=3 +tries=1 @127.0.0.1 "dc.$AXR_DOMAIN" A 2>/dev/null | grep -q 'status: NOERROR'; then
        echo "ok   forward $AXR_DOMAIN -> DC"
    else
        echo "FAIL forward $AXR_DOMAIN -> DC ($AXR_DC_IP)"; _rc=1
    fi
    if dig +time=3 +tries=1 @127.0.0.1 github.com A 2>/dev/null | grep -q 'status: NOERROR'; then
        echo "ok   forward external -> sink"
    else
        echo "FAIL forward external -> sink ($AXR_SINK_IP)"; _rc=1
    fi
    exit "$_rc"
fi

# --- packages ---------------------------------------------------------------
axr_pkg bind9 bind9-utils bind9-dnsutils

# --- displace the stub resolver ---------------------------------------------
# systemd-resolved owns :53 on a stock cloud image, so `named` cannot bind it
# without displacing the stub first (the cast recipe's own measured finding:
# a port-53 listener crashed on EADDRINUSE while its unit still reported
# healthy). This host then resolves through itself.
if systemctl is-active --quiet systemd-resolved 2>/dev/null; then
    systemctl disable --now systemd-resolved
fi
AXR_IP="$(axr_self_ip)"
[ -n "$AXR_IP" ] || axr_die "no global IPv4 address on this host"
rm -f /etc/resolv.conf
printf 'nameserver 127.0.0.1\nsearch %s %s\n' "$AXR_ZONE" "$AXR_DOMAIN" > /etc/resolv.conf

# --- the twin-internal zone --------------------------------------------------
# `svc.axr.lab`: the Linux side of the twin, one A record per host at its REAL
# address. No wildcard: a wildcard is how "the name resolved" stops meaning
# "the name resolved to the right host", and every engine in this twin is a
# claim about reaching a specific server.
#
# The SOA serial is content-derived, never a clock (the cast's own rule), so a
# re-run of this script produces a byte-identical zone.
cat > /etc/bind/db.svc-axr-lab <<AXR_ZONE_EOF
\$TTL 300
@   IN  SOA   ns.$AXR_ZONE. hostmaster.$AXR_ZONE. (
        2026092101 ; serial (content-derived, never a clock)
        3600       ; refresh
        600        ; retry
        604800     ; expire
        300 )      ; negative TTL
@   IN  NS    ns.$AXR_ZONE.
ns                       IN  A     $AXR_IP
resolver                 IN  A     $AXR_IP
web                      IN  A     $AXR_WEB_IP
files                    IN  A     $AXR_FILES_IP
sink                     IN  A     $AXR_SINK_IP
gw                       IN  A     $AXR_GW_SVC_IP
client                   IN  A     $AXR_CLIENT_IP
AXR_ZONE_EOF

cat > /etc/bind/named.conf.local <<AXR_LOCAL_EOF
// The twin's own Linux service names.
zone "$AXR_ZONE" {
    type master;
    file "/etc/bind/db.svc-axr-lab";
};

// The AD forest belongs to the DC, which is AUTHORITATIVE for it. Forwarding
// (never a second master copy) is what lets a LINUX client resolve
// dc.$AXR_DOMAIN, the _msdcs SRV records and any host record the DC registers
// -- i.e. what makes a real SPN (cifs/dc.$AXR_DOMAIN) and a real UNC path
// (\\\\dc.$AXR_DOMAIN\\SYSVOL) possible from a non-member.
//
// \`forward only\`: if the DC is down, the AD zone FAILS here rather than
// falling through to the sink, which would answer NXDOMAIN and make a dead DC
// look like a missing name.
zone "$AXR_DOMAIN" {
    type forward;
    forward only;
    forwarders { $AXR_DC_IP; };
};
zone "_msdcs.$AXR_DOMAIN" {
    type forward;
    forward only;
    forwarders { $AXR_DC_IP; };
};
AXR_LOCAL_EOF

cat > /etc/bind/named.conf.options <<AXR_OPT_EOF
options {
    directory "/var/cache/bind";
    listen-on { any; };
    listen-on-v6 { none; };
    allow-query { any; };
    recursion yes;
    allow-recursion { any; };
    // EVERYTHING not matched by a zone above goes to the SINK, which is
    // authoritative for the root over the cast's observed names. \`forward
    // only\` is load-bearing: with \`forward first\` bind9 would fall back to
    // the ROOT HINTS on a sink hiccup and the twin would resolve against the
    // real internet -- the exact containment hole this twin exists to close.
    forwarders { $AXR_SINK_IP; };
    forward only;
    // There are no root hints worth keeping when the sink IS the root.
    dnssec-validation no;
    version none;
};
AXR_OPT_EOF

# Debian's named.conf includes named.conf.default-zones, whose \`zone "."\`
# (type hint) is the root-hints path this resolver must never take.
sed -i '/named\.conf\.default-zones/d' /etc/bind/named.conf

named-checkconf || axr_die "named.conf rejected"
named-checkzone "$AXR_ZONE" /etc/bind/db.svc-axr-lab >/dev/null \
    || axr_die "the $AXR_ZONE zone is invalid"
axr_unit "$(axr_unit_name named bind9)"

# --- prove it, in-guest ------------------------------------------------------
# The authoritative half is decidable HERE and now, so it fails the op when
# wrong. The two FORWARDING halves depend on hosts whose own `run-script` may
# not have completed yet (there is no cross-host ordering between apps), so
# they are probed and REPORTED, never fatal -- a fatal check here would turn a
# race into a failed deploy. `--healthcheck` re-runs all three later, and
# README step 3 is the real gate.
AXR_ANS="$(dig +short +time=2 +tries=2 @127.0.0.1 "web.$AXR_ZONE" A | head -1)"
[ "$AXR_ANS" = "$AXR_WEB_IP" ] \
    || axr_die "authoritative zone answered '$AXR_ANS' for web.$AXR_ZONE, expected $AXR_WEB_IP"

if dig +time=3 +tries=1 @127.0.0.1 "dc.$AXR_DOMAIN" A 2>/dev/null | grep -q 'status: NOERROR'; then
    echo "axr-serve: AD zone $AXR_DOMAIN forwards to the DC ($AXR_DC_IP) -- answering"
else
    echo "axr-serve: NOTE $AXR_DOMAIN does not answer yet via $AXR_DC_IP" \
         "(the DC's promotion may still be running) -- re-check with --healthcheck"
fi
if dig +time=3 +tries=1 @127.0.0.1 github.com A 2>/dev/null | grep -q 'status: NOERROR'; then
    echo "axr-serve: external names forward to the sink ($AXR_SINK_IP) -- answering"
else
    echo "axr-serve: NOTE external names do not answer yet via $AXR_SINK_IP" \
         "(the sink's recipe may still be running) -- re-check with --healthcheck"
fi

echo "axr-serve: twin resolver ready on $AXR_IP (53/udp+tcp):" \
     "$AXR_ZONE authoritative, $AXR_DOMAIN -> $AXR_DC_IP, everything else -> $AXR_SINK_IP"
