#!/bin/bash
# scripts/o2-pcaps-fetch.sh
#
# Download pcap file(s) from the EXTERNAL o2 tcpdump sensor to a local path.
#
# Companion to o2-pcaps-list.sh. The tcpdump sensor's capture directory is a
# virtio-fs share mapped from a directory on the Proxmox host, so fetching is a
# plain copy off that host directory — `cp` when run on the host, `rsync`/`scp`
# when run remotely. Wildcards are supported and are resolved ON THE HOST side;
# the resolved file list (not a re-globbed pattern) is what gets copied.
#
# Usage:
#   ./scripts/o2-pcaps-fetch.sh [options] <pattern> <dest>
#
# Arguments:
#   <pattern>            filename or glob to fetch, e.g.
#                          cap-20260726-143000.pcap
#                          'cap-20260726-*'
#                          '*.pcap*'      (quote globs so YOUR shell doesn't eat them)
#   <dest>               destination. A directory (existing, or ending in '/')
#                        receives the matched files; a plain path is only valid
#                        when exactly one file matches (rename-on-copy).
#
# Options:
#   --host <user@host>   ssh/scp/rsync against this host (default: local copy).
#                        Also PCAP_HOST.
#   --dir  <path>        base capture dir on the host
#                        (default: /var/lib/axrange/o2-pcaps). Also PCAP_DIR.
#   --sensor <id>        per-sensor subdirectory under --dir. Also PCAP_SENSOR.
#   -n, --dry-run        list what would be fetched; copy nothing.
#   -h, --help           show this help.
#
# Examples:
#   ./scripts/o2-pcaps-fetch.sh 'cap-20260726-*' ./pcaps/
#   ./scripts/o2-pcaps-fetch.sh --host root@dev45 --sensor Tcpdump-SW-A '*.pcap*' ~/captures/
#   PCAP_HOST=root@dev45 ./scripts/o2-pcaps-fetch.sh cap-20260726-143000.pcap /tmp/one.pcap
set -euo pipefail

HOST="${PCAP_HOST:-}"
DIR="${PCAP_DIR:-/var/lib/axrange/o2-pcaps}"
SENSOR="${PCAP_SENSOR:-}"
DRYRUN=0
POS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --host)    HOST="$2"; shift 2 ;;
    --dir)     DIR="$2"; shift 2 ;;
    --sensor)  SENSOR="$2"; shift 2 ;;
    -n|--dry-run) DRYRUN=1; shift ;;
    -h|--help) sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    --) shift; while [ $# -gt 0 ]; do POS+=("$1"); shift; done ;;
    -*) echo "unknown option: $1" >&2; exit 2 ;;
    *)  POS+=("$1"); shift ;;
  esac
done

if [ "${#POS[@]}" -ne 2 ]; then
  echo "usage: $0 [options] <pattern> <dest>   (see --help)" >&2
  exit 2
fi
PATTERN="${POS[0]}"
DEST="${POS[1]}"

CAPDIR="$DIR"
[ -n "$SENSOR" ] && CAPDIR="$DIR/$SENSOR"

# Fail fast on an unreachable host instead of hanging on the TCP timeout.
SSH_OPTS=(-o ConnectTimeout=10 -o BatchMode=yes)

# Resolve the matching file list ON THE HOST (glob expanded there). CAPDIR/PATTERN
# are passed as positional args ($1/$2), never interpolated — so paths with $/"/space
# are data, not code, and no `printf %q`/login-shell-bash assumption is needed. The
# exit status propagates through command substitution (a `< <(…)` process substitution
# would have swallowed an ssh/remote failure and read it as "no matches"). Emits one
# basename per line, then a `__NODIR__`/`__OK__` sentinel so an ssh failure (empty
# output, non-zero rc) is never mistaken for "zero matches".
RESOLVER='
set -u
dir=$1; pat=$2
[ -d "$dir" ] || { echo "__NODIR__"; exit 3; }
cd "$dir" || { echo "__NODIR__"; exit 3; }
shopt -s nullglob
for f in $pat; do
  [ -e "$f" ] || continue        # nullglob keeps a literal non-existent name; drop it
  printf "%s\n" "$f"
done
echo "__OK__"
'

if [ -n "$HOST" ]; then
  out=$(ssh "${SSH_OPTS[@]}" "$HOST" bash -s -- "$CAPDIR" "$PATTERN" <<<"$RESOLVER") || {
    rc=$?
    echo "error: could not reach '$HOST' (ssh exit $rc)" >&2
    exit "$rc"
  }
else
  out=$(bash -s -- "$CAPDIR" "$PATTERN" <<<"$RESOLVER")
fi

if printf '%s\n' "$out" | grep -q '^__NODIR__$'; then
  echo "capture dir not found: $CAPDIR${HOST:+  (on $HOST)}" >&2
  exit 3
fi
if ! printf '%s\n' "$out" | grep -q '^__OK__$'; then
  echo "error: remote listing did not complete (partial/failed output)" >&2
  exit 4
fi
mapfile -t MATCHES < <(printf '%s\n' "$out" | grep -vE '^__OK__$')

if [ "${#MATCHES[@]}" -eq 0 ]; then
  echo "no files match '$PATTERN' in $CAPDIR${HOST:+  (on $HOST)}"
  exit 0
fi

# Destination validation: a directory (existing or trailing-slash) can take many
# files; a plain path can only take exactly one.
dest_is_dir=0
case "$DEST" in
  */) dest_is_dir=1 ;;
esac
[ -d "$DEST" ] && dest_is_dir=1
if [ "$dest_is_dir" -eq 0 ] && [ "${#MATCHES[@]}" -gt 1 ]; then
  echo "error: '$PATTERN' matched ${#MATCHES[@]} files but dest '$DEST' is not a directory." >&2
  echo "       give a directory (e.g. '${DEST%/}/') to receive multiple files." >&2
  exit 2
fi

echo "matched ${#MATCHES[@]} file(s) in $CAPDIR${HOST:+ on $HOST}:"
printf '  %s\n' "${MATCHES[@]}"
if [ "$DRYRUN" -eq 1 ]; then
  echo "(dry-run: nothing copied)"
  exit 0
fi

[ "$dest_is_dir" -eq 1 ] && mkdir -p "$DEST"

if [ -n "$HOST" ]; then
  # Copy the RESOLVED list (not a re-globbed pattern) → no TOCTOU drift, and
  # spaces/specials in CAPDIR are safe. rsync --files-from reads names relative
  # to the remote source dir; fall back to a per-file scp when rsync is absent.
  if command -v rsync >/dev/null 2>&1; then
    printf '%s\n' "${MATCHES[@]}" | rsync -avh -e "ssh ${SSH_OPTS[*]}" --files-from=- "$HOST:$CAPDIR/" "${DEST%/}/"
  else
    for f in "${MATCHES[@]}"; do
      scp "${SSH_OPTS[@]}" -p "$HOST:$CAPDIR/$f" "$DEST"
    done
  fi
else
  for f in "${MATCHES[@]}"; do
    cp -pv "$CAPDIR/$f" "$DEST"
  done
fi
echo "done -> $DEST"
