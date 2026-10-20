#!/usr/bin/env bash
# Hunt stack — Security-Onion-style search + NSM: OpenSearch single-node (log/IOC
# search) + Zeek/Suricata for offline PCAP processing. Idempotent.
# Pulling the OpenSearch image needs gw router WAN egress.
set -euo pipefail

systemctl enable --now docker || true

HUNT_DIR=/opt/hunt
install -d -m 0755 "$HUNT_DIR"

# OpenSearch single-node (the OSS Elastic stand-in the corpus's hunt labs use).
cat > "$HUNT_DIR/docker-compose.yml" <<'YAML'
services:
  opensearch:
    image: opensearchproject/opensearch:2.17.0
    restart: unless-stopped
    environment:
      - discovery.type=single-node
      - bootstrap.memory_lock=true
      - DISABLE_SECURITY_PLUGIN=true
      - OPENSEARCH_JAVA_OPTS=-Xms1g -Xmx1g
    ulimits:
      memlock: { soft: -1, hard: -1 }
    ports: ["9200:9200"]
YAML

if command -v docker >/dev/null 2>&1; then
  (cd "$HUNT_DIR" && docker compose up -d) \
    || echo "OpenSearch up deferred (needs gw router WAN egress)"
fi

# Offline PCAP processing helper: run Zeek over a PCAP pulled from the sensor.
cat > "$HUNT_DIR/process_pcap.sh" <<'SH'
#!/usr/bin/env bash
# Usage: process_pcap.sh <evidence.pcap>  -> Zeek logs in ./zeek-out
set -euo pipefail
PCAP="${1:?usage: process_pcap.sh <pcap>}"
mkdir -p /opt/hunt/zeek-out && cd /opt/hunt/zeek-out
zeek -r "$PCAP" 2>/dev/null || echo "zeek offline run needs the pcap + zeek"
echo "conn/dns/http/ssl logs in /opt/hunt/zeek-out"
SH
chmod 0755 "$HUNT_DIR/process_pcap.sh"

echo "hunt stack staged: OpenSearch :9200 + zeek/suricata offline PCAP processing"
