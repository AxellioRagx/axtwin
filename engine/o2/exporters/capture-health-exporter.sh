#!/usr/bin/env bash
#
# capture-health-exporter.sh — export capture appliance health as Prometheus metrics.
#
# Runs ON THE CAPTURE HOST (NOT the observability stack host). Wraps the
# tcpdump-sensor's own `capture-healthcheck` (exit 0/1) and emits a node_exporter
# textfile-collector file with five gauges. A node_exporter on the capture host, started
# with --collector.textfile.directory=<dir>, then exposes them; Prometheus
# scrapes node_exporter (o2/prometheus/prometheus.yml).
#
# This COMPLEMENTS o2/tcpdump-sensor/ — it does not modify it. It re-reads the same
# /etc/capture/capture.conf so the exported numbers match what the appliance uses.
#
# Metrics:
#   capture_up               1 if capture-healthcheck exits 0, else 0
#   newest_pcap_age_seconds  age of the newest pcap under CAPTURE_DIR
#   rx_drops_total           cumulative kernel RX drops on CAPTURE_IF (counter)
#   disk_pct                 % usage of the CAPTURE_DIR filesystem
#   promisc_on               1 if CAPTURE_IF is in PROMISC mode, else 0
#
# Config: reads observability.conf if present (for CAPTURE_TEXTFILE_DIR /
# CAPTURE_HEALTHCHECK_BIN / CAPTURE_CONF), each overridable by an env var of the same
# name. Designed to be fired by a systemd timer every 30-60s.
#
# Usage:
#   capture-health-exporter.sh                 # write textfile, exit 0
#   capture-health-exporter.sh --stdout        # print metrics to stdout (debug)
#   capture-health-exporter.sh --healthcheck   # self-test: can it produce metrics?

set -uo pipefail

# ---- Load config (observability.conf optional; env overrides) ---------------
# Look for observability.conf next to this script's parent dir, then /etc.
_self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for _cand in \
    "${OBSERVABILITY_CONF:-}" \
    "${_self_dir}/../observability.conf" \
    "/etc/axrange/observability.conf"; do
    if [ -n "${_cand:-}" ] && [ -r "$_cand" ]; then
        # shellcheck disable=SC1090
        . "$_cand"
        break
    fi
done

CAPTURE_TEXTFILE_DIR="${CAPTURE_TEXTFILE_DIR:-/var/lib/node_exporter/textfile_collector}"
CAPTURE_HEALTHCHECK_BIN="${CAPTURE_HEALTHCHECK_BIN:-/usr/local/bin/capture-healthcheck}"
CAPTURE_CONF="${CAPTURE_CONF:-/etc/capture/capture.conf}"

MODE="textfile"
case "${1:-}" in
    --stdout)      MODE="stdout" ;;
    --healthcheck) MODE="healthcheck" ;;
    "")            MODE="textfile" ;;
    *) echo "usage: $0 [--stdout|--healthcheck]" >&2; exit 2 ;;
esac

# ---- Read capture config for CAPTURE_IF / CAPTURE_DIR -----------------------
CAPTURE_IF=""
CAPTURE_DIR=""
if [ -r "$CAPTURE_CONF" ]; then
    # shellcheck disable=SC1090
    . "$CAPTURE_CONF"
fi
CAPTURE_IF="${CAPTURE_IF:-}"
CAPTURE_DIR="${CAPTURE_DIR:-/captures}"

# ---- 1. capture_up: run capture-healthcheck --------------------------------
capture_up=0
if [ -x "$CAPTURE_HEALTHCHECK_BIN" ]; then
    if "$CAPTURE_HEALTHCHECK_BIN" >/dev/null 2>&1; then
        capture_up=1
    fi
elif systemctl is-active --quiet capture.service 2>/dev/null; then
    # Fallback if the healthcheck binary isn't found but the service is up.
    capture_up=1
fi

