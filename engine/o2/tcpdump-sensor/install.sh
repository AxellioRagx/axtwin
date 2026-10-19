#!/usr/bin/env bash
#
# install.sh — idempotent installer for the cyber-range capture appliance
# (the tcpdump-sensor).
#
# Run as root on a Debian 12+ or Ubuntu 22.04+ host with the o2/tcpdump-sensor/
# directory of this repo present.
#
# Usage:
#   sudo ./install.sh                 # full install or refresh
#   sudo ./install.sh --no-start      # install everything but don't enable/start units
#   sudo ./install.sh --dry-run       # show what would happen; no changes
#
# Idempotent. Re-running is safe and will pick up changes to scripts and
# units. Operator edits to /etc/capture/capture.conf are preserved
# (capture.conf is copied from the example ONLY if /etc/capture/capture.conf
# does not already exist).
#
# See docs/tcpdump-sensor/README.md for the full spec.

set -euo pipefail

# ---------- arg parsing ------------------------------------------------------

NO_START=0
DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --no-start) NO_START=1 ;;
        --dry-run)  DRY_RUN=1 ;;
        -h|--help)
            sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "install.sh: unknown arg '$arg' (try --help)" >&2; exit 2 ;;
    esac
done

# ---------- helpers ----------------------------------------------------------

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
amber() { printf '\033[33m%s\033[0m\n' "$*"; }
say()   { echo "install.sh: $*"; }
die()   { red "install.sh: FATAL: $*" >&2; exit 1; }

run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "  + $*"
    else
        "$@"
    fi
}

# --dry-run can be run by non-root to preview the install plan.
if [ "$DRY_RUN" -eq 0 ] && [ "$(id -u)" -ne 0 ]; then
    die "must be run as root (or pass --dry-run to preview without changes)"
fi

# Where this script lives (resolve symlinks).
SRC="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
[ -f "$SRC/capture.conf.example" ] || die "could not find capture.conf.example at $SRC; install.sh must run from within the o2/tcpdump-sensor/ directory of the repo"

# ---------- distro check -----------------------------------------------------

if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    case "$ID" in
        debian)
            major=${VERSION_ID%%.*}
            if [ -z "${major:-}" ] || [ "$major" -lt 12 ] 2>/dev/null; then
                amber "WARN: tested on Debian 12+ (got $PRETTY_NAME); proceeding."
            fi ;;
        ubuntu)
            major=${VERSION_ID%%.*}
            if [ "$major" -lt 22 ] 2>/dev/null; then
                amber "WARN: tested on Ubuntu 22.04+ (got $PRETTY_NAME); proceeding."
            fi ;;
        *) amber "WARN: untested distro '$ID' ($PRETTY_NAME); proceeding." ;;
    esac
else
    amber "WARN: /etc/os-release missing; cannot identify distro"
fi

# ---------- dependency check -------------------------------------------------

missing=()
for cmd in tcpdump ethtool ip find awk sort head install systemctl udevadm; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        missing+=("$cmd")
    fi
done
# Compression: zstd preferred, gzip acceptable.
if ! command -v zstd >/dev/null 2>&1 && ! command -v gzip >/dev/null 2>&1; then
    missing+=("zstd|gzip")
fi
if [ ${#missing[@]} -gt 0 ]; then
    red "Missing required commands: ${missing[*]}"
    case "${ID:-}" in
        debian|ubuntu)
            say "Install with:  apt-get install -y tcpdump ethtool iproute2 zstd findutils gawk coreutils systemd udev" ;;
        *) ;;
    esac
    die "install dependencies and re-run"
fi

# ---------- system user ------------------------------------------------------

if ! id -u capture >/dev/null 2>&1; then
    say "creating system user 'capture'"
    run useradd --system --no-create-home --shell /usr/sbin/nologin --user-group capture
else
    say "user 'capture' already exists"
fi

# ---------- directories ------------------------------------------------------

# /captures — pcap output root. Must exist before tcpdump first runs.
if [ ! -d /captures ]; then
    say "creating /captures (mode 0750, capture:capture)"
    run install -d -m 0750 -o capture -g capture /captures
