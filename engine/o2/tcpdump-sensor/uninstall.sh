#!/usr/bin/env bash
#
# uninstall.sh — remove the cyber-range capture appliance (the tcpdump-sensor)
# from this host.
#
# By default, preserves /captures/ contents and /etc/capture/capture.conf
# (the operator's tuning). Use --purge to remove those too.
#
# Usage:
#   sudo ./uninstall.sh             # remove units, scripts, udev rule; keep data + config
#   sudo ./uninstall.sh --purge     # additionally remove /captures and /etc/capture
#   sudo ./uninstall.sh --dry-run   # show what would happen

set -euo pipefail

PURGE=0
DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --purge)   PURGE=1 ;;
        --dry-run) DRY_RUN=1 ;;
        -h|--help)
            sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "uninstall.sh: unknown arg '$arg'" >&2; exit 2 ;;
    esac
done

say() { echo "uninstall.sh: $*"; }
run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "  + $*"
    else
        "$@" || true   # uninstall steps are best-effort
    fi
}

[ "$(id -u)" -eq 0 ] || { echo "must be run as root" >&2; exit 1; }

# Stop + disable units first so we don't lose pcaps mid-write.
say "stopping units"
run systemctl stop capture-watchdog.timer
run systemctl stop capture-watchdog.service
run systemctl stop capture.service
run systemctl disable capture-watchdog.timer
run systemctl disable capture.service

say "removing systemd units"
for u in capture.service capture-watchdog.service capture-watchdog.timer; do
    run rm -f "/etc/systemd/system/$u"
done
run systemctl daemon-reload

say "removing scripts"
for s in capture-start capture-watchdog capture-healthcheck; do
    run rm -f "/usr/local/bin/$s"
done

say "removing udev rule"
run rm -f /etc/udev/rules.d/99-capture-promisc.rules
run udevadm control --reload-rules

say "removing /usr/share/doc/capture"
run rm -rf /usr/share/doc/capture

if [ "$PURGE" -eq 1 ]; then
    say "purging /captures (--purge)"
    run rm -rf /captures
    say "purging /etc/capture (--purge)"
    run rm -rf /etc/capture
    say "purging /var/lib/capture (--purge)"
    run rm -rf /var/lib/capture
    say "removing 'capture' user (--purge)"
    if id -u capture >/dev/null 2>&1; then
        run userdel capture
    fi
else
    say "preserving /captures, /etc/capture, /var/lib/capture (use --purge to remove)"
    say "preserving 'capture' user (use --purge to remove)"
fi

echo
say "uninstall complete."
