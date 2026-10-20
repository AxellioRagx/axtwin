#!/bin/sh
# axRange twin SINK -- one VM that stands in for the whole observed external world.
#
# HAND-WRITTEN shared content (not a per-twin generated `serve-*.sh`): every twin
# that needs a sink installs this same file. Its DATA -- the observed names and the
# external address set -- is generated per cast by `scripts/gen_sink_corpus.py` and
# delivered by that generator's `install-sink-corpus.sh`, which MUST run first.
#
# WHY. A twin's hosts run REAL protocol clients, and a real client needs a real
# server on the far end of every destination the cast observed (ruling 2026-09-21:
# *a proxy that all external IPs are routed to, which responds as if the remote
# servers were responding*). Egress to the real internet stays DENIED during
# measurement, so this box is the entire internet as far as the twin is concerned.
#
# WHAT IT SERVES
#   53/udp,tcp   bind9, AUTHORITATIVE FOR '.' over the generated corpus:
#                an observed name answers with its really-observed address,
#                every other name in the world is NXDOMAIN, nothing is forwarded.
#   443/tcp,udp  Caddy, TLS with a certificate minted PER SNI ON THE FLY from a
#                lab CA -- HTTP/1.1, HTTP/2 and HTTP/3 (QUIC).
#   80/tcp       Caddy, plain HTTP.
#   123/udp      chrony, serving (`local stratum 8`: it serves without being synced,
#                which it cannot be -- there is no upstream).
#   22/tcp       sshd, as shipped.
#   everything   a generic TCP/UDP responder, so an unclaimed port completes a
#     else       connection instead of answering RST.
#
# TOOL CHOICES, and what was rejected
#   DNS: bind9 over a ROOT ZONE. The requirement is "authoritative for everything,
#   NXDOMAIN outside the corpus", and a master zone for '.' IS that, by the book.
#   dnsmasq would need a catch-all `local=/#/` to turn "I have no upstream"
#   (SERVFAIL) into NXDOMAIN -- the right answer as a side effect of a forwarding
#   rule. bind9 is also what the cast's own resolver recipes run, and its zone can be
#   checked offline with `named-checkzone`.
#
#   TLS/HTTP: CADDY (bookworm ships 2.6.2-5), on-demand TLS issued by Caddy's
#   internal PKI whose ROOT IS OUR LAB CA. mitmproxy (bookworm ships 8.1.1-2) was
#   evaluated and rejected on two counts: 8.1.1 has NO QUIC (it landed upstream in
#   mitmproxy 10), so HTTP/3 would be impossible; and in reverse mode it still needs
#   a separate origin server behind it, i.e. Caddy or nginx anyway. mitmproxy's one
#   advantage is noted under LIMITS below.
#
#   HTTP/3 IS served, from Debian packages, because Caddy 2.6 enables HTTP/3 by
#   default on TLS servers. The health check asserts the udp/443 listener exists
#   rather than trusting that sentence.
#
# EGRESS. This recipe INSTALLS PACKAGES, so it assumes apt is reachable when it runs
# (provisioning time). Measurement-time egress denial is a later slice. Note the
# ordering consequence: the any-IP route is armed LAST, and a RE-RUN that needs to
# install a package will fail loudly rather than quietly -- by then this host resolves
# only the corpus.
#
# Delivered as an `install: [{script: ...}]` action, so it runs through `run-script`
# (OP-APP-030) IN-GUEST as root, honouring this shebang.
#
#   serve-sink.sh                 install + arm (idempotent)
#   serve-sink.sh --healthcheck   exit 0 only when every unit it starts is active
set -eu
umask 022

