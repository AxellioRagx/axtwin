#!/usr/bin/env bash
# SOC SIEM — Wazuh single-node via Docker. Idempotent. Image pull needs egress.
set -euo pipefail

systemctl enable --now docker || true

WAZUH_DIR=/opt/wazuh-docker
install -d -m 0755 "$WAZUH_DIR"
cat > "$WAZUH_DIR/docker-compose.yml" <<'YAML'
services:
  wazuh-manager:
    image: wazuh/wazuh-manager:4.9.0
    restart: unless-stopped
    ports: ["1514:1514/udp", "1515:1515", "55000:55000"]
    ulimits:
      memlock: { soft: -1, hard: -1 }
YAML

if command -v docker >/dev/null 2>&1; then
  (cd "$WAZUH_DIR" && docker compose up -d) \
    || echo "wazuh stack up deferred (needs core router WAN egress)"
fi

echo "SIEM staged: wazuh-manager single-node at $WAZUH_DIR"
