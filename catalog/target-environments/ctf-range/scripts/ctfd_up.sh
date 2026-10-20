#!/usr/bin/env bash
# CTFd scoreboard via Docker. Idempotent (compose up is a no-op when converged).
# Pulling the CTFd image needs egress through the gw router's WAN.
set -euo pipefail

systemctl enable --now docker || true

CTFD_DIR=/opt/ctfd
install -d -m 0755 "$CTFD_DIR"
cat > "$CTFD_DIR/docker-compose.yml" <<'YAML'
services:
  ctfd:
    image: ctfd/ctfd:3.7.0
    restart: unless-stopped
    ports: ["8000:8000"]
    environment:
      - UPLOAD_FOLDER=/var/uploads
    volumes:
      - ctfd-data:/var/uploads
volumes:
  ctfd-data:
YAML

if command -v docker >/dev/null 2>&1; then
  (cd "$CTFD_DIR" && docker compose up -d) \
    || echo "CTFd up deferred (needs router WAN egress)"
fi

echo "CTFd staged at $CTFD_DIR -> http://10.70.10.10:8000"