else
    # Bring permissions back in line if drift detected.
    cur_mode=$(stat -c %a /captures 2>/dev/null || echo "")
    cur_own=$(stat -c %U:%G /captures 2>/dev/null || echo "")
    if [ "$cur_own" != "capture:capture" ] || [ "$cur_mode" != "750" ]; then
        say "normalizing /captures permissions"
        run chown capture:capture /captures
        run chmod 0750 /captures
    fi
fi

# Refuse to proceed if /captures is on a CoW filesystem (hard rule §10).
fstype=$(findmnt -n -o FSTYPE --target /captures 2>/dev/null || true)
case "$fstype" in
    btrfs|zfs)
        die "/captures is on $fstype, which is forbidden for the capture path (see docs/tcpdump-sensor/README.md §10). Move /captures to XFS or ext4 with noatime,nodiratime before re-running."
        ;;
    "")
        amber "WARN: could not determine filesystem type of /captures" ;;
    *)
        say "/captures filesystem: $fstype" ;;
esac

# Warn if /captures appears not to have noatime (the mount could silently
# burn IO on access-time updates at line rate).
mountopts=$(findmnt -n -o OPTIONS --target /captures 2>/dev/null || true)
if [ -n "$mountopts" ] && ! echo "$mountopts" | grep -qw noatime; then
    amber "WARN: /captures mount options ($mountopts) do not include 'noatime'. Consider remounting with -o remount,noatime,nodiratime."
fi

# /var/lib/capture — runtime state (last-rx-drops counter).
if [ ! -d /var/lib/capture ]; then
    say "creating /var/lib/capture (mode 0755, root:root)"
    run install -d -m 0755 -o root -g root /var/lib/capture
fi

# /etc/capture — config.
if [ ! -d /etc/capture ]; then
    say "creating /etc/capture (mode 0755, root:root)"
    run install -d -m 0755 -o root -g root /etc/capture
fi

# /usr/share/doc/capture — for systemd unit's Documentation= line.
run install -d -m 0755 -o root -g root /usr/share/doc/capture

# ---------- config -----------------------------------------------------------

if [ ! -f /etc/capture/capture.conf ]; then
    say "installing default capture.conf (EDIT before starting capture)"
    run install -m 0644 -o root -g root "$SRC/capture.conf.example" /etc/capture/capture.conf
    new_config=1
else
    say "preserving existing /etc/capture/capture.conf"
    new_config=0
fi

# Always refresh the example file so operators have an up-to-date reference.
run install -m 0644 -o root -g root "$SRC/capture.conf.example" /etc/capture/capture.conf.example

# ---------- scripts ----------------------------------------------------------

for s in capture-start capture-watchdog capture-healthcheck; do
    say "installing /usr/local/bin/$s"
    run install -m 0755 -o root -g root "$SRC/bin/$s" "/usr/local/bin/$s"
done

# ---------- systemd units ----------------------------------------------------

for u in capture.service capture-watchdog.service capture-watchdog.timer; do
    say "installing /etc/systemd/system/$u"
    run install -m 0644 -o root -g root "$SRC/systemd/$u" "/etc/systemd/system/$u"
done
run systemctl daemon-reload

# ---------- copy README to /usr/share/doc -----------------------------------

if [ -f "$SRC/README.md" ]; then
    run install -m 0644 -o root -g root "$SRC/README.md" /usr/share/doc/capture/README.md
fi

# ---------- udev rule + offloads --------------------------------------------
#
# We substitute @CAPTURE_IF@ from /etc/capture/capture.conf into the
# installed udev rule. If capture.conf is the freshly-copied default and the
# operator hasn't picked their NIC yet, we still write the rule with the
# default value so re-running install.sh after editing capture.conf updates it.

# Source the live config if present; otherwise fall back to the example
# we just (would have) installed. Lets --dry-run work without an existing
# /etc/capture/.
if [ -r /etc/capture/capture.conf ]; then
    # shellcheck disable=SC1091
    . /etc/capture/capture.conf
elif [ -r "$SRC/capture.conf.example" ]; then
    # shellcheck disable=SC1090
    . "$SRC/capture.conf.example"
fi

if [ -z "${CAPTURE_IF:-}" ]; then
    amber "WARN: CAPTURE_IF unset in /etc/capture/capture.conf; skipping udev rule"
