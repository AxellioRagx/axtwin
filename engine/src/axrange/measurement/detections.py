"""Detection -> ATT&CK technique resolution (A4, docs/design/08-exercise-measurement.md).

A Wazuh/Suricata/host-audit alert is "covered" against a truth marker only if
it can be pinned to an ATT&CK technique id. Two resolution paths, tried in
order:

  1. **Self-label** — a modern Wazuh/Sigma/Suricata rule already carries an
     ATT&CK id (a Wazuh `<mitre><id>T1078</id></mitre>` field, a Suricata
     `metadata`/tag, or a bare `Txxxx[.yyy]` token in the rule/message text).
     `parse_technique_from_rule()` extracts it wherever it's embedded.
  2. **Fallback** — a small, DATA-DRIVEN substring map
     (`FALLBACK_RULE_TECHNIQUE_MAP`) for rules that don't self-label, keyed
     against the alert's rule name/signature. Deliberately small (§4: "keep it
     small and data-driven") — extend it as archetypes' rulesets grow; it is
     NOT meant to be exhaustive.

This module is O2-side analysis code (consumed by the Grafana dashboard's
query-time labeling today, and by a future automated scorer). The engine
(`axrange.ir` / `axrange.validator` / `axrange.planner` / `axrange.adapter_proxmox`
/ `axrange.cli`) does not import it (LES-025 reuse direction — a leaf, not a
dependency of the deploy path).
"""
from __future__ import annotations

import re

# Bare ATT&CK technique/sub-technique id, e.g. "T1003", "T1558.003".
_MITRE_ID_RE = re.compile(r"\bT\d{4}(?:\.\d{3})?\b")

# Small substring -> technique fallback map. Keys are matched
# case-insensitively against the alert's `rule_id`/`signature` text. Extend as
# needed; do not aim for exhaustive coverage (§4).
FALLBACK_RULE_TECHNIQUE_MAP: dict[str, str] = {
    "dcsync": "T1003.006",       # OS Credential Dumping: DCSync
    "drsuapi": "T1003.006",
    "kerberoast": "T1558.003",   # Steal or Forge Kerberos Tickets: Kerberoasting
    "asreproast": "T1558.004",   # ... AS-REP Roasting
    "as-rep": "T1558.004",
    "llmnr": "T1557.001",        # Adversary-in-the-Middle: LLMNR/NBT-NS Poisoning
    "nbt-ns": "T1557.001",
    "smb enum": "T1135",         # Network Share Discovery
    "smb share": "T1135",
    "ldap enum": "T1087.002",    # Account Discovery: Domain Account
    "ldap search": "T1087.002",
    "port scan": "T1046",        # Network Service Discovery
    "nmap": "T1046",
    "brute force": "T1110",      # Brute Force
    "hydra": "T1110",
    "golden ticket": "T1558.001",
    "silver ticket": "T1558.002",
}


def parse_technique_from_rule(*texts: str | None) -> str | None:
    """Self-label: return the first ATT&CK technique id found in any of
    `texts` (checked in order), or `None` if none contains one. Callers pass
    the most-authoritative field first (e.g. a Wazuh `<mitre><id>` value)
    followed by looser free-text fields (signature/message) as fallbacks
    within the self-label step itself."""
    for text in texts:
        if not text:
            continue
        m = _MITRE_ID_RE.search(text)
        if m:
            return m.group(0)
    return None


def technique_for_detection(
    *, rule_id: str = "", signature: str = "", mitre_raw: str = ""
) -> str | None:
    """Resolve one detection's ATT&CK technique id: self-label first
    (`mitre_raw`, then `signature`, then `rule_id`), else the small
    substring fallback map matched against `rule_id`+`signature`. `None` when
    neither source resolves — an unmapped detection is excluded from
    coverage joins (it is not evidence FOR or AGAINST a technique, just
    unattributed)."""
    self_label = parse_technique_from_rule(mitre_raw, signature, rule_id)
    if self_label:
        return self_label
    haystack = f"{rule_id} {signature}".lower()
    for needle, technique in FALLBACK_RULE_TECHNIQUE_MAP.items():
        if needle in haystack:
            return technique
    return None
