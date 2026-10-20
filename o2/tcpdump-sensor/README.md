# tcpdump-sensor — Install & Operate

Implementation of the cyber-range capture appliance. This is the **runbook**; the design spec lives at [`docs/tcpdump-sensor/README.md`](../docs/tcpdump-sensor/README.md) and the architecture at [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) §5.

The tcpdump-sensor is a black box: it takes a NIC, writes rotating compressed pcaps to a directory, and prunes the oldest when disk fills up. That's it. No database, no API, no scenario awareness.

---

## Quick install (Debian 12 / Ubuntu 22.04+)

```bash
sudo apt-get install -y tcpdump ethtool iproute2 zstd findutils gawk coreutils systemd udev
sudo ./install.sh           # from inside the o2/tcpdump-sensor/ directory
sudo $EDITOR /etc/capture/capture.conf   # set CAPTURE_IF and confirm CAPTURE_DIR
sudo ./install.sh           # re-run to apply config + start services
sudo capture-healthcheck
```

That's the happy path.

---

## What gets installed

| File | Purpose |
|---|---|
| `/etc/capture/capture.conf` | Operator config (KEY=value). Edits preserved across reinstalls. |
| `/etc/capture/capture.conf.example` | Reference copy, always up-to-date. |
| `/usr/local/bin/capture-start` | systemd ExecStart helper; sources config, runs tcpdump. |
| `/usr/local/bin/capture-watchdog` | Disk-pressure pruner. |
| `/usr/local/bin/capture-healthcheck` | Health probe — exit 0 if all checks pass. |
| `/etc/systemd/system/capture.service` | The tcpdump long-running unit. |
| `/etc/systemd/system/capture-watchdog.service` | Oneshot prune unit. |
| `/etc/systemd/system/capture-watchdog.timer` | Fires the watchdog every 60s. |
| `/etc/udev/rules.d/99-capture-promisc.rules` | Sets PROMISC on `$CAPTURE_IF` at boot. |
| `/captures/` | Output tree (`YYYY/MM/DD/<SOURCE_TAG>/cap-*.pcap.zst`). |
| `/var/lib/capture/` | Runtime state (last NIC RX-drops counter). |

A `capture` system user is also created. `tcpdump` drops to it after opening the NIC; all pcaps are owned by it.

---

## Configuration

Everything tunable lives in `/etc/capture/capture.conf`. The annotated example (also installed at `/etc/capture/capture.conf.example`) documents every knob. After editing:

```bash
sudo systemctl restart capture.service
sudo capture-healthcheck
```

The watchdog re-reads its config on the next tick (within 60s); no restart needed for thresholds.

### NIC requirements (hard rules)

- The capture NIC must have **no IP address**. `capture-start` refuses to launch if it does.
- The capture NIC must be in **PROMISC mode**. The installed udev rule sets this at boot; `capture-start` will try once more on launch and refuse to launch if PROMISC still isn't set.
- NIC offloads (gro/lro/tso/gso) must be **off**. `capture-start` reapplies this on every launch, but if you see otherwise in `capture-healthcheck`, run `ethtool -K $CAPTURE_IF gro off lro off tso off gso off`.

### GRE transport: use a flow-based gretap (multi-tunnel)

When `CAPTURE_IF` is the GRE mirror transport (the common case — scenario OVS bridges tunnel mirrored frames here over GRE), it must be a **flow-based** gretap, **not** a fixed-key one. The adapter stamps a **distinct GRE key per (bridge, target sensor)** (`gre_key_for_bridge_sensor`; per-tap sensor routing, 2026-06-03) so multiple tunnels arriving at this appliance coexist on the OVS sender side — many bridges can converge here, and one bridge can fan tunnels out to several sensors (two `key=0` tunnels to the same remote collide — see `docs/ARCHITECTURE.md` §4). A flow-based gretap decapsulates **any** key, so every tunnel routed to this appliance lands in one merged capture:

```bash
# Flow-based (collect_metadata) gretap — decaps GRE of ANY key from the cluster.
# Use a distinct name (gre-cap), NOT gretap0/gre0: the ip_gre module
# auto-creates non-flow-based fallback devices named gre0 + gretap0 on load, so
# `ip link add gretap0 …` fails with "File exists". (Live-verified dev45, 2026-06-08.)
sudo ip link add gre-cap type gretap external
sudo ip link set gre-cap up promisc on
# then set CAPTURE_IF=gre-cap in /etc/capture/capture.conf
```

