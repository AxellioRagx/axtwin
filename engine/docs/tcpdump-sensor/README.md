# tcpdump-sensor — Spec

Companion to `docs/ARCHITECTURE.md` §5. This document is the **implementation spec** for the v1 default capture appliance, the **tcpdump-sensor**. Claude Code: read this in full before writing any tcpdump-sensor code.

---

## 1. What the tcpdump-sensor IS and IS NOT

### Is

- A single host (VM or bare metal) that receives mirrored frames from one or more scenario zones via GRE tunnels (or directly, if developed/tested without GRE).
- Runs `tcpdump` on the decapsulated capture interface in promiscuous mode.
- Writes rotating, compressed pcap files to a local directory tree organized by date and tunnel source.
- Manages its own disk via a watchdog that prunes oldest files when usage crosses a soft threshold.
- Exposes a health-check command for external monitoring.

### Is NOT

- Aware of scenarios, runs, hosts, or phases (intentional — see CHANGELOG 2026-05-21 "Defer scenario-state correlation").
- A database. A queue. A message broker.
- Something that ships pcaps elsewhere automatically. (Operator pulls/syncs them on their own.)
- A multi-process pipeline. It is `tcpdump` + a watchdog. That's it.

---

## 2. Interface contract (what the rest of the platform sees)

The tcpdump-sensor's external contract is intentionally minimal:

| Surface | Detail |
|---|---|
| **Capture NIC** | `eth1` (configurable), promiscuous, no IP needed for capture role. |
| **Mgmt NIC** | `eth0`, has an IP, SSH + metrics scraping only. |
| **Config** | `/etc/capture/capture.conf` — KEY=value pairs, sourced by systemd unit. |
| **Output** | `/captures/` directory tree of compressed pcaps. |
| **Healthcheck** | `/usr/local/bin/capture-healthcheck` — exits 0 if healthy, non-zero with stderr explanation otherwise. |
| **Logs** | `journalctl -u capture.service` and `journalctl -u capture-watchdog.service`. |

Anything else the tcpdump-sensor does is an internal implementation detail, replaceable without breaking the contract.

### GRE transport — use a flow-based gretap (multi-tunnel)

**Required for the keyed-tunnel capture model (2026-06-01 / generalized 2026-06-03; carried into Option 1, 2026-06-04).**

The adapter stamps a distinct **GRE key per (bridge, sensor)** on every mirror tunnel, because two unkeyed (`key 0`) GRE tunnels to the same sensor IP collide at the datapath and only one egresses. For the tcpdump-sensor to decapsulate **any** key into one merged stream, its capture interface must be a **flow-based** gretap, created externally (not a fixed-`remote`/fixed-`key` gretap):

