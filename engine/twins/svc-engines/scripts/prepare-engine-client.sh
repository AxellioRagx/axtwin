#!/bin/sh
# axRange svc-engines -- prepare the ENGINE CLIENT (work order §5 E3/E4/E5).
#
# The one script action this host is allowed (`pass1_emit._emit_apps` permits
# exactly one `script:` per app), so it does the three things a protocol engine
# cannot do for itself, and nothing else:
#
#   0. **The one ambiguous package** -- `ntpdate`. Debian renamed the package
#      that ships that binary across the ntp->ntpsec transition (`ntpdate` on
#      bullseye, `ntpsec-ntpdate` on bookworm) and a `pkg:` action naming the
#      wrong one FAILS THE DEPLOY rather than degrading (Ansible's apt module
#      fails the task -> `provision-app` fails -> halt), in the middle of a run
#      that costs two Windows VMs and a forest promotion to reach. svc-verify
#      hit this and wrapped it in a script of its own; that slot is a limit of
#      one per app (`pass1_emit._emit_apps`), so here it is step 0 of this one.
#      Every other tool stays a plain `pkg:` action, where it is visible in the
#      op row and in `te info`.
#   1. **Realm config** -- `/etc/krb5.conf`. GAP-074, found by svc-verify's own
#      run: *"no automated consumer can obtain a ticket until realm config
#      becomes twin content -- a `drive: command` persona can do dns/http/ntp/
#      icmp unattended and CANNOT do Kerberos for exactly this reason."* This
#      file is that fix for this twin. `krb5-user` ships no realm on a fresh
#      host, and `kinit alice@CORP.AXR.LAB` without one fails before a single
#      packet leaves -- which reads at the sensor as "the Kerberos engine
#      produced nothing", i.e. a silent wrong answer (LES-001).
#   2. **A fail-loud tool census** -- every engine tool this twin's personas
#      need, checked once, loudly, here. The drivers probe again at arm time
#      (`CommandDriver._start_linux`), but that failure surfaces in a persona
#      log an hour later; this one fails the DEPLOY, which is where a missing
#      package belongs. It censuses two kinds of thing: BINARIES (`command
#      -v`) and MECHANISMS -- a plugin a tool dlopen's, which ships no binary
#      and which no `command -v` can ever catch (`AXR_MECHS`, and see
#      `command.py`'s `required_packages`).
#   3. **The resolver pointer** -- E3 hop 1 for Linux. Every Linux host in this
#      twin resolves through `resolver` (10.66.10.11); nothing here ever asks a
#      public resolver, and there is no `1.1.1.1` left in this guest's
#      configuration after this runs. **It is deliberately the LAST thing this
#      script does**: the twin resolver serves only observed names, so an
#      `apt-get install` after the repoint fails with `Could not resolve
#      'deb.debian.org'` (measured, dev45 2026-09-21). Install everything
#      first, repoint last.
#
# **Why `install:` and not an image bake.** Work order §5 E7 says this whole
# tool set (plus Chromium and the sink's lab CA) belongs in an image; LES-024
# agrees (bake fragile tooling rather than apt-install it in an isolated
# guest), and GAP-056 is the open gap. Legal here ONLY because this twin is a
# throwaway -- the same `install:` list on a standing twin trips `--update`'s
# in-place mutation refusal.
#
# **The lab CA is the one thing this script cannot do.** The sink MINTS its CA
# at run time (`serve-sink.sh`, `/etc/axr-sink/ca/axr-sink-ca.crt`), so it does
# not exist at image-build time and is not published over any port this guest
# can fetch. Until E7 bakes it, the browser persona sets
# `knobs.browser_ignore_cert_errors: true` and TLS fidelity is unaffected
# (the SNI, the ALPN, the JA3/JA4 and the certificate the sink presents are all
# real) -- only VALIDATION is skipped, and that is recorded in the persona and
# in this folder's README rather than hidden.
#
# Delivered as an `install: [{script: ...}]` action, so it runs through
# `run-script` (OP-APP-030) IN-GUEST as root, honouring this shebang.
#
#   prepare-engine-client.sh                configure + census (idempotent)
#   prepare-engine-client.sh --healthcheck  exit 0 only when every tool is present
set -eu
umask 022
mkdir -p /usr/local/lib/axr /var/log/axrange

AXR_RESOLVER_IP=10.66.10.11
AXR_REALM=CORP.AXR.LAB
AXR_DOMAIN=corp.axr.lab
AXR_DC_NAME=dc.corp.axr.lab