# ---- 2. newest_pcap_age_seconds --------------------------------------------
newest_pcap_age=-1
if [ -d "$CAPTURE_DIR" ]; then
    newest=$(find "$CAPTURE_DIR" -type f -name 'cap-*.pcap*' \
             -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | awk '{print $2}')
    if [ -n "$newest" ]; then
        mtime=$(stat -c %Y "$newest" 2>/dev/null || echo "")
        if [ -n "$mtime" ]; then
            newest_pcap_age=$(( $(date +%s) - mtime ))
        fi
    fi
fi

# ---- 3. rx_drops_total (counter) -------------------------------------------
# /proc/net/dev field 5 (rxDrop) for the CAPTURE_IF row. -1 if unreadable.
rx_drops=-1
if [ -n "$CAPTURE_IF" ]; then
    val=$(awk -v want="${CAPTURE_IF}:" '$1 == want { print $5 }' /proc/net/dev 2>/dev/null)
    if [ -n "${val:-}" ]; then
        rx_drops="$val"
    fi
fi

# ---- 4. disk_pct -----------------------------------------------------------
disk_pct=-1
if [ -d "$CAPTURE_DIR" ]; then
    pct=$(df --output=pcent "$CAPTURE_DIR" 2>/dev/null | tail -1 | tr -dc 0-9)
    if [ -n "${pct:-}" ]; then
        disk_pct="$pct"
    fi
fi

# ---- 5. promisc_on ---------------------------------------------------------
promisc_on=-1
if [ -n "$CAPTURE_IF" ]; then
    if ip link show "$CAPTURE_IF" >/dev/null 2>&1; then
        if ip link show "$CAPTURE_IF" 2>/dev/null | grep -q PROMISC; then
            promisc_on=1
        else
            promisc_on=0
        fi
    fi
fi

# ---- Emit Prometheus exposition format -------------------------------------
emit_metrics() {
    local labels="iface=\"${CAPTURE_IF}\""
    cat <<EOF
# HELP capture_up Whether capture-healthcheck reports the capture appliance healthy (1) or not (0).
# TYPE capture_up gauge
capture_up{${labels}} ${capture_up}
# HELP newest_pcap_age_seconds Age in seconds of the newest pcap under CAPTURE_DIR (-1 if none/unknown).
# TYPE newest_pcap_age_seconds gauge
newest_pcap_age_seconds{${labels}} ${newest_pcap_age}
# HELP rx_drops_total Cumulative kernel RX drops on the capture NIC (-1 if unknown).
# TYPE rx_drops_total counter
rx_drops_total{${labels}} ${rx_drops}
# HELP disk_pct Percent usage of the CAPTURE_DIR filesystem (-1 if unknown).
# TYPE disk_pct gauge
disk_pct{${labels}} ${disk_pct}
# HELP promisc_on Whether the capture NIC is in PROMISC mode (1), not (0), or unknown (-1).
# TYPE promisc_on gauge
promisc_on{${labels}} ${promisc_on}
EOF
}

case "$MODE" in
    stdout)
        emit_metrics
        ;;
    healthcheck)
        # Self-test: we can read the config and produce a metrics block.
        if [ -z "$CAPTURE_IF" ]; then
            echo "PROBLEM: CAPTURE_IF not resolved from $CAPTURE_CONF" >&2
            exit 1
        fi
        emit_metrics >/dev/null
        echo "OK: capture-health-exporter can produce metrics for ${CAPTURE_IF}"
        ;;
    textfile)
        if ! mkdir -p "$CAPTURE_TEXTFILE_DIR" 2>/dev/null; then
            echo "PROBLEM: cannot create textfile dir $CAPTURE_TEXTFILE_DIR" >&2
            exit 1
        fi
        out="${CAPTURE_TEXTFILE_DIR}/axrange_capture.prom"
        tmp="${out}.$$"
        # Atomic write: node_exporter must never read a half-written file.
        if emit_metrics > "$tmp" 2>/dev/null; then
            mv -f "$tmp" "$out"
        else
            rm -f "$tmp"
            echo "PROBLEM: failed to write metrics to $tmp" >&2
            exit 1
        fi
        ;;
esac

exit 0
