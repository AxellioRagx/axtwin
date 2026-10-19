#!/bin/bash
# scripts/o2-pcaps-list.sh
#
# List the pcap files produced by the EXTERNAL o2 tcpdump sensor.
#
# The tcpdump sensor is a hand-built VM declared as an external sensor
# (adapter.conf EXTERNAL_SENSORS, "just like XPress-SW"). Its capture directory
# is a virtio-fs share mapped from a directory on the Proxmox host, so the pcaps
# are readable directly on the host filesystem — no QGA, no reach-into-guest.
# This script just lists that host directory (locally, or over ssh to the host).
#
# Pcap naming (from the tcpdump capture stack): cap-<YYYYmmdd-HHMMSS>.pcap and,
# under sustained traffic, the size-rotation siblings cap-<ts>.pcap1, .pcap2, …
# (tcpdump's -C counter lands AFTER the extension — LES-085). The default glob is
# therefore `cap-*`, NOT `cap-*.pcap`.
#
# Usage:
#   ./scripts/o2-pcaps-list.sh [options] [glob]
#
# Options:
#   --host <user@host>   ssh to this host first (default: run locally).
#                        Also settable via PCAP_HOST.
#   --dir  <path>        base capture dir on the host
#                        (default: /var/lib/axrange/o2-pcaps). Also PCAP_DIR.
#   --sensor <id>        per-sensor subdirectory under --dir (default: none).
#                        Also PCAP_SENSOR.
#   -h, --help           show this help.
#
#   [glob]               optional filename pattern (default: cap-*), e.g.
#                        'cap-20260726-*'  or  '*.pcap*'
#
# Examples:
#   ./scripts/o2-pcaps-list.sh
#   ./scripts/o2-pcaps-list.sh --host root@dev45 --sensor Tcpdump-SW-A
#   PCAP_HOST=root@dev45 ./scripts/o2-pcaps-list.sh 'cap-20260726-1*'
set -euo pipefail

HOST="${PCAP_HOST:-}"
DIR="${PCAP_DIR:-/var/lib/axrange/o2-pcaps}"
SENSOR="${PCAP_SENSOR:-}"
PATTERN='cap-*'

while [ $# -gt 0 ]; do
  case "$1" in
    --host)   HOST="$2"; shift 2 ;;
    --dir)    DIR="$2"; shift 2 ;;
    --sensor) SENSOR="$2"; shift 2 ;;
    -h|--help) sed -n '2,35p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    --) shift; break ;;
    -*) echo "unknown option: $1" >&2; exit 2 ;;
    *)  PATTERN="$1"; shift ;;
  esac
done

CAPDIR="$DIR"
[ -n "$SENSOR" ] && CAPDIR="$DIR/$SENSOR"

# Fail fast on an unreachable host instead of hanging on the TCP timeout.
SSH_OPTS=(-o ConnectTimeout=10 -o BatchMode=yes)

# The listing runs on the target (local or remote). CAPDIR/PATTERN are passed as
# POSITIONAL ARGS ($1/$2), never interpolated into the script text — so a path
# containing $ or " is data, not code, and the remote side never needs `printf %q`
# (which emits $'…' that a dash login shell can't parse). The snippet is fed on
# stdin to a forced `bash`, and its exit status propagates through command
# substitution below (a process-substitution `< <(…)` would have hidden it).
LISTER='
set -u
dir=$1; pat=$2
[ -d "$dir" ] || { echo "__NODIR__"; exit 3; }
cd "$dir" || { echo "__NODIR__"; exit 3; }
shopt -s nullglob
n=0; tot=0
for f in $pat; do
  [ -e "$f" ] || continue        # nullglob keeps a literal non-existent name; drop it
  sz=$(stat -c %s "$f" 2>/dev/null || echo 0)
  mt=$(stat -c "%y" "$f" 2>/dev/null | cut -d. -f1)
  printf "%12d  %s  %s\n" "$sz" "$mt" "$f"
  n=$((n+1)); tot=$((tot+sz))
done
printf "__SUMMARY__ %d %d\n" "$n" "$tot"
'

if [ -n "$HOST" ]; then
  out=$(ssh "${SSH_OPTS[@]}" "$HOST" bash -s -- "$CAPDIR" "$PATTERN" <<<"$LISTER") || {
    rc=$?
    echo "error: could not reach '$HOST' (ssh exit $rc)" >&2
    exit "$rc"
  }
else
  out=$(bash -s -- "$CAPDIR" "$PATTERN" <<<"$LISTER")
fi

if printf '%s\n' "$out" | grep -q '^__NODIR__$'; then
  echo "capture dir not found: $CAPDIR${HOST:+  (on $HOST)}" >&2
  exit 3
fi

# `|| true`: grep -v exits 1 when the ONLY line is the summary (zero matches) —
# under `set -e` that would abort the script silently before the no-match message.
body=$(printf '%s\n' "$out" | grep -v '^__SUMMARY__' || true)
summary=$(printf '%s\n' "$out" | grep '^__SUMMARY__' | head -1)
count=$(echo "$summary" | awk '{print $2}')
bytes=$(echo "$summary" | awk '{print $3}')

if [ "${count:-0}" -eq 0 ]; then
  echo "no files match '$PATTERN' in $CAPDIR${HOST:+  (on $HOST)}"
  exit 0
fi
# bytes | human size
hsize=$(numfmt --to=iec --suffix=B "$bytes" 2>/dev/null || echo "${bytes}B")
[ -n "$body" ] && printf '%s\n' "$body"
printf '\ntotal: %s files, %s\n' "$count" "$hsize"
