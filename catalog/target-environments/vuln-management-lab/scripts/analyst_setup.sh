#!/usr/bin/env bash
# Analyst / triage host — recon tooling + a findings-tracking scaffold for the
# remediation lifecycle (the deferred vuln-mgmt ACTIVITY drives this). Idempotent.
set -euo pipefail

export PIP_BREAK_SYSTEM_PACKAGES=1
# python-gvm lets the deferred activity drive Greenbone over GMP.
python3 -m pip install --quiet --upgrade python-gvm 2>/dev/null \
  || echo "python-gvm deferred (needs egress)"

install -d -m 0755 /opt/vulnmgmt
cat > /opt/vulnmgmt/findings.csv <<'CSV'
id,host,cve,severity,status,owner,due
CSV
cat > /opt/vulnmgmt/README.txt <<'TXT'
Vulnerability Management lifecycle (deferred OP-ACT drives this):
  1. discover  - nmap sweep 10.90.20.0/24
  2. scan      - Greenbone full-and-fast scan of the fleet
  3. triage    - rank findings.csv by severity, assign owners
  4. remediate - patch/harden (deferred config kinds)
  5. rescan    - confirm closure, update status
TXT

echo "analyst staged: nmap (pkg) + python-gvm + findings tracker at /opt/vulnmgmt"