AXR_SINK_DIR=/etc/axr-sink
AXR_SINK_LIB=/usr/local/lib/axr-sink
AXR_ZONE_SRC="$AXR_SINK_DIR/db.axr-sink-root"
AXR_ZONE="/etc/bind/db.axr-sink-root"   # inside /etc/bind: see the AppArmor note below
AXR_CIDRS="$AXR_SINK_DIR/external-cidrs.txt"
AXR_CA_DIR="$AXR_SINK_DIR/ca"
AXR_CA_CRT="$AXR_CA_DIR/axr-sink-ca.crt"   # the well-known path an image bake reads
AXR_CA_KEY="$AXR_CA_DIR/axr-sink-ca.key"
AXR_TABLE=100                               # routing table + ip-rule priority
AXR_TCP_FALLBACK_PORT=9099
AXR_UDP_FALLBACK_PORT=9098

# `default` = accept ANY destination arriving on a LAN NIC (see ARM below).
# `cidrs`   = accept only the generated external set. Set AXR_SINK_ROUTE_MODE=cidrs
#             in the environment to narrow it.
AXR_SINK_ROUTE_MODE="${AXR_SINK_ROUTE_MODE:-default}"

# Each entry is the candidate unit names for one service, best first (Debian renames
# these between releases), joined by '|'.
AXR_UNITS="named|bind9 caddy chrony|chronyd ssh|sshd axr-sink-netsetup axr-sink-tcp-fallback axr-sink-udp-fallback"

# --- house helpers (same shapes as the cast's serve-*.sh recipes) -------------

# Install only what is MISSING, and refresh the index first: a cloned guest's apt
# index is stale by construction.
axr_pkg() {
    _missing=""
    for _p in "$@"; do
        if ! dpkg-query -W -f='${Status}' "$_p" 2>/dev/null \
                | grep -q '^install ok installed$'; then
            _missing="$_missing $_p"
        fi
    done
    if [ -n "$_missing" ]; then
        DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=300 update -qq
        # shellcheck disable=SC2086
        DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=300 install -y -qq \
            --no-install-recommends $_missing
    fi
}

# Debian's apt-daily-upgrade.timer is Persistent: a cloned image whose last run
# was at bake time fires it shortly after first boot, and unattended-upgrades
# then holds the dpkg lock for minutes and RESTARTS qemu-guest-agent when it
# upgrades it -- killing whatever this recipe is running over QGA. Measured on
# the corp-twin live run 1 (2026-09-26): the timer fired 15 min after boot, the
# QGA restart orphaned one attempt (exec-status "Invalid parameter 'pid'") and
# a package it was mid-upgrading read as not-installed, so apt-get met the lock
# and exited 100. This box is a disposable lab appliance whose egress is denied
# at measurement time: stop the timers for good, then wait out a run already in
# flight (bounded) before touching dpkg.
axr_apt_quiesce() {
    systemctl disable --now apt-daily.timer apt-daily-upgrade.timer >/dev/null 2>&1 || true
    _deadline=$(( $(date +%s) + 900 ))
    while systemctl is-active --quiet apt-daily.service \
            || systemctl is-active --quiet apt-daily-upgrade.service; do
        [ "$(date +%s)" -lt "$_deadline" ] \
            || axr_die "apt-daily/unattended-upgrades still running after 900 s -- dpkg is busy"
        sleep 5
    done
}

# A Debian package's unit name is not stable across releases (bind9 -> named,
# chrony -> chronyd). Resolve it from what is actually installed.
axr_unit_name() {
    for _u in "$@"; do
        if systemctl list-unit-files "$_u.service" 2>/dev/null | grep -q "^$_u\.service"; then
            echo "$_u"; return 0
        fi
    done
    echo "$1"
}

# FAIL LOUD. A server that did not start is indistinguishable downstream from the
# no-listener defect this whole file exists to prevent, and `systemctl is-active` is
# the only thing that tells them apart (LES-079/LES-086: never `nohup ... &`, which
# the guest-agent session reaps -- everything here is a real systemd unit).
axr_unit() {
    systemctl enable "$1" >/dev/null 2>&1 || true
    systemctl restart "$1"
    sleep 1
    if ! systemctl is-active --quiet "$1"; then
        systemctl status --no-pager --full "$1" >&2 || true
        journalctl -u "$1" --no-pager -n 40 >&2 || true
        echo "axr-sink: unit '$1' did not start" >&2
        exit 1
    fi
}

