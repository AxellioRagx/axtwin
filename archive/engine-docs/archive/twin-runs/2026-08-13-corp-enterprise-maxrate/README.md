# corp-enterprise — max-capture-rate run (dev45, 2026-08-13)

Snapshot of the **exact config deployed for every tier** during the
max-capture-rate measurement, plus the two lab facts that are gitignored at
their source and would otherwise not travel with this repo.

This folder is a **record, not a live catalog entry**. `corp-enterprise.yaml.snapshot`
is deliberately *not* named `.yaml`: twin-config discovery is recursive, and a
second file declaring `twin_config.id: corp-enterprise` made the id appear
twice in `axrange twin-config list`. Renaming it keeps the catalog clean. To
deploy from here, use `EXPANDED-IR.deployed.yaml` (below) or the real catalog
entry at `twin-configs/corp-enterprise/`.

---

## Result

| Rung | Senders | Offered | At `gre-cap` | pps | Window | Verdict |
|---|---|---|---|---|---|---|
| R1 `loadgen-l1` | 3 | 14.3 Mbit/s | 14.1 Mbit/s | 1,233 | 25 s | clean |
| R2 `loadgen-l2` | 3 | 22.5 Mbit/s | 22.5 Mbit/s | 1,962 | 25 s | clean |
| R3 `loadgen-l3` | 3 | 628 Mbit/s | 622 Mbit/s | 53,902 | 25 s | clean |
| **R4 `loadgen-l4`** | **3** | **1,521 Mbit/s** | **1,521 Mbit/s** | **131,868** | **240 s** | **sink path clean** |
| R4b `+corp` | 4 | 2,369 Mbit/s | 2,367 Mbit/s | 205,349 | 120 s | 5,965 sink drops (0.024%) |
| R5 `+corp+srv` | 5 | 2,665 Mbit/s | 2,660 Mbit/s | 231,041 | 90 s | 31,733 sink drops (0.15%) |

**Headline: ~1.5 Gbit/s / ~132 kpps sustained with a clean sink path.** Over a
240 s window (~31.7 M packets) the tc mirror dropped 0, and the sink's vhost,
NIC and decap stages were all clean. 10 packets (0.00003%) were dropped at the
host's gretap tx.

Those gretap drops are **not rate-correlated** — 10 at 1.5 Gbit/s over 240 s vs
2 at 2.6 Gbit/s over 90 s. That argues against congestion and for a per-frame
property; the most likely cause is occasional oversized/GSO frames from the
twin's background traffic exceeding the tunnel MTU (gretap MTU 1500, decap
device 1462). Unconfirmed — worth a look if bit-exact capture matters.

## What actually limits it

Two separate ceilings, found in this order:

1. **The gretap inherited `fq_codel`** — the host's default qdisc, an AQM that
   *deliberately drops* to hold latency down. On a capture path, where the
   whole point is that every frame survives, that is a silent loss source.
   Replacing it with a deep FIFO was worth roughly a **60% increase** in
   lossless throughput (R4b went from 9,349 drops to zero at ~2.38 Gbit/s):

   ```
   ip link set dev <tapg_*> txqueuelen 20000
   tc qdisc replace dev <tapg_*> root pfifo limit 20000
   ```

   **FIXED IN THE ADAPTER 2026-08-14** (CHANGELOG; Authority: user). During
   this run it was applied by hand and did not survive a `tap deploy`.
   `attach-gre-tunnel` now asserts `pfifo limit N` + `txqueuelen N` itself, in
   its *idempotent* section, so an adopted pre-existing gretap heals too.
   Tunable via `MIRROR_GRETAP_TXQUEUELEN` (default 20000; `0` opts out).
   Live-verified: the gretap was reset to `fq_codel`/`qlen 1000` and a plain
   `tap deploy` restored it with no manual step. **The manual re-assert in the
   "Reproducing" section below is no longer needed.**

2. **The sink NIC was single-queue — FIXED 2026-08-13 (Authority: user).**
   `XPress-SW-A`'s `net1` had no `queues=`, so a 32-core box ran every mirrored
   frame through one virtio rx ring; above ~2.4 Gbit/s that ring was the wall.
   `qm set 104 -net1 virtio=BC:24:11:70:84:02,bridge=o2v7dc98,queues=8`
   **hot-applied with no reboot**: ens19 kept 10.255.251.21, the NM profile
   re-attached itself, `ethtool -l ens19` went to `Combined: 8`, and the host
   tap gained 8 rx + 8 tx queues. Capture (`pxswcapture`, `gre-cap`) never
   stopped. Restore with the same command minus `,queues=8`.

   Why the replug was safe to attempt: the profile is pinned to
   `connection.interface-name: ens19` with `autoconnect: yes` and a manual
   address, and the MAC was held identical so the PCI slot — and therefore the
   interface name — did not move. That is LES-092's trap already defused; on a
   NetworkManager guest with a DHCP-mode profile this would instead have come
   back IP-less on an isolated net.

   **Result: the sink stopped being the bottleneck at any rate this twin can
   produce.** At the same ~2.6 Gbit/s that previously lost 31,733 packets, the
   sink is now completely clean.

