#!/usr/bin/env bash
# Single-node SIEM marker. Real Wazuh compose bring-up is deferred (image/egress);
# this stages the compose dir so the SIEM host is present in the estate.
set -uo pipefail
install -d -m 0755 /opt/siem
echo "siem: docker-compose staging dir at /opt/siem (Wazuh single-node deferred)" > /opt/siem/README
echo "siem_setup: staged"