axr_die() { echo "axr-sink: $*" >&2; exit 1; }

axr_self_ip() { ip -4 -o addr show scope global | awk '{split($4,a,"/"); print a[1]; exit}'; }
axr_gateway() { ip -4 route show default | awk '{print $3; exit}'; }

# Every non-loopback interface carrying a global v4 address. The any-IP rule is bound
# to these by name, never to "all interfaces", so loopback keeps ordinary semantics.
axr_lan_ifaces() { ip -4 -o addr show scope global | awk '{print $2}' | sort -u; }

# --- --healthcheck -----------------------------------------------------------

if [ "${1:-}" = "--healthcheck" ]; then
    axr_hc_rc=0
    for _u in $AXR_UNITS; do
        # shellcheck disable=SC2046  # word splitting on '|' is the point
        _r="$(axr_unit_name $(echo "$_u" | tr '|' ' '))"
        if systemctl is-active --quiet "$_r"; then
            echo "ok   unit $_r"
        else
            echo "FAIL unit $_r"; axr_hc_rc=1
        fi
    done
    # Listeners, not just units: a unit can be active with its socket gone.
    for _l in "tcp:22" "tcp:53" "tcp:80" "tcp:443" "udp:53" "udp:123" "udp:443"; do
        _proto="${_l%%:*}"; _port="${_l##*:}"
        # `ss -lnH -tu` ALWAYS prints Netid first, so the local address is $5 in
        # every case. `ss -lnt`/`-lnu` (one protocol) omit Netid and shift it to
        # $4 -- reading $5 there picks the PEER column ("*:*"), which never
        # matches a port, so every listener reads as FAIL while all of them are
        # up. Measured on this twin 2026-09-21: seven live listeners, seven
        # FAILs, and the op died at the udp/443 check below.
        if ss -lnH -tu 2>/dev/null | awk -v p="$_proto" '$1==p {print $5}' | grep -q ":$_port\$"; then
            echo "ok   listener $_proto/$_port"
        else
            # udp/443 is HTTP/3. It is reported, and it is fatal: the whole point of
            # choosing Caddy over mitmproxy was that QUIC is served.
            echo "FAIL listener $_proto/$_port"; axr_hc_rc=1
        fi
    done
    if ip rule show | grep -q "lookup $AXR_TABLE"; then
        echo "ok   any-ip rule"
    else
        echo "FAIL any-ip rule (nothing accepts foreign destinations)"; axr_hc_rc=1
    fi
    exit "$axr_hc_rc"
fi

# --- the corpus must already be here -----------------------------------------
# Fail loud, never improvise an empty one: a sink with no corpus NXDOMAINs every
# client in the twin, and the twin then looks broken in a way that reads like a
# client bug rather than like a missing data file.
[ -s "$AXR_ZONE_SRC" ] || axr_die "$AXR_ZONE_SRC is missing -- run install-sink-corpus.sh (scripts/gen_sink_corpus.py) first"
[ -s "$AXR_CIDRS" ] || axr_die "$AXR_CIDRS is missing -- run install-sink-corpus.sh first"
grep -q ' IN A' "$AXR_ZONE_SRC" || axr_die "$AXR_ZONE_SRC carries no A record"

mkdir -p "$AXR_SINK_DIR" "$AXR_SINK_LIB" "$AXR_CA_DIR" /var/log/axrange /var/www/axr-sink

# --- packages ----------------------------------------------------------------
axr_apt_quiesce
axr_pkg bind9 bind9-dnsutils caddy chrony socat nftables openssl curl ca-certificates iproute2

