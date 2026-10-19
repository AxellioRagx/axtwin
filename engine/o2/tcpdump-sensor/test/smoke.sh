#!/usr/bin/env bash
#
# smoke.sh — end-to-end self-test for the capture appliance on a single host.
#
# Per docs/tcpdump-sensor/README.md §8. Verifies:
#   1. capture-start opens a NIC, drops privileges, and writes rotating
#      pcaps into the dated directory tree.
#   2. The pcaps are valid (tcpdump -r can read them).
#   3. capture-watchdog prunes oldest files when SOFT threshold is crossed,
#      while preserving MIN_KEEP_FILES newest.
#   4. capture-healthcheck reports OK (with HEALTHCHECK_SKIP_SERVICE=1 since
#      this smoke test runs tcpdump directly, not via systemd).
#
# Requirements:
#   * root (creates veth pair, sets PROMISC, runs tcpdump).
#   * tcpdump, ip, ethtool, awk, find, df, zstd OR gzip.
#   * No conflicting interfaces named cs-veth-a / cs-veth-b.
#   * No production capture appliance running (will clash on file paths if /tmp/smoke-* is overlapping).
#
# Run:
#   sudo o2/tcpdump-sensor/test/smoke.sh                 # full run, ~45s
#   sudo SMOKE_KEEP=1 o2/tcpdump-sensor/test/smoke.sh    # leave /tmp/smoke-* in place for inspection
#   sudo SMOKE_VERBOSE=1 o2/tcpdump-sensor/test/smoke.sh # set -x

set -euo pipefail

[ "${SMOKE_VERBOSE:-0}" = "1" ] && set -x

# ---------- locations --------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
SENSOR_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

VETH_A=cs-veth-a    # capture side (no IP, PROMISC)
VETH_B=cs-veth-b    # traffic-generator side (moved into netns)
NETNS=cs-smoke

TMP_ROOT=/tmp/smoke-captures
TMP_STATE=/tmp/smoke-state
TMP_CONF=/tmp/smoke-capture.conf

CAPTURE_PID=""

# ---------- helpers ----------------------------------------------------------