A fixed-key gretap (`type gretap remote <node> local <appliance> key <K>`, or the keyless `key 0` default) only decaps that one key — fine for a single scenario, but it silently drops every *other* scenario's frames. There is no per-scenario *attribution* in v1 (capture is one time-ordered stream; "no scenario correlation"); per-key attribution is deferred (`OP-MIR-030 register-mirror-source`, reserved).

### Capture offloads and the transport MTU (measured 2026-10-01 on XPress-SW-A)

Two settings verified live on the `XPress-SW-A` sink (packetXpress `pxswcapture`, AF_XDP on `gre-cap`; the same physics applies to this appliance's `tcpdump` on `gre-cap`). Full record: [`docs/reference/capture-rate/2026-10-01-corp-twin-ladder.md`](../../docs/reference/capture-rate/2026-10-01-corp-twin-ladder.md).

1. **GRO off on BOTH the transport NIC and the decap device** — not only on `CAPTURE_IF`. `capture-start` turns offloads off on `gre-cap`, but the NIC *under* it (`eth1`/`ens19`) GROs the outer GRE packets, and the kernel's GRE GRO merges consecutive GRE-carried TCP segments into super-frames before decap. While the transport fragments full-size frames (MTU 1500, below) this is masked — IP fragments are never GRO'd — but the moment whole 1542/1546 B outer packets arrive, the capture shows **frames up to 3.7 KB** (wire frames never exceed 1514 B + 4 per tag) and pxswcapture overran its XSK queue (**24,227 `queue` drops in 25 s**). Kept on XPress-SW-A:

   ```bash
   sudo ethtool -K ens19 gro off rx-gro-hw off      # transport NIC
   sudo ethtool -K gre-cap gro off                  # decap device
   # persistence used there: NM profile  ethtool.feature-gro off, ethtool.feature-rx-gro-hw off
   # and a gre-cap.service drop-in:      ExecStartPost=/usr/sbin/ethtool -K gre-cap gro off
   ```
   Cost: none measurable — sensor CPU 10.7 % → 15.6 % going from 6.5 k to 218 k pps.

2. **Don't make the transport fragment.** On a 1500-MTU transport every full-size mirrored frame becomes two outer fragments (the gretaps carry `nopmtudisc ignore-df`, LES-356), the sensor reassembles, and smaller frames overtake the reassembled ones: **5–6 % of TCP segments landed in the capture out of wire order**, and the sensor took ~1.6 outer packets per captured frame. A 1600-byte path for the mirror (route MTU + the sensor's host-side tap leg; the guest NIC accepted 1546 B frames at MTU 1500) took reordering to **0** and outer packets to 1.0 per frame. corp-twin's scoped recipe (fwmark on its own gretaps only, so other twins' tunnels keep the 1500 path): `twins/corp-twin/scripts/mirror-mtu-path.sh`. Apply item 1 FIRST.

With both, the chain `tc mirred → 24 gretaps → o2v7dc98 → tap104i1 (8 queues) → ens19 (8 queues) → gre-cap → pxswcapture` carried **218 k pps / 1.96 Gbit/s sustained for 10 min (131 M packets, 254 k pps / 2.29 Gbit/s peak minute) with every drop counter at 0** and no fidelity damage (no holes, reordering, truncation or oversize frames in the stored pcaps). The limit of that run was the twin's own servers, not the capture path. Pushed further (probe P3), the first loss appeared at **~305 k pps / ~2.7 Gbit/s**: 7,141 pxswcapture `queue` drops in one 10 s interval (its single AF_XDP socket on the single-queue, generic-XDP `gre-cap` — one `afxdp_q0` thread), then 40 `tx_drop` on the sensor's vhost leg `tap104i1`. Beyond that needs a multi-queue capture path (several XSK queues / a native-XDP or multi-queue decap), not more cores.

### Standing up a second tcpdump-sensor (per-tap sensor routing verify)