Traffic generation was the limit up to R4: the scripted driver's rendered
client is a **single-threaded loop**, so one persona instance pegs exactly one
core (measured: 94.1% CPU). Offered load scales by adding **sending hosts**,
never by adding emits or raising `volume:` on one host.

## Deployed state (left standing)

```
te   corp-enterprise-890c29b2                 completed   plan_id corp-enterprise-4fc62c80
tap  tap-corp-enterprise-890c29b2-c9cab1c4    completed   tap-maxrate-wide.yaml  -> XPress-SW-A
tfc  tfc-corp-enterprise-890c29b2-b423b5cc    completed   tfc-maxrate-l4.yaml    (3 senders)
```

The **wide** tap (dmz + corp-lan + server-vlan, 15 ports) is left applied even
though rung 4 only generates on dmz: extra tapped segments cost nothing when
idle and mean the twin's real AD background traffic is captured too.

## Files

| File | Tier | What |
|---|---|---|
| `EXPANDED-IR.deployed.yaml` | te | **The artifact actually deployed** — the twin config expanded with `N_WORKSTATIONS=6`. Verified to reproduce `plan_id corp-enterprise-4fc62c80`. |
| `corp-enterprise.yaml.snapshot` | te | Source twin config, byte-identical to the shipped catalog entry. |
| `tap-maxrate-dmz.yaml` | tap | dmz only (4 ports) — pair with rungs 1–4. |
| `tap-maxrate-wide.yaml` | tap | dmz + corp-lan + server-vlan (15 ports) — pair with 4b/5. **Currently applied.** |
| `roles-loadgen.yaml` | tfc | Puts the 3 DMZ Linux hosts in one shared `loadgen` role. |
| `tfc-maxrate-l1..l5.yaml` | tfc | The ramp. `l4` is the max-lossless rung and is **currently applied**. |
| `personas/loadgen-*.yaml` | engine | Copies of the catalog entries. **These live in `axrange/personas/catalog/`** — the originals must be present there for any of these profiles to resolve. |
| `lab/binding-*.yaml` | lab | The `deploy-dev45` image-binding substitutions (see below). |
| `lab/adapter.conf.EXTERNAL_SENSORS.snippet` | lab | The `XPress-SW-A` declaration. |
| `measure.py` | — | The 6-stage drop-accounting harness. |

## The two lab facts that do not travel

Both live in gitignored paths, so a fresh clone silently lacks them:

1. **Image bindings.** corp-enterprise references `ubuntu-server-22@v5`
   (template 9201) and `win11-finance@v4` (template 9104). **Neither template
   nor the `local-zfs` storage they name exists on dev45** — 28 of 32 nodes
   could not clone. `lab/binding-*.yaml` repoint them at templates that do
   exist: `ubuntu-server-22@v5 → 620` (Debian 12) and `win11-finance@v4 → 101`
   (Server 2022, already q35/OVMF/TPM-2.0 and `ostype: win11`). Copy them back
   to `deploy-dev45/images/bindings/proxmox/`. Originals are backed up in
   `deploy-dev45/.binding-backup-20260813/`.

2. **`EXTERNAL_SENSORS`.** `XPress-SW-A` must be declared in `adapter.conf` or
   the tap plans fail *at deploy*, not at validate — `tap validate` reports
   `static OK` for an unknown sensor id (GAP-052). Its transport IP was read
   live via QGA; do not copy the value from `adapter.conf.example`, which is
   wrong for the real boxes.

## Reproducing

```bash
# 0. restore the two lab facts above, then:
axrange te  validate --config corp-enterprise --param N_WORKSTATIONS=6 --images deploy-dev45
axrange te  deploy   --config corp-enterprise --param N_WORKSTATIONS=6 --images deploy-dev45
axrange tap deploy   twin-configs/corp-enterprise/tap-maxrate-wide.yaml
axrange tfc deploy   twin-configs/corp-enterprise/tfc-maxrate-l4.yaml

# (no manual qdisc step: attach-gre-tunnel asserts it since 2026-08-14)

python3 measure.py --seconds 240 --vmids <mesh vmids> --label "R4"
```

`--images deploy-dev45` is **required** with `--config`: the expander
materialises to a tempdir, so the default "images live beside the scenario"
resolution finds nothing.

## Gotchas worth keeping

- **Measure for long enough.** Marginal rates look clean in short windows. R4b
  passed two 25 s windows and a 90 s window, then lost 5,965 packets over
  120 s. Any "no drops" claim needs a window long enough to catch bursts.
- **`peer_scope: one` could resolve to the sender itself — FIXED 2026-08-14**
  (CHANGELOG; Authority: user). With a mesh role, the first cut of these
  personas had every host sending to its own IP — real traffic, `fails=0`, a
  healthy-looking log, and **zero frames on the wire**, because loopback never
  reaches a tap. `peer_scope: all` already excluded self (LES-170); the
  single-peer path did not. `_resolve_peers` now takes `exclude_host_id` and
  filters before the empty check, so "the role's only holder is me" skips with
  a warning instead of self-peering, while a genuinely absent role still fails
  loud. This also un-defeated the shipped `domain-controller` persona, whose
  DC-to-DC replication emit was self-peering on every single-DC twin.
