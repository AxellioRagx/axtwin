#!/bin/sh
# axRange svc-engines -- the WEB TIER (nginx, plain HTTP on 80 + real TLS on 443).
#
# Modelled on `twin-configs/svc-verify/scripts/serve-web-tier-10-80-72-core.sh`
# (aximprint's real generated recipe, copied byte-identical into that twin),
# with the house helpers copied verbatim. TWO deliberate changes, both
# load-bearing for the engine round:
#
#   1. **The certificate names `web.svc.axr.lab`** -- the name THIS twin's
#      resolver publishes for this host -- instead of the cast compiler's node
#      group name `web-tier-10-80-72-core.corp.axr.lab`. The whole point of
#      the round is real name -> real SNI -> a certificate that matches; a CN
#      that no name in the twin resolves to makes every TLS result unreadable
#      (client bug? server bug? chain bug?). The name is ALSO deliberately in
#      `svc.axr.lab` and not in the AD zone: `corp.axr.lab` belongs to the DC.
#   2. **Port 80 is served too**, so the browser engine's plain-HTTP emit and
#      any HTTP->HTTPS behaviour have somewhere internal to land. The cast
#      recipe served 443 only because its source host was only ever SEEN on
#      443; this twin is not reproducing that host's census.
#
# **What this cert is NOT.** Self-signed, like the cast's. It is not the sink's
# lab CA, and nothing in the twin trusts it by default -- an internal TLS
# client either passes `-k` (the `command` driver's `https` template does) or
# fails validation loudly. Trusting it is E7's image-bake job, not this
# recipe's.
#
# Body content is synthetic BY DESIGN: no HTTP response body was ever recorded
# (aximprint GAP-019), so there is nothing here to reproduce and nothing here
# is a claim about the original. The PROTOCOL is real.
#
# Delivered as an `install: [{script: ...}]` action, so it runs through
# `run-script` (OP-APP-030) IN-GUEST as root, honouring this shebang.
set -eu
umask 022
mkdir -p /usr/local/lib/axr /var/log/axrange

AXR_RESOLVER_IP=10.66.10.11
AXR_VHOST=web.svc.axr.lab

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

# FAIL LOUD. A server that did not start is indistinguishable downstream from the no-listener
# defect this whole file replaces (axRange LES-079/LES-086; never `nohup ... &`).
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

# --- --healthcheck -----------------------------------------------------------
if [ "${1:-}" = "--healthcheck" ]; then
    _rc=0
    _u="$(axr_unit_name nginx)"
    if systemctl is-active --quiet "$_u"; then echo "ok   unit $_u"; else echo "FAIL unit $_u"; _rc=1; fi
    for _l in "tcp:80" "tcp:443"; do
        _p="${_l##*:}"
        if ss -lnt 2>/dev/null | awk '{print $4}' | grep -q ":$_p\$"; then
            echo "ok   listener tcp/$_p"
        else
            echo "FAIL listener tcp/$_p"; _rc=1
        fi
    done
    exit "$_rc"
fi

# --- packages ---------------------------------------------------------------
axr_pkg nginx openssl

# --- the certificate ---------------------------------------------------------
# CN *and* SAN are the vhost name: a client that validates (or a sensor that
# reads the cert subject out of the handshake) has to see the same name it
# asked for. Regenerated only when absent, so a re-run is idempotent and the
# fingerprint a capture recorded stays stable.
if [ ! -f /etc/ssl/axr/serve.crt ]; then
    mkdir -p /etc/ssl/axr
    openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
        -keyout /etc/ssl/axr/serve.key -out /etc/ssl/axr/serve.crt \
        -subj "/CN=$AXR_VHOST" -addext "subjectAltName=DNS:$AXR_VHOST" >/dev/null 2>&1 \
        || axr_die "certificate generation failed"
    chmod 600 /etc/ssl/axr/serve.key
fi

mkdir -p /var/www/html
cat > /var/www/html/index.html <<AXR_INDEX_EOF
<!doctype html><title>$AXR_VHOST</title>
<h1>$AXR_VHOST</h1>
<p>axRange svc-engines twin. Body content is synthetic by design; the protocol
is not.</p>
AXR_INDEX_EOF

rm -f /etc/nginx/sites-enabled/default
cat > /etc/nginx/sites-available/axr-serve <<AXR_NGINX_EOF
server {
    listen 80 default_server;
    server_name $AXR_VHOST;
    root /var/www/html;
    location / { try_files \$uri \$uri/ /index.html; }
}
server {
    listen 443 ssl default_server;
    server_name $AXR_VHOST;
    ssl_certificate     /etc/ssl/axr/serve.crt;
    ssl_certificate_key /etc/ssl/axr/serve.key;
    root /var/www/html;
    location / { try_files \$uri \$uri/ /index.html; }
}
AXR_NGINX_EOF
ln -sfn /etc/nginx/sites-available/axr-serve /etc/nginx/sites-enabled/axr-serve
nginx -t
axr_unit "$(axr_unit_name nginx)"

# --- E3: this host resolves through the twin resolver ------------------------
# Done LAST, after every apt pull: from here on this guest's world is the
# twin's. systemd-resolved is displaced rather than reconfigured, for the same
# reason the bind9 recipes displace it -- a 127.0.0.53 stub in front of the
# twin resolver is one more thing that can answer differently.
if systemctl is-active --quiet systemd-resolved 2>/dev/null; then
    systemctl disable --now systemd-resolved
fi
rm -f /etc/resolv.conf
printf 'nameserver %s\nsearch svc.axr.lab corp.axr.lab\n' "$AXR_RESOLVER_IP" > /etc/resolv.conf

echo "axr-serve: web tier ready ($AXR_VHOST on 80/tcp + 443/tcp, self-signed)"
