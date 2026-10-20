#!/usr/bin/env bash
# Emulation host — Atomic Red Team / Caldera staging for the purple-team loop
# (red emulates an ATT&CK technique -> blue measures detection -> tune -> repeat).
# Idempotent. Needs core router WAN egress for the clones.
set -euo pipefail

# Atomic Red Team — the atomics library keyed to ATT&CK technique IDs.
if [ ! -d /opt/atomic-red-team ]; then
  git clone --depth 1 https://github.com/redcanaryco/atomic-red-team \
    /opt/atomic-red-team 2>/dev/null || echo "atomics clone deferred (needs egress)"
fi

# A small ATT&CK technique plan the deferred OP-ACT emulation would execute.
install -d -m 0755 /etc/axrange
cat > /etc/axrange/emulation_plan.txt <<'TXT'
T1059  Command and Scripting Interpreter   -> ws1
T1021  Remote Services (SMB)               -> ws1 -> ws2
T1003  OS Credential Dumping               -> ws2
T1048  Exfiltration Over Alternative Proto -> app-srv -> red
TXT

echo "emulation staged: atomic-red-team + ATT&CK plan (purple loop, OP-ACT-driven)"
