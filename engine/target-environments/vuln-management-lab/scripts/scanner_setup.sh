#!/usr/bin/env bash
# Greenbone Community Edition vulnerability scanner via Docker (OSS Tenable/Nessus
# stand-in). Idempotent. Pulling the GCE images needs gw router WAN egress.
set -euo pipefail

systemctl enable --now docker || true

GVM_DIR=/opt/greenbone
install -d -m 0755 "$GVM_DIR"

# Greenbone Community Containers compose (upstream-published stack). Pinned.
# First boot syncs the NVT/SCAP feeds (large; needs egress + time).
if [ ! -f "$GVM_DIR/docker-compose.yml" ]; then
  curl -fsSL -o "$GVM_DIR/docker-compose.yml" \
    https://greenbone.github.io/docs/latest/_static/docker-compose-22.4.yml \
    2>/dev/null || echo "GCE compose fetch deferred (needs egress)"
fi

if command -v docker >/dev/null 2>&1 && [ -f "$GVM_DIR/docker-compose.yml" ]; then
  (cd "$GVM_DIR" && docker compose up -d) \
    || echo "Greenbone up deferred (needs router WAN egress + feed sync)"
fi

# Scan-scope crib for the deferred scan ACTIVITY.
install -d -m 0755 /etc/axrange
cat > /etc/axrange/scan_scope.txt <<'TXT'
TARGETS=10.90.20.0/24
WEB_OLD=10.90.20.10  LINUX_TGT=10.90.20.20  WIN_TGT=10.90.20.30
TXT

echo "scanner staged: Greenbone Community at $GVM_DIR (feed sync on first run)"