else
    say "writing /etc/udev/rules.d/99-capture-promisc.rules for CAPTURE_IF=$CAPTURE_IF"
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "  + sed s/@CAPTURE_IF@/$CAPTURE_IF/g $SRC/udev/99-capture-promisc.rules > /etc/udev/rules.d/99-capture-promisc.rules"
    else
        sed "s/@CAPTURE_IF@/${CAPTURE_IF}/g" "$SRC/udev/99-capture-promisc.rules" \
            > /etc/udev/rules.d/99-capture-promisc.rules
        chmod 0644 /etc/udev/rules.d/99-capture-promisc.rules
    fi
    run udevadm control --reload-rules

    # Apply now if the NIC is present.
    if ip link show "$CAPTURE_IF" >/dev/null 2>&1; then
        run ip link set "$CAPTURE_IF" promisc on
        run ethtool -K "$CAPTURE_IF" gro off lro off tso off gso off || \
            amber "WARN: ethtool -K failed on $CAPTURE_IF; check NIC driver"
    else
        amber "NOTE: $CAPTURE_IF does not exist on this host yet — udev rule will fire when it appears"
    fi
fi

# ---------- enable + start ---------------------------------------------------

# Determine whether we can start: NIC must exist AND not have an IP,
# CAPTURE_DIR must exist, capture.conf must not be the unedited default if the
# defaults don't match the host.
can_start=1
reason=""

if [ -z "${CAPTURE_IF:-}" ]; then
    can_start=0; reason="CAPTURE_IF unset"
elif ! ip link show "$CAPTURE_IF" >/dev/null 2>&1; then
    can_start=0; reason="$CAPTURE_IF does not exist on this host"
elif ip -4 -o addr show dev "$CAPTURE_IF" | grep -q 'inet '; then
    can_start=0; reason="$CAPTURE_IF has an IPv4 address (remove it; see docs/tcpdump-sensor/README.md §10)"
elif ip -6 -o addr show dev "$CAPTURE_IF" scope global | grep -q 'inet6 '; then
    can_start=0; reason="$CAPTURE_IF has a global IPv6 address (remove it)"
elif [ ! -d "${CAPTURE_DIR:-/captures}" ]; then
    can_start=0; reason="CAPTURE_DIR '${CAPTURE_DIR:-/captures}' does not exist"
fi

if [ "$NO_START" -eq 1 ]; then
    say "--no-start specified; not enabling/starting units"
elif [ "$can_start" -eq 0 ]; then
    amber "NOT starting capture.service: $reason"
    amber "Edit /etc/capture/capture.conf and re-run install.sh (or: systemctl enable --now capture.service capture-watchdog.timer)."
else
    say "enabling and starting capture.service + capture-watchdog.timer"
    run systemctl enable capture.service capture-watchdog.timer
    # Restart rather than start so a re-run picks up new units.
    run systemctl restart capture.service
    run systemctl restart capture-watchdog.timer
fi

# ---------- summary ----------------------------------------------------------

echo
green "=== capture appliance install summary ==="
echo "  config:           /etc/capture/capture.conf $( [ "$new_config" -eq 1 ] && echo '(new — review it)' )"
echo "  capture dir:      ${CAPTURE_DIR:-/captures} (fs: ${fstype:-?})"
echo "  capture NIC:      ${CAPTURE_IF:-?}"
if [ -n "${CAPTURE_IF:-}" ] && ip link show "$CAPTURE_IF" >/dev/null 2>&1; then
    if ip link show "$CAPTURE_IF" | grep -q PROMISC; then
        echo "  promisc:          on"
    else
        echo "  promisc:          OFF (capture-start will try to set it)"
    fi
fi
if [ "$NO_START" -eq 0 ] && [ "$can_start" -eq 1 ] && [ "$DRY_RUN" -eq 0 ]; then
    echo "  capture.service:  $(systemctl is-active capture.service 2>/dev/null || echo unknown)"
    echo "  watchdog timer:   $(systemctl is-active capture-watchdog.timer 2>/dev/null || echo unknown)"
else
    echo "  capture.service:  not started ($([ "$NO_START" -eq 1 ] && echo --no-start || echo "$reason"))"
fi
echo
echo "Next steps:"
echo "  - Verify with: capture-healthcheck"
echo "  - Tail logs:   journalctl -u capture.service -f"
echo "  - Edit config: \$EDITOR /etc/capture/capture.conf && systemctl restart capture.service"