# --- the lab CA --------------------------------------------------------------
# One CA for the whole sink, generated once and kept. Caddy mints a leaf per SNI
# UNDER this root, so a client only ever has to trust this one certificate -- which
# is why it is written to a stable, well-known path for an image bake to pick up and
# install into the twin's client trust stores.
if [ ! -s "$AXR_CA_CRT" ] || [ ! -s "$AXR_CA_KEY" ]; then
    openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes \
        -keyout "$AXR_CA_KEY" -out "$AXR_CA_CRT" \
        -subj "/O=axRange/CN=axRange sink lab CA" \
        -addext "basicConstraints=critical,CA:TRUE" \
        -addext "keyUsage=critical,keyCertSign,cRLSign" >/dev/null 2>&1 \
        || axr_die "lab CA generation failed"
fi
# Caddy runs as the `caddy` user and must read the key to sign its intermediate.
chown root:caddy "$AXR_CA_KEY" 2>/dev/null || true
chmod 0640 "$AXR_CA_KEY"
chmod 0644 "$AXR_CA_CRT"
install -m 0644 "$AXR_CA_CRT" /usr/local/share/ca-certificates/axr-sink-ca.crt
update-ca-certificates >/dev/null 2>&1 || true

# --- DNS: bind9, authoritative for '.' ---------------------------------------
# systemd-resolved owns :53 on a stock cloud image and `named` cannot bind it while
# the stub is there.
if systemctl is-active --quiet systemd-resolved 2>/dev/null; then
    systemctl disable --now systemd-resolved
fi
AXR_IP="$(axr_self_ip)"
[ -n "$AXR_IP" ] || axr_die "no global IPv4 address on this host"
AXR_GW="$(axr_gateway)"
# The SINK's own resolver. `AXR_RESOLVER` overrides; otherwise an existing,
# WORKING resolv.conf is left alone, and only an empty one falls back to the
# gateway.
#
# It used to unconditionally `rm -f` this file and point at the twin's gateway.
# That gateway is a ROUTER, not a resolver, so on every re-run this recipe made
# its own apt step unresolvable and hung there -- measured 2026-09-23, twice,
# each time looking like a stalled package install rather than a DNS change the
# recipe had made to itself. A recipe that rewrites the resolver it is about to
# depend on has to prove the new one answers; this one could not.
if [ -n "${AXR_RESOLVER:-}" ]; then
    printf 'nameserver %s\n' "$AXR_RESOLVER" > /etc/resolv.conf
elif [ ! -s /etc/resolv.conf ]; then
    printf 'nameserver %s\n' "${AXR_GW:-127.0.0.1}" > /etc/resolv.conf
fi
# Fail loud rather than hang: if the configured resolver cannot answer the
# archive's name, say so now, naming the file and the detour, instead of
# spending the op's whole timeout inside apt.
if ! getent hosts deb.debian.org >/dev/null 2>&1; then
    echo "axr-sink: WARNING /etc/resolv.conf ($(grep -h nameserver /etc/resolv.conf | tr '\n' ' ')) cannot resolve deb.debian.org." >&2
    echo "axr-sink:         set AXR_RESOLVER=<a resolver that can> for this run, or pre-install the packages." >&2
fi

# The zone lives under /etc/bind, not under /etc/axr-sink: Debian's AppArmor profile
# for named grants /etc/bind/** and denies most everything else, so a zone file
# outside it loads on an AppArmor-less box and fails on a stock one.
sed "s/@@SINK_IP@@/$AXR_IP/g" "$AXR_ZONE_SRC" > "$AXR_ZONE"
chmod 0644 "$AXR_ZONE"