# Every binary an svc-engines persona invokes, as `binary:package` pairs. The
# `command` driver's own `required_binaries()` is the authority on which tool
# each proto uses; this list is the same set, written where a human reads it.
# `kvno` is listed beside `kinit` deliberately: the `kerberos` template's
# FOLLOW-UP step runs it (`command._follow_up_kerberos`), and the TGS exchange
# it produces is where the detection value lives -- a `kinit` alone never asks
# for a service ticket.
AXR_TOOLS="dig:dnsutils curl:curl kinit:krb5-user kvno:krb5-user \
ldapsearch:ldap-utils smbclient:smbclient rpcclient:smbclient \
xfreerdp:freerdp2-x11 ssh:openssh-client sshpass:sshpass snmpget:snmp \
ping:iputils-ping ntpdate:ntpsec-ntpdate chromium:chromium"

# The other half of the census, and the half `command -v` is STRUCTURALLY
# blind to: things a tool dlopen's, which ship no binary at all. As
# `file:package` pairs, checked by existence of the file.
#
# `libgssapiv2.so` is the Cyrus-SASL GSSAPI mechanism plugin that
# `ldapsearch -Y GSSAPI` loads. **`ldap-utils` does not depend on it.**
# Without it the bind fails with `Unknown authentication method (-6) ...
# SASL(-4): no mechanism available: No worthy mechs found` -- which reads as
# "the DC refused GSSAPI" and is in fact "this client cannot spell GSSAPI".
# Measured on dev45 2026-09-21. The multiarch directory is globbed rather
# than hard-coded to `x86_64-linux-gnu` so an arm64 guest reads the same.
# `libsasl2-modules` is the base plugin set the mech package assumes.
AXR_MECHS="/usr/lib/*/sasl2/libgssapiv2.so:libsasl2-modules-gssapi-mit"

axr_die() { echo "axr-client: $*" >&2; exit 1; }

# Per-tool status goes to STDERR (so a caller can read it) and the list of
# MISSING binaries -- the machine-readable part -- to stdout. Keeping the two
# on separate streams is what lets both callers below use one function without
# parsing each other's output.
axr_census() {
    _missing=""
    for _pair in $AXR_TOOLS; do
        _bin="${_pair%%:*}"
        _pkg="${_pair##*:}"
        if command -v "$_bin" >/dev/null 2>&1; then
            echo "ok   $_bin ($_pkg)" >&2
        else
            echo "FAIL $_bin -- install $_pkg" >&2
            _missing="$_missing $_bin"
        fi
    done
    for _pair in $AXR_MECHS; do
        _glob="${_pair%%:*}"
        _pkg="${_pair##*:}"
        # No quotes on $_glob: the glob has to expand. `set -- ` + `[ -e ]`
        # on the first word is the POSIX way to ask "did it match anything".
        # shellcheck disable=SC2086
        set -- $_glob
        if [ -e "$1" ]; then
            echo "ok   $1 ($_pkg)" >&2
        else
            echo "FAIL $_glob -- install $_pkg" >&2
            _missing="$_missing $_glob"
        fi
    done
    echo "$_missing"
}

# --- --healthcheck -----------------------------------------------------------
if [ "${1:-}" = "--healthcheck" ]; then
    _miss="$(axr_census)"
    if [ -n "$(echo "$_miss" | tr -d ' ')" ]; then
        echo "FAIL missing tools:$_miss"; exit 1
    fi
    echo "ok   every engine tool present"
    exit 0
fi

# --- 0. ntpdate, under whichever name this release uses ----------------------
# Try both, then assert the outcome that actually matters: `ntpdate` on PATH.
# The binary lands in /usr/sbin, which is on root's PATH but not on every
# non-login PATH -- the check below says which case failed.
if ! command -v ntpdate >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get update -qq || true
    for _pkg in ntpsec-ntpdate ntpdate; do
        if DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
                --no-install-recommends "$_pkg" >/dev/null 2>&1; then
            echo "axr-client: installed package '$_pkg' for the ntpdate binary"
            break
        fi
        echo "axr-client: package '$_pkg' not installable here, trying next" >&2
    done
fi
if ! command -v ntpdate >/dev/null 2>&1 && [ -x /usr/sbin/ntpdate ]; then
    axr_die "/usr/sbin/ntpdate exists but is not on PATH ($PATH)"
fi

