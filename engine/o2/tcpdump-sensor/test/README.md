# tcpdump-sensor — Smoke Test

`smoke.sh` is the end-to-end self-test for the capture appliance (the tcpdump-sensor). It runs on a single host with no Proxmox / GRE dependency, using a veth pair to simulate the mirror feed.

## What it verifies

1. `capture-start` opens a NIC, drops privileges to `capture`, and writes rotating pcaps into `/tmp/smoke-captures/YYYY/MM/DD/smoke/`.
2. The first pcap is readable by `tcpdump -r`.
3. Pcap files are owned by the `capture` user (privilege drop succeeded).
4. `capture-watchdog` prunes oldest pcaps when disk usage crosses `SOFT_THRESHOLD_PCT`, while preserving `MIN_KEEP_FILES` newest.
5. `capture-healthcheck` exits 0 (`HEALTHCHECK_SKIP_SERVICE=1` because we run tcpdump directly, not via systemd).
6. `SIGTERM` cleanly stops the capture.

## Running

```bash
sudo o2/tcpdump-sensor/test/smoke.sh
```

The test takes ~45 seconds. On success it prints `ALL CHECKS PASSED` and exits 0.

### Useful env vars

| Var | Effect |
|---|---|
| `SMOKE_VERBOSE=1` | `set -x` for full trace |
| `SMOKE_KEEP=1`    | Leave `/tmp/smoke-*` in place after exit (for inspection) |

## Requirements

- Root.
- `tcpdump`, `ip`, `ethtool`, `awk`, `find`, `df`, `mountpoint`.
- One of `zstd` or `gzip` (the smoke test sets `COMPRESS_CMD=none`, but install.sh's dependency check requires one of these be present).
- A free pair of interface names: `cs-veth-a`, `cs-veth-b`.
- A free namespace name: `cs-smoke`.
- About 70 MB free under `/tmp` (the test mounts a 64 MB tmpfs).

The test is self-contained: it does **not** require `install.sh` to have been run, and does **not** touch `/etc/capture/` or `/etc/systemd/system/`. It uses the scripts directly from this repo's `o2/tcpdump-sensor/bin/` and a throwaway config at `/tmp/smoke-capture.conf` via the `CAPTURE_CONF` env var.

## What the test does NOT cover

These need a real environment:

- Cross-host GRE/ERSPAN tunneling (needs two hosts).
- 1 Gbps+ sustained throughput tuning (needs a real NIC and a real mirror source).
- Real Proxmox SDN/OVS integration (needs a cluster).
- udev rule firing on real NIC hotplug.
- Reboot survivability (needs a reboot).

When the Proxmox adapter exists (see `docs/OPEN_QUESTIONS.md` Q5), we'll add integration tests that exercise those paths.