Per-tap sensor routing (2026-06-03) lets a deploy route different taps to different sensors. Each tcpdump-sensor is an **independent instance of this component on its own host** — there is no multi-appliance mode to enable; you just install the appliance again on a second host with its own `vnet-transport` IP. This walkthrough predates two later refactors and is kept only for the appliance-side install steps (1-4 below), which are unaffected — the orchestrator-side wiring in steps 5-6 has moved twice since: sensor routing became per-tap-plan-binding, not IR-declared (2026-07-13, `docs/WORK_ORDER_sensor_agnostic_te.md` — `SENSOR_<ID>_TRANSPORT_IP`/`SENSOR_TRANSPORT_IP` adapter.conf keys no longer exist), and taps are now authored entirely in a standalone tap plan against a deployed twin's published topology, not in the scenario IR at all (2026-07-28, `docs/WORK_ORDER_tap_eligibility.md` — the `tests/fixtures/ir/valid_two_sensor/scenario.yaml` fixture this section named is retired). To verify per-tap sensor routing today: stand up two tcpdump-sensors under `axrange o2 up`, then bind one net to each via a tap-plan's `bindings:` (`docs/design/06-taps-sensors.md`; `tests/test_o2_taps.py`'s fan-out/exclusive-routing tests cover this mechanism directly):

1. **Host:** a second VM/box with a NIC on `vnet-transport`, its own IP (e.g. `10.255.255.20`), no IP on the capture iface.
2. **Flow-based gretap** (as above) — `gre-cap`. Both appliances use a flow-based gretap; the per-(bridge, sensor) keys keep their streams distinct on the wire and each appliance decaps only the tunnels pointed at it.
3. **`/etc/capture/capture.conf`** on appliance #2: `CAPTURE_IF=gre-cap`, `SOURCE_TAG=sensor2` (so its pcaps are distinguishable from appliance #1's `sensor1`), `CAPTURE_DIR=/captures`.
4. **Install + start:** `cd o2/tcpdump-sensor && sudo ./install.sh` on that host, then `sudo systemctl start capture.service`.
5. **Orchestrator `adapter.conf`:** add `SENSOR_S2_TRANSPORT_IP=10.255.255.20` (and point `SENSOR_S1_TRANSPORT_IP` / `SENSOR_TRANSPORT_IP` at appliance #1). The lowercased `<ID>` in `SENSOR_<ID>_TRANSPORT_IP` is the logical sensor id the IR's `sensors:` references.
6. Deploy the fixture, ping `h-a ↔ h-b`, and confirm **appliance #2 sees only h-a's frames, appliance #1 only h-b's** — no cross-contamination, both keys decapping.

### Filesystem requirements (hard rule)

`/captures/` must be on **XFS or ext4** mounted with `noatime,nodiratime`. **Never btrfs or zfs** — copy-on-write filesystems kill sustained sequential write performance. `install.sh` refuses to proceed if it detects btrfs or zfs at the target path.

A typical dedicated mount looks like:

```bash
# /etc/fstab
/dev/disk/by-label/captures /captures xfs noatime,nodiratime,nofail 0 2
```

---

## Day-to-day operation

### Check it's working

```bash
sudo capture-healthcheck
# OK: tcpdump active; newest pcap age=12s; disk 34%

sudo journalctl -u capture.service -n 50
sudo journalctl -u capture-watchdog.service -n 50
```

### Watch live

```bash
sudo journalctl -u capture.service -f
# look for: "N packets captured, M packets received by filter, P packets dropped by kernel"
# the "dropped by kernel" number is the one to watch.
```

### Inspect output

```bash
ls -lh /captures/$(date -u +%Y/%m/%d)/<source-tag>/
sudo -u capture tcpdump -nn -r /captures/.../cap-*.pcap.zst | head
# (zstd-compressed; tcpdump on Debian 12+ / Ubuntu 22.04+ reads .zst directly)
```

If your tcpdump build doesn't handle `.zst` natively:

```bash
zstdcat /captures/.../cap-XXX.pcap.zst | sudo -u capture tcpdump -nn -r -
```

### Pause / resume capture

```bash
sudo systemctl stop  capture.service       # pauses; pcaps stay on disk
sudo systemctl start capture.service       # resumes (new file with current timestamp)
```

The watchdog keeps running even when capture is paused — it prunes based on disk usage regardless.

### Force a prune

```bash
sudo systemctl start capture-watchdog.service
# runs once now; the .timer keeps firing every 60s as usual
```

### Reload config

```bash
sudo $EDITOR /etc/capture/capture.conf
sudo systemctl restart capture.service
```

The watchdog re-sources the config on its next tick; restart not needed for watchdog-only changes.

---

## Troubleshooting

### `capture-healthcheck` reports `capture.service is not active`

```bash
sudo systemctl status capture.service
sudo journalctl -u capture.service -n 100 --no-pager
```

Common causes:
- `CAPTURE_IF` has an IP. Remove it (`sudo ip addr flush dev <iface>`).
- `CAPTURE_IF` is wrong (typo, NIC is named differently — e.g. Predictable Names like `enp3s0f1`).
- `/captures/` is on btrfs or zfs.

### `capture-healthcheck` reports NIC RX drops

```bash
ethtool -S $CAPTURE_IF | grep -i drop
```

Apply the §5 tuning ladder from `docs/ARCHITECTURE.md`:

1. `sudo ethtool -G $CAPTURE_IF rx <max>`  (NIC ring size)
2. `sudo ethtool -K $CAPTURE_IF gro off lro off tso off gso off`  (re-check offloads)
3. Pin `tcpdump` to a CPU core distinct from NIC IRQ cores. Identify NIC IRQ cores via `/proc/interrupts | grep $CAPTURE_IF`.
4. Increase `KERNEL_RING_KB` in `capture.conf` (e.g. to 1048576 = 1 GB).
5. Increase the kernel socket buffers: `sudo sysctl -w net.core.rmem_max=536870912 net.core.rmem_default=268435456 net.core.netdev_max_backlog=300000`.

If you've gone through the ladder and still see drops above ~1–2 Gbps, you've hit the libpcap single-thread ceiling. Time to graduate to `netsniff-ng` or PF_RING (out of v1 scope).

### Disk filling faster than the watchdog can prune

This means your sustained capture rate exceeds your retention policy. Choose one:

- Increase storage.
- Reduce `SOFT_THRESHOLD_PCT` (start pruning sooner, keep less).
- Add a `EXCLUDE_BPF` to drop expensive flows you don't care about.
- Switch `HARD_ACTION` from `alert` to `stop_capture` so you stop ingesting when too far behind.

### Capture stopped at midnight UTC

Unlikely with the current scripts (`capture-start` and the watchdog both pre-create today's and tomorrow's dated directories), but if you see `tcpdump: ... permission denied` or `no such file` in the journal around midnight, check:

```bash
ls -ld /captures/$(date -u +%Y/%m/%d)
```

The directory should exist with mode 0750 owned by `capture:capture`. If missing, the watchdog (running every 60s) should create it within a minute; verify with `journalctl -u capture-watchdog.service -n 10`.

### `WARN: /captures mount options do not include 'noatime'`

You're paying for atime updates on every pcap write. Remount:

```bash
sudo mount -o remount,noatime,nodiratime /captures
# Make it permanent in /etc/fstab.
```

---

## Smoke test

```bash
sudo o2/tcpdump-sensor/test/smoke.sh
```

End-to-end self-test on a single host using a veth pair. Takes ~45s. See `o2/tcpdump-sensor/test/README.md` for details. Does not require `install.sh` to have been run.

---

## Uninstall

```bash
sudo ./uninstall.sh             # remove units, scripts, udev rule; KEEP /captures + config
sudo ./uninstall.sh --purge     # ALSO remove /captures, /etc/capture, /var/lib/capture, capture user
```

---

## Capacity reference

| Stack | Sustained | Storage @ sustained, 7-day retention |
|---|---|---|
| `tcpdump` defaults | ~300 Mbps | ~22 TB raw / ~10 TB zstd |
| `tcpdump` tuned (ladder above) | ~1–2 Gbps | ~75–150 TB raw / ~30–60 TB zstd |
| `netsniff-ng` TPACKET_V3 *(future)* | ~3–5 Gbps | ~225–375 TB raw |
| PF_RING ZC *(future)* | ~10 Gbps | ~750 TB raw |

Storage math: 1 Gbps sustained = ~125 MB/s = ~10 TB/day raw; zstd typically gets ~2× on network traffic.

---

## Architecture references

- Capture appliance design: [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) §5.
- Mirror plane / GRE / OVS: [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) §3–§4.
- tcpdump-sensor spec (implementation contract): [`docs/tcpdump-sensor/README.md`](../docs/tcpdump-sensor/README.md).
- Hard rules (don't break these): `docs/tcpdump-sensor/README.md` §10.
- Scenario correlation: explicitly **not** present — see CHANGELOG entry `2026-05-21 — Defer scenario-state correlation in capture pipeline`.

---

## What's NOT in this tcpdump-sensor (yet)

- **Scenario-aware tagging** of pcaps. Deferred. Pcaps are organized by date + `SOURCE_TAG` only; a future correlator can index them by reading network sensor metadata if/when needed.
- **Multi-appliance scaling / HA.** Deferred. One appliance, one host.
- **Cross-site replication** of `/captures/`. Out of scope; use whatever you already use for filesystem replication.
- **`netsniff-ng` / PF_RING / DPDK** capture backends. Out of v1 scope. The black-box contract means we can swap any of these in later without touching anything upstream.
- **TLS decryption / DPI.** Not in scope at any iteration of the tcpdump-sensor.

See `docs/OPEN_QUESTIONS.md` for the broader list of things still in design.