cat > /etc/bind/named.conf.options <<'AXR_OPT_EOF'
options {
    directory "/var/cache/bind";
    listen-on { any; };
    listen-on-v6 { none; };
    allow-query { any; };
    // No recursion and no forwarders: there is no upstream, by design. Everything
    // this server knows is in the root zone below; everything else is NXDOMAIN.
    recursion no;
    dnssec-validation no;
    // The built-in RFC1918/AS112 empty zones would shadow names in the corpus and
    // answer them from thin air.
    empty-zones-enable no;
    // A cast records the names it SAW. Some of them (service labels, vendor
    // hostnames) are not strict RFC-952 host names, and refusing to load a real
    // observation on style grounds would be the wrong trade.
    check-names master ignore;
    version none;
};
AXR_OPT_EOF

cat > /etc/bind/named.conf.local <<AXR_LOCAL_EOF
// The observed external world, as a master zone for the ROOT. This is what makes
// the sink authoritative for everything and NXDOMAIN outside the corpus.
zone "." {
    type master;
    file "$AXR_ZONE";
};
AXR_LOCAL_EOF

# Debian's named.conf includes named.conf.default-zones, which declares its own
# `zone "."` (type hint). Two root zones is a config error, and named refuses to
# start -- so the include goes.
sed -i '/named\.conf\.default-zones/d' /etc/bind/named.conf

named-checkconf || axr_die "named.conf rejected"
named-checkzone . "$AXR_ZONE" >/dev/null || axr_die "the sink root zone is invalid"
axr_unit "$(axr_unit_name named bind9)"

# --- HTTP/HTTPS: Caddy, per-SNI certificates minted on the fly ---------------
cat > /var/www/axr-sink/index.html <<'AXR_INDEX_EOF'
<!doctype html>
<title>axRange sink</title>
<h1>axRange twin sink</h1>
<p>This host answers for every external address the cast observed. The PROTOCOL is
real -- real DNS, a real TLS handshake with a certificate for the name you asked
for, real HTTP. The BODY is generic: no HTTP response body was ever recorded, so
there is nothing here to reproduce and nothing here is a claim about the original.</p>
AXR_INDEX_EOF

cat > /etc/caddy/Caddyfile <<AXR_CADDY_EOF
# axRange twin SINK -- written by serve-sink.sh; do not hand-edit.
{
	# Caddy's own PKI, rooted in OUR lab CA. Caddy signs an intermediate with this
	# root and then mints a leaf per SNI, so the twin's clients need exactly one
	# trusted certificate: $AXR_CA_CRT.
	pki {
		ca local {
			name "axRange sink lab CA"
			root {
				cert $AXR_CA_CRT
				key $AXR_CA_KEY
			}
		}
	}
	# On-demand issuance asks a policy endpoint before minting. This sink says yes
	# to every name -- it is standing in for the whole internet, and the corpus has
	# already decided which names resolve here at all.
	on_demand_tls {
		ask http://127.0.0.1:9180/ask
	}
	servers {
		# h3 = HTTP/3 over QUIC on udp/443, alongside HTTP/1.1 and HTTP/2.
		protocols h1 h2 h3
	}
}

http://127.0.0.1:9180 {
	respond 200
}

:80 {
	handle {
		root * /var/www/axr-sink
		try_files {path} /index.html
		file_server
	}
}

:443 {
	tls {
		issuer internal
		on_demand
	}
	handle {
		root * /var/www/axr-sink
		try_files {path} /index.html
		file_server
	}
}
AXR_CADDY_EOF

caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1 \
    || { caddy validate --config /etc/caddy/Caddyfile >&2 || true; axr_die "Caddyfile rejected"; }
axr_unit caddy

# --- NTP: chrony, serving ----------------------------------------------------
cat > /etc/chrony/chrony.conf <<'AXR_CHRONY_EOF'
# axRange twin SINK -- written by serve-sink.sh.
# No `pool`/`server`: there is no upstream. `local stratum 8` lets chronyd serve
# time anyway, which is the whole job -- a client's NTP exchange has to complete.
driftfile /var/lib/chrony/chrony.drift
local stratum 8
allow all
logdir /var/log/chrony
AXR_CHRONY_EOF
axr_unit "$(axr_unit_name chrony chronyd)"