# --- 1. Kerberos realm -------------------------------------------------------
# The KDC is named, not an IP literal -- that is the POINT of E3 and the
# reversal of the old IP-literals-only containment rule (ruling 1, 2026-09-21).
# A ticket is issued against an SPN (`cifs/dc.corp.axr.lab`), not against an
# address, so a realm config that names the DC by IP produces tickets whose
# service principals cannot match anything the DC registered.
#
# `dns_lookup_kdc = false`: the KDC is pinned here rather than discovered via
# `_kerberos._tcp` SRV. Both work through this twin's chain (the resolver
# forwards the AD zone to the DC, which serves the SRV records), but a pinned
# KDC makes a Kerberos failure mean "the KDC did not answer" and never "the SRV
# lookup was the thing that broke", which is a distinction the engine round
# needs to keep.
#
# `rdns = false` stops the libs from reverse-resolving the KDC's address into a
# different name than the one in the SPN -- the classic "KDC reply did not
# match expectations" that reads like a Kerberos fault and is a DNS fault.
cat > /etc/krb5.conf <<AXR_KRB5_EOF
# axRange svc-engines -- written by prepare-engine-client.sh; do not hand-edit.
[libdefaults]
  default_realm = $AXR_REALM
  dns_lookup_realm = false
  dns_lookup_kdc = false
  rdns = false
  ticket_lifetime = 24h
  forwardable = true

[realms]
  $AXR_REALM = {
    kdc = $AXR_DC_NAME
    admin_server = $AXR_DC_NAME
  }

[domain_realm]
  .$AXR_DOMAIN = $AXR_REALM
  $AXR_DOMAIN = $AXR_REALM
AXR_KRB5_EOF

# --- 2. the census -----------------------------------------------------------
AXR_MISSING="$(axr_census)"
if [ -n "$(echo "$AXR_MISSING" | tr -d ' ')" ]; then
    axr_die "missing engine tools:$AXR_MISSING -- the personas that use them would
fail at arm time with a MISSING TOOL line nobody reads until the window is over"
fi

# Chromium specifically: present-but-unrunnable is a real outcome on a
# headless guest (a missing shared library, a sandbox that cannot start), and
# it fails per page load rather than at arm time.
chromium --headless=new --no-sandbox --disable-gpu --version >/dev/null 2>&1 \
    || chromium --version >/dev/null 2>&1 \
    || axr_die "chromium is installed but will not run headless"

# --- 3. E3 hop 1: resolve through the twin resolver --------------------------
# **LAST, and the order is the fix.** Measured on dev45 2026-09-21: once this
# host resolves through the twin resolver, `apt-get install` fails with
# `Could not resolve 'deb.debian.org'` -- correct MEASURE-phase behaviour
# (the resolver is authoritative for the 613 observed names and has no
# upstream to ask) arriving in the middle of PROVISIONING. So the rule, for
# this script and for every recipe on a twin with its own name service:
#
#   INSTALL EVERYTHING FIRST, REPOINT DNS LAST.
#
# Everything above this line may apt (step 0 does); nothing below may. If you
# need a package on this host AFTER the deploy, repoint /etc/resolv.conf at
# the lab resolver, install, and repoint back -- and remember a capture window
# that overlapped that detour is not a contained one (work order §5 E1/E3).
#
# systemd-resolved is displaced rather than reconfigured: a 127.0.0.53 stub in
# front of the twin resolver is one more thing that can answer differently,
# and Chromium's own resolver would see the stub, not the chain.
if systemctl is-active --quiet systemd-resolved 2>/dev/null; then
    systemctl disable --now systemd-resolved
fi
rm -f /etc/resolv.conf
printf 'nameserver %s\nsearch svc.axr.lab %s\n' "$AXR_RESOLVER_IP" "$AXR_DOMAIN" > /etc/resolv.conf

# --- 4. report the chain, without making it fatal ----------------------------
# The resolver, the DC and the sink are OTHER hosts whose run-script may not
# have completed (no cross-host ordering between apps). Probed and REPORTED;
# README step 3 is the gate.
for _n in "web.svc.axr.lab" "$AXR_DC_NAME" "github.com"; do
    if dig +short +time=2 +tries=1 "$_n" A 2>/dev/null | grep -q '[0-9]'; then
        echo "axr-client: resolves $_n -> $(dig +short +time=2 +tries=1 "$_n" A | head -1)"
    else
        echo "axr-client: NOTE $_n does not resolve yet (the resolver/DC/sink may still be provisioning)"
    fi
done

echo "axr-client: engine client ready -- realm $AXR_REALM (kdc $AXR_DC_NAME)," \
     "resolver $AXR_RESOLVER_IP, $(echo "$AXR_TOOLS" | wc -w) tools present"
