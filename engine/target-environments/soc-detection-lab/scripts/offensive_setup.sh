#!/usr/bin/env bash
# Attacker host — stage the corpus's recurring red-team kit (red team).
# apt packages (nmap, hydra, netcat) install via provision-app; this script
# adds the pip/git-sourced tools. Idempotent. Needs router WAN egress for pip.
set -euo pipefail

export PIP_BREAK_SYSTEM_PACKAGES=1
PIPX="python3 -m pip install --quiet --upgrade"

# Impacket + CrackMapExec/NetExec + Responder — the corpus's AD attack staples
# (Top-10 AD attack methods: Kerberoasting, Pass-the-Hash, LLMNR poisoning).
$PIPX impacket 2>/dev/null || echo "impacket install deferred (needs egress)"

# Responder (LLMNR/NBT-NS poisoning) — clone if not present.
if [ ! -d /opt/Responder ]; then
  git clone --depth 1 https://github.com/lgandx/Responder /opt/Responder \
    2>/dev/null || echo "Responder clone deferred (needs egress)"
fi

# A target list for the recon stage of the deferred attack chain (OP-ACT).
install -d -m 0755 /etc/axrange
cat > /etc/axrange/targets.txt <<'TXT'
10.50.10.0/24   # dmz
10.50.20.0/24   # corp
TXT

echo "attacker staged: nmap/hydra/netcat (pkg) + impacket/Responder (opt)"