# --- SSH ---------------------------------------------------------------------
axr_unit "$(axr_unit_name ssh sshd)"

# --- generic TCP/UDP fallback ------------------------------------------------
# An unknown protocol should get a connection, not an RST: a client that is refused
# behaves differently (retries, immediate failure) from one that is answered, and the
# difference shows up in the capture as a defect of the twin.
cat > /etc/systemd/system/axr-sink-tcp-fallback.service <<AXR_TCPU_EOF
[Unit]
Description=axRange sink generic TCP responder
After=network-online.target

[Service]
ExecStart=/usr/bin/socat -T30 TCP-LISTEN:$AXR_TCP_FALLBACK_PORT,fork,reuseaddr EXEC:/bin/cat
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
AXR_TCPU_EOF

cat > /etc/systemd/system/axr-sink-udp-fallback.service <<AXR_UDPU_EOF
[Unit]
Description=axRange sink generic UDP responder
After=network-online.target

[Service]
ExecStart=/usr/bin/socat -T5 UDP-RECVFROM:$AXR_UDP_FALLBACK_PORT,fork,reuseaddr EXEC:/bin/cat
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
AXR_UDPU_EOF

# nftables sends every port nothing else claims to those two responders. REDIRECT
# lives in nat/prerouting, so it only ever sees the FIRST packet of a flow -- the
# sink's own established connections (apt at provisioning time) are untouched.
#
# NO BACKTICKS in this heredoc's body (or any other unquoted one here): the
# delimiter is unquoted so $AXR_IP expands, and so does a backtick -- as a
# COMMAND. The comment below once quoted `tcpdump` in backticks, and every run
# of this recipe ran tcpdump in-guest, forever, while writing this file: the
# corp-twin live run 1 (2026-09-26) hung its run-script for the whole 600 s
# budget right here (docs/WORK_ORDER_corp_twin.md §11.D). A test
# (tests/test_twin_recipe_heredocs.py) now refuses it in every twin recipe.
cat > "$AXR_SINK_DIR/nftables.conf" <<AXR_NFT_EOF
# axRange twin SINK -- written by serve-sink.sh; do not hand-edit.
# create-then-delete: the idempotent preamble for a re-run.
table inet axr_sink
delete table inet axr_sink

table inet axr_sink {
	chain prerouting {
		type nat hook prerouting priority dstnat; policy accept;
		# SERVED ports are redirected to THEMSELVES for a foreign destination,
		# and that redirect is load-bearing -- measured 2026-09-23.
		#
		# The any-IP route below lets the kernel ACCEPT a packet addressed to
		# someone else's address, but it does not make that address usable as a
		# SOURCE: building the SYN-ACK, the kernel asks for a route out of
		# 140.82.113.3, finds it is not a local address, and refuses. The SYN is
		# then dropped at the listening socket, silently -- visible only as
		# 'nstat TcpExtListenDrops' climbing (1316 of them on the first live
		# run) while tcpdump shows the SYN arriving and nothing going back.
		# Every layer an operator checks -- ip rule, table 100, 'ip route get'
		# (which answers 'local'), nft counters, rp_filter, the listening socket
		# -- looks correct, which is what made this expensive to find.
		#
		# A dstnat REDIRECT rewrites the destination to this host BEFORE the
		# routing decision, so the socket is reached the ordinary way and the
		# source is this host's own address. Conntrack then rewrites the reply
		# back to the original destination, so the CLIENT still sees the answer
		# coming from the address it dialled -- which is the property the whole
		# sink exists for. The fallback responders below already worked for
		# exactly this reason; the served ports were excluded from the redirect
		# and were the only ones relying on any-IP alone.
		#
		# 'ip daddr != <sink address>' keeps traffic addressed to the sink's own
		# address on the ordinary path (a redirect there would be a no-op, but
		# an explicit condition says so).
		ip daddr != $AXR_IP tcp dport { 22, 53, 80, 443 } redirect
		ip daddr != $AXR_IP udp dport { 53, 123, 443 } redirect
		tcp dport { 22, 53, 80, 443 } return
		udp dport { 53, 123, 443 } return
		meta l4proto tcp redirect to :$AXR_TCP_FALLBACK_PORT
		meta l4proto udp redirect to :$AXR_UDP_FALLBACK_PORT
	}
}
AXR_NFT_EOF

