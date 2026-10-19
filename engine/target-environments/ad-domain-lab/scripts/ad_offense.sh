#!/usr/bin/env bash
# Attacker — stage the corpus's full Active Directory attack kit (red team).
# apt tools (nmap, hashcat, john, ldap-utils, smbclient) install via provision-app;
# this adds the pip/git-sourced AD tools. Idempotent. Needs gw router WAN egress.
set -euo pipefail

export PIP_BREAK_SYSTEM_PACKAGES=1
PIP="python3 -m pip install --quiet --upgrade"

# Impacket (secretsdump/GetUserSPNs/getTGT) + NetExec (CrackMapExec successor) +
# bloodhound.py (remote AD enumeration). The "Top 10 AD attack methods" toolkit.
$PIP impacket 2>/dev/null      || echo "impacket deferred (needs egress)"
$PIP netexec 2>/dev/null       || echo "netexec deferred (needs egress)"
$PIP bloodhound 2>/dev/null    || echo "bloodhound.py deferred (needs egress)"

# Responder (LLMNR/NBT-NS/MDNS poisoning) — the LLMNR-poison kill-chain entry.
if [ ! -d /opt/Responder ]; then
  git clone --depth 1 https://github.com/lgandx/Responder /opt/Responder \
    2>/dev/null || echo "Responder clone deferred (needs egress)"
fi

# Recon/kill-chain crib for the deferred attack activity (OP-ACT). Targets the
# domain segment; the DC is the Kerberoast/DCSync objective.
install -d -m 0755 /etc/axrange
cat > /etc/axrange/ad_targets.txt <<'TXT'
DC=10.60.10.10        # kerberoast / dcsync objective
WS=10.60.10.20        # initial foothold (phish/lateral)
SUBNET=10.60.10.0/24  # responder / netexec sweep
TXT

echo "AD attacker staged: impacket/netexec/bloodhound.py + Responder (egress-gated)"
