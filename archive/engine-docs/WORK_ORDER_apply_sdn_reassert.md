# Work Order — `apply-sdn-config`'s post-apply tap re-assert scan doesn't scale (GAP-046)

> **STATUS: OPEN.** Authority: user (2026-07-28, to open it). Route: Pipeline (`/dev`) —
> code-writer → tester → code-reviewer → doc-writer → **gated** live-verify (a busy cluster is
> required to reproduce). **Not a version-locked change** — this is an `adapter_proxmox` implementation
> fix (`rest.py`) plus an optional `adapter.conf` timeout knob; no IR/OP_CATALOG/VALIDATOR/ADAPTER
> *schema* change, no op-count/rule-count change. **Closes `docs/PRODUCT_GAPS.md` GAP-046.**
> **Spec:** this file. Discovered by the tap-eligibility live-verify (CHANGELOG 2026-07-28
> "Live-verify (dev45)"); pre-existing, unrelated to that change.

## 1. The problem (GAP-046)

`ProxmoxRest.op_apply_sdn_config` (`axrange/adapter_proxmox/rest.py:555-660`) does the real SDN work
first (`PUT /cluster/sdn` + wait for the `reloadnetworkall` task + verify the vnet bridges
materialized — all of which succeed), then runs a **node-wide** SSH housekeeping scan (`rest.py:631-659`,
one `run_ssh(..., timeout_s=60)`) that re-asserts tap ports on the `te`-tier OVS bridges:

```sh
for br in $(ovs-vsctl list-br | grep '^tebr_'); do
  current_ports=$(ovs-vsctl list-ports "$br")
  for vmid in $(qm list | awk '$3=="running"{print $1}'); do
    cfg=$(qm config "$vmid")          # <-- a Perl process spawn, per (bridge × running-VM)
    ... if tap not already a port, ovs-vsctl add-port ...
  done
done
```

**It is O(bridges × running-VMs) and spawns a `qm config` (a heavy Perl process) inside the inner
loop** — the same VM's config is re-read once per bridge. On a busy node it blows the hardcoded 60s
SSH timeout: the tap-eligibility live-verify hit `ssh <node>: timeout after 60s`, 3 retries, twice,
with **41 running VMs** on dev45 (32 of them one standing twin) × 2 `tebr_*` bridges ≈ 82 `qm config`
spawns. The failure is **load-driven, not transient** — a retry can't out-wait it, and the load is
another (protected) twin the deploy is forbidden to touch. `error_class=adapter-timeout`.

Everything *else* in the op succeeds (the SDN apply itself, all downstream VM/NIC/router ops) — only
the ancillary scan times out. (It surfaced as a *deploy failure* only because of the separate
GAP-047 DAG-wiring gap; that is a different work order — see §5.)

## 2. The load-bearing safety constraint — DO NOT narrow the scan's blast radius

The scan is node-wide **on purpose**, and this is the landmine that makes the naive fix wrong:

- `PUT /cluster/sdn` triggers a `reloadnetworkall` / `ifreload` that briefly downs/ups bridges
  **across the whole node**, not just this deploy's.
- The `pve-bridge` hook that re-attaches a VM's tap only runs at **VM start** — so a running VM that
  is merely *adopted* by a re-deploy (already up, not restarted) can silently lose its tap attachment
  when the reload bounces its bridge.
- Therefore the re-assert must cover **every running VM on every `tebr_*` bridge**, including VMs
  belonging to *other* twins. **Scoping the scan to "this deploy's own bridges/VMs" would leave every
  other running twin's taps detached after the reload — a silent capture/networking data-loss bug**
  (the LES-118 family: a coarse-grained node-wide side effect must be repaired at node-wide
  granularity). The fix makes the scan **faster**, never **narrower**.

## 3. Resolve FIRST: is the re-assert still needed at all under SDN-native? (the retire path)

`rest.py:576-580` carries an explicit, still-open watch-item: *"confirm whether an SDN apply still
detaches running taps under SDN-native — if it does not, retire this re-assert; if it does, keep it."*
The re-assert was carried over from the 2026-05-29 pre-SDN-native bring-up. **If it never actually
re-attaches anything on a modern PVE 9.2 SDN-native node, the cleanest fix is to delete it entirely.**

- **Instrument + observe.** The scan already `echo`s `re-attached $tap to $br` for every port it
  re-adds. Add a deploy-time INFO log of how many re-attachments the scan actually performed, and on
  the gated live-verify (busy cluster, ≥1 other running twin, then a `PUT /cluster/sdn` from a new
  deploy) record whether that count is **ever non-zero**.
