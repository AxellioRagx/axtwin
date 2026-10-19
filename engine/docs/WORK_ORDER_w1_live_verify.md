# Work Order — W1 (role/persona decoupling) gated `dev45` live-verify

> **STATUS: DONE — PASSED 2026-08-04** (all 8 proofs; cluster returned to the §2 baseline; 4 bugs found + fixed during the run, see CHANGELOG 2026-08-04). Authority: **user (2026-08-04)** — the user explicitly authorized deploying and
> destroying on `dev45` for this verify ("validate the changes live on dev45"). **Route:** Pipeline
> (`/dev`) — this is a **live-verifier** work order, not a code one: no production code should change
> unless the run surfaces a bug, in which case fix → re-test → re-verify. **Blocks**
> `docs/WORK_ORDER_corp_enterprise_bundle.md` (the user's sequencing: verify before building the
> bundle). **Verifies:** `docs/WORK_ORDER_role_persona_tier.md` (W1, code/tests/docs DONE 2026-08-04).

## 1. Why this run exists

W1 made a host's semantic **role** and its **persona** `tfc`-owned and runtime-swappable. The whole
claim is *"you can repurpose a standing twin without redeploying a node."* That is exactly the class
of claim a green unit suite cannot settle (LES-001): every assertion so far is against fakes, and the
one thing that matters — **that no VM is touched** — is only observable on a cluster.

## 2. Cluster preconditions (verified 2026-08-04, before this order was written)

`dev45` carries **zero `te`/`tap`/`tfc`/`exr` resources**. Live VMs are all protected or unrelated:

| vmid | name | what it is |
|---|---|---|
| 100, 101, 620, 9000, 9351, 9353 | `img-*`, `rocky10-dev-template`, `vyos-*` | templates — **never touch** |
| 102 | `o2-services` | standing O2 docker host (Grafana/Loki/Prometheus/Caldera/Wazuh) |
| 883610 | `o2-o2-suricata` | standing O2 suricata sensor (`o2-suricata`) |
| 260 | `Tcpdump-SW-A` | **external** tcpdump sink (`o2-tcpdump`) — virtio-fs host share |
| 103, 104 | `XPress-SW`, `XPress-SW-A` | **external** packetXpress sinks |
| 8100 | `ragx-dev` | unrelated dev VM |

SDN: exactly one `o2zbe2d1` zone + one `o2v7dc98` vnet (the O2 surface). Standing O2 deploy
`o2-surface-025c0709` is `completed`.

**Invariants for the whole run:** every row above must be **byte-identical at the end** — same vmid,
same status, same uptime for the running ones. The O2 zone/vnet must survive. If any of them changes,
the run has found a blast-radius bug and that is the headline finding, outranking everything else.

**Environment:** `--db deploy-dev45/state.db`, `--images deploy-dev45` (GAP-024: point at the PARENT
of `images/`, not at `images/` itself). Default node `dev45`.

## 3. Use a SMALL twin, not `corp-enterprise`

Deploy a focused ~4-host twin, **not** the 32-host `corp-enterprise`. This run is isolating W1's
mechanism; a 32-VM deploy adds ~30 minutes and a dozen unrelated failure modes, and
`corp-enterprise` gets its own deploy under the follow-on bundle work order anyway. Author it as a
throwaway twin-config in the same new `name:`/`role:` schema, since exercising that schema on a real
cluster is itself one of the things being verified:

- a `kind: router` gw with WAN egress (so hosts can reach apt if a persona needs it),
- `ws` ×2 on a LAN segment, `srv` ×1 on a second segment,
- **at least one node deliberately declaring NO `role:`** — the bare-bone case, whose entire role must
  come from the traffic profile,
- **at least one node whose `name:` differs from its `role:`** — the separation the pre-split single
  field could not express.

## 4. What the run must prove

Numbered so a partial run reports precisely how far it got.

1. **New twin-config schema deploys.** `te deploy --config <verify-id>` reaches `completed`. Node ids
   come from `name:`; `te inventory` shows `role` from `role:` where declared and `role=?`/none for
   the bare-bone node. **Record every host's vmid.**