Use a **distinct device name** (e.g. `gre-cap`), **not** `gretap0` or
`gre0` — the `ip_gre` kernel module auto-creates fallback devices named exactly
`gre0` and `gretap0` when it loads, so `ip link add gretap0 …` fails with
`RTNETLINK answers: File exists`, and that pre-existing `gretap0` is **not**
flow-based (it won't decap keyed traffic). Live-verified on dev45, 2026-06-08.

```bash
ip link add gre-cap type gretap external       # flow-based: decaps any key/remote
ip link set gre-cap up
ip link set gre-cap promisc on
# then CAPTURE_IF=gre-cap in capture.conf
```

A fixed-`remote` (or `key 0`) gretap silently drops keyed traffic — capturing nothing from a keyed tunnel while still looking "up." This is the fix the 2026-06-03 live-verify flagged: migrate `CAPTURE_IF` from a fixed-`remote` gretap to `type gretap external` before any keyed capture works. Under Option 1 the *sender* is a `tc`-fed gretap netdev rather than an OVS GRE port, but the keying + flow-based-decap contract on the tcpdump-sensor side is identical.

(The component-side `o2/tcpdump-sensor/README.md` carries this same requirement — keep both consistent.)

---

## 3. Files Claude Code should produce

Suggested layout under `o2/tcpdump-sensor/` at the repo root:

```
o2/tcpdump-sensor/
├── README.md                          # User-facing install + ops runbook
├── install.sh                         # Idempotent installer (root)
├── uninstall.sh                       # Symmetric remover
├── capture.conf.example               # Annotated config; copied to /etc/ on install
├── systemd/
│   ├── capture.service                # The tcpdump unit
│   ├── capture-watchdog.service       # Oneshot prune unit
│   └── capture-watchdog.timer         # Runs prune every 60s
├── bin/
│   ├── capture-start                  # Helper: builds tcpdump args from capture.conf
│   ├── capture-watchdog               # The prune script
│   └── capture-healthcheck            # The health probe
├── udev/
│   └── 99-capture-promisc.rules       # Sets PROMISC on the capture NIC at boot
└── test/
    ├── smoke.sh                       # End-to-end self-test on a single host
    └── README.md                      # How to run the smoke test
```

This is a suggested shape, not a contract — propose adjustments to the user if better.

---

## 4. The `tcpdump` command — exact form

```bash
tcpdump \
    -i "${CAPTURE_IF}" \
    -p \
    -n \
    --immediate-mode \
    -B "${KERNEL_RING_KB}" \
    -s "${SNAPLEN}" \
    -G "${ROTATE_SECONDS}" \
    -C "${ROTATE_MEGABYTES}" \
    -W 999999999 \   # historically -W 0; tcpdump 4.99+ rejects 0; see below
    -z "${COMPRESS_CMD}" \
    -Z "${CAPTURE_USER}" \
    -w "${CAPTURE_DIR}/%Y/%m/%d/${SOURCE_TAG}/cap-%Y%m%d-%H%M%S.pcap" \
    ${EXCLUDE_BPF}
```

**The `-W <unlimited>` rule is mandatory.** A finite `-W` activates a numeric-counter filename mode that silently disables strftime in the filename pattern and overwrites pcaps in a numeric-counter ring. The strftime template is what gives us dated directories; we MUST keep it. Originally the spec ratified `-W 0` for "unlimited"; tcpdump 4.99+ (Debian 12 default) rejects `-W 0` as `invalid number of output files 0`, so the slice MVP runbook ships `-W 999999999` — a value high enough to be effectively unlimited while staying in the strftime-preserving regime. Documented in `docs/ARCHITECTURE.md` §5. Surfaced during real-cluster capture-appliance bring-up 2026-05-29; see CHANGELOG.

**Note on the `-w` path:** the `%Y/%m/%d` and `%Y%m%d-%H%M%S` tokens are expanded by tcpdump's own strftime at each rotation, **not** by the shell at start-up. This means every rotation that crosses midnight UTC creates a new dated directory automatically.

**The dated subdirectory must exist before tcpdump opens its first output file.** `capture-start` should `mkdir -p` today's AND tomorrow's dated paths at exec time (since tcpdump cannot create them on its own). The watchdog (which runs every 60s) refreshes both defensively each cycle, so a continuously-running tcpdump-sensor will always have tomorrow's dir in place well before midnight rollover.

See CHANGELOG entry 2026-05-21 (strftime in pcap output path; pre-create today + tomorrow dirs).

---

## 5. The watchdog — exact behavior

```bash
#!/usr/bin/env bash
set -euo pipefail

# Sourced from /etc/capture/capture.conf:
#   CAPTURE_DIR, SOFT_THRESHOLD_PCT, HARD_THRESHOLD_PCT,
#   TARGET_AFTER_PRUNE_PCT, MIN_KEEP_FILES, HARD_ACTION

source /etc/capture/capture.conf

usage=$(df --output=pcent "$CAPTURE_DIR" | tail -1 | tr -dc 0-9)

# Defensive: ensure today's directory exists for tcpdump rotations crossing midnight.
mkdir -p "$CAPTURE_DIR/$(date -u +%Y/%m/%d)"

if [ "$usage" -ge "$HARD_THRESHOLD_PCT" ]; then
    logger -t capture-watchdog "CRITICAL: disk ${usage}% >= hard threshold ${HARD_THRESHOLD_PCT}%"
    case "$HARD_ACTION" in
        stop_capture|both)
            systemctl stop capture.service
            logger -t capture-watchdog "stopped capture.service"
            ;;
    esac
    # Continue pruning regardless of HARD_ACTION
elif [ "$usage" -lt "$SOFT_THRESHOLD_PCT" ]; then
    exit 0
fi

# Build oldest-first list, skip the newest MIN_KEEP files.
mapfile -t victims < <(
    find "$CAPTURE_DIR" -type f \( -name 'cap-*.pcap' -o -name 'cap-*.pcap.zst' -o -name 'cap-*.pcap.gz' \) \
        -printf '%T@ %p\n' \
        | sort -n \
        | head -n -"$MIN_KEEP_FILES" \
        | awk '{ for (i=2; i<=NF; i++) printf "%s%s", $i, (i<NF?" ":"\n") }'
)

for f in "${victims[@]}"; do
    rm -f -- "$f"
    usage=$(df --output=pcent "$CAPTURE_DIR" | tail -1 | tr -dc 0-9)
    if [ "$usage" -le "$TARGET_AFTER_PRUNE_PCT" ]; then
        break
    fi
done

logger -t capture-watchdog "pruned to ${usage}% (target ${TARGET_AFTER_PRUNE_PCT}%)"
```

Notes:

- The `awk` snippet handles paths with spaces. Most pcap paths won't have them, but the watchdog must not break if they ever do.
- `MIN_KEEP_FILES` is the safety net that prevents deleting the file tcpdump is currently writing to. Default 10.
- The watchdog never starts capture back up after stopping it on hard threshold — that's an operator decision. The healthcheck will report this state.

---

## 6. The health probe — exact behavior

```bash
#!/usr/bin/env bash
# /usr/local/bin/capture-healthcheck
# Exits 0 if all checks pass, non-zero (and prints why) otherwise.
set -uo pipefail

source /etc/capture/capture.conf

problems=()

# 1. tcpdump process alive
if ! systemctl is-active --quiet capture.service; then
    problems+=("capture.service is not active")
fi

# 2. Newest pcap mtime is fresh
newest=$(find "$CAPTURE_DIR" -type f -name 'cap-*.pcap*' -printf '%T@ %p\n' \
         | sort -n | tail -1 | awk '{print $2}')
if [ -z "$newest" ]; then
    problems+=("no pcap files found under $CAPTURE_DIR")
else
    age=$(( $(date +%s) - $(stat -c %Y "$newest") ))
    if [ "$age" -gt 60 ]; then
        problems+=("newest pcap age=${age}s exceeds 60s")
    fi
fi

# 3. NIC promisc still on
if ! ip link show "$CAPTURE_IF" 2>/dev/null | grep -q PROMISC; then
    problems+=("$CAPTURE_IF is not in PROMISC mode")
fi

# 4. NIC RX drops counter delta
prev_drops_file=/var/lib/capture/last-rx-drops
curr_drops=$(awk -v iface="$CAPTURE_IF" '$1 ~ iface":" {gsub(":",""); print $5}' /proc/net/dev)
prev_drops=$(cat "$prev_drops_file" 2>/dev/null || echo 0)
delta=$((curr_drops - prev_drops))
echo "$curr_drops" > "$prev_drops_file"
if [ "$delta" -gt 0 ]; then
    problems+=("NIC RX drops +${delta} since last check")
fi

# 5. Disk usage
pct=$(df --output=pcent "$CAPTURE_DIR" | tail -1 | tr -dc 0-9)
if [ "$pct" -ge "$HARD_THRESHOLD_PCT" ]; then
    problems+=("disk usage ${pct}% >= hard threshold ${HARD_THRESHOLD_PCT}%")
fi

# 6. NIC offloads should be OFF
for off in gro lro tso gso; do
    state=$(ethtool -k "$CAPTURE_IF" 2>/dev/null | awk -v o="$off:" '$1 == o {print $2}')
    if [ "$state" = "on" ]; then
        problems+=("$off offload is ON (should be off for capture)")
    fi
done

if [ ${#problems[@]} -eq 0 ]; then
    echo "OK: tcpdump active, newest pcap age=${age}s, NIC RX drops 0, disk ${pct}%"
    exit 0
else
    printf 'PROBLEM: %s\n' "${problems[@]}" >&2
    exit 1
fi
```

External monitoring runs this on a cron/systemd timer or scrapes its output via SSH.

---

## 7. Config file — annotated example

```bash
# /etc/capture/capture.conf
# Sourced by capture-start, capture-watchdog, capture-healthcheck.
# All values are required unless marked OPTIONAL.

# --- Capture interface ---
CAPTURE_IF=eth1
SOURCE_TAG=zone-default                # Subdir under /captures/YYYY/MM/DD/
EXCLUDE_BPF=""                         # OPTIONAL; e.g. "not host 10.0.0.10"

# --- Rotation ---
ROTATE_SECONDS=300                     # tcpdump -G
ROTATE_MEGABYTES=1024                  # tcpdump -C
COMPRESS_CMD="zstd -1"                 # tcpdump -z; "gzip -1" also fine

# --- Buffering ---
KERNEL_RING_KB=524288                  # tcpdump -B (524288 = 512 MB)
SNAPLEN=0                              # 0 = full packet; 96 = headers-only

# --- User to drop to ---
CAPTURE_USER=capture                   # Created by install.sh

# --- Storage ---
CAPTURE_DIR=/captures

# --- Retention thresholds ---
SOFT_THRESHOLD_PCT=80
HARD_THRESHOLD_PCT=92
TARGET_AFTER_PRUNE_PCT=70
MIN_KEEP_FILES=10
HARD_ACTION=alert                      # alert | stop_capture | both
```

---

## 8. Smoke test — what `o2/tcpdump-sensor/test/smoke.sh` must do

End-to-end self-test, no external dependencies. Runs on a single host with `tcpdump`, `iproute2`, and root.

1. **Setup:** create a veth pair (`veth-a` and `veth-b`); make `veth-a` the capture interface.
2. **Start the tcpdump-sensor** against `veth-a`, with `CAPTURE_DIR=/tmp/smoke-captures`, a 30-second rotation, very low size threshold.
3. **Generate traffic:** push synthetic frames into `veth-b` (e.g., `tcpreplay` of a known small pcap, or `ping` + `nc`).
4. **Assert:** at least one pcap file appears under `/tmp/smoke-captures/<date>/<tag>/`.
5. **Assert:** pcap size is non-zero and openable by `tcpdump -r`.
6. **Force prune:** fill `/tmp/smoke-captures` past SOFT threshold (write filler junk in another file); run the watchdog manually; assert oldest pcaps deleted, newest MIN_KEEP retained.
7. **Healthcheck:** run `capture-healthcheck`; assert exit 0.
8. **Teardown:** stop the tcpdump-sensor, delete the veth pair, clean `/tmp/smoke-captures`.

If any assertion fails, exit non-zero with a clear message.

---

## 9. Install script outline

`o2/tcpdump-sensor/install.sh` (idempotent, run as root):

1. Verify deps: `tcpdump`, `zstd` (or `gzip`), `ethtool`, `iproute2`, `systemd`. Refuse and report if anything missing.
2. Create `capture` user if missing.
3. Create `/captures/` if missing; chown to `capture:capture`; chmod 750.
4. Create `/var/lib/capture/`; chown to root, chmod 755.
5. Create `/etc/capture/`; install `capture.conf` from `capture.conf.example` only if not present (don't overwrite operator changes).
6. Install systemd units to `/etc/systemd/system/`. Reload daemon.
7. Install udev rule to set PROMISC on `CAPTURE_IF` at boot.
8. Trigger udev for the current state so PROMISC takes effect now.
9. Apply ethtool offload-off settings to `CAPTURE_IF`.
10. Install scripts to `/usr/local/bin/`. Chmod 755.
11. Enable + start `capture.service` and `capture-watchdog.timer` only if `capture.conf` looks complete (sanity check: NIC exists, CAPTURE_DIR exists).
12. Print a summary: services running, NIC state, capture dir path.

`uninstall.sh` does the inverse, preserving `/captures/` contents by default (with a `--purge` flag to delete them).

---

## 10. Hard rules (don't break these)

- The capture process never runs as root once started. `-Z capture` is mandatory.
- `-W <unlimited>` is mandatory (see §4). v1 ships `-W 999999999`; historical spec said `-W 0` but newer tcpdump rejects 0.
- Promiscuous mode is set via udev rule, NOT by tcpdump's default toggling — that's why we pass `-p`. If the NIC is not in PROMISC mode at start, capture will silently miss broadcast/multicast frames addressed to other MACs.
- NIC offloads (GRO/LRO/TSO/GSO) must be OFF on the capture NIC. Pcaps with offloads enabled lie about wire reality.
- The capture interface has NO IP address. If it has an IP, scenario traffic can route back to the appliance, which is bad in several ways.
- Filesystem on `/captures/` must be XFS or ext4 with `noatime,nodiratime`. **Never btrfs/zfs** — CoW is a poor fit for sustained sequential writes.

---

## 11. Things the spec deliberately leaves to Claude Code's judgment

- Exact shell-script structure (Bash vs POSIX sh; how much error-handling boilerplate).
- Whether to ship a Debian `.deb`, a Makefile-based install, or just shell scripts.
- Specific systemd unit hardening directives (`PrivateTmp=`, `ProtectSystem=`, etc.) — apply sensible defaults.
- Whether to add Prometheus textfile exporter output alongside the healthcheck (nice-to-have).
- Log output format (text vs structured JSON via journald fields).

For any of these where you make a non-obvious choice, add a CHANGELOG entry with `**Authority:** implementation (Claude Code)`.

---

## 12. Original bring-up questions (historical)

(These were the tcpdump-sensor's bring-up questions; the tcpdump-sensor is now live-verified on two clusters, so they're kept for context only. The `CLAUDE.md` gate they mirrored was retired 2026-07-07 — see `CLAUDE.md` "Starting Work".)

1. **Deployment target:** bare metal, Proxmox VM, or both? Affects whether install.sh has to handle differing NIC naming, whether we ship a cloud-init template, etc.
2. **Target OS:** Debian stable (recommended; matches Proxmox host base) or Ubuntu LTS?
3. **Test infrastructure available:** is there a Proxmox cluster Claude Code can deploy this on, or only a local dev VM? Smoke test should work on either; integration test against a real GRE source needs the cluster.
4. **Expected mirror traffic rate:** rough order of magnitude. Default-tier tcpdump is ~1–2 Gbps tuned; if you're aiming higher, the default tcpdump-sensor isn't enough and `netsniff-ng` should be the v1 implementation instead.
