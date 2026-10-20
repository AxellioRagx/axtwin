#!/usr/bin/env bash
# Player/attacker kit — stage exploitation libraries on top of the apt tools.
# Idempotent. Needs gw router WAN egress for pip/git.
set -euo pipefail

export PIP_BREAK_SYSTEM_PACKAGES=1
python3 -m pip install --quiet --upgrade pwntools 2>/dev/null \
  || echo "pwntools deferred (needs egress)"

# SecLists wordlists (gobuster/hydra fuel) — clone if absent.
if [ ! -d /opt/SecLists ]; then
  git clone --depth 1 https://github.com/danielmiessler/SecLists /opt/SecLists \
    2>/dev/null || echo "SecLists clone deferred (needs egress)"
fi

install -d -m 0755 /etc/axrange
cat > /etc/axrange/arena.txt <<'TXT'
WEB=10.70.10.20    SVC=10.70.10.30    WIN=10.70.10.40
SCOREBOARD=http://10.70.10.10:8000
TXT

echo "player kit staged: nmap/hydra/gobuster/sqlmap (pkg) + pwntools/SecLists (opt)"