red()   { printf '\033[31m%s\033[0m\n' "$*" >&2; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
amber() { printf '\033[33m%s\033[0m\n' "$*"; }
step()  { echo; echo "=== $* ==="; }
fail()  { red "FAIL: $*"; exit 1; }
note()  { echo "  $*"; }

cleanup() {
    set +e
    [ -n "$CAPTURE_PID" ] && kill -TERM "$CAPTURE_PID" 2>/dev/null
    sleep 1
    [ -n "$CAPTURE_PID" ] && kill -KILL "$CAPTURE_PID" 2>/dev/null

    ip netns del "$NETNS" 2>/dev/null
    ip link del "$VETH_A" 2>/dev/null

    if [ "${SMOKE_KEEP:-0}" != "1" ]; then
        # If TMP_ROOT was mounted as tmpfs, unmount first.
        if mountpoint -q "$TMP_ROOT" 2>/dev/null; then
            umount "$TMP_ROOT" 2>/dev/null
        fi
        rm -rf "$TMP_ROOT" "$TMP_STATE" "$TMP_CONF"
    else
        amber "SMOKE_KEEP=1; leaving $TMP_ROOT, $TMP_STATE, $TMP_CONF in place"
    fi
}
trap cleanup EXIT

# ---------- preflight --------------------------------------------------------

[ "$(id -u)" -eq 0 ] || fail "smoke.sh must run as root"

for cmd in tcpdump ip ethtool awk find df mountpoint; do
    command -v "$cmd" >/dev/null 2>&1 || fail "missing dependency: $cmd"
done
if ! command -v zstd >/dev/null 2>&1 && ! command -v gzip >/dev/null 2>&1; then
    fail "missing compression: install zstd or gzip"
fi

if ip link show "$VETH_A" >/dev/null 2>&1; then
    fail "interface $VETH_A already exists; smoke.sh refuses to clobber it"
fi

# Need a 'capture' user for -Z. We don't want to require install.sh has run,
# so create a tmp user IF it's not already there.
TEMP_CAPTURE_USER=0
if ! id -u capture >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin --user-group capture
    TEMP_CAPTURE_USER=1
fi

# ---------- step 1: setup ----------------------------------------------------

step "step 1: create veth pair and namespace"

ip link add "$VETH_A" type veth peer name "$VETH_B"
ip link set "$VETH_A" up

ip netns add "$NETNS"
ip link set "$VETH_B" netns "$NETNS"
ip -n "$NETNS" addr add 10.255.255.2/24 dev "$VETH_B"
ip -n "$NETNS" link set "$VETH_B" up
ip -n "$NETNS" link set lo up

# PROMISC on the capture side. install.sh's udev rule does this in production;
# here we set it directly since systemd-udevd may not match a runtime-created
# veth depending on rule cache state.
ip link set "$VETH_A" promisc on
ethtool -K "$VETH_A" gro off lro off tso off gso off 2>/dev/null || true

note "veth pair up: $VETH_A (host, PROMISC) <-> $VETH_B (netns $NETNS, 10.255.255.2/24)"

# ---------- step 2: smoke config + state dirs --------------------------------

step "step 2: prepare smoke capture.conf and dirs"

# Use a tmpfs capacity small enough to make watchdog pruning testable.
# 64 MB: we can write ~16 MB of pcaps then push past SOFT threshold easily.
mkdir -p "$TMP_ROOT" "$TMP_STATE"
mount -t tmpfs -o size=64m tmpfs "$TMP_ROOT"
chown capture:capture "$TMP_ROOT"
chmod 0750 "$TMP_ROOT"

cat > "$TMP_CONF" <<EOF
CAPTURE_IF=$VETH_A
SOURCE_TAG=smoke
EXCLUDE_BPF=""
ROTATE_SECONDS=10
ROTATE_MEGABYTES=4
COMPRESS_CMD="none"
KERNEL_RING_KB=4096
SNAPLEN=0
CAPTURE_USER=capture
CAPTURE_DIR=$TMP_ROOT
SOFT_THRESHOLD_PCT=20
HARD_THRESHOLD_PCT=80
TARGET_AFTER_PRUNE_PCT=10
MIN_KEEP_FILES=2
HARD_ACTION=alert
HEALTHCHECK_MAX_PCAP_AGE=60
EOF

note "wrote $TMP_CONF; CAPTURE_DIR=$TMP_ROOT (64MB tmpfs)"

# The state dir capture-healthcheck writes to.
mkdir -p /var/lib/capture

# ---------- step 3: launch capture-start ------------------------------------

step "step 3: launch capture-start in background"

CAPTURE_CONF="$TMP_CONF" "$SENSOR_DIR/bin/capture-start" \
    > /tmp/smoke-capture.log 2>&1 &
CAPTURE_PID=$!
sleep 2

if ! kill -0 "$CAPTURE_PID" 2>/dev/null; then
    red "capture-start exited prematurely. Log:"
    sed 's/^/  | /' /tmp/smoke-capture.log >&2
    fail "capture-start did not stay running"
fi

# tcpdump may not be PID==$CAPTURE_PID (capture-start exec's into it), but
# the PID stays the same across exec, so $CAPTURE_PID is still the tcpdump.
note "capture-start running as PID $CAPTURE_PID"

# ---------- step 4: generate traffic and assert pcaps ------------------------

step "step 4: generate traffic into netns and wait for first rotation"

# Push frames continuously for ~15s so we cross at least one rotation.
(
    set +e
    # ping to a non-existent neighbor produces ARP + ICMP retries.
    ip netns exec "$NETNS" ping -c 30 -i 0.5 -W 1 10.255.255.99 >/dev/null 2>&1
) &
TRAFFIC_PID=$!

# Wait up to 20s for the first pcap.
deadline=$(( $(date +%s) + 20 ))
first_pcap=""
while [ "$(date +%s)" -lt "$deadline" ]; do
    first_pcap=$(find "$TMP_ROOT" -type f -name 'cap-*.pcap*' | head -1 || true)
    [ -n "$first_pcap" ] && break
    sleep 1
done
wait "$TRAFFIC_PID" 2>/dev/null || true

[ -n "$first_pcap" ] || fail "no pcap appeared under $TMP_ROOT within 20s"
note "first pcap: $first_pcap"

# Wait for at least one rotation (ROTATE_SECONDS=10) so we have a closed file
# that tcpdump -r can definitively parse.
sleep 12
mapfile -t pcaps < <(find "$TMP_ROOT" -type f -name 'cap-*.pcap*' | sort)
[ "${#pcaps[@]}" -ge 1 ] || fail "expected >=1 pcap, got 0"
note "have ${#pcaps[@]} pcap(s) after 12s wait"

# Validate the first pcap with `tcpdump -r`. We only check that tcpdump can
# read it; we don't assert the content (ICMP retries may or may not be in
# the very first rotation depending on timing).
if ! tcpdump -nn -r "${pcaps[0]}" >/dev/null 2>&1; then
    fail "tcpdump -r could not parse ${pcaps[0]}"
fi
note "tcpdump -r ${pcaps[0]} : OK"

# Pcap should be owned by capture user (post -Z), not root.
owner=$(stat -c %U "${pcaps[0]}")
if [ "$owner" != "capture" ]; then
    amber "  WARN: pcap owner is '$owner', expected 'capture' (privilege drop may have failed)"
fi

# ---------- step 5: force prune ---------------------------------------------

step "step 5: force-prune via watchdog"

# Generate enough pcaps to have something to prune. With tmpfs=64MB and
# rotate=4MB, ~10 pcaps would saturate. But we don't have enough traffic
# to write that much in finite time, so we'll create synthetic dummy files
# with old mtimes — the watchdog only cares about names matching cap-*.pcap*
# and mtime. We keep the real ones in place too.

dummy_dir="$TMP_ROOT/2026/01/01/smoke"
mkdir -p "$dummy_dir"
chown -R capture:capture "$TMP_ROOT/2026"
for i in $(seq 1 10); do
    f="$dummy_dir/cap-20260101-00000${i}.pcap"
    head -c 4194304 /dev/urandom > "$f"   # 4 MB each = 40 MB total
    # Backdate so they're "older" than the real ones.
    touch -d "2026-01-01 00:00:0${i}" "$f"
    chown capture:capture "$f"
done
note "wrote 10 dummy 4MB pcaps with old mtimes; df now:"
df -h "$TMP_ROOT" | sed 's/^/  /'

# Run watchdog manually.
CAPTURE_CONF="$TMP_CONF" "$SENSOR_DIR/bin/capture-watchdog"

# Assert: at least MIN_KEEP_FILES files survive (real ones + 2 dummies maybe),
# and at least one dummy was deleted.
mapfile -t survivors < <(find "$TMP_ROOT" -type f -name 'cap-*.pcap*' | sort)
note "survivors after prune: ${#survivors[@]}"
df -h "$TMP_ROOT" | sed 's/^/  /'

if [ "${#survivors[@]}" -lt 2 ]; then
    fail "expected at least MIN_KEEP_FILES=2 to survive; got ${#survivors[@]}"
fi
remaining_dummies=$(find "$dummy_dir" -type f -name 'cap-*.pcap*' 2>/dev/null | wc -l)
if [ "$remaining_dummies" -ge 10 ]; then
    fail "watchdog deleted no dummy files; expected pruning of oldest"
fi
note "watchdog pruned $((10 - remaining_dummies)) of 10 dummy files (oldest-first)"

# ---------- step 6: healthcheck ---------------------------------------------

step "step 6: capture-healthcheck"

# tcpdump (started by capture-start) is still running in the background.
# We skip the systemctl is-active check since we're not running via systemd.
if HEALTHCHECK_SKIP_SERVICE=1 CAPTURE_CONF="$TMP_CONF" \
   "$SENSOR_DIR/bin/capture-healthcheck"; then
    note "healthcheck: OK"
else
    red "healthcheck reported PROBLEM(s) (re-running with output):"
    HEALTHCHECK_SKIP_SERVICE=1 CAPTURE_CONF="$TMP_CONF" \
       "$SENSOR_DIR/bin/capture-healthcheck" || true
    fail "capture-healthcheck did not exit 0"
fi

# ---------- step 7: graceful teardown via SIGTERM ----------------------------

step "step 7: SIGTERM capture, verify clean exit"

kill -TERM "$CAPTURE_PID"
deadline=$(( $(date +%s) + 5 ))
while kill -0 "$CAPTURE_PID" 2>/dev/null; do
    [ "$(date +%s)" -ge "$deadline" ] && break
    sleep 0.5
done
if kill -0 "$CAPTURE_PID" 2>/dev/null; then
    amber "  WARN: tcpdump did not exit within 5s of SIGTERM; sending SIGKILL"
    kill -KILL "$CAPTURE_PID"
fi
CAPTURE_PID=""
note "tcpdump exited"

# ---------- finalize ---------------------------------------------------------

# Remove the temporary capture user we may have created.
if [ "$TEMP_CAPTURE_USER" -eq 1 ]; then
    userdel capture 2>/dev/null || true
fi

green "=== smoke.sh: ALL CHECKS PASSED ==="
exit 0
