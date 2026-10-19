#!/usr/bin/env bash
# Red-team kit — scan/brute (pkg) + pip/git AD & C2 tooling. Idempotent.
# Needs core router WAN egress for pip/git.
set -euo pipefail

export PIP_BREAK_SYSTEM_PACKAGES=1
PIP="python3 -m pip install --quiet --upgrade"
$PIP impacket 2>/dev/null || echo "impacket deferred (needs egress)"
$PIP netexec 2>/dev/null  || echo "netexec deferred (needs egress)"

if [ ! -d /opt/Responder ]; then
  git clone --depth 1 https://github.com/lgandx/Responder /opt/Responder \
    2>/dev/null || echo "Responder clone deferred (needs egress)"
fi

install -d -m 0755 /etc/axrange
cat > /etc/axrange/killchain.txt <<'TXT'
1 phish     -> ws1 (10.20.10.20)
2 lateral   -> ws2 (10.20.10.21) via SMB
3 pivot     -> file-srv (10.20.20.20), app-srv (10.20.20.10)
4 exfil     -> /srv/crownjewel/customers.csv
TXT

echo "red kit staged: nmap/hydra (pkg) + impacket/netexec/Responder (egress-gated)"