- **If it is always zero** across a representative busy-cluster run (SDN-native apply does not detach
  adopted taps): **retire the scan** — delete `rest.py:627-659` and its watch-item comment, record the
  finding, done. This is the preferred outcome; measure before assuming §4 is needed.
- **If it is ever non-zero**: the re-assert is load-bearing → keep it and make it scale per §4.

## 4. If still needed — make the scan scale (correctness preserved, node-wide)

Rewrite the scan so it is O(running-taps), single-SSH-session, with **no per-`(bridge × VM)` Perl
spawns**, while re-asserting the exact same node-wide set:

- **Read every VM's net config once, cheaply.** Don't call `qm config` per (bridge × VM). Read the
  cluster config store directly in one pass — e.g. `grep -HE '^net[0-9]+:.*bridge=tebr_' /etc/pve/qemu-server/*.conf`
  (pmxcfs; covers every VM on the node in a single file read, no Perl process) — or, if a `qm`-free
  read is rejected, call `qm config` **once per running VM** (hoisted out of the bridge loop), not
  bridges×VMs times.
- **Snapshot bridge membership + live taps once.** One `ovs-vsctl` dump of every `tebr_*` bridge's
  ports and one `ip -o link` (or reuse the config read) — then compute the missing `(bridge, tap,
  tag)` set in shell/awk and `ovs-vsctl --may-exist add-port` only those. The existing
  `ip link show $tap` guard already skips stopped VMs, so the `qm list` running-filter can be dropped
  or done once, not per iteration.
- **Keep it idempotent + `--may-exist`** (LES-011 / LES-033 posture): re-running must re-add only
  genuinely-absent ports, never duplicate or churn a correct one.
- **Backstop: make the SSH timeout configurable and/or proportional.** Add an `adapter.conf` knob
  (e.g. `SDN_REASSERT_TIMEOUT_S`, default a sane value) threaded into the `run_ssh(...)` call, and/or
  scale it with the running-VM count — so a genuinely large node has headroom even after the
  algorithmic fix. This is a backstop, **not** the primary fix (an O(bridges×VMs) scan with a bigger
  timeout still degrades).

## 5. Boundaries / out of scope

- **Do NOT narrow the scan to one deploy** (§2) — node-wide is correctness.
- **No version-locked schema change** — this is `rest.py` + an optional `adapter.conf` key only. If
  the build finds it *cannot* avoid touching a version-locked contract, STOP and ask.
- **GAP-047 is a separate work order** — the `network-ready` barrier never actually gating on
  `apply-sdn-config` (`predecessors=("apply-sdn-config[$scenario]",)` referencing a scope var the
  `net-id`-scoped barrier doesn't carry, silently dropped by `pass3_ordering._resolve_pattern`). It's
  what turned this timeout into a visible *deploy* failure rather than a silent one, and it deserves
  its own fix (make the edge resolvable, and/or make `_resolve_pattern` fail-loud on a
  zero-op predecessor pattern). Do not fold it in here; cross-reference only.
- Don't touch the SDN apply/verify logic itself (`rest.py:582-625`) — it works; only the trailing
  re-assert scan is in scope.

## 6. Validation

- **Unit:** with a mock `run_ssh`, assert the rewritten scan command (a) still targets **all**
  `tebr_*` bridges and **all** running VMs (not scoped to one deploy), (b) issues no per-VM×bridge
  `qm config`, (c) is idempotent (`--may-exist`), and (d) honors the configurable timeout. If the
  retire path (§3) is taken instead, assert the op no longer issues the scan at all and still returns
  `{"applied": True}` after the SDN apply/verify.
- **Gated live-verify (dev45) — the repro is the acceptance.** With a busy node (≥1 other standing
  twin up — e.g. the ~30-VM `corp-enterprise`), deploy a small fresh twin and confirm
  `apply-sdn-config` completes **well under the timeout** (no `adapter-timeout`), the twin's and the
  other twins' taps are all correctly attached afterward (`ovs-vsctl list-ports` per `tebr_*`), and
  capture on both still works. Record the scan's re-attachment count (feeds the §3 decision).

## 7. Depends on / related

- `docs/PRODUCT_GAPS.md` GAP-046 (this) + GAP-047 (sibling, separate); `docs/LESSONS.md` LES-168
  (the perf-cost lesson) + the LES-118 family (coarse-scope side effects).
- `docs/ADAPTER.md` §8 (the SDN apply + tap re-assert, historical); `axrange/adapter_proxmox/rest.py`
  (`op_apply_sdn_config`), `axrange/adapter_proxmox/ssh.py` (`run_ssh`),
  `axrange/adapter_proxmox/config.py` (the `adapter.conf` knob, if added).
- No sequence dependency on other open work orders.