2. **A profile targets by NAME.** `tfc deploy <profile> --target <scenario-name>` resolves to the live
   twin with **no deploy id anywhere in the file**. This is the LES-072 trap W1 closes; a profile
   hardcoding a minted id would defeat the entire point.
3. **Roles are tfc-assigned.** The profile's `roles:` block gives the bare-bone node a role;
   `te inventory` then shows it as **effective**, with `role_baseline` still reporting the twin's own
   (none). Check `te inventory --json` carries **both** fields (LES-149: the human view's
   `(tfc; baseline: …)` decoration must not leak into the machine-readable one).
4. **Personas actually run.** Confirm on the guest that each assigned persona's transient unit is
   live (`systemctl is-active axr-persona-*`), and — the real proof — that traffic appears **on the
   wire**, via a tap plan bound to a standing O2 sensor. A green `drive-persona` op is not evidence
   (LES-001).
5. **⭐ THE HEADLINE: a swap touches no VM.** Apply a second profile that changes *both* a role and a
   persona on already-running hosts. Then assert, before vs. after:
   - every vmid **identical**,
   - every guest's **uptime monotonically increasing** (no reboot),
   - **no `clone-vm`/`start-vm`/`configure-*` op ran** on the twin deploy (its op rows are untouched —
     the `tfc` deploy is a separate deploy id),
   - the **new** persona's unit is running and the **old** one is **gone** (de-scope, LES-112: a swap
     leaving both running is the specific bug `_stop_dropped_instances` exists to prevent),
   - the wire shows the new traffic shape.
6. **De-scoped roles revert.** Narrow the profile so a host drops out of `roles:`, re-apply, and
   confirm `te inventory` shows that host back on its twin **baseline** — not pinned to the stale
   role forever (the GAP-045 shape `_retire_dropped_roles` pre-empts).
7. **`tfc destroy` reverts and cleans.** Every host returns to baseline role; persona units stop.
8. **Teardown is clean and bounded.** `te destroy` removes the verify twin's resources and — per §2 —
   leaves **every** protected row untouched. Re-run `te destroy`: idempotent no-op.

## 5. Known traps this run is walking into

- **LES-096 / `--fresh`:** an already-`completed` op never re-publishes changed inputs. This twin is
  brand new, so it does not apply — but if the run redeploys the same verify twin after editing its
  config, use `--fresh` or the inventory will report stale facts and the verify will "pass" on them.
- **GAP-046:** `apply-sdn-config`'s trailing cluster-wide SSH tap-reassert scan is O(bridges ×
  running-VMs) with a hardcoded 60s timeout. dev45 currently runs ~7 VMs, so it should pass — but if
  the deploy fails on `ssh dev45: timeout after 60s`, that is GAP-046 and **not** a W1 regression.
  Record it and continue; do not "fix" W1 for it.
- **GAP-031:** a bare `--config` without `--images` fails `REF-120` for every node. Always pass
  `--images deploy-dev45`.
- **GAP-021:** truth-marker shipping needs TE egress. Not exercised here (no `exr`), noted so it is
  not mistaken for a new failure.

## 6. Done criteria

All eight numbered items pass, **or** the run stops at the first that does not and reports: which
item, the exact command, the observed vs. expected, and whether it is a W1 regression or a
pre-existing gap. A partial pass is a legitimate outcome and must be reported as one — never rounded
up.

On success: flip `docs/WORK_ORDER_role_persona_tier.md` and the CHANGELOG entry from *live-verify
PENDING* to *PASSED* with an evidence paragraph (the vmid/uptime before-and-after table is the
evidence that matters), and note it in `CLAUDE.md`'s W1 blockquote.

## 7. Cleanup obligation

The verify twin, its tap plan, and its traffic profile are **throwaway** — destroy all three before
finishing, in cascade order (`tfc`/`tap` before `te`, which `te destroy` now does itself, LES-171).
Leave `dev45` exactly as §2 describes it. If anything cannot be cleaned, say so explicitly with the
resource ids rather than leaving it for the next session to trip over.