# --- ARM: accept traffic addressed to ANY IP ---------------------------------
# THE CORE TRICK, and it looks alarming, so read this before changing it.
#
# The twin's router sends every external destination here. Without help the kernel
# drops those packets: their destination is not one of this host's addresses.
# `ip route add local <prefix> dev lo` tells the kernel "deliver this to me", which
# is exactly what is wanted -- but putting it in the MAIN local table would also
# make every destination local for LOCALLY GENERATED packets, so the sink could not
# answer its own clients (a reply's route lookup would resolve to `local` and the
# packet would loop back into the host).
#
# So it goes in a table of its own, selected by an `ip rule` that matches on the
# INPUT interface. Packets arriving on a LAN NIC hit table $AXR_TABLE and are
# accepted for any destination; packets this host generates have iif `lo`, miss the
# rule, and route normally through `main` -- back to the client, and out to apt.
#
# rp_filter must be 0: reverse-path validation re-runs the rule set with the
# incoming interface, hits the same local default route, concludes the packet's
# SOURCE is local, and drops it as martian.
#
# ip_forward stays 0. This host TERMINATES the external world, it does not relay it.
cat > /etc/sysctl.d/99-axr-sink.conf <<'AXR_SYSCTL_EOF'
net.ipv4.conf.all.rp_filter = 0
net.ipv4.conf.default.rp_filter = 0
net.ipv4.ip_forward = 0
net.ipv4.ip_nonlocal_bind = 1
AXR_SYSCTL_EOF

cat > "$AXR_SINK_LIB/netsetup.sh" <<AXR_NETSETUP_EOF
#!/bin/sh
# axRange twin SINK -- any-IP acceptance. Written by serve-sink.sh; do not hand-edit.
# Run at provisioning AND at every boot (axr-sink-netsetup.service), because an ip
# rule, an ip route and an nft ruleset are all runtime state.
set -eu
AXR_TABLE=$AXR_TABLE
AXR_CIDRS=$AXR_CIDRS
AXR_MODE=\${AXR_SINK_ROUTE_MODE:-$AXR_SINK_ROUTE_MODE}

