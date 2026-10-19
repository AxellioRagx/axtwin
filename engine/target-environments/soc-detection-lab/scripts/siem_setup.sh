#!/usr/bin/env bash
# SOC SIEM — Wazuh single-node stack via Docker (SIEM + EDR telemetry).
# Idempotent: only brings the stack up if it isn't already running.
# NOTE: pulling the Wazuh images needs egress through the `fw` router's WAN.
set -euo pipefail

systemctl enable --now docker || true

WAZUH_DIR=/opt/wazuh-docker
install -d -m 0755 "$WAZUH_DIR"

# Minimal single-node compose. (The official multi-node deploy is heavier; this
# is the lab-sized SIEM the corpus's home labs use.) Pinned for reproducibility.
cat > "$WAZUH_DIR/docker-compose.yml" <<'YAML'
services:
  wazuh-manager:
    image: wazuh/wazuh-manager:4.9.0
    restart: unless-stopped
    ports: ["1514:1514/udp", "1515:1515", "55000:55000"]
    ulimits:
      memlock: { soft: -1, hard: -1 }
YAML

# Idempotent up: `docker compose up -d` is a no-op when already converged.
if command -v docker >/dev/null 2>&1; then
  (cd "$WAZUH_DIR" && docker compose up -d) \
    || echo "wazuh stack pull/up deferred (needs router WAN egress)"
fi

echo "SIEM staged: wazuh-manager single-node at $WAZUH_DIR"