- **A stale apt cache fails every `pkg:` install on a fresh clone — FIXED
  2026-08-14** (CHANGELOG; Authority: user). During this run nothing in
  `axrange/` set Ansible's `update_cache`, so installs ran against whatever the
  template baked. Four hosts failed; an in-guest `apt-get update` (25.7 MB)
  fixed all of them, and mail-server — refreshed pre-emptively — passed the
  same op in the same run. `render_playbook` now opens the apt play with a
  non-fatal refresh, live-verified against a deliberately emptied index.
- **First SSH to XPress-SW-A can stall >90 s.** Use a `ControlMaster` socket;
  multiplexed commands are instant.
- **`intensity:` on a tfc assignment is inert** — parsed and validated, never
  reaches the driver. Volume lives only in the persona catalog.

---

## Report

Both reports live in **`docs/reports/`** (see that folder's README):
`2026-08-13-capture-rate-corp-enterprise.html` is the original run,
`2026-08-14-capture-rate-post-fixes.html` the re-measurement after the three
capture-plane fixes landed (lossless 1.5 -> 2.6 Gbit/s).

This folder keeps the machinery that produced them, since the scratchpad it was
built in is ephemeral: `report/genplot.py` (the 08-13 report, from a hand-typed
`RUNGS` table), `report/genplot2.py` (the 08-14 report, generated from measured
JSON rather than transcribed numbers), `report/after.jsonl` (that measured
data), `report/ramp.sh` (the driver that walked all seven rungs on identical
120s windows) and `report/capture-rate.html` (a copy of the 08-13 page).

Chart palette is the validated dataviz default (blue `#2a78d6` / orange
`#eb6834`), checked in both light and dark. Worth recording why it is two hues
and not three: the first attempt used orange + red for the two loss stages and
**failed the validator** at normal-vision ΔE 7.1 against a floor of 15 — full-
colour readers could not separate them. The better fix was not a third hue but
fewer: all of the loss at R4b/R5 is at one stage (the sink), which is a sentence,
not a second series.


---

## Phase 2 — after multiqueue (2026-08-13)

| Rung | Senders | Offered | Captured | pps | tc overlimits | gretap | sink | Verdict |
|---|---|---|---|---|---|---|---|---|
| **R4** | 3 | 1,539 Mbit/s | 1,542 Mbit/s | 133,629 | **0** | 6 | **0** | zero-drop |
| R5 | 5 | 2,583 Mbit/s | 2,586 Mbit/s | 224,158 | 1,605 | 10 | **0** | 0.005% |
| R7 | 12 | 2,748 Mbit/s | 2,714 Mbit/s | 235,250 | 325,446 | 3 | **0** | ~1.4% |

**The sink is clean at every rung, at every rate the twin can reach.** Before
multiqueue the same 2.6 Gbit/s cost 31,733 sink drops (0.152%); after, zero.

The bottleneck moved rather than vanished: **all 17 tapped ports mirror into one
single-queue gretap netdev**, and `tc mirred` starts logging `overlimits` past
~2.6 Gbit/s. That is now the thing to fix if a higher ceiling is wanted —
per-sensor gretap fan-out, or a multiqueue-capable tunnel device.

**Generation is exhausted, so the true new ceiling is un-measured.** Rung 7 is
every host in the twin that can emit:

| Sender family | Hosts | Offered | Per host |
|---|---|---|---|
| Linux (`scripted`, Python) | 5 | ~2,449 Mbit/s | ~490 Mbit/s, one core pegged at 94% |
| Windows (`scripted`, PowerShell) | 6 | ~80 Mbit/s | ~13 Mbit/s |

The PowerShell emitter is ~40× weaker per host, so the Windows fleet is breadth,
not throughput. Offered load scales only by adding Linux hosts.

### A counter that lies

The harness differences tc's mirrored-packet count against the gretap's
transmitted count (`[2b]`). **Ignore it below ~1%.** The two are read in separate
SSH round-trips, so at 230k pps a fifth of a second of skew invents ~46,000
phantom missing frames — and at R4 it came out *negative*, which is impossible
and proves the point. `overlimits` is the countable signal. The harness now
labels `[2b]` advisory and only flags it past 1%.

### New files from this phase

| File | What |
|---|---|
| `personas/loadgen-win.yaml` | Windows fleet sender (`os_family: windows`, Scheduled Task per LES-120) |
| `personas/loadgen-mgmt.yaml` | The last Linux sender, monitoring-collector → gw on mgmt |
| `tap-maxrate-all.yaml` | All four segments (17 ports) → XPress-SW-A |
| `tfc-maxrate-l6.yaml` | 11 senders (adds the Windows fleet) |
| `tfc-maxrate-l7.yaml` | 12 senders — the twin's full generating capacity |
| `lab/xpress-sw-a.net1.after-multiqueue` | The `net1` line as it now stands (gitignored at source) |