sysctl -q --system >/dev/null 2>&1 || true
# rp_filter is max(all, <iface>), so per-interface values have to go too.
for f in /proc/sys/net/ipv4/conf/*/rp_filter; do echo 0 > "\$f" 2>/dev/null || true; done

# Idempotent: drop any rule we left behind, then re-add.
while ip rule del priority "\$AXR_TABLE" 2>/dev/null; do :; done
ip route flush table "\$AXR_TABLE" 2>/dev/null || true

if [ "\$AXR_MODE" = cidrs ]; then
    # Narrow: accept only the addresses the cast actually observed.
    grep -v '^#' "\$AXR_CIDRS" | grep '[0-9]' | while read -r cidr; do
        ip route add local "\$cidr" dev lo table "\$AXR_TABLE" 2>/dev/null || true
    done
else
    # Wide (default): accept whatever the router chose to send here. The router,
    # not this host, decides what counts as external -- and a CDN address one
    # rotation away from the observed one still lands on a server instead of a
    # black hole. Nothing can be "stolen": the rule only matches packets that
    # already arrived on this host's LAN NIC.
    ip route add local 0.0.0.0/0 dev lo table "\$AXR_TABLE"
fi

for IF in \$(ip -4 -o addr show scope global | awk '{print \$2}' | sort -u); do
    ip rule add iif "\$IF" priority "\$AXR_TABLE" lookup "\$AXR_TABLE"
done

nft -f $AXR_SINK_DIR/nftables.conf
AXR_NETSETUP_EOF
chmod 0755 "$AXR_SINK_LIB/netsetup.sh"

cat > /etc/systemd/system/axr-sink-netsetup.service <<AXR_NETU_EOF
[Unit]
Description=axRange sink any-IP acceptance (policy route + nftables)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
Environment=AXR_SINK_ROUTE_MODE=$AXR_SINK_ROUTE_MODE
ExecStart=$AXR_SINK_LIB/netsetup.sh

[Install]
WantedBy=multi-user.target
AXR_NETU_EOF

systemctl daemon-reload
axr_unit axr-sink-tcp-fallback
axr_unit axr-sink-udp-fallback
axr_unit axr-sink-netsetup

# --- prove it, in-guest ------------------------------------------------------
# Cheap local probes that turn the three ways this box fails silently into a failed
# op: a zone that loaded but answers nothing, a resolver that answers everything,
# and a TLS listener that cannot mint the name it was asked for.
AXR_PROBE_NAME="$(awk '$2=="IN" && $3=="A" && $1!="ns.axr-sink." {sub(/\.$/,"",$1); print $1; exit}' "$AXR_ZONE")"
[ -n "$AXR_PROBE_NAME" ] || axr_die "no probe name in $AXR_ZONE"

AXR_ANSWER="$(dig +short +time=2 +tries=2 @127.0.0.1 "$AXR_PROBE_NAME" A | head -1)"
[ -n "$AXR_ANSWER" ] || axr_die "the sink did not answer its own corpus name '$AXR_PROBE_NAME'"

dig +time=2 +tries=2 @127.0.0.1 axr-sink-no-such-name.invalid A 2>/dev/null \
    | grep -q 'status: NXDOMAIN' \
    || axr_die "a name outside the corpus did NOT get NXDOMAIN -- the sink is answering from thin air"

curl -sf -o /dev/null --max-time 5 "http://127.0.0.1/" || axr_die "HTTP on :80 did not answer"

# On-demand issuance mints on the FIRST handshake for a name, so give it a few tries.
AXR_TLS_OK=0
AXR_TRY=0
while [ "$AXR_TRY" -lt 10 ]; do
    if : | openssl s_client -connect 127.0.0.1:443 -servername "$AXR_PROBE_NAME" \
            -verify_return_error -CAfile "$AXR_CA_CRT" 2>/dev/null \
            | openssl x509 -noout -text 2>/dev/null \
            | grep -qi "DNS:$AXR_PROBE_NAME"; then
        AXR_TLS_OK=1; break
    fi
    AXR_TRY=$((AXR_TRY + 1))
    sleep 2
done
[ "$AXR_TLS_OK" = 1 ] \
    || axr_die "TLS on :443 did not present a certificate for '$AXR_PROBE_NAME' signed by the lab CA"

# HTTP/3 is the reason Caddy was chosen over mitmproxy. Assert the listener rather
# than trusting the release notes.
ss -lnH -tu | awk '$1=="udp" {print $5}' | grep -q ':443$' \
    || axr_die "no udp/443 listener -- HTTP/3 (QUIC) is not being served"

AXR_NAMES="$(grep -c ' IN A' "$AXR_ZONE")"
AXR_NCIDRS="$(grep -c '^[0-9]' "$AXR_CIDRS")"
echo "axr-sink: ready on $AXR_IP -- $AXR_NAMES dns records, $AXR_NCIDRS external cidrs,"
echo "axr-sink: serving 53(dns) 80/443(http1.1+h2+h3, per-SNI certs) 123(ntp) 22(ssh),"
echo "axr-sink: anything else -> generic tcp/udp responder; CA at $AXR_CA_CRT"
