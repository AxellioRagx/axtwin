# Product Gaps — living register

A running list of **gaps in the implemented product discovered during real use** — things
that are missing, brittle, or under-finished and would block or surprise a real operator.
We add entries here *as we hit them* so we don't lose track of what's still missing from the
final product.

**How this differs from neighbouring docs:**
- `docs/OPEN_QUESTIONS.md` — *design* topics not yet locked into the specs (forward-looking).
- `docs/CHANGELOG.md` — what *was decided/built* (backward-looking, authoritative).
- **This file** — what's *built but incomplete/brittle* in the shipped product (the gap between
  "works in our verified path" and "robust for an arbitrary operator/cluster").

**Entry format:** one `## GAP-NNN` section. Keep `Status` current; when closed, leave the entry
with `Status: ✅ Resolved` + the CHANGELOG/commit that closed it (don't delete — the trail is the
point). Severity: **High** (blocks a core flow), **Medium** (painful workaround), **Low** (polish).

---

## GAP-001 — Template resolution is brittle: hard-coded VMID coupling, no fallback, no auto-build

**Status:** 🔴 Open
**Severity:** High (blocks `deploy` on any cluster whose templates don't exactly match the binding)
**Discovered:** 2026-06-26 — `ad-domain-lab` deploy on dev45 from the Grafana UI. `clone-vm[siem]`
and `clone-vm[ws01]` failed with `HTTP 500 unable to find configuration file for VM 9201/9104 on
node 'dev45'`, halting 24 downstream ops. The debian binding (`axrpool:base-620`) cloned fine; the
ubuntu-server-22 (`local-zfs:vm-9201`) and win11-finance (`local-zfs:vm-9104`) bindings pointed at
templates that don't exist on this cluster.

**Symptom:** deploy fails mid-run on `clone-vm` (`OP-HOST-010`) the moment a referenced template
VMID is absent on the target node, and every op downstream of that host halts
`halted-due-to-upstream`. The failure is late (mid-deploy), not at validate/plan time, and the
error is a raw Proxmox 500, not an actionable "template missing — build it / fix the binding".

**Root cause:** `clone-vm` resolves the source template **only** via the image binding's exact
`template_vmid` (`<images>/bindings/proxmox/<image>.yaml` → `binding.storage.template_vmid`). The
binding is **per-cluster, hand-maintained, and drifts** from cluster reality (on dev45 the debian
binding was updated to 620 but ubuntu/win11 were left at placeholder ids 9201/9104). There is **no
resolution layer** between "image ref" and "raw VMID".

**Missing capabilities (what the final product needs):**
1. **Pre-flight template validation** — at plan/deploy start, verify every referenced template
   exists on the target node and fail **early** with a clear, actionable message
   (`template ubuntu-server-22 (vmid 9201) not found on dev45 — run 'axrange image build
   ubuntu-server-22' or fix the binding`) instead of failing mid-deploy on `clone-vm`.
2. **Resolve by stable identity, not raw VMID** — match templates by name/tag (e.g. a deterministic
   template name or a Proxmox tag the image-build pipeline stamps) so bindings survive VMID changes
   and are portable across clusters. The raw VMID becomes a cache, not the contract.
3. **Fallback resolution** — if the bound template is missing, locate an equivalent on the cluster
   by image-id/name before giving up.
4. **Auto-build on miss (opt-in)** — if no template resolves, optionally invoke `axrange image
   build <image>` to create it, then proceed (the build pipeline already exists; it just isn't
   wired into the deploy path as a fallback).
5. **Binding ↔ cluster reconciliation** — a command to list cluster templates and validate/sync the
   bindings against them (`axrange image verify` / `image list-templates`), surfacing drift before a
   deploy ever runs.

**Impact:** deploys are not portable to an arbitrary cluster without hand-curating per-image
bindings to that cluster's exact VMIDs; drift fails late and opaquely; multi-cluster operation and
"clone the repo and deploy" are not yet real.

**Workaround (today):** build the missing template (`axrange image build <image>`) and/or hand-edit
`template_vmid` in the binding to match an existing template on the cluster, then re-run `deploy`
(resume-safe — completed ops are skipped, failed/halted retried).

**Related:** `docs/IMAGE_MANIFEST.md` (manifest + binding contract), `docs/IMAGE_BUILD.md`
(`axrange image build`), `docs/ADAPTER.md` (`clone-vm` / `OP-HOST-010`), `docs/VALIDATOR.md`
(pre-flight is a natural fit for a cluster-side rule).

---

## GAP-002 — apt-based provisioning (`provision-app` `pkg:`) is fragile and opaque when it fails

**Status:** 🔴 Open
**Severity:** Medium (fails a provisioning op; deploy is resume-safe so it's retryable, but the
failure is non-deterministic and hard to diagnose)
**Discovered:** 2026-06-26 — same dev45 `ad-domain-lab` deploy as GAP-001.
`provision-app[sensor-stack@detect]` failed at `TASK [install suricata]` with an `apt-get`
failure **even though egress works** (the attacker's apt-heavy `ad-offense` app on the same
segment completed, and `configure-router[gw]` was up). So this is not a network/egress problem.

**Symptom:** a `pkg:` install in a config-class app fails mid-deploy, and the stored error is
**truncated before the actual apt `E:` line** (the API/UI shows `…'/usr/bin/apt-get -y` and stops),
so the real cause — transient fetch hiccup vs. package-not-in-repo vs. empty apt cache vs. dpkg lock
— is **not visible without re-running**. The ansible apt result reported `cache_updated: false`.

**Root cause / contributing factors:**
- **No guaranteed `apt-get update` before install.** On a fresh clone with empty/stale package
  lists, an apt install without a cache update can fail to locate or fetch the package.
- **`pkg:` assumes the package is in the guest's base repos.** Some `sensor-stack` packages are not
  in Debian main (e.g. `zeek` needs an external repo). The **pre-baked sensor-image** path exists
  precisely to avoid runtime apt, but generic apt apps still attempt it.
- **No in-op retry/backoff** for transient apt/mirror blips — one hiccup fails the whole op (only the
  deploy-level resume retries, and only if the operator re-runs).
- **Failure output is truncated** by the executor, so the decisive `E:` line is lost — a
  diagnosability gap on its own (affects every `provision-app`/`run-script` failure, not just this).

**Missing capabilities:**
1. **Deterministic apt** — ensure `update_cache` (an `apt-get update`) before `pkg:` installs in the
   render, so a fresh clone's empty package lists don't cause spurious "can't find/fetch" failures.
2. **Package-availability awareness** — flag at plan time when a `pkg:` is not in the guest's base
   repos (e.g. `zeek`), or steer such installs to a pre-baked image, so structural failures don't
   surface only at deploy time.
3. **Bounded in-op retry/backoff** for transient apt/network failures before failing the op.
4. **Full error capture** — stop truncating `provision-app`/`run-script` failure output (or persist
   full logs) so the root cause is visible from the API/UI without re-running. *(Diagnosability —
   candidate to split into its own gap; it slowed this very diagnosis.)*
5. **Pre-baked-image escape hatch, generalized** — any app whose packages are flaky over apt should
   be bakeable into the image (the `suricata-sensor` already does this; generalize the pattern).

**Impact:** config-class provisioning is non-deterministic across clusters/templates and opaque when
it fails — the current experience is "it failed; re-run and maybe it works," with no visible reason.

**Workaround (today):** re-run `deploy` (resume-safe) — transient failures often clear on retry; for
**structural** failures (package not in the base repo), bake the tool into the image instead of
installing over apt at deploy time.

**Related:** `docs/SLICE_CONFIG.md` (`provision-app` / `run-script`), `docs/design/06-taps-sensors.md`
(pre-baked sensor images), `axrange/config_exec/` (Ansible render + QGA delivery), GAP-001 (both are
deploy-time robustness gaps).

---

## GAP-003 — Fabric cross-leaf symmetric-IRB: duplicate L3VNI Router-MAC → Type-5 "self mac" reject

**Status:** ✅ **Resolved 2026-07-01** — root-caused + fixed + **live-verified end-to-end on dev45**
(`ha`→`hb` cross-leaf ping 4/4, ttl 62). Fix: `axrange/adapter_proxmox/fabric_render.py` `_l3vni_rmac`
(unique per-leaf L3VNI Router-MAC), CHANGELOG 2026-07-01.
**Severity:** was Medium (blocked cross-leaf inter-segment routing; control plane + intra-leaf IRB always
worked). **Discovered:** 2026-06-30, root-caused 2026-07-01 — `fabric-verify` rig on dev45 (VyOS 1.5 /
**FRR 10.6.1**). See `docs/FABRIC_VERIFICATION.md` §9.

**Symptom (was):** a host on `leaf-1`'s segment couldn't reach a host on `leaf-2`'s segment — `ha`→`hb`
returned `From 10.1.10.254 Destination Net Unreachable`; `show ip route vrf optra 10.1.20.0/24` →
`% Network not in table`. Type-2/3 (L2VNI) reflected fine; only Type-5 (L3VNI) never installed.

**ROOT CAUSE (found via `debug bgp updates` on the receiving leaf):** the leaf **received** the peer's
Type-5 but **dropped it on input** with:

```
rcvd UPDATE about RD 10.255.0.4:10999 [5]:[0]:[24]:[10.1.20.0] label 10999 l2vpn evpn
   -- DENIED due to: self mac;
```

**Both leaves had the *identical* L3VNI Router-MAC** (`d6:2b:80:fd:94:79`) because the `br<l3vni>` bridge
on every **linked clone** auto-derived the same MAC. FRR stamps that MAC into every EVPN Type-5 it
originates (the RMAC extended-community), and applies a loop check that DENIES any inbound Type-5 whose
RMAC == the local router's own RMAC. So each leaf rejected all peers' Type-5 as its own. Type-2/3 carry
no RMAC-self check → they were unaffected (which is exactly why the failure looked Type-5-specific and
symmetric, and misled the earlier "RR doesn't reflect Type-5" / "RT doesn't bind" diagnoses — the RR
*did* reflect it and the RT *did* bind; the drop was on the receiving leaf).

**FIX:** emit a **unique per-leaf MAC** on `br<l3vni>`, keyed off the leaf's loopback
(`_l3vni_rmac`: `02:<vni_hi>:<vni_lo>:<loopback last 3 octets>`, a locally-administered unicast MAC —
e.g. leaf `10.255.0.3` → `02:2a:f7:ff:00:03`). Each leaf's Router-MAC is then distinct, so peers accept
the Type-5. Distinct from the L2VNI `_anycast_mac`, which is intentionally *shared* across leaves.

**Live-verify (dev45, FRR 10.6.1):** applying the exact per-leaf MACs the renderer now emits made
`leaf-1` install `10.1.20.0/24` in the optra VRF `via 10.255.0.4 br10999 onlink` (and the reverse on
leaf-2), and **`ha`→`hb` pinged 4/4, 0% loss, ttl 62** (routed through the L3VNI). The baked
`_l3vni_rmac` reproduces those distinct MACs for the rig (offline-verified: leaf-1 `02:2a:f7:ff:00:03`,
leaf-2 `02:2a:f7:ff:00:04` — the same values proven live), so the closed loop is: renderer emits distinct
per-leaf RMACs ⇒ peers accept the Type-5 ⇒ cross-leaf routes.

**Note on the trail:** the earlier "RT doesn't bind on VyOS 1.5" (2026-06-30) and "pure-RR spines don't
reflect Type-5" (2026-07-01 re-scope) were both **misdiagnoses** on the way here — the first from a grep
artifact on a partially-applied config, the second from not yet having traced the *receiving* leaf's
input drop. The `route-target both` syntax fix (2026-06-30) was still correct and necessary. Sibling
follow-on (still open): tenant-VRF **internet egress** (Finding C — `next-hop-vrf` invalid VyOS).

**Related:** `docs/design/04-networking-capture.md` (symmetric IRB), `axrange/adapter_proxmox/fabric_render.py`
(`_render_irb`), `docs/FABRIC_VERIFICATION.md` §9. Sibling follow-on (same run): tenant-VRF **internet
egress** (Finding C — `next-hop-vrf` is invalid VyOS; egress design deferred).

---

## GAP-004 — SDN `create-*` idempotency guard matched only "already exists", not PVE's "already defined"

**Status:** ✅ **Resolved 2026-07-01** — fix in `axrange/adapter_proxmox/rest.py` (`_is_already_present`
shared guard across all four SDN create-ops + a `_subnet_exists` check-then-act pre-check on
`create-subnet`); +3 regression tests (suite 493). CHANGELOG 2026-07-01.
**Severity:** was Medium–High (broke `deploy` **resume-safety** for any scenario with a `sensors:` block,
and one narrow miss **cascaded to wedge the entire deploy** — see below).
**Discovered:** 2026-07-01 — hit repeatedly while redeploying the `fabric-verify` rig for GAP-003 on
dev45 (PVE 9.2). Surfaced by the rapid deploy/destroy churn, but it is a real, always-present gap.

**Symptom:** on a redeploy/resume of a scenario whose sensor needs an auto-provisioned transport subnet,
`create-subnet` hard-fails:

```
proxmox REST POST /cluster/sdn/vnets/<vnet>/subnets: HTTP 500
  create sdn subnet object failed: sdn subnet object ID 'axr…-10.255.250.0-24' already defined
```

**Root cause (two parts):**
1. **Wrong idempotency string.** The four SDN `create-*` ops (`create-ovs-bridge`, `create-sdn-zone`,
   `create-vnet`, `create-subnet`) each tolerate an "object already present" collision as success
   (Model C — a resume must be a no-op). But the guard matched **only** `"already exists"`, while PVE 9.2
   reports an SDN **subnet** collision as `"already defined"`. So the guard missed it and re-raised.
   (vnets/zones happen to say "already exists", which is why only subnet was seen failing — but all four
   shared the same fragile single-string match.)
2. **No check-then-act on `create-subnet`.** Its three siblings each do a GET pre-check
   (`_bridge_exists`/`_zone_exists`/`_vnet_exists`) and only POST if absent — the string catch is a mere
   backstop. `create-subnet` had **no** pre-check, so it relied entirely on the (wrong-string) backstop.
   The subnet ID is deterministic per-plan, so every redeploy re-POSTs the same ID.

**Why one subnet failure wedged the whole deploy (blast radius):** `create-subnet` fail →
`apply-sdn-config` goes `halted-due-to-upstream` → **no cluster-wide `PUT /cluster/sdn`** → none of the
scenario's vnets materialize (fabric segments included) → VMs boot with no network → VyOS routers get no
DHCP lease → `configure-router` (which discovers a VyOS IP by `arp-scan` on `vmbr0`) hangs and fails
"could not resolve guest IP … after 12 attempts". A narrow miss in the **sensor** path took down the
**fabric** path because `apply-sdn-config` is a shared barrier everything networked depends on.

**Fix:**
- `_is_already_present(exc)` — one shared helper matching a broadened set of PVE collision wordings
  (`already exists` / `already defined` / `already registered`); applied to **all four** create-op
  backstops so they can't drift. A false positive only ever turns a create into a no-op (the Model-C
  intent).
- `_subnet_exists(vnet, cidr)` pre-check on `create-subnet` so it check-then-acts like its siblings
  (matches on the subnet's `cidr`, falling back to the dashed-cidr embedded in the object id) — it no
  longer even reaches the POST in the normal resume case, independent of PVE's error text.

**Residual / future hardening:** string-matching PVE error text is inherently fragile; the GET-before-POST
pre-check (now on all four ops) is the robust path and should stay the primary mechanism, with the string
guard only a backstop. Adjacent: teardown completeness — confirm `axrange destroy` always removes a
vnet's subnets before the vnet (`teardown.py:94`) so orphans don't accumulate (they still get tolerated
now, but shouldn't be created in the first place).

**Related:** `axrange/adapter_proxmox/rest.py` (the four `op_create_*` + `_is_already_present` +
`_subnet_exists`), `docs/SLICE_ROUTER.md` (create-subnet / Phase 1), `docs/PLANNER.md` (Model C
idempotency), GAP-001/GAP-002 (sibling deploy-time robustness gaps).

---

## GAP-005 — `Destroy` from the Grafana UI fails: DELETE request body dropped by the datasource proxy

**Status:** ✅ Resolved (2026-06-29) — `axrange/api/app.py` (query-param confirmation) +
`o2/grafana/plugins/axellio-axrange-control-panel/src/io/api.ts` (client sends the
query param). Regression tests: `tests/test_api.py::test_destroy_allow_via_query_param`,
`::test_destroy_no_confirmation_anywhere_400`.
**Severity:** High (blocks teardown from the UI — the only teardown surface a non-CLI operator has)
**Discovered:** 2026-06-29 — destroying a failed `ad-domain-lab` deploy on dev45 from the Grafana
control panel threw an API error on the `DELETE /api/deploys/{id}` call.

**Symptom:** the panel's Destroy button issues `DELETE /api/deploys/{id}` with a JSON body
`{allow_destroy: true}` through Grafana's **datasource proxy**. The API requires that confirmation,
and when the body doesn't arrive it refuses with `400 "destroy requires allow_destroy: true in the
request body"` (or a 422 if the body is absent entirely). The operator sees a generic API error and
no teardown happens, while the deploy's VMs keep running.

**Root cause:** sending a request **body on an HTTP DELETE** is poorly supported — Grafana's
datasource proxy (and many HTTP intermediaries) drop DELETE bodies. The confirmation was carried
**only** in the body, so a stripped body turned an authorized destroy into a rejection.

**Fix:** the API now accepts `allow_destroy` as a **query parameter** (`?allow_destroy=true`) in
addition to the body — query params always survive the proxy — and the body is made optional; the
effective confirmation is `query OR body`. The plugin client sends the query param (and keeps the
body for direct, non-proxied callers). The 400 guard still holds when confirmation is absent from
*both* places. CLI teardown was never affected (it doesn't go through the proxy).

**Related:** `docs/design/11-api-ui.md` (datasource-proxy write verbs — the open question this
realizes), `docs/design/11-api-ui.md` (destroy contract), GAP-006 (the sibling UI teardown gap hit
in the same session).

---

## GAP-006 — Orphaned/failed deploy is un-destroyable from the UI once the toolbar loses its selection

**Status:** ✅ Resolved (2026-06-29) —
`o2/grafana/plugins/axellio-axrange-control-panel/src/components/` (`AxrangeControlPanel.tsx`,
`ControlToolbar.tsx`, `DeployTree.tsx`).
**Severity:** High (a deploy with live VMs becomes un-tearable from the UI — strands real resources)
**Discovered:** 2026-06-29 — after the GAP-005 DELETE error, clicking around the panel lost the
active-deploy selection; the Destroy button then greyed out "as if nothing was deployed," even though
5 VMs from the failed `ad-domain-lab` deploy were still running on dev45. (Cleaned up out-of-band via
`axrange destroy <id> --allow-destroy` from the CLI — the engine state was intact.)

**Symptom:** the toolbar's Destroy button was gated on `canDestroy = !!activeDeployId &&
activeDeployDetail?.deploy.status !== 'destroyed'`. `activeDeployId` was **in-memory React state**,
set only when you deploy or have a deploy selected *this session*; gating also required a
successfully-loaded deploy **detail**. Re-clicking the selected row in the deploy list **toggled the
selection off**. So any of: a panel re-render, navigating away, a failed detail fetch, or an
accidental re-click left `activeDeployId`/detail empty → Destroy disabled, with **no path to
re-target an existing (failed/orphaned) deploy** for teardown.

**Root cause:** teardown was reachable only through the toolbar's transient single-selection, and the
enablement depended on a loaded detail. There was no selection-independent way to destroy a deploy
that already exists in the list.

**Fix:** three changes, all in the control panel —
1. **Destroy is centralized in the parent** (`AxrangeControlPanel`) as one handler (confirm →
   teardown → refresh list + detail → rail notice), shared by the toolbar and the list.
2. **Per-row Destroy button in the Deploys list** (`DeployTree`) for any deploy not already
   `destroyed`/`running` — **selection-independent**, so an orphaned/failed deploy is always tearable
   directly from its row regardless of toolbar state.
3. **Robust gating** — the active deploy's status is resolved from the loaded detail **OR** the list
   summary, so Destroy/Run enable the instant a deploy is selected without waiting on (or depending
   on) a successful detail fetch.

**Related:** `docs/design/11-api-ui.md` (teardown Destroy), GAP-005 (the DELETE-body fix hit in
the same session), `axrange destroy` (state-driven CLI teardown — the always-available fallback).

---

## GAP-007 — Pass-4 CLU cluster rules (CLU-010..070) are catalogued but entirely unwired — unenforced at deploy time

**Status:** 🔴 Open
**Severity:** Medium (silent gap in the "98 catalog rules" headline — the 7 cluster-satisfiability
rules never actually run against a real cluster)
**Discovered:** 2026-07-07 — whole-`axrange/` code review, while confirming which validator rules
are live on the `axrange deploy` path.

**Symptom:** `docs/VALIDATOR.md` and `CLAUDE.md` describe **98 catalog rules across 4 passes**
including CLU-010..070 (host-capacity placement, RAM/CPU/disk headroom, cross-scenario VLAN
collision, `template_vmid` existence on the target cluster). All 7 are registered in
`axrange/validator/catalog.py`. But `validate_with_cluster()` (`axrange/validator/engine.py`) —
the ONLY entry point that runs Pass 4 — is a documented **stub**: it accepts a `cluster_snapshot`
argument, appends `4` to `passes_run`, and **emits zero CLU findings** ("Recorded but skipped" per
its own docstring). Worse: neither `axrange/cli/main.py` nor `axrange/api/service.py` ever calls
`validate_with_cluster()` at all — both `deploy`/`validate` code paths call `validate_full()`
(Passes 1–3 only). So the cluster pass isn't just a no-op stub, it's **never invoked** on the real
deploy path.

**Root cause:** Pass 4 was deliberately deferred pending the Proxmox adapter ratification
(`docs/OPEN_QUESTIONS.md` Q5, since resolved) but the wiring was never revisited afterward — the
adapter has existed and been live-verified since 2026-05-28, yet nothing calls
`validate_with_cluster` with a real `cluster_snapshot`.

**Impact:** a scenario can validate/plan/deploy cleanly and still (a) place a host on a node without
enough RAM/CPU/disk headroom, (b) collide on a VLAN ID already used by a different scenario on the
same cluster, or (c) reference a `template_vmid` that doesn't exist on the target node (this last one
overlaps GAP-001, which documents the resulting late, opaque `clone-vm` 500). CLU-010..070 exist on
paper (they inflate the "103 rules" count truthfully — they're catalogued and unit-testable in
isolation) but provide zero deploy-time protection today.

**Missing capabilities:** (1) take a real `cluster_snapshot` (node list, capacity, existing VLANs,
existing templates) via the adapter's REST client before `deploy` proceeds; (2) call
`validate_with_cluster()` with it from the `deploy` CLI/API path (not just `validate_full`); (3)
implement the CLU-010..070 check bodies against that snapshot (currently unwritten — the stub has no
check logic to call even if it were invoked).

**Related:** GAP-001 (template resolution — CLU-010's target overlaps), `docs/VALIDATOR.md` §5.10
(CLU rule table), `docs/OPEN_QUESTIONS.md` Q5 (resolved, but Pass-4 wiring never followed up).

---

## GAP-008 — SSH backend is per-op `subprocess.run(["ssh", ...])`, no connection pooling

**Status:** 🔴 Open
**Severity:** Low (performance/cost, not correctness — every SSH-backed op pays a fresh TCP+auth
handshake)
**Discovered:** 2026-07-07 — whole-`axrange/` code review (adapter reviewer), confirming the standing
"Immediate Next Actions" refinement-bucket item was still true after this pass's adapter fixes.

**Symptom:** `axrange/adapter_proxmox/ssh.py`'s `run_ssh` shells out to the `ssh` binary via
`subprocess.run` on every single SSH-backed op (VyOS `vbash` sets, `tc`/`ovs-vsctl`/`ip link`
commands, SDN network-apply re-asserts). A scenario with many SSH-routed ops pays a full new
TCP-connect + SSH-handshake + auth round trip per op instead of reusing one multiplexed connection
(`ControlMaster`/`ControlPersist`, or a persistent `paramiko`/`asyncssh` session).

**Root cause:** the SSH backend was built as the simplest thing that works for the slice-MVP bring-up
and has not been revisited for connection reuse since — this was already called out as a "standing
refinement bucket" item in `CLAUDE.md` §6 before this review pass; the review re-confirmed it's still
the current implementation (no regression, just still open).

**Impact:** deploy latency scales with SSH-op count more than necessary on clusters/scenarios with
many VyOS routers or many taps; not a correctness bug, but a real cost as scenario size grows.

**Related:** `CLAUDE.md` "Immediate Next Actions" §6 (adapter refinement bucket, pre-existing);
`docs/ADAPTER.md` §3 (REST+SSH transport split).

---

## GAP-009 — Imagebuild sysprep conformance is exit-code-only; no in-guest `GeneralizationState==7` post-check

**Status:** 🔴 Open
**Severity:** Low (a narrower version of GAP-002/finding-#3's "wait success ≠ provision success"
pattern, scoped to Windows sysprep specifically)
**Discovered:** 2026-07-07 — whole-`axrange/` code review (imagebuild reviewer).

**Symptom:** the Windows imagebuild path fails the seal on a non-zero sysprep exit code (this pass's
fix — see CHANGELOG 2026-07-07), but does not follow up with an in-guest read of
`HKLM\SYSTEM\Setup\Status\SysprepStatus\GeneralizationState` to confirm the machine actually reached
generalization state `7` (fully generalized, ready to template) before sealing. A sysprep run that
exits 0 but leaves the machine in an intermediate/incomplete generalization state would not be
caught.

**Root cause:** deferred during this pass — the exit-code + `errors[]`-equivalent checks were judged
the higher-value fix for this round (matching the `_assert_cloud_init_clean` pattern on the Linux
side, see LES-039); the Windows-specific registry post-check was scoped out as a follow-on.

**Impact:** a rare sysprep edge case (exit 0, generalization incomplete) could seal a template that
looks conformant but isn't fully generalized — narrower and less likely than the cloud-init
non-fatal-inner-failure pattern GAP-002/LES-039 close for Linux, but the same class of gap.

**Related:** LES-039 (the Linux-side sibling fix, same session), `docs/IMAGE_BUILD.md` §3 (Windows
W0 build), `docs/SLICE_WINDOWS_AD.md`.

---

## GAP-010 — STR-120/CAP-030's lenient-skip means a `kind: host` node with no `requires:` gets no OS-family assertion

**Status:** 🔴 Open
**Severity:** Low (an authoring-completeness gap, not a runtime-correctness one — silence, not a
wrong deploy)
**Discovered:** 2026-07-07 — whole-`axrange/` code review, while auditing CAP-020/030's binding-fan-
out fix for related lenient-skip edges.

**Symptom:** `STR-120` ("every node has exactly one OS-family keyword in `requires:`") and `CAP-030`
("image's OS family matches the node's OS-family keyword") both intentionally skip a node that has no
`requires:` block at all, rather than erroring "missing OS-family assertion." A `kind: host` node
authored with no `requires:` silently gets zero OS-family validation — it will bind to whatever image
the `image:` ref resolves to with no cross-check that the image's actual OS family is what the
scenario author intended.

**Root cause:** `requires:` is optional by IR_SCHEMA design (a node can rely purely on `image:` to
pin behavior), so the validator treats "no `requires:`" as "author didn't ask for an assertion" rather
than "author forgot one" — a deliberate leniency, not a bug, but one that leaves a real authoring
footgun unaddressed.

**Impact:** a typo'd or wrong `image:` ref on a `requires:`-less host node deploys without any
OS-family cross-check catching the mismatch; the failure (if any) surfaces later and less legibly, at
whatever config-class op first assumes the wrong OS's shell/package manager.

**Related:** `docs/VALIDATOR.md` §5.1 (STR-120), §5.4 (CAP-030), CAP-020's fan-out fix (same review
pass, adjacent area).

---

## GAP-011 — Several adapter ops hardcode `default_node`; no real multi-node placement

**Status:** 🔴 Open
**Severity:** Medium (blocks meaningful use of a multi-node Proxmox cluster — everything effectively
lands on one node)
**Discovered:** 2026-07-07 — whole-`axrange/` code review (adapter + planner reviewers).

**Symptom:** several `adapter_proxmox` ops resolve their target Proxmox node via a single configured
`default_node` rather than consulting the planner's Pass-4 placement decision
(`deploys.placement` — host node ID → Proxmox node name, `docs/PLANNER.md` §7.2) on a per-op basis.
Multi-node clusters (`pxtwin1`, `dev45` are both single-node-in-practice today) work by construction
because "the one node" and `default_node` coincide, but the placement model as designed
(`docs/PLANNER.md` §3.4 Pass 4) is not actually exercised end-to-end for ops that hardcode the
default.

**Root cause:** both live clusters used for verification to date are effectively single-node, so the
gap has never been forced open by a real multi-node deploy; the placement DAG pass computes a
decision that a subset of ops don't consume.

**Impact:** deploying to an actual multi-node cluster with real placement spread would not
distribute load the way the plan's `placement` map claims — some ops would land wherever
`default_node` points regardless of the computed placement.

**Related:** `docs/PLANNER.md` §3.4 (Pass 4 — Placement), `docs/ADAPTER.md` §3 (adapter/backend
routing), CLU-010 (GAP-007 — host-capacity placement, the validator-side sibling of this gap).

---

## GAP-012 — `verify-capture` fails hard when `SENSOR_SSH_HOST` is unset; no plan-time check, drift-prone key

**Status:** ✅ **Resolved (live-verified) — 2026-07-15** (`docs/WORK_ORDER_sensor_agnostic_te.md` §5 +
`docs/WORK_ORDER_taps.md` §3, folded into one dev45 run; CHANGELOG 2026-07-15). Built 2026-07-13
(CHANGELOG "sensor-agnostic te" design + build entries). The dependency is **removed, not worked
around**: a `te` deploy no longer emits `verify-capture` (or `attach-gre-tunnel`/`install-ovs-mirror`)
at all — it emits only `record-tap-intent` (OP-MIR-005, declarative, no adapter call). `verify-capture`
is now emitted exclusively by the **A2 tap playout** (`axrange o2 tap apply`), which reads the sensor
over **QGA only** via its `sensor_vmid` input, itself resolved from A1's standing sensor inventory at
tap-bind time. The `SENSOR_SSH_*` "no vmid ⇒ SSH fallback" code path (`run_ssh_sensor`,
`_default_sensor_confirm`/`sensor_confirm_fn`) is **deleted**, not left dead; `ConfigBackend._verify_capture`
now raises loud if no `sensor_vmid` is threaded in, rather than falling back to an unset
`SENSOR_SSH_HOST`. `SENSOR_TRANSPORT_IP`/`SENSOR_<ID>_TRANSPORT_IP`/`SENSOR_SSH_*`/`SENSOR_CAPTURE_IFACE`
and the `sensors` map are removed from `AdapterConfig`/`adapter.conf.example` — there is nothing left
in `adapter.conf` for a stale key to drift from. **Live-verified on dev45 2026-07-15:** tap-plan
deploy `tap-tap-verify-c82bd2b5-9c0c7097` ran `verify-capture` over QGA against sensors
`o2-tcpdump`/`o2-suricata` with **no `SENSOR_SSH_*`/`SENSOR_TRANSPORT_IP` set anywhere in
`adapter.conf` or env**, confirming the symptom below cannot recur on real hardware.
**Severity:** Medium (fails the capture heartbeat mid-deploy and halts its downstream barrier, even
though every resource-creating op succeeded; the deploy reports FAILED for a config-file reason)
**Discovered:** 2026-07-08 — the tier-ownership-tags live-verify on dev45 (deploy of the ADAPTER §6
slice-MVP). All 24 resource ops succeeded (VMs, SDN, gretap, mirror, taps), but
`verify-capture[h-a@corp@default]` failed after 3 attempts with `verify-capture: SENSOR_SSH_HOST is
not set; the heartbeat can't read the sensor`, halting `mirror-ready[default]`. The deploy's overall
`Result: FAILED` was entirely attributable to that missing config, not to any deployed resource.

**Symptom:** on a cluster whose `adapter.conf` doesn't define `SENSOR_SSH_HOST` (the SSH host the
`verify-capture` heartbeat reads captured packets from), the op fails **mid-deploy** with a raw
"not set" error and halts its barrier — after all the real infra/VM ops have already succeeded. The
failure is late and reads as a deploy failure rather than an "operator hasn't configured the sensor
side" prerequisite. Compounding it, the dev45 `adapter.conf` still carries a **stale, pre-rename `SINK_TRANSPORT_IP` key** (the current code expects `SENSOR_TRANSPORT_IP` and a distinct `SENSOR_SSH_HOST`),
so the config file has drifted from the code's expected keys after the capture-appliance→sensor rename.

**Root cause:** `verify-capture` requires an SSH channel to the sensor to confirm a sentinel packet
arrived, but (a) nothing checks at plan/deploy-start that `SENSOR_SSH_HOST` is configured before the
deploy invests in creating VMs and networks, and (b) the `adapter.conf(.example)` key names were not
reconciled on existing cluster config files during the capture-appliance→sensor rename, so a
pre-rename `adapter.conf` silently lacks the keys the current code reads.

**Missing capabilities:** (1) a plan-time / deploy-preflight check that, when a scenario has a tap +
`verify-capture` in its plan, asserts `SENSOR_SSH_HOST` (and `SENSOR_TRANSPORT_IP`) are set and fails
**early** with an actionable message instead of mid-deploy; (2) treat a missing sensor-SSH config as a
skippable/soft condition when the operator hasn't stood up a sensor (verify-capture is a heartbeat, not
a resource) — or make the requirement explicit at validate time; (3) a config-file migration note /
loader alias so a stale pre-rename `SINK_TRANSPORT_IP` key is either read as `SENSOR_TRANSPORT_IP` or flagged
as deprecated, rather than silently absent.

**Impact:** deploys of any tapped scenario fail late on a cluster whose `adapter.conf` predates the
capture-appliance→sensor rename or simply lacks a configured sensor host, masking an
otherwise-successful deploy as FAILED.

**Workaround (today):** set `SENSOR_SSH_HOST` (and `SENSOR_TRANSPORT_IP`) in the target cluster's
`adapter.conf`, or deploy a scenario without a `verify-capture` heartbeat, then re-run (resume-safe).

**Related:** `docs/design/06-taps-sensors.md` (`OP-MIR-040 verify-capture`; the capture-appliance→
sensor rename), `adapter.conf.example`, GAP-001/GAP-002 (sibling deploy-time robustness gaps).

---

## GAP-013 — OVS mirror teardown is not check-then-act idempotent (reports "removed" for an already-absent mirror)

**Status:** 🔴 Open
**Severity:** Low (cosmetic idempotency imperfection — teardown is functionally clean, no leak, no
error; only the reported action is misleading on a re-run)
**Discovered:** 2026-07-08 — the tier-ownership-tags live-verify on dev45. The first `axrange destroy`
swept 8 resources cleanly (`Result: DESTROYED`, 0 not cleared); the **idempotent re-destroy** correctly
reported every resource `absent` **except the two OVS mirrors**, which reported `mirror 607525: removed`
/ `mirror 851340: removed` again (`2 resource(s) removed`) despite the VMs and bridge already being gone.

**Symptom:** on a second `destroy` of an already-destroyed deploy, the mirror teardown step re-issues
its removal and reports "removed" rather than "absent", unlike every sibling teardown step (gretap,
vnet, zone, bridge, VM) which check presence first and report "absent" on the second run. The overall
result is still `DESTROYED` with `0 not cleared`, so it's purely a reporting/idempotency-shape wrinkle.

**Root cause:** the mirror teardown path (per-tap `tc`/OVS mirror removal) issues its delete
unconditionally and treats the delete as success, rather than doing a GET/presence pre-check
(check-then-act) the way the SDN/VM teardown steps do. On a re-run the target is already gone, so the
delete is a tolerant no-op that still reports "removed". Not introduced by the tier-tag change (that
change only renamed the mirror `mir-<vmid>`→`tapm-<vmid>`); the teardown logic was pre-existing.

**Missing capability:** a presence pre-check on the mirror teardown so a re-destroy reports "absent"
(and counts 0 removed) for an already-gone mirror, matching the idempotency shape of the other
teardown steps. Aligns with the standing guidance that check-then-act (not error-string tolerance) is
the robust idempotency mechanism (cf. GAP-004's residual note).

**Impact:** none functional — teardown is complete and idempotent in effect. The only cost is a
misleading "removed" line + non-zero removed-count on a re-run, which could confuse an operator reading
teardown output as a source of truth for what was actually present.

**Related:** `axrange/planner/teardown.py` + `axrange/adapter_proxmox/teardown.py` (mirror teardown
step), `docs/ADAPTER.md` (Option 1 capture — per-tap `tc`/OVS mirror), LES-011 (OVS mirror plumbing),
GAP-004 (check-then-act as the robust idempotency path).

## GAP-014 — Default-agent exporter dedup matches `tool:` names case-sensitively

**Status:** 🔴 Open
**Severity:** Low (polish — a mismatched-case authored entry produces a harmless duplicate agent,
not a broken deploy)
**Discovered:** 2026-07-11 — the A3 Stage A (`overlays.adversary` + default-agent sweep) code review.

**Symptom:** `axrange/overlays/default_agents.py`'s `EXPORTER_TOOL_ALIASES` (`"node-exporter"`,
`"prometheus-node-exporter"`, `"windows_exporter"`) and `hosts_with_explicit_defense_tool`'s
membership check (`entry.tool in tool_names`) are both exact-string, case-sensitive matches. An
author who hand-writes an `overlays.defense` entry with `tool: Node-Exporter` or
`tool: NODE-EXPORTER` (a plausible authoring variance — nothing in the IR schema or validator
normalizes `tool:` casing) will NOT be recognized as "already covers the exporter default," so the
default-agent sweep (`scenario.default_agents`, on by default) installs a **second**, redundant
node-exporter alongside the hand-authored one instead of skipping it.

**Missing capability:** case-insensitive (or otherwise normalized) matching in the dedup check —
either lowercase both sides before comparing, or canonicalize `tool:` at IR-parse time.

**Impact:** cosmetic/wasteful (a duplicate agent process, not a broken deploy or a security gap);
no scenario in `target-environments/` currently hits this (none hand-author an exporter-named
`overlays.defense` entry), so it hasn't been hit in practice.

**Related:** `axrange/overlays/default_agents.py` (`EXPORTER_TOOL_ALIASES`,
`hosts_with_explicit_defense_tool`), `docs/design/08-exercise-measurement.md` (the default-agent
mechanism), `docs/WORK_ORDER_attack_defense.md`.

---

## GAP-015 — Default-ON exporter agent assumes egress; times out (600s) + retries on a no-router scenario

**Status:** 🔴 Open
**Severity:** Medium (any scenario without router-NAT egress that carries the default-ON exporter
agent will spend 600s/attempt timing out, holding the deploy lease and reporting FAILED — even
though every resource-creating op succeeded)
**Discovered:** 2026-07-13 — the resource-ownership-tags live-verify on dev45 (deploy of
`tap-verify.yaml`, a minimal 2-host tapped scenario with **no `kind: router` node** and therefore no
NAT egress). `install-agent[default-exporter-h-a@…]` happened to succeed, but
`install-agent[default-exporter-h-b@…]` timed out with `agent exec […/agent-default-exporter-h-b.sh]
on vmid=… did not finish in 600s` (`adapter-timeout`) and the executor moved to a second 600s
attempt — keeping the deploy `running` and the lease held for 17+ min before it was stopped.

**Symptom:** on a scenario with no egress path, the default-ON exporter agent
(`scenario.default_agents`, default-ON since 2026-07-11) runs an in-guest install script that
apt-fetches the node exporter; with no route to the package mirror the script hangs for the full
600s adapter-exec timeout, then retries. The deploy neither fails fast nor skips the agent — it
grinds through retries while holding the per-scenario lease, and ultimately reports FAILED for a
reason unrelated to the scenario's own components.

**Root cause:** the default-agent sweep injects the exporter `install-agent` for every eligible host
unconditionally, and the agent's install path assumes outbound network reachability (apt). Minimal
or deliberately-isolated scenarios (no router/NAT, air-gapped segments) have no such path, so the
install cannot complete. There is no (a) plan-time check that the target host has an egress route
before scheduling an egress-dependent agent, (b) baked/offline exporter delivery, or (c) fast-fail
on "no route" instead of a full 600s timeout.

**Missing capabilities:** (1) make the default-ON exporter opt-out-able per scenario/host, or gate
it on the presence of an egress path (router node / SNAT subnet) the way `provision-app` ordering
now gates on `configure-router`; (2) bake the exporter into the golden image (cf. LES-024 — bake
fragile/external-repo tools at build time) so the in-guest step is a start, not an apt-fetch;
(3) fail fast with an actionable "no egress route for exporter install" instead of a 600s hang.

**Impact:** any tapped/minimal scenario without a router (e.g. `tap-verify` and similar
capture/tag smoke tests) is pushed to a FAILED result and a multi-minute lease hold purely by the
default agent — surprising for an operator running a deliberately-small environment.

**Workaround (today):** give the scenario a `kind: router` node (or a `create-subnet` SNAT egress),
or disable the default agents for that scenario (`scenario.default_agents: false`); the tag/resource
proof itself is unaffected — every infra op completes before the agent step.

**Related:** `axrange/overlays/default_agents.py` + `axrange/config_exec/{render,backend}.py`
(`install-agent` / `OP-OVL-070`), `docs/design/08-exercise-measurement.md` (default-agent
mechanism, default-ON authority), `docs/SLICE_ROUTER.md` (egress/NAT), LES-024 (bake external-repo
tools into the image), GAP-014 (sibling default-agent gap).

**Reproduced:** 2026-07-16 — the dev45 CLI-lifecycle smoke (`docs/WORK_ORDER_cli_lifecycle.md`) on
`blast-check.yaml`, a minimal no-router single-host TE, hit the same symptom (exporter `install-agent`
timing out with no egress path). No new finding — confirms this gap is still live; no separate entry
created.

---

## GAP-016 — `axrange/caldera:5.0.0` returns a silent aiohttp 500 on every HTTP path (no traceback)

**Status:** ✅ **Resolved (live-verified) — 2026-07-15** (`docs/WORK_ORDER_o2_server_stack.md` §3.3 +
§5). Fixed with a `yarl==1.9.4` pin in `o2/caldera/Dockerfile`; **live-verified on the real §2
dedicated O2-services VM** (VMID 102 `o2-services` on dev45, cloned from `img-debian-12-cloud`) —
the built image ran clean on the VM (build smoke-gate → 200, `caldera` container `Up (healthy)`,
`GET /` → 200) and was PROVEN end-to-end by A3: a Sandcat agent downloaded from Caldera and
beaconed (paw `ahkojg`, host `agent1`, group `red`). **Root cause corrected below — the original
`apispec`/`webargs` hypothesis was disproven, not confirmed** (CHANGELOG 2026-07-15 "O2
server-stack bring-up").
**Severity:** High (blocks the A3 Stage B live-verify entirely — Caldera is one of the three
agent→server pairs; the AD purple-team loop's red-team side is dark until this is fixed)
**Discovered:** 2026-07-15 — the A3/A4 gated live-verify attempt on `dev45`, first real
podman-rootless bring-up of `o2/compose/docker-compose.yml` on the `ragx-dev` orchestrator host.

**Symptom:** the `axrange/caldera:5.0.0` container boots, listens on `0.0.0.0:8888`, and passes its
own internal `/system/ready` websocket check — but **every** HTTP path, including static assets
under `/assets/` and `/api/v2/health`, returns a bare aiohttp 500 ("Server got itself in trouble")
with **no server-side traceback** in the container logs. This is a distinct, still-live issue from
the earlier magma-dist build crash (LES-064's neighbor finding — now written up as **LES-077**) —
`dist/`/`assets/` are present in the image, so the Dockerfile fix for that earlier problem holds;
this is a runtime request-handling failure, not a missing-build-artifact one.

**Root cause — CORRECTED 2026-07-15, the earlier hypothesis below was DISPROVEN:** ~~`requirements.txt`
correctly pins `aiohttp==3.9.3` and `marshmallow==3.20.1`, but `apispec` (resolved to 6.10.0) and
`webargs` (resolved to 8.7.1) are unpinned transitive dependencies — both too new for the pinned
`aiohttp-apispec==3.0.0b2` beta.~~ A live in-container diagnosis (podman, 2026-07-15) confirmed
`apispec==6.10.0`/`webargs==8.7.1` serve fine — that was not the cause. The **actual** root cause:
**`aiohttp==3.9.3`'s `BaseRequest.url` passes the raw `Host: <host>:<port>` header straight into
`yarl.URL.build(host=...)`, and `yarl>=1.13` rejects any host containing `:`**
(`ValueError: Host 'localhost:8888' cannot contain ':'`) — on every request, before
routing/middleware ever runs. `requirements.txt` pins `aiohttp` but not `yarl`, an unpinned
transitive that resolved to `1.24.2`. See LES-074 for the general lesson (a swallowed-exception
symptom needs an un-swallow, not a plausible-sounding pin-skew guess).

**Fix applied:** pin `yarl==1.9.4` in `o2/caldera/Dockerfile` (a 5.0.0-era patch; upstream Caldera
5.3.0's `aiohttp==3.10.11` fixes this at the source via `authority=` instead of `host=`, so this pin
can be dropped if `CALDERA_VERSION` ever moves to 5.3.0+ — recorded as a future migration option,
not a decision made). Also added a build-time HTTP smoke gate (python3 `urllib`, probes `GET /` in
a retry loop, fails `docker build` on a 500) so a regression to this class of bug is caught at build
time, not at A3 live-verify time.

**A second, separate bug found during the same diagnosis (still open, not this GAP's fix):**
unauthenticated `GET /api/v2/health` returns 500 on Caldera 5.0.0 — an upstream app bug (`IndexError`
on `access[0]` for anonymous callers on an `authentication_exempt` route with an empty permissions
tuple), unrelated to the `yarl` bug. The build smoke gate and any future compose healthcheck
**must probe `GET /`, not `/health`** — do not "fix" the probe to `/health` later; Caldera 5.3.0
re-guards `/health` upstream.

**Missing capabilities:** (1) [DONE] pin `yarl==1.9.4` and rebuild — validated locally and live on
the §2 O2-services VM; (2) [DONE] rebuilt + re-verified on the real VM, compose healthcheck
confirmed passing (`Up (healthy)`); (3) optionally, bump `CALDERA_VERSION` to 5.3.0+ later to drop
the pin at the source — still a future option, not decided.

**Impact:** resolved — the Sandcat agent→Caldera server pair (A3 Stage B) is now live-verified;
Caldera serves 200 and enrolled a real Sandcat agent. A4's coverage dashboard can draw on a live
Caldera going forward (not exercised in the A4 run itself, which used the `activity`-engine path).

**Related:** `o2/caldera/Dockerfile`, `docs/design/08-exercise-measurement.md` (Stage B), LES-064
(the sibling Caldera healthcheck gotcha from the same compose file), LES-074 (the `pip check` /
yarl root-cause lesson), LES-075 (the build-time smoke gate mechanics), LES-077 (the magma build
precondition, closing this GAP's earlier dangling pointer), `docs/WORK_ORDER_attack_defense.md` §4
(now PASSED — 2026-07-15). **Closed by:** `docs/WORK_ORDER_o2_server_stack.md` §3.3 + §5 (DONE).

---

## GAP-017 — `wazuh/wazuh-manager:4.9.2` crash-loops on boot: missing `/etc/filebeat/filebeat.yml`

**Status:** ✅ **Resolved (live-verified) — 2026-07-15** (`docs/WORK_ORDER_o2_server_stack.md` §3.2 +
§5). **The fix EVOLVED during the live bring-up** — the committed filebeat-stub config alone did
NOT hold on the real VM: filebeat 8.x refuses to start on a bind-mounted config file it doesn't own
(`"config file (\"/etc/filebeat/filebeat.yml\") must be owned by the user identifier (uid=0) or
root"`), and a git-tracked file necessarily carries the HOST's ownership (uid 1000 in this case),
which git cannot control — so the stub alone still crash-looped the container exactly like the
original missing-file symptom. **The durable fix** is an additional empty s6 `down` file
(`o2/wazuh/filebeat-down`, mounted at `/etc/services.d/filebeat/down:ro`) that disables the
filebeat s6 service outright — the work order's own named alternative (§3.2: "or disable the
filebeat s6 service"). With both mounts in place the manager ran stable on the O2-services VM: all
daemons up, `:55000` API responding 401 (auth-required, i.e. alive); PROVEN by A3 — `agent1`
enrolled in the Wazuh manager, ID `001`, status Active.
**Severity:** High (blocks the A3 Stage B live-verify — Wazuh is the blue-team agent→server pair)
**Discovered:** 2026-07-15 — same `ragx-dev` first-bring-up session as GAP-016.

**Symptom:** the `wazuh/wazuh-manager:4.9.2` container exits immediately on start with
`error loading config file: stat /etc/filebeat/filebeat.yml: no such file or directory; Filebeat
exited code=1`. The manager's own entrypoint expects a filebeat config to already be present (it
ships filebeat as a co-process for shipping its own logs/alerts), and the compose stanza under
`o2/compose/docker-compose.yml` does not mount or bake one in.

**Root cause:** the official Wazuh manager image at this tag auto-starts filebeat as an s6-managed
co-process, normally used to ship the manager's alerts to a sibling wazuh-**indexer**. This O2 stack
is deliberately manager-only (no indexer — alerts read via the manager API :55000 / local
`alerts.json`), so filebeat is unconfigured *and* unneeded, and its hard-exit on a missing config
tears the whole container down via s6 even though the manager daemons themselves are fine.

**Fix applied:** `o2/wazuh/filebeat-stub.yml` — empty `filebeat.inputs`, a local `output.file` (no
Elasticsearch/indexer output) — mounted read-only at `/etc/filebeat/filebeat.yml` (kept for
intent/documentation), **plus** the load-bearing `o2/wazuh/filebeat-down` empty s6 `down` file
mounted at `/etc/services.d/filebeat/down:ro`, which disables the filebeat service at the s6
supervisor level before it ever reads the stub — ownership-independent, and what actually keeps
the manager up. This is a **stub + service-disable, not a real alerting path** — if O2 ever wants
native Wazuh alerting via filebeat (rather than reading `alerts.json`/the manager API), replace the
`down` file with a real config pointed at a wazuh-indexer; that remains a separate design call, not
decided by this fix.

**A second, unrelated blocker found on the same VM (not this GAP's own fix, recorded here for
completeness):** the O2-services VM's default `kvm64` CPU model lacks the **x86-64-v2** baseline
Wazuh 4.9.2's glibc requires — the manager fatal-errored on boot (`Fatal glibc error: CPU does not
support x86-64-v2`) until the VM was reconfigured with `qm set 102 -cpu host` (+ reboot). Any future
O2-services VM (or any VM cloning a modern Debian image expected to run recent glibc-linked
binaries) should be built with `-cpu host` or an explicit v2-or-higher CPU model from the start,
not `kvm64` — see LES-081.

**Impact:** resolved — the Wazuh agent→server pair is now live-verified; a real Wazuh agent
enrolled (`agent1`, ID `001`, Active) and A3's host-defense agent enrollment proof is closed.

**Related:** `o2/compose/docker-compose.yml` (wazuh-manager service), `o2/wazuh/filebeat-stub.yml`,
`o2/wazuh/filebeat-down` (the actual fix), LES-063 (the sibling Wazuh port protocol gotcha from the
same compose file), LES-080 (the s6-down-file pattern this fix generalizes to),
`docs/WORK_ORDER_attack_defense.md` §4 (now PASSED — 2026-07-15). **Closed by:**
`docs/WORK_ORDER_o2_server_stack.md` §3.2 + §5 (DONE).

---

## GAP-018 — Prometheus/Grafana fail to start after a hung `podman-compose up`: stale rootlessport left bound to 9090

**Status:** ✅ **Resolved (live-verified) — 2026-07-15** (`docs/WORK_ORDER_o2_server_stack.md` §2 +
§5). Fixed by the §2 dedicated **rootful**-docker O2-services VM (VMID 102 `o2-services` on
dev45, Docker CE 29.6.1 + compose v2) — this eliminates the whole `rootlessport`-orphan class (no
rootless port-forwarding helper to leak): clean iptables port publishing, all five compose
services came up `Up (healthy)` on the first bring-up of the new VM. The §3.1 hygiene item
(reverting a clobbered `o2/prometheus/prometheus.yml` template-placeholder render artifact back to
`__CAPTURE_HOST__:__CAPTURE_NODE_EXPORTER_PORT__`, plus a healthcheck/port audit that found no
other issues) landed the same day as related cleanup, but the actual fix for this GAP was the VM
move, not the config revert.
**Severity:** Medium (an operational/environment hygiene gap, not a design flaw — self-inflicted by
a prior hung compose run, but the compose stack has no self-healing/cleanup story for it)
**Discovered:** 2026-07-15 — same `ragx-dev` first-bring-up session as GAP-016/GAP-017.

**Symptom:** `podman-compose up -d` for `o2/compose/docker-compose.yml` fails to bind Prometheus's
published port: `rootlessport listen tcp 0.0.0.0:9090: bind: address already in use`. Grafana (which
depends on Prometheus being reachable) does not come up cleanly either. Only `loki` came up healthy
(HTTP 200) in this run.

**Root cause:** a previous, presumably hung or improperly torn down `podman-compose up` left a
rootless port-forwarding helper process (`rootlessport`) still bound to `9090` from an earlier
container that podman itself no longer considers running — a known rootless-podman networking
footgun (the port-forward helper can outlive the container it was forwarding for if the teardown
sequence is interrupted).

**Missing capabilities:** (1) an operator runbook note (this file / `docs/RUNBOOK_o2_live_verify.md`)
to check for and kill stale `rootlessport` processes (`pgrep -af rootlessport`) before a compose
bring-up on a host with prior interrupted runs; (2) longer-term, a `docker compose down` /
`podman-compose down` pass (with `--remove-orphans`) as a documented precondition before any `up`,
or a preflight check in the runbook that fails fast with an actionable message instead of the raw
`bind: address already in use`.

**Impact:** resolved on the `ragx-dev` orchestrator's rootless podman by simply not standing the
stack up there any more — the O2-services VM (rootful docker) is the standing home for the compose
stack going forward. The underlying rootless-podman footgun (missing `--remove-orphans` / stale
`rootlessport` cleanup) is not itself patched anywhere and would still bite a future rootless
bring-up attempt; the operator runbook note in "Missing capabilities" above remains a good idea if
rootless podman is ever used for this stack again, but is no longer required to unblock A3/A4.

**Related:** `o2/compose/docker-compose.yml`, `docs/RUNBOOK_o2_live_verify.md` (GATE 0 — bring up O2
Stage-B servers), `docs/WORK_ORDER_attack_defense.md` §4 (now PASSED — 2026-07-15). **Closed by:**
`docs/WORK_ORDER_o2_server_stack.md` §2 (the dedicated rootful VM) + §3.1 hygiene.

---

## GAP-019 — Caldera 5.0.0: unauthenticated `GET /api/v2/health` returns 500 (upstream app bug, mitigated by probing `/`)

**Status:** 🟡 Mitigated — not fixed at the source (upstream Caldera code), worked around by never
probing `/health` anonymously.
**Severity:** Low (does not block anything currently — GAP-016's build smoke gate and any future
compose healthcheck already probe `GET /` instead — but is a footgun for whoever next touches either
probe)
**Discovered:** 2026-07-15 — the same in-container GAP-016 diagnosis (podman), while confirming the
`yarl==1.9.4` fix with a spot-check across several `/api/v2/*` routes.

**Symptom:** an unauthenticated `GET /api/v2/health` request against a healthy, correctly-patched
`axrange/caldera:5.0.0` (the `yarl` bug fixed) still returns HTTP 500 — even though `/health` is
declared `authentication_exempt` and is exactly the kind of endpoint a container healthcheck would
naturally reach for.

**Root cause:** an upstream Caldera 5.0.0 app bug — the `/health` handler indexes into
`access[0]` to determine the caller's permission scope, and an anonymous (unauthenticated) caller
has an **empty** `access` tuple, so the handler itself raises `IndexError` before it can return a
health payload. This is unrelated to the GAP-016 `yarl` bug (a transport-layer failure on *every*
route); this is an application-layer bug specific to the `/health` route's own authorization logic.

**Missing capabilities:** none required to keep the current mitigation working — just discipline.
If this is ever to be fixed at the source rather than avoided: patch the `/health` handler upstream
(guard the `access[0]` index) or wait for a Caldera version where it's re-guarded (Caldera 5.3.0
re-guards `/health`, per the GAP-016 diagnosis, though it wasn't independently confirmed whether that
re-guard also fixes the anonymous-500 case or just changes the response).

**Impact:** none today — `o2/caldera/Dockerfile`'s build-time smoke gate (added for GAP-016) probes
`GET /`, not `/health`, specifically because of this bug, and that choice is called out in code
comments. The risk is purely a **future regression**: anyone "cleaning up" the probe to the more
semantically obvious `/health` route will reintroduce a false-500 on every healthcheck/build.

**Related:** `o2/caldera/Dockerfile` (build smoke gate probes `GET /`, comment references this),
GAP-016 (the `yarl` bug this was found alongside; do not conflate the two), LES-075 (the smoke-gate
mechanics lesson — this GAP is the reason it explicitly calls out "probe `/`, not `/health`"). No
fix tracked — mitigation is considered sufficient; promote to a real fix only if the
compose healthcheck (still using the internal `/system/ready` check, per GAP-016) is ever
strengthened to hit an HTTP route and someone reaches for `/health` by habit.

---

## GAP-020 — Shared `export.ship: loki` bootstrap self-killed its own wrapper and lost the shipper to QGA session reaping

**Status:** ✅ Resolved (code + live-verified) — 2026-07-15 (`axrange/config_exec/render.py`,
`docs/WORK_ORDER_measurement.md` §3).
**Severity:** High (the never-before-live-verified `export.ship: loki` path (sensors design Phase
C2) and the A4 truth-marker shipper share this bootstrap — both were silently non-functional: the
shipper process never actually stayed running after the bootstrap that was supposed to launch it)
**Discovered:** 2026-07-15 — the A4 gated live-verify (`docs/WORK_ORDER_measurement.md` §3), first
real exercise of the `export.ship: loki` code path end-to-end.

**Symptom:** two independent, compounding bugs in the shared `_render_loki_tail_shipper` bootstrap
(`axrange/config_exec/render.py`), used by both the sensor Loki shipper (`render_sensor_loki_shipper`,
sensors Phase C2) and the activity truth-marker shipper (`render_activity_truth_shipper`, A4):
(1) the bootstrap opened with `pkill -f '<pkill_pattern>'` where `pkill_pattern` (e.g.
`ship-<id>.py`) is a **literal substring of the delivering wrapper script's own filename**
(`ConfigBackend._deploy_sensor` writes the bootstrap to `sensor_shipper_path(id) + ".sh"`, i.e.
`.../ship-<id>.py.sh` — `.py` matches as a substring of `.py.sh`) — so the bootstrap's own `pkill`
step SIGTERMed itself before it ever wrote or launched the actual shipper; (2) even past that, the
shipper was launched with a bare `nohup python3 <path> >log 2>&1 &`, which is **reaped when the
qemu-guest-agent exec session that delivered the command ends** — proven live: only a detached,
named systemd transient unit survived past the QGA call that started it, a plain backgrounded
`nohup` did not.

**Root cause:** the bootstrap was authored and unit-tested for its rendered TEXT shape, but never
executed end-to-end over the real QGA delivery path until this live-verify — a case of LES-077's
"authored but never actually run" class applying to in-guest delivery scripts, not just
Dockerfiles/compose stacks. `pkill_pattern` was derived from `shipper_path` without checking
whether any *other* path in the same delivery flow (the `.sh` wrapper) also contains it as a
substring; the `nohup &` backgrounding assumption (works fine over an interactive SSH session)
does not hold over an execute-and-disconnect channel like `ga.exec`.

**Fix applied:** `_render_loki_tail_shipper` no longer carries any `pkill` — it takes a `unit:` name
parameter instead of `pkill_pattern:` and launches via a detached, deterministically-named
`systemd-run --unit=<unit> --collect python3 <shipper_path> ...` transient (with a
`|| (setsid python3 <shipper_path> </dev/null ... &)` fallback for a guest with no systemd, none of
our current images lack it). Idempotent restart on redeploy is `systemctl stop <unit>` +
`systemctl reset-failed <unit>` ahead of the new launch (both harmless no-ops if the unit doesn't
exist yet) — no pkill anywhere in the path. `unit` is derived stably per sensor/activity id
(`axr-ship-<sensor>` / `axr-truth-ship-<activity>`) so it's stable across redeploys of the same
shipper and unique across different ones. 5 new regression tests
(`tests/test_activity_exec.py`, `tests/test_sensor_exec.py`) assert no `pkill`/`nohup` in the
rendered output and that the systemd unit name is stable+unique. **Live re-proven:** `deploy-sensor`
now auto-starts the shipper and `systemctl is-active <unit>` reports `active`.

**Impact:** both `export.ship: loki` shippers (sensor + truth-marker) now actually run and survive
past their QGA delivery session — closes the last blocker on A4's Loki-based coverage join.

**Related:** `axrange/config_exec/render.py` (`_render_loki_tail_shipper`, `render_sensor_loki_shipper`,
`render_activity_truth_shipper`), LES-051 (a related but distinct self-kill class — that one is the
invoking shell's own argv containing the pattern; this one is a *different file on disk* whose name
happens to be a superstring of the pattern), LES-078/LES-079 (the two lessons this finding produced),
`docs/WORK_ORDER_measurement.md` §3 (PASSED — 2026-07-15).

---

## GAP-021 — TE-host truth-marker shipping requires TE egress; a TE may legitimately have none

**Status:** 🟡 Resolved-for-verify (open design question) — 2026-07-15.
**Severity:** Medium (a correctly air-gapped/no-router TE with `overlays.adversary`/scheduled
activity cannot ship its `[axr-truth]` markers to the O2 Loki at all, silently losing A4 coverage
for that run — no error, just an empty truth stream)
**Discovered:** 2026-07-15 — the A4 gated live-verify (`docs/WORK_ORDER_measurement.md` §3).

**Symptom:** the activity truth-marker shipper (`render_activity_truth_shipper`, A4) runs **on the
TE host** and pushes to the O2-services VM's Loki over the TE's own network egress. The verify
scenario's TE host initially had no egress (no `kind: router`/SNAT), so the shipper could not reach
Loki at all — the truth stream never arrived, silently (no adapter-level failure; the push attempts
just failed/timed out inside the guest, invisible to the deploy's op-level success/failure).

**Root cause:** the design places the truth shipper on the TE host itself (closest to the activity
runner emitting the markers), which couples "can this TE ship its truth markers" to "does this TE
have egress" — two orthogonal concerns. A TE may be deliberately egress-isolated (an air-gapped
segment, an OT/ICS conduit test) while still running scheduled/scored activity that A4 should be
able to measure.

**Fix applied (for this verify only):** the verify scenario's network was given `egress: true`
(Simple-zone SNAT) so the TE host could reach the O2 Loki. This unblocks the specific live-verify
run but does not change the underlying coupling.

**Open design question (not decided here):** should truth markers instead route via the
transport/sensor plane (which is always present and already reaches O2, per the A2 tap-playout
design) rather than requiring the TE host itself to have egress? That would decouple A4
measurement from a TE's own egress posture, at the cost of routing the truth stream through a
different, less direct path. Recorded for whoever next touches the A4 shipper placement — not a
decision made by this pass.

**Impact:** any egress-isolated TE that wants A4 coverage must currently add TE-host egress (a real,
if incidental, requirement on the TE's own network design) — worth calling out to whoever authors a
deliberately isolated TE and still wants attack-coverage scoring.

**Related:** `axrange/config_exec/render.py` (`render_activity_truth_shipper`),
`docs/design/08-exercise-measurement.md`, `docs/WORK_ORDER_measurement.md` §3 (PASSED — 2026-07-15,
with this caveat), `docs/SLICE_ROUTER.md` (egress/NAT model). No fix tracked as a code change —
open design question only.

---

## GAP-022 — No mechanism auto-adds a TE host to Prometheus's static scrape targets (A3 exporter half descoped)

**Status:** 🔴 Open — descoped from A3's live-verify, not a design decision to leave unfixed.
**Severity:** Low (the Prometheus exporter→server pair is the one of A3's three agent→server pairs
that was NOT exercised in the 2026-07-15 live-verify; Sandcat→Caldera and Wazuh-agent→manager both
passed)
**Discovered:** 2026-07-15 — the A3 gated live-verify (`docs/WORK_ORDER_attack_defense.md` §4).

**Symptom:** `o2/prometheus/prometheus.yml`'s scrape targets are a **static list** — nothing in the
engine or `o2control` writes a newly-deployed TE host's exporter address into it. A3's default-ON
node-exporter agent (`scenario.default_agents`) installs and runs the exporter on every TE host, but
Prometheus never learns to scrape it unless an operator hand-edits the compose config and restarts
Prometheus.

**Root cause:** no service-discovery mechanism (file-based SD, Consul, etc.) wires a TE deploy's
host inventory into the standing Prometheus config; the static-target model this stack ships with
(fine for the fixed sensor/capture hosts) doesn't extend to the dynamically-varying set of TE hosts
across deploys.

**Missing capability:** a Prometheus file-based service-discovery target file that `o2control` (or
the engine, on deploy) writes/updates per live TE deploy, so a TE host's exporter is scraped
automatically without an operator touching the compose stack.

**Impact:** the exporter→Prometheus half of A3 remains unverified end-to-end (the exporter itself
runs and is reachable; scraping it into the standing Prometheus is the missing wiring). Recorded as
a follow-on, not blocking anything else in the A3/A4 arc.

**Related:** `o2/prometheus/prometheus.yml`, `docs/design/08-exercise-measurement.md` (the three
agent→server pairs), `docs/WORK_ORDER_attack_defense.md` §4 (PASSED with this pair descoped).

---

## GAP-023 — `delete_mirror_snippets` always reports "removed", never "absent", on an idempotent re-destroy

**Status:** 🔴 Open
**Severity:** Low (cosmetic idempotency imperfection — teardown is functionally clean, no leak, no
error; only the reported action is misleading on a re-run — same class as GAP-013, different code
path)
**Discovered:** 2026-07-16 — the dev45 CLI-lifecycle smoke (`te deploy → te info → te status → te
destroy → te destroy` on `deploy-dev45/scenarios/blast-check.yaml`).

**Symptom:** on a second (idempotent) `te destroy` of an already-destroyed deploy, the `"mirror"`
teardown action for the per-VM snippet files (`axrange-mirror-<vmid>.sh`,
`axr-userdata-<vmid>.yaml`) reports `removed` again, unlike every sibling teardown step (gretap,
vnet, zone, ovs-bridge) which check presence first and report `absent` on the second run. The
overall result is still a clean idempotent destroy — no leak, no error — so this is purely a
reporting-shape wrinkle.

**Root cause:** `ProxmoxTeardownBackend.delete_mirror_snippets`
(`axrange/adapter_proxmox/teardown.py` ~line 72) issues an unconditional `rm -f
/var/lib/vz/snippets/axrange-mirror-<vmid>.sh axr-userdata-<vmid>.yaml` and unconditionally returns
`"removed"` — `rm -f` is itself idempotent (no error if the files are already gone), but the
*return value* doesn't reflect whether anything was actually present to remove, unlike
`delete_gretap`/`delete_vnet`/etc., which do a presence pre-check (`ip -o link show` / equivalent)
before deciding `"removed"` vs `"absent"`.

**Missing capability:** a presence pre-check (e.g. `test -e <path>` before the `rm -f`, or check the
`rm -f` exit against a `find`) so a re-destroy reports `absent` (and counts 0 removed) for
already-gone snippet files, matching the idempotency-reporting shape of the other teardown steps.

**Impact:** none functional. The only cost is a misleading `removed` line on a re-run, which could
confuse an operator reading teardown output as a source of truth for what was actually present.

**Related:** `axrange/planner/teardown.py` (`delete_mirror_snippets` call site, step 2 "Mirror plane
— the per-VM hook + cloud-init snippets"), `axrange/adapter_proxmox/teardown.py`
(`delete_mirror_snippets`), **GAP-013** (the same unconditional-vs-check-then-act reporting class,
on the OVS mirror object itself rather than its snippet files — open at the same time, distinct code
path), `docs/WORK_ORDER_cli_lifecycle.md` (the smoke that surfaced this).

---

## GAP-024 — `--images` pointed at the `images/` subdirectory (instead of its parent root) silently yields an empty manifest registry + a false `REF-120` validate failure

**Status:** 🔴 Open
**Severity:** Low (operator footgun — a wrong-but-plausible flag value fails loud enough to notice,
but the error message doesn't point at the actual mistake)
**Discovered:** 2026-07-16 — the dev45 CLI-lifecycle smoke; passing `--images
deploy-dev45/images` (the deeper path, which *looks* like the natural thing to point at) instead of
the root `--images deploy-dev45` produced a `REF-120` "manifest not found" validate failure against
a scenario whose image manifests genuinely exist on disk.

**Symptom:** `axrange te validate/plan/deploy --images <root>` expects `<root>` to be the directory
*containing* `images/manifests/` and `images/bindings/<platform>/` — not the `images/` directory
itself. Pointing `--images` at the `images/` subdirectory makes the loader look for
`<root>/images/images/manifests/`, which never exists, so `axrange/manifest/loader.load_registry`
silently returns an **empty registry** (by design — `docs/PRODUCT_GAPS.md` intentionally tolerates a
missing manifest dir for dev/test contexts, per its own docstring) and the validator's `REF-120`
then fires a normal, correctly-shaped "manifest not found for image X" failure — indistinguishable
from "the manifest genuinely doesn't exist," even though the real cause is a one-level-too-deep
`--images` path.

**Root cause:** `load_registry`'s "missing directory → empty registry, no error" tolerance
(intentional, for dev/test ergonomics) means a *systematically* wrong `--images` root produces
exactly the same downstream symptom as a *correctly* pointed root whose manifests are actually
absent — there's no distinguishing diagnostic between "no images here" and "you pointed one level
too deep."

**Missing capability:** a startup-time sanity check/warning when `--images <root>` resolves to zero
manifests AND zero bindings AND `<root>/images` (i.e. `<root>` one level up) DOES contain a
`manifests/`/`bindings/` pair — a strong signal the operator meant the parent. Short of that, the
`--images` help text (`axrange/cli/main.py`, all 8 `p.add_argument("--images", ...)` sites) could be
more explicit that the expected value is the directory *above* `images/`, not `images/` itself.

**Impact:** wastes operator time chasing a false "manifest missing" REF-120 failure when the real
issue is a one-directory-level flag mistake; no data loss or functional gap, purely a diagnosability
footgun.

**Related:** `axrange/manifest/loader.py` (`load_registry`, the intentional missing-dir tolerance),
`axrange/cli/main.py` (all `--images` argument definitions), `docs/VALIDATOR.md` (REF-120),
`docs/WORK_ORDER_cli_lifecycle.md` (the smoke that surfaced this).

---

## GAP-025 — Standing O2 sensor's `deploy-sensor` render launched tcpdump/zeek via `nohup … &`, reaped by QGA session end; sensor silently captured nothing

**Status:** ✅ Resolved (code + unit-verified) — 2026-07-17 (`axrange/config_exec/render.py`).
Dev45 live-verify still **pending**.
**Severity:** High (the standing O2 `tcpdump-sensor`/`zeek-sensor` — the data spine every A2/A3/A4
observability plane assumes is actually recording — silently captured nothing at all; no error
anywhere in the deploy op's result).
**Discovered:** 2026-07-17 — a dev45 investigation into why the standing O2 sensor's pcap directory
stayed empty after a normal `axrange o2 deploy` + `axrange tap deploy`.

**Symptom:** `deploy-sensor`'s in-guest render (`render_sensor_setup`, `axrange/config_exec/render.py`)
launched tcpdump and zeek with a bare background `nohup <tool> … >log 2>&1 &`. The launching QGA
`exec` call returned success (the `nohup` command itself started fine), and the op reported COMPLETED
— but the daemon was a child of the QGA exec session and was reaped the moment that session ended, so
moments after the deploy finished, nothing was actually capturing. `verify-capture` (which reads the
wire live via a separate mechanism, not the daemon this bug killed) did not catch it because it
exercises a different code path.

**Root cause:** the exact same failure class as **GAP-020** (the shared `export.ship: loki` bootstrap)
— a process backgrounded with `nohup … &` over a qemu-guest-agent `exec` session is reaped when that
session's process group goes away; `nohup` only blocks SIGHUP, which is not how QGA tears a session
down. GAP-020's fix (a detached, named `systemd-run --unit= --collect` transient) was applied to the
Loki shipper at the time but never ported to `render_sensor_setup`'s tcpdump/zeek branches, which kept
the old `nohup` pattern (suricata was unaffected — it self-daemonizes via `-D`).

**Fix applied:** tcpdump and zeek now launch via `_systemd_daemon_start_block` — a detached,
deterministically-named transient systemd unit (`systemd-run --unit=axr-sensor-<safe> --collect`)
with a fail-loud `systemctl is-active --quiet` start check that dumps the unit's journal + log on
failure. `--collect` makes a re-`o2 up`'s restart idempotent with no `pkill`+reap race (there is no
`pkill` in the new path at all). Paired with a retention overhaul (accumulating timestamped pcaps +
an in-guest disk-%/file-count prune watchdog, ported from `o2/tcpdump-sensor/bin/capture-watchdog`)
so capture history now also survives a tap removal or sensor restart, which the old `-W 50` cyclic
ring did not. Two review-caught bugs fixed in the same pass — see `docs/LESSONS.md` LES-085
(prune glob missing `-C`-rotated `.pcapN` files) and LES-086 (the general QGA-reaping pattern).
5-ish new/updated regression tests in `tests/test_sensor_exec.py` + `tests/test_adapter_proxmox.py`;
full suite 904 pytest tests, green.

**Impact:** closes a silent, total capture-plane failure for the two most commonly deployed standing
O2 sensor types. **Still pending: a dev45 live-verify** — deploy a tapped TE, confirm capture
actually runs under systemd (`systemctl is-active axr-sensor-<safe>`), survives a sensor VM
restart/re-`o2 up`, and that prior pcaps persist across an `o2 tap destroy`.

**Related:** `axrange/config_exec/render.py` (`_systemd_daemon_start_block`,
`_render_capture_pcap_watchdog`, `render_sensor_setup`), GAP-020 (the same failure class, first found
on the Loki shipper — this closes it for the last two sensor types), `docs/LESSONS.md` LES-085/
LES-086, `docs/CHANGELOG.md` 2026-07-17 "Standing O2 sensor capture supervision + retention fix".

## GAP-026 — Config-declared EXTERNAL sensor (XPress-SW) tap path — reachability, declaration, and end-to-end capture-through-sink all live-verified on dev45

**Status:** ✅ Resolved / Closed (2026-07-21, live-verified end-to-end on dev45). Nothing left open on
this gap.
**Severity (at close):** none — was Medium at discovery, narrowed to Low once reachability landed,
now fully closed.
**Discovered:** 2026-07-21 — the dev45 live-verify attempt for the new config-declared
`EXTERNAL_SENSORS` tap-target feature (`docs/CHANGELOG.md` 2026-07-21, `docs/design/06-taps-sensors.md`). Closed the same day after two follow-up investigations (reachability, then end-to-end capture).

**Resolved in stages, all 2026-07-21:**
- **Reachability + declaration.** The operator granted **SSH access** to XPress-SW
  (`ssh -i ~/.ssh/id_ed25519 fxadmin@10.80.72.40`, its management IP on `ens18`, passwordless-sudo) —
  a working manual config/verify channel into the node. XPress-SW is **Rocky Linux 10.2**,
  NetworkManager-managed — not a black box. The hot-plugged capture NIC (`qm set 103 -net1
  virtio,bridge=o2v7dc98`) enumerated fine with **no reboot needed** (came up as `ens19`, link-UP, MAC
  already learned in the PVE bridge FDB) — the actual root cause of the original "unreachable"
  symptom was NetworkManager auto-creating a DHCP profile for the new NIC that sat stuck `activating`
  (no DHCP server on the isolated transport net), leaving it IP-less; fixed with a static NM profile
  (`ipv4.method manual` + `ipv4.addresses 10.255.251.20/24` + `ipv4.never-default yes`). See
  `docs/LESSONS.md` LES-092 for the diagnostic pattern. `ens19` now holds `10.255.251.20/24`
  persistently and answers/ARP-resolves from the transport segment (`o2v7dc98`); `adapter.conf`
  declares it (`EXTERNAL_SENSORS=[{"id":"XPress-SW","vmid":103,"transport_ip":"10.255.251.20",
  "type":"packetxpress","capture_iface":"gre-cap"}]`), validated to parse and auto-protect XPress-SW
  by both name and vmid 103.
- **End-to-end capture-through-sink — PROVEN.** A `tap-verify` TE (`tap-verify-09a5090a`, 23/23 ops —
  only `record-tap-intent` emitted, confirming the sensor-agnostic-te substrate) was deployed, then a
  tap-plan binding `h-a → [XPress-SW]` was applied (`tap deploy` → `tap-tap-verify-09a5090a-4bb7e9ab`,
  completed). The loud `[warn] capture UNVERIFIED for external sensor 'XPress-SW' ...` warning
  **surfaced as designed** (LES-089 confirmed live, not just unit-tested). Out-of-band verification via
  SSH — `tcpdump -i gre-cap` on XPress-SW — caught **44 decapsulated frames** while 20 pings ran on
  the tapped host: 20 matched ICMP echo request/reply pairs (`h-a 10.62.0.10` ↔ `h-b 10.62.0.11`) plus
  corp-net ARP. The mirror path (tap → keyed gretap → XPress-SW's `gre-cap` decap) works end-to-end
  through a real, config-declared external sink.
- **Teardown protection — POSITIVELY confirmed under a real gated destroy**, not just "no
  guard-violation logged": `tap destroy` (1 resource) then `te destroy tap-verify-09a5090a` (7
  resources) both reported DESTROYED cleanly, and XPress-SW (vmid 103) was inspected afterward and
  found completely untouched — still `running`, `ens19` still at `10.255.251.20`, `gre-cap` still up
  (packetXpress's xdp decap active). Confirmed by inspecting the `op_attempts` table and `qm` directly
  (not merely the absence of a warning): a config-declared external sink is never even a member of the
  teardown's resource set, so there was nothing for the protect-guard to have to refuse in this run —
  the auto-protection exists as defense-in-depth for a future path that might wrongly enumerate it.
  The standing O2 surface was also left untouched.

**Impact:** the config-declared-external-sensor feature — teardown protection (both the guard logic
and its exercised absence-of-need), tap-target resolution, the loud capture-UNVERIFIED warning, and
the mirror path itself — is now fully proven on real hardware against a real, reachable, previously
hand-provisioned appliance, from cold declare through tap-apply through mirrored-traffic capture
through clean teardown. No further verification work is outstanding for this feature.

**Related:** `docs/CHANGELOG.md` 2026-07-21 "external sink nodes: teardown protection + config-declared
tap target (XPress-SW)", `docs/design/06-taps-sensors.md`, `docs/LESSONS.md` LES-089/LES-092,
`axrange/o2control/taps.py` (`merge_external_sensors`, `_compose_ops`).

---

## GAP-027 — Published twin inventory's `hosts[].role` is always `None` — no persona-typed `role:` field exists yet

**Status:** ✅ Resolved (`axrange/ir/schema.py`'s `Node.role`, `docs/WORK_ORDER_traffic_tier.md` Part 1,
CHANGELOG 2026-07-24 "`role:` IR field + `scenario_schema_version` 1→2"). A real, persona-typed
`role:` node field now exists (Authority: user — a version-locked `IR_SCHEMA` 1→2 bump); `HostInfo.role`
in the published twin inventory sources from `clone-vm`'s new `node_role` input (unconditional op, so
an untapped twin still publishes a real roster) instead of always `None`. Unit/suite-verified (1370
passed / 12 skipped / 0 xfailed, 1382 collected); the Traffic tier's own gated `dev45` live-verify
(`docs/WORK_ORDER_traffic_tier.md` §4) exercises this end-to-end and is still **pending**. A twin
config's `nodes[].role` (`docs/WORK_ORDER_twin_configs.md`) propagates through the expander for free.

**Original entry (for the trail), pre-fix:**

**Status:** 🔴 Open (expected — a downstream work order closes it)
**Severity:** Low (a documented, non-fabricated gap in a brand-new contract, not a regression)
**Discovered:** 2026-07-23 — building `axrange/planner/inventory.py` (`docs/WORK_ORDER_tier_split.md`).

**Symptom:** every `HostInfo` returned by `axrange te inventory` / `GET /api/deploys/{id}/inventory`
carries `role: null`, for every host, on every twin, with no exceptions.

**Root cause:** the design brief's `twin_inventory` contract (`docs/design/02-twin-model.md`)
publishes `hosts[].role` as the persona-assignable slot the future Traffic tier binds personas onto
(`corp-workstation`, `domain-controller`, …) — but no IR field carrying that value exists yet. An
earlier revision of `build_twin_inventory` derived a stand-in from `node.kind` (`_coarse_host_role()`,
since removed): it read `"host"` for nearly every node, a constant-almost-everywhere value that is a
null signal masquerading as a populated field — worse than an honest `None`. Fabricating a plausible-
looking role would be exactly the kind of silent-success the published contract must not produce.

**Missing capability:** a real `role:` IR field on `IdentityEntry`/node definitions, persona-typed,
that `build_twin_inventory` can read directly instead of deriving anything.

**Impact:** none yet — no consumer of `hosts[].role` exists (the Traffic tier that would bind personas
onto it isn't built). Tracked so `persona_library`/`traffic_tier` don't have to rediscover this gap;
closes automatically once either work order lands a real `role:` field and threads it through
`build_twin_inventory`.

**Related:** `axrange/planner/inventory.py` (module docstring's `role` bullet),
`docs/design/02-twin-model.md`, `docs/WORK_ORDER_persona_library.md`,
`docs/WORK_ORDER_twin_configs.md`.

---

## GAP-028 — `tap_points[].id` is a mechanical `host:net:nic` interface id, not the design brief's semantic, author-declared tap-point namespace

**Status:** ✅ Resolved (`axrange/planner/tappoints.py`, `docs/WORK_ORDER_tap_points.md`, CHANGELOG
2026-07-24 "Tap points BUILT") — **mechanism superseded 2026-07-28, gap stays closed under a
stronger fix.** The original fix (below, kept for the trail) grouped `build_twin_inventory`'s
published mechanical `host:net:nic` ids into a named, topologically-scoped `TapPoint` catalog
(`segment`/`boundary`/`host`/`leg`). **`docs/WORK_ORDER_tap_eligibility.md` (Authority: user) then
removed that catalog entirely** — no more published `tap_points[]`, no more `id`/`labels[]` — in
favor of full-topology eligibility: every interface a twin publishes is now unconditionally
capture-eligible, so a tap-point *name* was never really the fix this gap needed — mechanical
addressing needing no hand-flagging at all was. A `tap_point:` id still resolves, now computed on
demand at bind time against the twin's full topology rather than looked up in a published catalog
(`docs/design/06-taps-sensors.md`). Gated `dev45` live-verify **PASSED 2026-07-28** — a freshly
deployed twin (LES-096) confirmed `tap_point:`/`selector:` addressing resolves correctly against
full topology (`docs/WORK_ORDER_tap_eligibility.md`, CHANGELOG 2026-07-28 "Live-verify (dev45,
2026-07-28)").

**Original entry (for the trail), pre-fix:**

**Status:** 🔴 Open (expected — `tap_points` addresses it)
**Severity:** Low (a naming-layer gap, not a correctness bug — the id is stable and unique, just not
semantic)
**Discovered:** 2026-07-23 — building `axrange/planner/inventory.py` (`docs/WORK_ORDER_tier_split.md`).

**Symptom:** every `TapPointInfo.id` published by `axrange te inventory` looks like `ws01:corp-lan:0`
(host, net_id, nic index) — a mechanically-derived, redeploy-stable interface id — never a semantic,
author-declared place name like the design brief's `corp-lan`/`idmz-conduit` examples
(`docs/design/02-twin-model.md`), which name a PLACE that aggregates several
interfaces (e.g. every host leg on a segment, or the router legs joining two segments).

**Root cause:** `build_twin_inventory` derives `tap_points[]` straight off `record-tap-intent`'s
per-(host, nic) `tapped_nics` entries — there is no grouping/naming layer above that yet. The `id`
it publishes is a fine, STABLE join key (redeploy-stable, collision-free) but callers must not assume
it IS the brief's semantic tap-point namespace.

**Missing capability:** a semantic grouping/naming layer that maps one-or-more interface ids onto an
author-declared tap-point name, scoped topologically (`segment` / `boundary` / `host` / `leg` per
brief §4.1) — `docs/WORK_ORDER_tap_points.md`'s job. **Note the model changed 2026-07-23
(Authority: user):** the grouping axis is topological, NOT the directional `EW`/`NS` taxonomy the
original `tap_directional` order proposed; direction survives only as an optional `labels[]` value.

**Impact:** none yet — no consumer assumes semantic tap-point names today (A2's tap-plan playout
already consumes the mechanical id and works correctly against it). Tracked so `tap_points`
doesn't have to rediscover this gap, and so no future caller of `build_twin_inventory` mistakenly
treats today's `id` as already being the brief's namespace.

**Related:** `axrange/planner/inventory.py` (module docstring's "Tap-point id/location namespace"
note), `docs/design/02-twin-model.md`, `docs/WORK_ORDER_tap_points.md`.

---

## GAP-029 — Two boundary shapes are inexpressible as `boundary` tap points: WAN-egress and VXLAN-bridged segment pairs

**Status:** ✅ Resolved (**WAN-egress half live-verified on dev45 2026-07-28** — `perimeter: wan`
resolved the router uplink and captured real egress on a fresh twin) — VXLAN-bridged-pair half
still 🔴 Open. `docs/design/06-taps-sensors.md` /
`docs/WORK_ORDER_tap_scopes.md` (BUILT 2026-07-27, CHANGELOG "Tap scopes BUILT") add a
**`perimeter`** scope and promote the router's WAN uplink into the published inventory: the data
was never missing — `pass1_emit.py:869-882` already emits an `attach-vm-to-vnet` op for it
(`net_id: "__wan__"`, real `nic_index`, `vmbr0`) and `inventory.py:344-345` was simply discarding
it. `perimeter: wan` (tx & rx on the uplink) is the spelling; no adapter change was needed since the
uplink is `tap<vmid>i<nic>` like any other port. **Resolved via** the brief's §9 open decision (2)
option (b): `record-tap-intent` now always marks a router's WAN port tappable (the minimal,
reversible unblock — the larger structural move of tap eligibility from the twin IR to the tap plan
is `docs/WORK_ORDER_tap_eligibility.md`, deliberately left OPEN and user-gated, not folded into this
build). **Caveat (LES-096):** `perimeter: wan` can only resolve on a twin **deployed fresh after
2026-07-27** — an already-`completed` `record-tap-intent` op on an older deploy never republishes
the new WAN-tappable input, so a twin deployed before this change needs a `--fresh` redeploy, not a
resume, before `perimeter: wan` will work against it. The gated `dev45` live-verify **PASSED
2026-07-28** for exactly this reason — a freshly deployed twin (`tap-elig-verify`, not the standing
`corp-enterprise` one) confirmed `perimeter: wan` resolves the WAN uplink and captures real egress
(CHANGELOG 2026-07-28 "Live-verify (dev45, 2026-07-28)").
The **VXLAN half is untouched** — `boundary` and `perimeter` both key off a joining host,
and a spine-mediated pair has none.
*(Original status: 🔴 Open — a real gap an operator will hit, not a corner case — one of the two is
this brief's own worked example)*
**Severity:** Low-Medium (no workaround for the WAN-egress case beyond an author manually declaring
a `leg`-scoped point on the router's LAN-side interface nearest the boundary; VXLAN pairs have no
single-host workaround at all — there is no host to attach `tap:` to)
**Discovered:** 2026-07-24 — building `docs/WORK_ORDER_tap_points.md` (`axrange/planner/tappoints.py`).

**Symptom:** `derive_default_tap_points` cannot produce a `boundary` tap point for either of two real
topology shapes:

1. **A segment↔WAN boundary** — `docs/design/02-twin-model.md`'s own `corp-enterprise`
   catalog entry names `wan-egress` (a `boundary` point "at the router's WAN leg") as one of its
   four tap points, and `dmz-perimeter` names `perimeter` (internet↔dmz) the same way. Neither is
   derivable: `__wan__` is excluded from `TwinInventory.hosts[].nets[]` entirely (`inventory.py`'s
   own docstring — a router's synthetic WAN uplink isn't a twin segment), and today's `tap:` IR
   extension has no `NetRef` shape for that synthetic uplink to carry `tap: true` on. There is **no
   signal at all** here — not even the actionable `TapPointResolutionError` an untapped-but-real
   boundary now gets (GAP-028's fix) — because the point can't be *constructed* in the first place.
2. **A VXLAN-bridged segment pair with no single joining host** — `fabric-verify`'s `va`↔`vb` (two
   tenant access segments joined only through the underlay fabric — leaf-1/leaf-2/border-1 route
   between them, but no *single* host has a leg on both `va` and `vb` directly). `boundary`
   derivation keys off "a host with legs on both segments" (`_host_legs`/`derive_default_tap_points`
   in `axrange/planner/tappoints.py`) — a fabric-mediated pair with no such host is undiscoverable by
   that heuristic, even though `border-1`'s `vbl`↔`wan-core` boundary (a DIFFERENT pair, one hop
   over) derives and resolves correctly today (proof that the mechanism works when a joining host
   exists — this is specifically the no-joining-host case).

**Root cause:** both are hosts-and-legs derivation gaps, not resolution bugs — `resolve_tap_point`
would work fine given a well-formed `TapPoint` for either shape; there's simply no data
`derive_default_tap_points` can construct one from. Case 1 needs a `NetRef` for the WAN uplink in
the `tap:` IR extension (a real, if narrow, IR extension); case 2 has no host-centric analog at all
— a VXLAN-bridged pair's "boundary" is a property of the fabric's forwarding topology, not any one
host's interface list.

**Impact:** an operator following the brief's own `corp-enterprise`/`dmz-perimeter` catalog examples
today cannot tap the boundary those configs literally name (`wan-egress`, `perimeter`) — they can
only fall back to tapping the router's LAN-side `leg` nearest the WAN uplink (partial: captures
everything crossing that leg, including intra-LAN traffic transiting the router, not a clean
WAN-only view) or a `segment`-scoped point on the segment nearest the boundary. Not blocking (every
archetype validates and deploys fine; capture just can't be scoped this precisely yet), but a real
operator surprise the moment they try to realize the brief's own headline `wan-egress` example.

**Missing capability:** a WAN-leg-addressable `NetRef` shape in the `tap:` IR extension (case 1); a
fabric/forwarding-topology-aware `boundary` derivation that doesn't require a single joining host
(case 2) — likely `twin_configs`' declarative `tap_points:` block territory (an author names the
boundary explicitly, sidestepping auto-derivation's host-centric heuristic entirely) rather than a
further `tappoints.py` heuristic.

**Related:** `axrange/planner/tappoints.py` (module + `derive_default_tap_points` docstrings, "WAN-
adjacent boundary points are a SEPARATE, harder gap"), `docs/design/02-twin-model.md`,
`target-environments/fabric-verify/scenario.yaml` (`border-1`'s `tap: true`, the WORKING case one hop
over from the VXLAN-pair gap), `docs/WORK_ORDER_twin_configs.md`.

---

## GAP-030 — A twin config's declared `tap_points[].id`/`labels`/`scope` don't reach the IR/inventory — label-based selection is inert

**Status:** ✅ Dissolved 2026-07-28 (`docs/WORK_ORDER_tap_eligibility.md`, Authority: user) — **not
fixed, made moot.** The twin-config `tap_points:` block, `TwinConfigTapPoint`, `_apply_tap_points`,
and the `id`/`labels[]`/`default_sensor` fields this gap tracked as lossy are all REMOVED outright
(D3) — there is no more label field anywhere in a twin config or the IR for a tap plan to select
on, so "label-based selection is inert" stopped being a fixable statement; it no longer exists to
be inert. A stale `tap_points:` key in a twin config now errors loudly instead of silently dropping
the metadata this gap described. `id`/`scope`/`target`-based selection (never broken, per the
original entry below) survives via the tap plan's own `selector:`/`tap_point:` addressing, now
resolved directly against the twin's full topology rather than a twin-config-sourced catalog — see
`docs/design/06-taps-sensors.md`.

**Original entry (for the trail), pre-dissolution:**

**Status:** 🔴 Open (explicitly deferred, not accidental — see the twin_configs build's own
CHANGELOG entry)
**Severity:** Low-Medium (no functional loss for `id`/`scope`/`target`-based selection, which works
fine; only the free-form `labels[]` selection §4.1 markets — "bind everything labelled `ns` to
sensor-b" — is unusable for a config-sourced twin)
**Discovered:** 2026-07-24 — building `docs/WORK_ORDER_twin_configs.md`
(`axrange/twinconfig/expand.py`), anticipated in general terms by GAP-029's own closing paragraph
("likely `twin_configs`' declarative `tap_points:` block territory").

**Symptom:** a twin config's `tap_points: [{ id, scope, target, labels, default_sensor }]` (brief
§6.1) is a real, typed field (`TwinConfigTapPoint`) and its materialization is real —
`_apply_tap_points` correctly sets the underlying IR `tap:` flag(s) for `segment`/`boundary`/
`host`/`leg` and raises `TapPointMaterializationError` at expansion time if a point can't be wired.
But once expansion is done, the **only** trace of that tap point left in the produced Scenario IR
is a boolean `tap: true` on a network/node/leg — the point's `id`, `labels[]`, and `default_sensor`
are consumed and then **dropped**. `TwinInventory`/`derive_default_tap_points`
(`axrange/planner/tappoints.py`, `docs/WORK_ORDER_tap_points.md`) then has to re-derive a
mechanical `host:net:nic`-shaped tap-point identity from the flags alone, with **no memory** of the
author's chosen `id`/`labels` — a tap-plan binding by `selector: {labels: [ns]}` (the headline
`docs/design/02-twin-model.md` use case) cannot match anything a `twin_configs`-sourced twin
publishes, because the labels the author wrote were never carried through.

**Root cause:** the version-locked Scenario IR (`docs/IR_SCHEMA.md`) has no `tap_points:`/label
field to carry this metadata in — `twin_configs`' own scope explicitly excluded an `IR_SCHEMA`
change (see that build's CHANGELOG entry), so the only channel available at expansion time is the
existing boolean `tap:` flag, which is label-free by construction. This is the twin_configs-specific
instance of the general limit GAP-028/GAP-029 already logged for hand-authored IR.

**Missing capability:** a version-locked `IR_SCHEMA` `tap_points:` field (or an out-of-band
sidecar the expander could also emit) that survives from a twin config's `tap_points[]` all the way
through to the published inventory, so `selector:`-based tap-plan binding actually works for
config-sourced twins. Natural owner: `traffic_tier` (the consumer that would actually select by
label) per this brief's own build-arc sequencing, or `tap_points`' own follow-on if it ships first.

**Impact:** every twin-config-sourced twin can still be tapped precisely by `id`/`scope`/`target`
(all real, resolvable identities) — only the free-form `labels[]` convenience selector is
unusable, which is a real but narrower loss than "capture can't be scoped" would be.

**Workaround (today):** bind tap plans to a config-sourced twin's tap points by `id` (each config's
`tap_points[].id` — e.g. `corp-lan`, `idmz-conduit` — is real and stable) rather than by `labels:`
selector.

**Partial closure (2026-07-25, `docs/WORK_ORDER_tap_target_resolution.md`):** a DIFFERENT, previously
conflated welding problem is now fixed — a tap plan's `target:` (which deployed twin to bind
against) was itself a literal, per-deploy-minted `te` deploy id (the LES-072 staleness trap: any IR
edit to the target twin mints a new deploy id and silently orphans every tap-plan file hardcoding
the old one), so a "predefined library plan" was never fully possible even for `id`/`scope`/
`target`-based selection. `target:` is now OPTIONAL and resolves a **scenario/twin-config NAME** to
the newest live `te` deploy of that scenario (`resolve_target_deploy`), plus a `--target`/API-body
override — a tap plan can travel with a twin-config by name instead of a deploy id. This closes the
*target*-welding half of "predefinable tap plans," NOT the *label*-selection half this gap entry is
actually about — GAP-030 stays 🔴 Open for `labels[]` selection specifically (still needs the
`IR_SCHEMA`/sidecar carry-through described above); see `docs/design/06-taps-sensors.md` for the
target-resolution writeup.

**Related:** `axrange/twinconfig/expand.py` (`_apply_tap_points`), `axrange/planner/tappoints.py`,
`docs/design/02-twin-model.md`, GAP-028, GAP-029, `docs/WORK_ORDER_twin_configs.md`,
`docs/WORK_ORDER_traffic_tier.md` (not yet written — the eventual consumer),
`docs/WORK_ORDER_tap_target_resolution.md` (partial closure — target-by-name, not label-selection).

---

## GAP-031 — No top-level `images/` catalog ships in the repo; `te validate/deploy --config <id>` has nothing to point `--images` at out of the box

**Status:** ✅ **Resolved (dev45-specific) — 2026-07-25.** The dev45 twin-model live-verify
(`docs/RUNBOOK_twin_model_live_verify.md` Stages 0–4, CHANGELOG 2026-07-25) confirmed dev45's
`deploy-dev45/images/` root now carries bindings resolving every image `corp-enterprise` needs:
`windows-server-2022` → template **101**, `win11-finance` → template **101** (a substitution — see
below), and `ubuntu-server-22` → a **620-based substitute** binding (also a substitution, not a
dedicated `ubuntu-server-22` template build). **This resolves the immediate blocker for `dev45`
specifically — it does NOT resolve the general gap.** No general-purpose, repo-committed `images/`
catalog with per-cluster-portable bindings exists yet; a fresh clone pointed at a *different* cluster
still hits this gap exactly as described below. Leaving the rest of this entry intact as the
general-case description.
**Severity:** Medium (blocks the `twin_configs` gated live-verify as written — both configs picked
for it, `sme-flat` and `corp-enterprise`, reference images no shipped manifest root actually has)
**Discovered:** 2026-07-24 — preparing `docs/WORK_ORDER_twin_configs.md`'s gated live-verify; the
repo has `tests/fixtures/images/` (test-only, minimal) and a cluster-specific
`deploy-dev45/images/manifests/` (only `debian-12-cloud` + `suricata-sensor`), but no top-level
`images/` an operator running `axrange te validate --config sme-flat` from a fresh clone can point
`--images` at.

**Symptom:** all five starter `twin-configs/*.yaml` reference images beyond `debian-12-cloud`
(`ubuntu-server-22@v5`, `win11-finance@v4`, `windows-server-2022@v1`, `vyos@1.5`) — none of which
have a manifest/binding under any committed, general-purpose images root. Running `axrange te
validate --config <id>` (or `plan`/`deploy`) from repo root with no `--images` override, or with
`--images deploy-dev45` (the one cluster-specific root that exists), fails `REF-120` ("manifest not
found") for every config except the narrowest slices of `sme-flat`, `ics-ot`, and `dmz-perimeter`
that happen to only touch `debian-12-cloud`/`ubuntu-server-22` — and `ubuntu-server-22` itself is
still missing everywhere. This is the same `REF-120` symptom GAP-024 already describes for a
*wrong* `--images` path; this gap is the case where the path is *right* but the catalog underneath
it is simply incomplete for the new twin-configs content.

**Root cause:** `target-environments/`'s archetypes were built and live-verified against
per-cluster image sets curated ad hoc (`deploy-dev45/images/`, hand-maintained per GAP-001); no
general-purpose, repo-committed images root was ever assembled that covers the full image set the
*shipped* content (`target-environments/` + now `twin-configs/`) collectively references. Each new
content set has quietly assumed "the operator's cluster already has what it needs" rather than the
repo shipping (or generating) a complete manifest/binding catalog.

**Missing capability:** either (a) a committed top-level `images/` manifest+binding catalog that
covers every image every shipped `target-environments/`/`twin-configs/` entry references (bindings
would still need per-cluster `template_vmid` values, but the manifests + binding *shape* could
ship), or (b) each twin-config's `README`/docs explicitly enumerating its image prerequisites so an
operator knows to `axrange image build` them before a first deploy, or (c) a `--check-only`/dry
preflight that reports every missing image across a whole config in one pass instead of failing
`REF-120` one at a time.

**Impact:** a fresh clone cannot `validate`/`plan`/`deploy` most of the five starter twin-configs
without first hand-building (`axrange image build <image>`) or hand-binding several images per
target cluster — the "clone the repo and deploy" experience GAP-001 already flags as unfinished
extends to the new twin-config catalog too.

**Workaround (today):** `axrange image build <image>` each referenced image on the target cluster
before validating/deploying a config that needs it (`ubuntu-server-22`, `win11-finance`/
`windows-server-2022`, `vyos` per `docs/SLICE_WINDOWS_AD.md`/`docs/SLICE_ROUTER.md`), or restrict a
first run to the subset of a config's nodes that resolve to already-built images.

**Related:** GAP-001 (the sibling per-cluster image-binding drift gap), GAP-024 (`--images` path
footgun), `docs/IMAGE_BUILD.md`, `docs/WORK_ORDER_twin_configs.md` (the gated live-verify this
blocks).

**dev45 substitution note (2026-07-25):** closing the immediate blocker used **substitute** bindings,
not dedicated per-image builds — `win11-finance` was bound to the `windows-server-2022` template
(vmid 101; a Windows-Server-flavored stand-in for a workstation-flavored image) and `ubuntu-server-22`
was bound to a 620-based substitute (the `debian-12-cloud` template lineage). Both are real,
bootable VMs that satisfied the live-verify's actual goal (stand up 32 hosts, publish roles, bind taps
and traffic), but neither is the *named* image the config declares — a future pass building the real
`win11-finance`/`ubuntu-server-22` templates should not assume dev45's bindings already match.

---

## GAP-032 — No shipped persona sets `service_name`/`process_match`; every `real-service`-drive assignment fails loud until a caller supplies the binding

**Status:** ✅ **Resolved — 2026-07-25** (`docs/WORK_ORDER_persona_library.md`, CHANGELOG 2026-07-25
"Twin-model arc: dev45 live-verify Stages 1–4 PASSED", fix #5). `axrange/personas/drivers/
real_service.py`'s Windows branch (`Get-Service`/`Get-Process`) plus catalog bindings on the 11
real-service personas close the missing-binding half of this gap; the Stage 4 live-verify drove a
real Windows `real-service` assignment (the DC) through `tfc deploy` successfully. Leaving the
original description below for the record — general per-image `service_name`/`process_match`
authoring guidance (item (b) below) remains a content nice-to-have, not a blocker.
**Severity:** Low-Medium (fail-loud, not silent — was blocking the highest-fidelity persona class
out of the box; now bound)
**Discovered:** 2026-07-24 — building `docs/WORK_ORDER_traffic_tier.md` (`axrange/o2control/traffic.py`).

**Symptom:** every persona in the §5.2 starter catalog whose `drive: real-service` (`domain-
controller`, `file-server`, `mail-server`, `web-tier`, `db-tier`, `egress-proxy`, `monitoring-
collector`, `protocol-gateway`, `dmz-web`, `vpn-concentrator`, `bastion-jump`) fails `tfc deploy`
with `PersonaDriverError("...either service_name (systemd unit) or process_match (pgrep -f ...) is
required...")` the moment it's assigned — `RealServiceDriver.start`/`.stop` (`axrange/personas/
drivers/real_service.py`) requires one of `service_name`/`process_match`, sourced from either a
direct kwarg (the traffic-profile assignment, which the schema doesn't expose a slot for either) or
`persona.knobs`, and no catalog persona YAML (`axrange/personas/catalog/*.yaml`) sets either key.

**Root cause:** `real-service`'s whole premise (§5, "an actual daemon whose ambient chatter *is* the
traffic") assumes the persona's driver can assert the daemon is alive — but *which* systemd unit or
process pattern corresponds to "this host's DC" / "this host's file server" is twin/image-specific
(a `windows-server-2022` DC's AD services aren't the same unit name as a Samba-based `file-server`),
so the starter catalog correctly left it unbound rather than guessing wrong. Nobody has yet supplied
the per-image binding this needs.

**Missing capability:** either (a) a twin-config's `nodes[]` entry (or the traffic profile's
`assignments[]`) carrying an explicit `service_name`/`process_match` override threaded through to
the driver (the `_DRIVER_EXTRA_KEYS` plumbing in `traffic.py` already supports an extra kwarg
reaching the driver — `service_name`/`process_match` are already in that tuple, unused because
nothing populates them today), or (b) each `real-service` catalog persona gaining a documented
per-`os_family`/per-image `knobs.service_name` default (e.g. `domain-controller` → `NTDS` on
Windows).

**Impact:** every twin config that assigns a `real-service` persona (`corp-enterprise`'s
`domain-controller`/`file-server`/`mail-server`, `datacenter`'s `web-tier`/`db-tier`, `ics-ot`'s
`protocol-gateway`, `dmz-perimeter`'s `dmz-web`/`vpn-concentrator`/`bastion-jump`) cannot actually
apply a traffic profile that includes them today — only `scripted`/`replay` assignments (the
`corp-workstation` family, `plc`/`historian`, etc.) work out of the box. Fails loud, never silently.

**Workaround (today):** restrict a traffic profile's `assignments[]` to `scripted`/`replay`-drive
personas, or hand-supply `service_name`/`process_match` via a future profile/twin-config extension
once (a) above lands.

**Related:** `docs/design/02-twin-model.md`, `docs/WORK_ORDER_traffic_tier.md`,
`axrange/personas/drivers/real_service.py`, `axrange/o2control/traffic.py`'s `_DRIVER_EXTRA_KEYS`.

---

## GAP-033 — `replay`-drive persona bindings are code-complete + tested, but no curated pcap templates ship with the repo

**Status:** 🔴 Open (content gap — the mechanism works, the content doesn't exist yet)
**Severity:** Low (fail-loud at resolve time — a missing `template:` path is an ordinary file-not-
found, not a silent no-op)
**Discovered:** 2026-07-24 — building `docs/WORK_ORDER_traffic_tier.md`.

**Symptom:** a traffic profile's `replay: [{template, onto_role, orig_ip, iface}]` binding
(`docs/design/02-twin-model.md`) resolves and drives correctly (`ReplayDriver`, `tcpreplay`
with address remap) against **any** pcap the operator supplies at `template:` — but the repo ships
**none**. The design brief's own `historian`/`iot-device` personas (§5.2) call out `replay` as
"best for high-rate or hard-to-synthesize flows (historian ingest, IoT telemetry)," yet there is no
`personas/templates/` (or similar) directory with an actual OPC-UA/MQTT/Modbus capture to point at.

**Root cause:** curating a representative pcap (capturing or sourcing a real OT/IoT protocol
conversation, scrubbing it of any real-world identifying data, and validating it replays cleanly
through `tcpreplay` with an address remap) is a content-authoring task, not a code task — it was
correctly scoped out of `traffic_tier`'s code/test/doc build (§2/§4 of the work order test only
against a synthetic/fixture pcap).

**Missing capability:** a small library of curated, scrubbed pcap templates (at minimum: one OPC-UA
or Modbus historian-ingest capture, one MQTT/CoAP IoT-beacon capture) checked into the repo (or
documented as a separate download) with a known-good `orig_ip` to remap from.

**Impact:** `historian`/`iot-device` (and any other `replay`-drive persona) cannot be exercised
end-to-end today without an operator supplying their own pcap — the code path is real and tested
against a fixture, but there is nothing "batteries-included" for a first-time user to point a
traffic profile's `replay:` block at.

**Workaround (today):** capture or source a pcap by hand and reference it via an absolute
`template:` path; the driver itself needs no further code changes to consume it.

**Related:** `docs/design/02-twin-model.md` (`historian`/`iot-device` persona notes),
`docs/WORK_ORDER_traffic_tier.md`, `axrange/personas/drivers/replay.py`.

---

## GAP-034 — `exr`'s same-host overlap guard serializes sequential replay only; a genuinely concurrent double-apply race remains possible

**Status:** 🔴 Open (documented-by-design limit, not a bug)
**Severity:** Low-Medium (no data corruption — worst case is two overlapping exercise runs
double-driving the same host's agents; fail mode is noisy, not silent)
**Discovered:** 2026-07-24 — building `docs/WORK_ORDER_exercise_tier.md` (`axrange/o2control/exercise.py`).

**Symptom:** `axrange exr deploy`'s same-host overlap guard reads the CURRENT persisted state (every
other non-terminal `exr` deploy bound to the same twin), checks whether its own target hosts overlap
any of theirs, and refuses if so — but the check and the subsequent persist of the new `exr` deploy
row are not wrapped in a single atomic transaction/lock spanning both. Two `axrange exr deploy`
invocations issued back-to-back (or genuinely concurrently, e.g. from two operators/scripts) can both
read "no overlap yet" before either has persisted, and both proceed.

**Root cause:** the guard is a read-then-persist pattern with no shared mutex spanning the two steps
— it is the same shape LES-116 names generally: a cross-deploy uniqueness/overlap guard that reads
persisted state without holding a lock across check→persist only prevents the *sequential* collision
(operator A's run completes, operator B's later run correctly sees the overlap and refuses); it does
**not** prevent the *concurrent* one.

**Missing capability:** a twin-scoped lock (mutex) held across the overlap-check-and-persist window —
analogous to the planner's existing per-scenario deploy lease (`docs/PLANNER.md` §6.2) but scoped to
`(twin_deploy_id, target_hosts)` for the `exr` tier specifically.

**Impact:** the documented, expected use (a human or script running one exercise, waiting for it to
finish or explicitly destroying it, then replaying) is unaffected — the guard does its job for that
sequential case. Only a genuinely concurrent double-apply is at risk, and that is not the tier's
supported operating mode today (`docs/WORK_ORDER_exercise_tier.md` describes replay as a sequential
loop).

**Workaround (today):** don't invoke `axrange exr deploy`/`run` concurrently against the same twin
from more than one process/operator; treat the guard as sequential-safe, not concurrency-safe.

**Related:** `docs/LESSONS.md` LES-116, `axrange/o2control/exercise.py`, `docs/WORK_ORDER_exercise_tier.md` §2.

---

## GAP-035 — No auto-reap for a completed `exr` run; it persists until an explicit `exr destroy` or a `te` destroy cascade

**Status:** 🔴 Open (scope gap against the work order's own text)
**Severity:** Low (no correctness risk — a completed run doing nothing further is harmless; it's a
tidiness/resource-accounting gap, not a functional one)
**Discovered:** 2026-07-24 — building `docs/WORK_ORDER_exercise_tier.md` (`axrange/o2control/exercise.py`).

**Symptom:** `docs/WORK_ORDER_exercise_tier.md` §2 says an `exr` run's ephemeral state is torn down by
"`destroy` (or auto-reap on completion)." Only the `destroy` half is built — `axrange exr destroy`
(explicit) and the cascade from a `te destroy`. There is no mechanism that notices an `exr` run's
activity/measurement completed and automatically tears it down on its own.

**Root cause:** auto-reap needs a completion signal (e.g. `wait-actor-ready`'s marker, or a
run-duration timeout) wired to an automatic `destroy_deploy` call — that trigger/scheduler was
correctly scoped out of this build (the work order names it as an "or," not a hard requirement, and
building a reap scheduler is a different-shaped problem than the playout itself).

**Impact:** an operator (or automation) that runs many exercises must remember to `exr destroy` each
one (or rely on the next `te destroy`'s cascade) — a completed run's `exr_`-tagged resources (agent
installs, activity runner state) sit around, tagged and harmless, but not self-cleaning.

**Workaround (today):** explicit `axrange exr destroy` after a run completes, or a `te destroy` at
the end of a testing session (which cascades all bound `tap`/`tfc`/`exr` state).

**Related:** `docs/WORK_ORDER_exercise_tier.md` §2, `axrange/o2control/exercise.py`.

---

## GAP-036 — Same-prefix host names beyond 15 chars collapse into one AD computer object (NetBIOS truncation)

**Status:** 🔴 Open (content/config fidelity gap, not a code bug)
**Severity:** Medium (silently loses fidelity at scale — 25 intended workstation identities become
1, with no error anywhere in the pipeline)
**Discovered:** 2026-07-25 — the dev45 twin-model live-verify, Stage 2 (`docs/
RUNBOOK_twin_model_live_verify.md`, `te deploy --config corp-enterprise`, CHANGELOG 2026-07-25).

**Symptom:** the `corp-enterprise` twin-config's `N_WORKSTATIONS=25` expansion names each node
`corp-workstation01` .. `corp-workstation25` (19 characters). Windows' NetBIOS computer-name limit is
15 characters, so every one of those 25 hostnames truncates to the identical 15-char prefix at domain
join — `Get-ADComputer` on the DC after the deploy showed **one** computer object, not 25. All 25 VMs
exist and are individually reachable (DNS/IP-wise), but AD/Kerberos/group-policy machinery that keys
off the computer object sees only one machine.

**Root cause:** the twin-config's node-naming template (`axrange/twinconfig/`) generates names long
enough to be readable/unique at the IR/inventory level, but nothing checks the **NetBIOS 15-char
ceiling** before those names reach `join-host-to-domain` (`docs/SLICE_WINDOWS_AD.md`). Proxmox/Linux
naming has no such limit, so this was never hit until a real multi-node Windows twin-config ran.

**Missing capabilities:** (1) a validator rule (or twin-config expander check) flagging any
Windows-joined node name that exceeds 15 characters, ideally with a suggested shorter naming scheme;
(2) the twin-config's expander could auto-derive a NetBIOS-safe short name (e.g. a numeric suffix
scheme guaranteed under 15 chars) distinct from the longer, human-readable node id used everywhere
else in the IR/inventory; (3) at minimum, a loud warning at `join-host-to-domain` plan/deploy time
when a target name would collide with a sibling's truncated form.

**Impact:** any twin-config or hand-authored IR with ≥2 same-15-char-prefix Windows-joined hosts
silently loses per-host AD identity — group policy, Kerberos delegation, and per-machine auditing all
key off the (collapsed) single computer object instead of the 25 the operator intended.

**Workaround (today):** keep Windows-joined node names ≤15 characters and give same-role nodes a
NetBIOS-safe distinguishing suffix (e.g. `wkstn01`..`wkstn25` instead of `corp-workstation01`..`25`).

**Related:** `docs/SLICE_WINDOWS_AD.md` (`join-host-to-domain`), `docs/design/02-twin-model.md` (twin configs), `axrange/twinconfig/` (node-naming template), LES-016 (the sibling truncation
class this reproduces).

---

## GAP-037 — Windows guest clock drifts ~6h unsynced on the workstation fleet (no NTP/w32time sync verified)

**Status:** 🔴 Open
**Severity:** Low-Medium (doesn't block a deploy; corrupts anything timestamp-sensitive — Kerberos
ticket validity, event-log correlation, Loki join-by-window measurement)
**Discovered:** 2026-07-25 — the dev45 twin-model live-verify, Stages 2–4 (`corp-enterprise` twin's
Windows workstation fleet).

**Symptom:** Windows workstation guests' in-guest clock was observed roughly **6 hours** off from the
host/cluster's wall clock, with no evidence of `w32time` (or any NTP client) actually syncing it post
domain-join. The DC itself (the domain's PDC-emulator, normally the authoritative time source) was not
independently confirmed synced either.

**Root cause:** the imagebuild/domain-join pipeline (`docs/SLICE_WINDOWS_AD.md`) enables the Windows
services needed for AD (QGA, sysprep, domain services) but does not appear to assert or fix time-sync
configuration (`w32time` pointed at the DC, or the DC itself pointed at a reachable NTP source) as
part of first-boot or domain-join.

**Impact:** Kerberos is time-sensitive (a >5-minute skew is the classic silent-auth-failure threshold)
— a 6h skew is far past that, so any AD auth path relying on ticket validity windows is at risk of
mysterious failures traceable only to clock skew; any cross-host log/measurement correlation (A4's
technique+host+**window** join in Loki) is corrupted by an unknown, unbounded per-host skew.

**Missing capabilities:** (1) verify/fix `w32time` sync as part of Windows first-boot or
`join-host-to-domain`; (2) a plan/deploy-time or `verify-capture`-adjacent heartbeat that checks guest
clock skew against host wall-clock and warns/fails if it exceeds a threshold; (3) confirm the DC
itself has a working outbound (or host-provided) NTP source.

**Impact severity note:** ~~this run's Kerberos-dependent flows (domain join, SMB sessions) still
worked despite the skew, so the auth-failure risk is real but not yet observed — treat this as a
latent risk, not a proven breakage.~~ **Superseded — now OBSERVED (2026-08-14 exr run, CHANGELOG).**
The predicted "mysterious failure traceable only to clock skew" happened: the `corp-enterprise`
DC's clock was **6h slow** (05:45 vs the attacker's real 11:45 UTC — the domain is internally
self-consistent because members sync *from* the DC, so only the non-domain-joined `attacker` sees
the gap), and it broke the AD kill-chain's **Kerberoast** step (`GetUserSPNs -request` →
`KRB_AP_ERR_SKEW(Clock skew too great)`) while every step that does NOT depend on a pre-auth Kerberos
ticket succeeded — **DCSync (NTLM/DRSUAPI) ✅ and AS-REP roast (no pre-auth) ✅, Kerberoast
(pre-auth TGT) ❌**. That precise `NTLM✅/AS-REP✅/Kerberoast❌` signature is now a documented
diagnostic tell for this gap (LES-194). So: severity upgraded from latent to **proven breakage of an
exercise-tier attack path**.

**Workaround (proven, 2026-08-14):** sync the **attacker** to the DC (match the domain's time, do not
"fix" the DC), then the Kerberos step completes — the exr's Kerberoast returned a real
`$krb5tgs$23$*svc-sql$…` hash immediately after `date -u <DC-time>` on the attacker. For a
Windows-side fix, `w32tm /resync` or a scheduled correction post-deploy as before. **The AD kill-chain exr spec now self-heals this on the ATTACKER side (2026-08-14):** `twin-configs/corp-enterprise/exr-ad-killchain.yaml` step 0 syncs the attacker to the DC's `currentTime` before the Kerberos steps (step 8 restores it), so the exercise-tier attack path no longer breaks on this gap — but that is a per-exercise workaround, not the fix. The right durable fix is still (1)/(2)/(3) above — verify/fix `w32time` at first-boot or `join-host-to-domain`, and a skew heartbeat — which also fixes the measurement-window corruption the attacker-side workaround cannot (truth markers still land at DC-time during the run).

**Related:** `docs/SLICE_WINDOWS_AD.md`, `docs/design/08-exercise-measurement.md` (A4's
technique+host+window Loki join, which this skew would corrupt if left uncorrected).

---

## GAP-038 — Persona per-instance skip-warnings fan out one line per (host, optional-peer) pair with no dedup

**Status:** 🔴 Open (polish — noisy, not incorrect)
**Severity:** Low
**Discovered:** 2026-07-25 — the dev45 twin-model live-verify, Stage 4 (`tfc deploy` against 25
`corp-workstation` instances, fix #4's optional emit-peers).

**Symptom:** when a `corp-workstation` persona's optional peer (e.g. `print-server`) is absent from
the twin, `tfc deploy` logs a skip-warning **per host instance** — with 25 workstation instances all
missing the same optional peer, the deploy output carries 25 near-identical warning lines instead of
one aggregate "N instances skipped peer X (absent from twin)" summary.

**Root cause:** `apply_traffic_profile`'s optional-peer resolution (fix #4,
`axrange/o2control/traffic.py`) warns at the point of resolving each individual assignment, which is
correct per-instance behavior but was never given a dedup/aggregation pass across instances sharing
the same missing-peer cause.

**Impact:** cosmetic — no functional effect (every instance still applies correctly, minus the
missing-peer emission); just a noisy deploy log at twin-config scale (N≥10 same-role instances).

**Missing capability:** aggregate identical (persona, missing-peer-role) skip warnings across
instances into one summary line before/after the per-instance apply loop.

**Workaround (today):** none needed — ignore the repeated lines; they're all instances of the same
already-understood condition.

**Related:** `axrange/o2control/traffic.py` (`apply_traffic_profile`), fix #4 in CHANGELOG 2026-07-25
"Twin-model arc: dev45 live-verify Stages 1–4 PASSED".

---

## GAP-039 — The `scripted` persona driver is a generic TCP-connect emitter, not a real protocol client

**Status:** 🔴 Open (known limitation, not a regression)
**Severity:** Low (traffic looks like the right port/cadence on the wire; it is not a protocol-correct
conversation an IDS's deep protocol parser would fully recognize)
**Discovered:** 2026-07-25 — the dev45 twin-model live-verify, Stage 4, while confirming captured
traffic at the tap matched persona `emits[]` declarations.

**Symptom:** the `scripted` driver (`axrange/personas/drivers/scripted.py`, both the Linux and the new
Windows-PowerShell path from fix #5) opens a TCP connection to the declared peer/port on the declared
cadence — it does not speak the actual application protocol (no real SMB negotiate, no real Kerberos
AS-REQ, no real LDAP bind). This is enough to exercise port-level/flow-level detections (Suricata
flow/port rules, NetFlow-style analysis) and to produce a plausible cadence/volume signature, but a
protocol-aware IDS signature expecting a valid protocol handshake would not fire on it the way it
would on genuine client traffic.

**Root cause:** the design brief's own §5 language calls `scripted` "a small in-guest agent runs real
client flows... over the QGA exec channel" — the *design intent* was real protocol clients
(`smbclient`/`ldapsearch`/a Modbus client, per §5's own bullet list); what shipped in
`persona_library`'s build is a simpler generic TCP-connect emitter that satisfies the cadence/volume/
peer contract but not the protocol-fidelity one.

**Impact:** any purple-team exercise measuring *protocol-aware* detection coverage (vs. flow/port-level
coverage) against `scripted`-persona background traffic will under-represent what a real client's
traffic would trigger. Does not affect `real-service` (actual daemon chatter) or `replay` (real
captured protocol conversations) personas.

**Missing capability:** per-protocol real client implementations behind the `scripted` driver (the
design brief's own examples: `smbclient` for SMB, `ldapsearch` for LDAP, a Modbus client for OT) —
a content/build task, not a schema change; the driver's launch mechanism (transient systemd
unit/Scheduled Task) is already correct and would host a real client the same way.

**Workaround (today):** for protocol-fidelity-sensitive exercises, prefer `real-service` (an actual
daemon) or `replay` (a real captured pcap) personas over `scripted` ones on the flows that matter.

**Related:** `docs/design/02-twin-model.md` (persona drive modes), `axrange/personas/
drivers/scripted.py`, GAP-033 (the sibling `replay`-template content gap).

---

## GAP-040 — Twin-configs have no incremental add: editing a deployed config's node list forces a whole new deploy, never a resume/extension

**Status:** ✅ Resolved (live-verified) — build 2026-08-11, `dev45` live-verify PASSED 2026-08-14
(`docs/WORK_ORDER_twin_incremental_update.md`, CHANGELOG 2026-08-11). `axrange te deploy --update`
implements the ADD/REMOVE half of this gap: a node can now be added to or removed from a standing
twin without a full redeploy, with the standing deploy's `plan_id` pinned so every unchanged
resource keeps its identity. Code/tests/docs are complete; **the gated live-verify has not yet run**
(the work order marks it mandatory — this feature deletes real VMs). In-place mutation of a
surviving node's own fields remains unresolved BY DESIGN (refused loudly, never redone —
`docs/PLANNER.md` §9.2's per-field redo-rules table is still unbuilt), and sibling-tier
(`tap`/`tfc`/`exr`) auto-reconciliation is warned about, not fixed — see LES-126's forward-pointer
for the current, honest scope of what's actually resolved.
**Severity:** Medium (a real workaround exists — hand-provision onto an existing host — but it defeats
the point of authoring the capability declaratively in the twin config)
**Discovered:** 2026-07-25 — the dev45 twin-model live-verify, Stage 5, while trying to add an
`attacker` node to the standing `corp-enterprise-f525eed4` twin for the AD kill-chain exercise.

**Symptom:** editing `twin-configs/corp-enterprise.yaml` to add a node (even a purely additive one, no
existing node touched) does not extend the already-deployed twin. Re-running `te deploy --config
corp-enterprise` against the edited config computes a **different** `plan_id` and would kick off a
whole new ~30-VM deploy, not a resume of the standing one.

**Root cause:** `derive_plan_id(scenario_id, ir_hash)` keys plan/deploy identity on the **content hash**
of the expanded IR. Any edit to the twin config — additive or not — changes that hash. There is no
plan-diff / re-planning layer to reconcile "this is the same twin, plus one node" against a standing
deploy's state; `docs/PLANNER.md` §9 lists plan-diff/re-planning as explicitly unbuilt.

**Impact:** any iterative exercise-authoring workflow that wants to grow a standing twin incrementally
(add an attacker host, add a service) cannot do so through the twin-config path at all — the operator
must choose between (a) accepting a full redeploy of the whole twin (expensive: tens of VMs, tens of
minutes, and a break in state/traffic continuity) or (b) working around the twin-config model entirely
by hand-provisioning the missing capability directly onto an already-deployed host.

**Workaround (used live, 2026-07-25):** hand-provisioned the impacket toolkit onto the existing
`egress-proxy` host over QGA, and created the vulnerable AD accounts (`svc-sql`, `jsmith`) directly on
the live DC via QGA `New-ADUser`, rather than editing + redeploying the twin config. This works but
means the authored twin-config content (the `attacker` node now in `corp-enterprise.yaml`) and the
run's actual topology diverge — the config is validated/future-ready but was not what was live-verified.

**Missing capability:** plan-diff / re-planning (`docs/PLANNER.md` §9) — reconcile an edited IR against
a standing deploy's persisted state and emit only the delta ops, rather than re-keying the whole plan
on content hash. This is pre-existing unbuilt planner machinery (`docs/CLAUDE.md`'s "standing
refinement bucket"), now with a concrete twin-model use case motivating it.

**Related:** `docs/PLANNER.md` §9, `docs/design/02-twin-model.md`, CHANGELOG 2026-07-25 "Twin-model
arc: dev45 live-verify Stage 5 (`exercise_tier` capstone) PASSED", LES-126.

---

## GAP-041 — Twin-configs/exercise specs have no script-staging mechanism for real credential-capture tools (e.g. Responder/LLMNR)

**Status:** 🔴 Open (known limitation, not a regression)
**Severity:** Low/Medium (blocks real-hash-capture done-criteria on kill-chain exercises; does not
block the technique/measurement half, which uses truth markers instead)
**Discovered:** 2026-07-25 — the dev45 twin-model live-verify, Stage 5, running the LLMNR-poisoning
step of the AD kill chain.

**Symptom:** the kill chain's LLMNR-poisoning step ran but stayed **attempt-only** — no real NTLM hash
was captured off the wire, unlike the AS-REP/Kerberoast/DCSync steps which produced real material.

**Root cause:** capturing a real hash via LLMNR/NBT-NS poisoning needs a tool like Responder staged as
a **persistent listener** on the attacker host, timed against the kill chain's other steps. Neither
twin-configs nor the exercise spec have a first-class "stage this script/tool onto node X and keep it
running" primitive — `provision-app` targets apt-installable packages, `run-script` is a one-shot QGA
exec, and personas' scripted-driver launch mechanism (transient `systemd-run`/Scheduled Task) is scoped
to persona traffic-emission, not exercise tooling.

**Impact:** any exercise whose done-criterion requires a *real* captured artifact (a hash, a session
token) rather than a simulated technique marker cannot express the staging step declaratively yet.

**Workaround:** none exercised this session — the step was left attempt-only; a future run could
hand-stage the tool over QGA the same way GAP-040's workaround hand-provisioned impacket.

**Missing capability:** a script/tool-staging primitive for exercise hosts (deploy a long-running
listener, not just a one-shot command or an apt package) — plausibly an `exr`-tier or `run-script`
extension.

**Related:** `docs/WORK_ORDER_exercise_tier.md`, `docs/SLICE_CONFIG.md` (`run-script` OP-APP-030),
CHANGELOG 2026-07-25 "Twin-model arc: dev45 live-verify Stage 5 (`exercise_tier` capstone) PASSED".

---

## GAP-042 — `adapter.conf` is gitignored, so operator keys (e.g. `O2_SERVICES_VMID`) silently vanish between sessions/boxes with no recovery signal

**Status:** ✅ Resolved (the message) / 🟡 Open (the deeper gap — see below)
**Severity:** Low/Medium (real debugging-time cost when hit; polish, not a blocker)
**Discovered:** 2026-07-26 — an operator burned real debugging time on `axrange o2 info` printing a
bare `(not configured)` for every O2 endpoint after `O2_SERVICES_VMID` had silently gone missing from
`adapter.conf` (the file is gitignored, so it doesn't travel with the repo across sessions/boxes and
has no diff/history to catch a dropped key).

**Symptom:** `o2 info` (CLI and `GET /api/o2/info`) rendered a bare `(not configured)` per unresolved
endpoint — no hint at what to set or where, forcing the operator to re-derive the fix from source.

**Root cause:** `AdapterConfig.resolve_o2_endpoints` (`axrange/adapter_proxmox/config.py`) had a
terminal fallback string with zero actionable content, and nothing validates that a services VM an
operator *expects* to be configured actually is — a dropped/typo'd key degrades silently to the same
generic placeholder as "never configured at all".

**Fix (this entry):** the fallback is now actionable — `"(not configured — set O2_SERVICES_VMID or a
per-service O2_<SVC>_URL in adapter.conf)"` — naming both the services-VM knob and the per-service
override, consumed verbatim by the CLI (`cli/main.py::cmd_o2_info`) and the API
(`api/service.py::o2_info` → `O2InfoDTO`) since both read this dict without transformation.

**Still open (the deeper gap):** there is no validation step anywhere that flags "this cluster/binding
expects an O2 services VM and `adapter.conf` doesn't declare one" — the actionable message only helps
once an operator is already looking at `o2 info`'s output. A future `axrange o2 info`/`doctor`-style
check that warns proactively (e.g. at `o2 up`/`o2 status` time, or a config-lint verb) would close this
fully; not attempted here since it's a design addition, not a message wording fix.

**Related:** `docs/WORK_ORDER_access_facts.md` Part B, `axrange/adapter_proxmox/config.py`
(`resolve_o2_endpoints`), `axrange/adapter_proxmox/resolve.py`.

---

## GAP-043 — SDN vnet names are NOT twin-unique: destroying one twin deletes another twin's vnet (live data-loss, hit on dev45)

**Status:** 🟡 Open (fix in progress — `axrange/adapter_proxmox/naming.py`)
**Severity:** **HIGH** — silent, cross-twin, live-network data loss. Not a corner case: it fires whenever
two `te` twins coexist on one cluster and share a *network name*, which the shipped twin-configs and
archetypes routinely do (`dmz`, `corp-lan`, `mgmt`, …).
**Discovered:** 2026-07-27 — **live on `dev45`, by causing it.** Real outage, not a review finding.

**Symptom:** `axrange te destroy dmz-perimeter-a2d0ba5b` reported a clean
`21 resource(s) removed; 0 not cleared` — and took vnet `teva393e` with it. That vnet belonged to
zone `te672588`, the **standing `corp-enterprise-f525eed4` twin**. Four corp-enterprise VMs
(`te-672588-egress-proxy`, `-mail-server`, `-dmz-web`, and the `-gw` router) lost their tag-30
network. Nothing in the destroy output indicated it had touched another deploy's resource — the
teardown believed, correctly by its own records, that the vnet was its own.

**Root cause:** `naming.py`'s `vnet_id_for_net(net_id, *, tier)` hashes **only `net_id`**:

```python
h = hashlib.sha256(net_id.encode()).hexdigest()[:5]
return f"tev{h}"
```

Its siblings are plan-scoped — `zone_id_for_plan` → `te<6hex of plan_id>`, `underlay_bridge_for_plan`
→ `tebr_<6hex of plan_id>`. **The vnet is the only SDN object with no twin discriminator.** Two twins
with a same-named network therefore compute the same vnet id.

The damage path is the Model-C adopt in `adapter_proxmox/rest.py` `op_create_vnet`:

```python
vnet_id = vnet_id_for_net(net_id, tier=ctx.tier)
zone_id = inp.get("zone_id") or zone_id_for_plan(ctx.plan_id, tier=ctx.tier)   # <- plan-scoped!
if self._vnet_exists(vnet_id):
    return {"vnet_id": vnet_id}     # twin B adopts twin A's vnet, records it as its own
```

Twin B's `create-vnet` finds twin A's vnet already present, returns success, and persists it in its
own op outputs. `planner/teardown.py` is **outputs-driven**, so twin B's destroy deletes it. The
tier-tag protect matrix cannot help: both are `te`-tagged, so this is same-tier collateral damage,
which no blast-radius guard was designed to catch. Note the zone id on the very next line *is*
plan-scoped — the inconsistency between two adjacent lines is the entire bug.

**Blast radius beyond `te`:** `o2_vnet_id_for_component` / `tfc_vnet_id_for_component` /
`exr_vnet_id_for_component` share the identical shape and carry the same latent bug for two
`tfc`/`exr` deploys bound to different twins.

**Why no test caught it:** every existing naming test asserts one plan at a time. There was no test
asserting that *two different plans* produce *different* names for the same net — the exact property
that was silently false.

**Fix:** thread the plan discriminator into the vnet hash (required keyword arg, no
silently-legacy default), for all four tier variants; keep the name *shape*, charset, 8-char cap, and
`is_*_vnet` predicates unchanged. Existing deployed twins are unaffected because teardown deletes
from **recorded** outputs rather than recomputed names — a property that must be pinned by a test, not
assumed.

**Same family as:** LES-118 (shared-gretap de-scope data loss) and **LES-143** (a node-shared bridge
entering a per-`(bridge, sensor)` naming function), logged hours earlier the same day for gretap
devices on `vmbr0`. LES-143's rule — *before admitting a value into a resource-naming function, ask
whether it is twin-scoped* — applies verbatim here and would have caught this. The vnet case is
strictly worse: gretaps collide only when two twins tap the same sensor; vnets collide whenever two
twins share a **network name**.

**Operational note:** any cluster that has run two twins with overlapping network names may already
have cross-owned vnets recorded in `state.db`. Those records are data, not code — the fix prevents
new collisions but cannot retroactively disentangle existing ones. Check before destroying a twin on
a cluster with others live.

**Related:** `axrange/adapter_proxmox/naming.py`, `axrange/adapter_proxmox/rest.py` (`op_create_vnet`),
`axrange/planner/teardown.py`, LES-143, LES-118.

---

## GAP-044 — Persisted op inputs go stale on a re-applied deploy, so `tap status` misreports live capture direction

**Status:** ✅ Resolved (build; gated `dev45` live-verify pending) — **both** halves landed
together and are guard-tested. The **direction** dimension is fixed by the executor-side inputs
write-back; the **sensors** dimension (GAP-045) by retiring dropped `attach-gre-tunnel` op rows, and
both read-back surfaces now filter positively to `status == COMPLETED` (LES-113, scope widened to
`taps.py`). Closing on the write-back alone would have been premature — see GAP-045.
**Severity:** Medium-High — not data loss, but it makes a read-back verb **over-report** capture
coverage, which is the LES-001 posture inverted: an operator reads "both directions" and believes
they are capturing bidirectionally when the wire is rx-only.
**Discovered:** 2026-07-27 — **live on `dev45`**, during the tap-scopes re-apply against the
standing `corp-enterprise` twin. Found by cross-checking two verbs against the actual `tc` state.

**Symptom:** after re-applying `corp-final.yaml` under the new rx-only semantics, three sources
disagreed about the same deploy:

| Source | Reports |
|---|---|
| `tc filter show` on the host (ground truth) | `ingress-mirred=3, egress-mirred=0` → **rx-only** |
| `axrange tap info <id>` | `direction=rx` ✅ |
| `axrange tap status <id>` | `[both+rx]`, `[both]` ❌ |

**Root cause:** `axrange/planner/state.py` persists op rows with **`INSERT OR IGNORE`** — deliberate,
so a resume never clobbers op status/timestamps. But it means that on a **re-apply against an
existing deploy id**, `inputs_resolved_json` is written once and **never updated again**. The
executor runs from the freshly-composed *in-memory* plan, so the wire is correct; the database keeps
the inputs from the first-ever apply, forever.

The persisted row for a server-vlan mirror still read:

```json
{"gre_port_input": "gre_port__0__XPress-SW", "net_id": "server-vlan",
 "nic_index": 0, "sensor_id": "XPress-SW"}
```

— no `direction` key (it predates the field), and **one** sensor where the wire has three.
`tap status`'s direction derivation reads exactly this, finds no `direction`, and falls back to
`"both"` per the LES-096 safe default. **The default is correct in isolation; the data under it is
stale.**

**Why `tap info` LOOKED right — corrected 2026-07-27 after code review disproved two earlier
readings.** `tap info` does **not** re-resolve. `cmd_tap_info` → `tap_info_from_deploy` reads the
*same* persisted `install-ovs-mirror.inputs_resolved_json` rows as `tap status`; only `tap info`
against a **spec file with no existing deploy** takes the re-resolving path. Nor did it merely
"isolate the fresh row" (the second wrong reading). Reproduced against a pre-fix tree, it printed
**both** values:

```
STATUS: corp -> both+rx            <- the live symptom
INFO:   h-a    corp 0 -> both      <- ALSO STALE
INFO:   r-edge corp 0 -> rx        <- fresh
```

Neither verb was trustworthy. The real asymmetry is **aggregation**: `tap status` unions across every
host on a net, so one stale member widens the result into a single confident token (`both+rx`) that
reads as a coverage *claim*; `tap info` itemizes per-`(host, net, nic)`, so the same defect scatters
across rows where a stale value reads as a *different binding* rather than an error — and the fresh
row gets mistaken for confirmation. An aggregate is never the place to notice staleness. See LES-158.

**Why the tests could not catch it:** every test builds a deploy once. This appears only on a
**re-applied** deploy with pre-existing op rows — which is the tap tier's *normal* operating mode,
since re-sharding capture in place without disturbing the twin is its entire premise.

**Fix direction:** a blanket upsert is wrong — it would falsely claim new inputs are live for an op
that a resume legitimately **skipped** as already-`completed`. The correct rule is to write back
`inputs_resolved_json` **when an op actually executes**, so persisted inputs always describe what was
really applied, and a skipped op keeps the inputs that genuinely ran.

**Not defence in depth:** an earlier revision of this entry recommended making `tap status`
re-resolve "the way `tap info` does". That recommendation rested on the false premise above and is
**withdrawn** — there is no parity to restore, and a resolve-from-spec path would answer a *third*
question ("what the plan wants") rather than "what is applied". Real defence in depth here is ground
truth on the wire: note that `verify-capture` proves a frame arrived but asserts **nothing** about
rx-vs-both, so nothing in the product currently checks capture direction against `tc`.

**The attempt-start write point makes a consumer-side status filter mandatory.** Refreshing inputs
when an op starts means a *never-applied* set of inputs can land in the row — including on the
lock-resolution and lock-timeout paths, where the op returns `attempts=0` and the adapter was never
called at all. Any surface rendering these rows as live state must filter positively to
`status == COMPLETED`. `o2control/traffic.py` already does this under **LES-113**; `o2control/taps.py`
did not, because LES-113's `Scope:` line never named it. See LES-159.

**Audit follow-on:** "persisted op inputs reflect current state" is an assumption worth checking
wherever else it is relied on. The tap de-scope path reads persisted state too, though it keys off
`scope_json` (not outputs, as an earlier revision of this entry said), which review found unaffected.

**Related:** `axrange/planner/state.py` (`INSERT OR IGNORE`), `axrange/planner/executor.py`,
`axrange/o2control/taps.py` (`_bindings_summary_from_ops`, `tap_info_from_deploy`), LES-096, LES-001.

---

## GAP-045 — A de-scoped tap sensor is reported by `tap status` forever: the gretap netdev is deleted but its `attach-gre-tunnel` op row is never retired

**Status:** ✅ Resolved (build; gated `dev45` live-verify pending) — fixed in the same pass as
GAP-044. `_delete_dropped_gretaps` now marks every dropped pair's `attach-gre-tunnel` op `destroyed`
**unconditionally**, including pairs whose device is KEPT under VLAN-zone aliasing, and both read
surfaces filter to `status == COMPLETED`. The unconditional-ness is pinned by a test that fails under
the gated variant (LES-160) — the gated variant otherwise passes all 131 tap tests.
**Severity:** Medium-High — same over-report class as GAP-044, in the **sensors** dimension rather
than the direction dimension. An operator reads a sensor as still receiving capture after its gretap
has been deleted.
**Discovered:** 2026-07-27 — code review of the GAP-044 fix; **reproduced on the fixed tree**, so it
is a distinct, pre-existing defect that the GAP-044 write-back does not close.

**Symptom:** bind `corp -> [pktxpress-1, suricata-1]`, then re-apply narrowed to
`corp -> [pktxpress-1]`:

```
tap status: corp -> ['pktxpress-1', 'suricata-1']   <- WRONG, suricata-1's gretap is deleted
tap info:   corp -> ['pktxpress-1']                 <- correct
```

**Root cause:** `_delete_dropped_gretaps` (`axrange/o2control/taps.py`) removes the netdev but never
calls `store.mark_op_destroyed` on the dropped `attach-gre-tunnel` op rows — even though the *same
function*, ~20 lines earlier, does exactly that for orphaned `verify-capture` ops. Because
`_bindings_summary_from_ops` builds its net→sensors map from unfiltered `attach-gre-tunnel`
**scopes**, the dropped pair is reported indefinitely.

**Why it matters beyond the wrong string:** this is the *identical shape* to the GAP-044 live
incident — an aggregate `status` verb over-claiming coverage while its itemized `info` peer
disagrees (LES-158). It is why **GAP-044 must not be closed on the strength of the inputs write-back
alone**: the originating complaint ("`tap status` over-reports capture coverage") survives through
this second door.

**Fix:** `mark_op_destroyed` each dropped `(net, sensor)` pair's op, gated on
`result.succeeded and dropped_pairs` — precedent in the same function's `verify-capture` handling and
in `traffic.py::_stop_dropped_instances`. Note the marking is a **point-in-time annotation**, not a
durable fact (`reset_incomplete_ops` reverts it on the next re-apply, LES-113) — the
`status == COMPLETED` filter on both read-back surfaces is what makes it effective. The two changes
are a pair; neither works alone.

**Related:** GAP-044, LES-113 (scope widened to `taps.py` by this finding), LES-158, LES-159,
`axrange/o2control/taps.py` (`_delete_dropped_gretaps`, `_bindings_summary_from_ops`,
`tap_info_from_deploy`), `axrange/o2control/traffic.py` (`_stop_dropped_instances`, the precedent).

---

## GAP-046 — `apply-sdn-config`'s cluster-wide SSH tap re-assert scan doesn't scale (hardcoded 60s timeout, O(bridges × running-VMs)); fails deploys on a busy node

**Status:** 🔴 Open (pre-existing defect; not caused by, and not fixed by,
`docs/WORK_ORDER_tap_eligibility.md` — merely surfaced by its live-verify)
**Work order:** `docs/WORK_ORDER_apply_sdn_reassert.md` (OPEN, 2026-07-28) — leads with the
"is the re-assert still needed under SDN-native?" retire path (the `rest.py:576-580` watch-item),
then an efficient single-pass rewrite if it is, with the node-wide blast radius preserved.
**Severity:** High (blocks a new `te`/`o2`/`tap` deploy on any node with many running VMs)
**Discovered:** 2026-07-28 — `docs/WORK_ORDER_tap_eligibility.md` gated `dev45` live-verify. The
fresh `tap-elig-verify` twin's `apply-sdn-config` failed twice (3 retries each,
`error_class=adapter-timeout`, `ssh <node>: timeout after 60s`), while the other 30/31 ops (all
VM/NIC/router ops) succeeded.

**Symptom:** `apply-sdn-config` times out and retries under `run_ssh`'s adapter-timeout error class
on a cluster with many running VMs — even though the SDN change itself (`PUT /cluster/sdn`)
succeeds; the op only fails on its trailing housekeeping scan.

**Root cause:** `op_apply_sdn_config` (`axrange/adapter_proxmox/rest.py`, ~lines 555-660) does the
real SDN work first (`PUT /cluster/sdn` + verify — which succeeds), then unconditionally runs a
**node-wide** SSH housekeeping scan re-asserting tap ports on every `tebr_*` bridge for every
currently-running VM on the node (`for br in tebr_*; for vmid in running; qm config $vmid`), not
scoped to this deploy's own resources. With 41 running VMs on dev45 (32 from the standing
`corp-enterprise` twin) the O(bridges × VMs) scan exceeds the hardcoded `run_ssh(...,
timeout_s=60)`. This is load-driven, not transient — a retry can't out-wait a scan whose cost grows
with unrelated lab occupancy.

**Missing capabilities:** scoping the re-assert scan to this deploy's own `tebr_*` bridge(s) + VMs
instead of every running VM on the node; and/or a configurable (or size-proportional) SSH timeout
instead of a hardcoded 60s; and/or skipping the scan entirely when nothing about tap state actually
changed.

**Workaround (today):** deploy when the node is quieter (fewer running VMs). Note the SDN apply
itself succeeds — only the ancillary re-assert scan times out, and (per GAP-047) the deploy
proceeds downstream regardless, so the op's own retries are the only visible cost today.

**Related:** GAP-047 (the same run's other finding), LES-168, `docs/ADAPTER.md` §8,
`axrange/adapter_proxmox/rest.py` (`op_apply_sdn_config`).

---

## GAP-047 — `network-ready` barrier never actually gated on `apply-sdn-config` (scope-var mismatch silently drops the edge), since the original op-registry commit

**Status:** 🔴 Open (pre-existing defect; not caused by, and not fixed by,
`docs/WORK_ORDER_tap_eligibility.md` — merely surfaced by its live-verify)
**Severity:** Medium (a deploy proceeds past a failed/incomplete `apply-sdn-config`; masks SDN-apply
failures instead of halting on them)
**Discovered:** 2026-07-28 — `docs/WORK_ORDER_tap_eligibility.md` gated `dev45` live-verify;
confirmed by in-process DAG inspection (`plan.predecessors("network-ready") == frozenset()`,
`plan.descendants("apply-sdn-config") == []`); traced via `git log -S` to the original op-registry
commit `47c86b0`.

**Symptom:** on the same live-verify run, `apply-sdn-config` failed twice (GAP-046) yet every op
downstream of `network-ready` proceeded and completed anyway — the barrier never actually waited on
it.

**Root cause:** `OP-BAR-010 network-ready` (`axrange/ops/registry.py`) has `scope="net-id"` but
declares `predecessors=("apply-sdn-config[$scenario]",)` — referencing `$scenario`, a scope var the
barrier's own `net-id` scope doesn't carry. `axrange/planner/pass3_ordering.py::_resolve_pattern`
silently drops a predecessor pattern that references an absent scope var, so this incoming edge has
never existed — present since the original op-registry commit (`47c86b0`), not a regression from
any recent change, and invisible to the pytest suite because nothing asserts the DAG's edge shape
directly.

**Missing capabilities:** a predecessor pattern resolvable from `network-ready`'s `net-id` scope
back to `apply-sdn-config` (or a scenario-scoped barrier `network-ready` can depend on instead);
and/or a fail-loud warning/error in `_resolve_pattern` when a declared predecessor resolves to zero
ops, so a silent DAG gap like this one can't hide undetected; and/or a DAG-shape test asserting
`network-ready`'s expected in-edges are non-empty.

**Workaround (today):** none — the gap is invisible in normal operation (nothing currently depends
on `apply-sdn-config` actually blocking `network-ready`, so no deploy has visibly misbehaved from
it). Treat any `apply-sdn-config` failure in a deploy log as a real problem to investigate manually
— the DAG will not halt the deploy for it.

**Related:** GAP-046 (the same run's other finding), LES-167, `docs/PLANNER.md` §3.3,
`axrange/ops/registry.py` (`OP-BAR-010 network-ready`), `axrange/planner/pass3_ordering.py`
(`_resolve_pattern`).

---

## GAP-048 — A persona emit targeting the reserved `internet`/`egress` peer tokens puts NOTHING on the wire; the skip is silent

**Status:** 🔴 Open
**Severity:** Medium (a bound persona reports green and generates a fraction of its declared
traffic; affects 11 emits across the shipped catalog)
**Discovered:** 2026-07-28 — while authoring the high-volume persona wave (CHANGELOG). Not a
regression: present since the persona library landed 2026-07-24.

**Symptom:** an `emits[]` entry whose `peer_role` is one of the reserved external tokens
(`egress`, `internet`) never generates traffic. `o2control.traffic._resolve_peers` deliberately
skips reserved tokens (no twin host holds them, correctly), so no peer IP is threaded; the guest
client then logs `skip <proto> -- peer_role internet not resolved` and moves on. The persona's
`drive-persona` op still completes, its systemd unit is still alive, and `tfc status`/`tfc info`
still show the instance as applied — so every layer an operator would check reports success.

**Blast radius (shipped catalog, pre-2026-07-28 entries):** 11 emits use `internet` and 3 use
`egress`. Concretely: `egress-proxy`'s entire NS traffic (https + dns to `internet`) is inert, so
the twin's designated egress role emits nothing northbound; `iot-device`'s https, `vip-exec`'s
https, and `dmz-web`/`vpn-concentrator`'s inbound-from-internet emits are likewise inert. The
practical effect is that the twin's **north-south** traffic is far thinner than the catalog reads,
which matters most for the `perimeter: wan` tap scope (built 2026-07-27), whose whole purpose is
capturing that edge.

**Root cause:** the reserved tokens describe a *destination outside the twin's role vocabulary*,
but nothing ever assigns them a concrete address. The design note in `_resolve_peers` treats
log-and-skip as graceful degradation — reasonable for an unresolvable twin role, wrong for a token
that is always "unresolvable" by construction.

**Fix options (undecided — needs a call):**
1. Resolve `egress`/`internet` to the twin's **default gateway / router WAN leg** at bind time, so
   the traffic at least traverses the perimeter and is capturable there. Closest to author intent,
   and directly feeds the WAN tap scope.
2. Resolve them to an operator-configured off-twin address (an `adapter.conf` knob), for twins
   whose egress genuinely leaves the lab.
3. Keep the skip but make it **loud** — a bind-time warning listing every inert emit, so a persona
   that silently generates half its declared traffic is at least visible at `tfc plan`.

(1) and (3) compose and are probably both right: resolve where a gateway exists, warn where it
doesn't.

**Workaround (today):** target a real twin role that carries egress instead — the 2026-07-28
personas use `egress-proxy`, which resolves normally and produces real frames.

**Related:** CHANGELOG 2026-07-28 (persona volume lever — caveats), LES-169,
`axrange/o2control/traffic.py` (`_resolve_peers`), `axrange/personas/drivers/scripted.py`
(`resolve_emit_peers`), `docs/design/07-traffic-personas.md`.

---

## GAP-049 — Two Scenario IR fields reach zero consumers (`Activity.adversary`, `Network.isis_area`)

**Status:** 🔴 Open (reported, deliberately not removed — the IR is version-locked)
**Severity:** Low (dead authoring surface, no wrong behavior — but it is the same trap class that
produced the twin-config sweep, and the IR is the *more* visible spec of the two)
**Discovered:** 2026-08-04 — the user-requested inert-field audit of the `te` config specs
(CHANGELOG 2026-08-04 "Inert-field sweep").

**Symptom:** both fields are parsed and carried by `axrange/ir/schema.py`, and neither is read by
anything:

- **`Activity.adversary`** (`ir/schema.py:387`, self-described "informational: adversary-profile/plan")
  — zero consumers. The only `adversary` hits elsewhere are a *different* concept
  (`overlays.adversary` / `AdversaryEntry` / `agent_kind=adversary`, `config_exec/backend.py:976`,
  `api/dtos.py:534`), which are genuinely wired. Same-name-different-field, the exact confusion that
  makes this class hard to spot by grep alone.
- **`Network.isis_area`** (`ir/schema.py:416`, self-described "informational; per-router NET carries
  it") — zero consumers. The fabric slice reads the per-router NET, not this.

Checked and **not** inert in the same sweep: `Activity.technique` (real — `[axr-truth]` marker
emission, `config_exec/render.py:103`), and every `overlays.identity` user field.

**Impact:** an author who sets either gets no error and no effect — the `persona:` failure mode that
motivated the whole audit, one layer up in the more prominent spec.

**Why not fixed in the same pass:** `IR_SCHEMA` is a **version-locked contract**. Removing a field
means a `scenario_schema_version` bump plus user sign-off (`CLAUDE.md`, "Design Document
Maintenance"), unlike `axrange/twinconfig/schema.py`, which is not version-locked and could be swept
directly. Bundling a locked-schema change into an unlocked cleanup would have been exactly the kind of
scope creep the gate exists to prevent.

**Fix (when someone picks it up):** either remove both with a `scenario_schema_version` bump and an
`STR-`-class rule rejecting leftovers (the `STR-200` precedent from
`docs/WORK_ORDER_tap_eligibility.md`), or wire them — `isis_area` in particular may be worth wiring
rather than deleting when the fabric slice gets its live deploy verify.

**Related:** CHANGELOG 2026-08-04, LES-172, `docs/WORK_ORDER_tap_eligibility.md` (STR-200 precedent),
`docs/design/04-networking-capture.md` (fabric).

---

## GAP-050 — Mirror-snippet teardown has no pre-existence check, so an idempotent second `te destroy` reports "N removed" instead of 0

**Status:** 🔴 Open (cosmetic accounting, not a state bug)
**Severity:** Low
**Discovered:** 2026-08-04 — the W1 dev45 live-verify, proof 8 (idempotent re-destroy).

**Symptom:** unlike the vm/vnet/zone/bridge teardown steps — which check-then-act and report `absent`
(counted as neither removed nor error) when the resource is already gone — the twin's own
mirror-snippet deletion step (`axrange/planner/teardown.py`, `delete_mirror_snippets`) issues an
`rm -f`-shaped delete of each host's snippet file unconditionally and counts it as "removed" every
time. So a genuinely idempotent second `te destroy` of an already-destroyed twin reports e.g.
"5 resource(s) removed" (one per host) rather than the clean "0 removed" the other resource kinds
produce.

**Impact:** purely an accounting/reporting nuance. The operation is safe either way (`rm -f` on a
snippet path on the PVE node), the final cluster state is correct, and nothing downstream depends on
the count. It only makes an idempotent re-destroy *look* like it did work it didn't.

**Fix:** give `delete_mirror_snippets` the same check-then-act shape as the other teardown steps —
test for the snippet file's existence first and report `absent` when it's already gone, so the count
reflects reality on a repeat destroy.

**Related:** CHANGELOG 2026-08-04 (W1 live-verify, finding 5), `docs/PLANNER.md` (teardown idempotency).

---

## GAP-051 — Windows domain-joins dominate AD-twin deploy time (~1–2 min each, effectively serialized)

**Status:** 🔴 Open (performance / operability, not a correctness bug)
**Severity:** Medium (makes a routine AD twin a 60–90 min deploy; amplifies every transient-failure/
resume cycle)
**Discovered:** 2026-08-05 — the corp-enterprise bundle live-verify (25-workstation AD twin on dev45).

**Symptom:** `join-host-to-domain` runs per workstation and is slow (~1–2 min each) and effectively
serialized, so a 25-workstation fleet alone adds ~40+ minutes to the twin deploy. In the bundle
live-verify the whole ~30-VM deploy ran 60–90 min, dominated by the domain joins; one workstation
(ws09) also went an outlier ~28 min before an executor restart cleared it (the VM itself was healthy —
QGA responsive, uptime fine — so the stall was on the join path / executor, not the guest).

**Impact:** every AD-heavy twin (corp-enterprise, ad-domain-lab, purple-team-range) is a long deploy,
and the length compounds with the resume-on-transient-failure pattern — each transient apt failure or
reaped executor means re-running a deploy whose tail is dominated by these slow joins.

**Fix directions (undecided):** (1) parallelize the joins where the DC can take concurrent enrollments
(they appear to pipeline loosely already — 8 joined within ~1 min windows — but ws09 serialized behind
a stall); (2) a per-join watchdog/timeout so an outlier join fails fast into the retry path instead of
hanging ~28 min; (3) investigate the per-join cost itself (w32time/AD-replication settle, `Restart-
Computer` round-trips). None are blocking; the joins do succeed.

**Related:** LES-017 (`wait-domain-ready` stability probes), CHANGELOG 2026-08-05 (bundle live-verify).

---

## GAP-052 — `tap validate` static mode does not check sensor-id existence; a stale/renamed sensor reference passes "static OK" then hard-fails at deploy

**Status:** 🔴 Open
**Severity:** Medium (checked-in content can reference a nonexistent sensor and look valid until deploy)
**Discovered:** 2026-08-05 — the bundle live-verify preflight caught `tap-default.yaml`/`tap-reshard.yaml`
referencing `o2-tcpdump`, a sensor decommissioned 2026-07-26. `tap validate` printed "static OK" and
even listed `o2-tcpdump` as a resolved target; only `apply_tap_plan`'s `resolve_tap_plan_bindings`
(which needs a live target + the sensor registry) hard-fails with `unknown sensor`.

**Symptom:** a tap plan's `sensors: [<id>]` is checked for shape at static-validate time but NOT for
existence — neither against the standing O2 surface's sensor list nor against `adapter.conf`'s
`EXTERNAL_SENSORS`. So a stale reference (a decommissioned/renamed sensor) validates clean and only
fails when `tap deploy` resolves it against the live twin + sensor registry, taking the whole plan
down atomically (one bad binding fails all bindings).

**Impact:** WO-2's own "re-confirmed to resolve" check for the shipped tap plans was satisfied by the
static-OK output and missed the stale `o2-tcpdump` — a false-confidence gap. Any checked-in tap plan
can drift out of sync with the actual sensor inventory undetected until a live deploy.

**Fix:** `tap validate` should, when a `--db`/adapter.conf is reachable (its existing two-mode
posture), warn on a sensor id that is neither a standing-surface sensor nor a configured
`EXTERNAL_SENSORS` id — the same "don't print static OK for a reference that can't resolve" discipline
`REF-120` applies to image ids. At minimum, scope the "static OK" wording to "shape only — sensor
existence unchecked."

**Related:** LES-177, CHANGELOG 2026-08-05 (bundle live-verify — the `o2-tcpdump` fix), GAP-026
(external sensors).

---

## GAP-053 — Twin-config `count:` fan-out allocates IPs POSITIONALLY across the whole `nodes:` list; growing a `count:` node that isn't declared last shifts every later sibling's auto-assigned IP

**Status:** 🔴 Open
**Severity:** Medium (silent IP reassignment on an incremental grow — no error, no warning, but every
later-declared node's auto-IP moves)
**Discovered:** 2026-08-11 — found in passing while building W2 (`axrange te deploy --update`,
`docs/WORK_ORDER_twin_incremental_update.md`); not caused by, and not fixed by, W2 itself.

**Symptom:** `axrange/twinconfig/expand.py`'s `count:` node expansion (e.g. `N_WORKSTATIONS` fanning
out `count: ${N_WORKSTATIONS}` workstation nodes) allocates each fanned-out node's IP address
POSITIONALLY over the WHOLE `nodes:` list, not scoped to the count-node's own block. Growing a
`count:` node that is not the LAST entry in `nodes:` therefore shifts the auto-assigned IP of every
node declared AFTER it in the file — even though nothing about those later nodes changed in the
authored config.

**Reproduced:** on all 5 shipped twin-configs (`sme-flat`, `corp-enterprise`, `datacenter`, `ics-ot`,
`dmz-perimeter`) — none of them declares its `count:` node last in `nodes:`, so all 5 are exposed.

**Why this matters for `--update` (W2) specifically:** an incremental `--update` classifies a change
by comparing an op's OLD persisted `inputs_resolved` against the NEW build's resolved inputs
(`plandiff.diff_plan`). A later sibling's `configure-cloud-init`/`attach-vm-to-vnet` IP shifting due
to this bug would classify that SURVIVING, otherwise-untouched node as **Mutated** — `--update` would
correctly REFUSE (per its own §3.1 semantics: mutation is always a hard refusal, never silently
redone), so this bug cannot cause `--update` to silently corrupt a live twin. But it does mean growing
a `count:` node via `--update` on an affected config will hard-refuse with a confusing "mutated"
report on unrelated nodes, unless the count node happens to be declared last.

**Root cause:** not yet root-caused precisely beyond "positional allocation across the flattened node
list rather than scoped to each `count:` block's own IP range" — needs investigation in
`axrange/twinconfig/expand.py`.

**Missing capability:** IP allocation for a `count:` fan-out should be scoped to that node's own
declared range/block, independent of sibling node declaration order — so growing one `count:` node
never perturbs another node's assigned IP.

**Workaround (today):** declare every `count:` node last in a twin-config's `nodes:` list (untested
whether this is fully sufficient — it only avoids shifting nodes declared textually after it; two
`count:` nodes in the same config may still interact).

**Related:** `docs/WORK_ORDER_twin_incremental_update.md` (found during W2's build, not fixed by it);
`axrange/twinconfig/expand.py`; the 5 shipped `twin-configs/*`.

---

## GAP-054 — Persisted `run-script` op inputs can silently diverge from deployed guest content after an out-of-band push; `--reapply` covers only future same-op-different-content drift

**Status:** 🔴 Open
**Severity:** Medium (state-store inaccuracy on a standing twin's already-`completed` ops; does not
corrupt the twin itself, but corrupts what `--update`/an operator can trust about its recorded state)
**Discovered:** 2026-08-31/09-02 — the CorpAPCN twin-fidelity loop pushed corrected `run-script`
responder content to 19 guests over QGA, out-of-band from the engine, because the only supported
alternative at the time was a full 138-VM redeploy (`docs/WORK_ORDER_responder_op_row_drift.md`).

**Symptom:** A `run-script` op's persisted `inputs_resolved.script_content` can differ from the text
actually delivered to and running on the guest, with nothing in the state store recording that a
divergence occurred. A subsequent `te deploy --update` (without `--reapply`) classifies such an op
`Unchanged` — comparing persisted-vs-newly-built inputs, never consulting the guest — and silently
keeps asserting a state it is not maintaining. If anything else later forces the op to re-run
(`--fresh`, a redeploy, an escalate-retry), the guest's newer content is silently **reverted** to the
stale persisted text, with no diff shown and no record a revert occurred.

**Impact:** exactly 19 `run-script` ops on the standing `te corpapcn-twin-0e4f7d58` (138 VMs, dev45)
are in this state today — and a further iteration-19 re-push (commit `128f498c`, 2026-09-02) added to
the drift rather than reducing it, since it was a personas/`tfc`-tier content change, not a
`run-script` `script_content` mutation `--reapply` covers. More generally, **any** out-of-band change
to any `config`-class op's delivered content, on any twin, produces the same defect — there is no
reason to think this was the last time the escape hatch gets used.

**Fix:** `--reapply` (`docs/WORK_ORDER_responder_op_row_drift.md` option A, CHANGELOG 2026-09-02)
gives a supported path **going forward** for the one narrow case (a content-only `run-script`
mutation) — it does not, and structurally cannot, retroactively repair a row that drifted before the
flag existed (no hash of the delivered content was ever recorded for those rows). The two rejected
options remain available if a broader fix is wanted later: Option B (record out-of-band drift
explicitly in the state store — needs a `SCHEMA_VERSION` bump that hard-fails every existing
`state.db`) or Option C (verify a content hash at `--update` time and fail loud on mismatch — needs a
new `OP_CATALOG` output for `OP-APP-030`, and only helps ops deployed after the change).

**Related:** `docs/WORK_ORDER_responder_op_row_drift.md`, CHANGELOG 2026-09-02 "Content-only reapply
for `--update`...", `docs/LESSONS.md` LES-205/LES-206/LES-207.

---

## GAP-055 — `load_config`'s gitignored `adapter.conf` fallback resolves via `Path.cwd()`, with no test-mode seam

**Status:** 🔴 Open
**Severity:** Low (test/dev-ergonomics hazard, not a production defect — the cwd fallback is
intentional operator convenience for interactive CLI use)
**Discovered:** 2026-09-02 — `test_apply_tap_plan_reshard_surfaces_dropped_gretap_in_warnings` reached
a real (gitignored) `./adapter.conf` and opened a real SSH session to its `OVS_SSH_HOST`, because the
test omitted a fake `teardown_backend` injection (`docs/LESSONS.md` LES-208) and `load_config(None)`
silently fell back to whatever `adapter.conf` happened to be sitting in the process's cwd.

**Symptom:** `axrange/adapter_proxmox/config.py`'s `load_config(None)` (or an equivalent no-path call)
resolves the config file relative to `Path.cwd()` with no way for a caller (a test, a sandboxed tool
run) to say "there is no config here, fail loud" or "use exactly this path and nothing else." Every
code path that eventually calls `load_config` inherits this — not just tests, but any tool invocation
run from an unexpected working directory.

**Impact:** a test's pass/fail (and here, its *runtime* — an ~11 s SSH timeout) can depend on which
directory it happens to run from and what `adapter.conf` (if any) is sitting there — this repo's own
worktree-based `/dev` pipeline runs subagents from varying cwds, so the same test can behave
differently in the main tree vs. an isolated worktree. The one instance found (LES-208) was a missing
test injection, not a `load_config` bug per se, but the underlying `Path.cwd()` fallback is what let a
missing injection turn into a real network call instead of an immediate, obvious failure.

**Fix:** give `load_config` an explicit test-mode/strict seam — e.g. an env var or parameter that
disables the cwd fallback and raises immediately when no explicit path is given — so a test missing an
injection fails fast and locally instead of reaching for a real host.

**Related:** CHANGELOG 2026-09-02 (commit `0b38f46b`), `docs/LESSONS.md` LES-208.

---

## GAP-056 — `drive: command` personas need `dnsutils`/`curl`/`ntpdate` baked into the image; unusable (except `icmp`) on a plain `debian-12-cloud@v1` twin

**Status:** 🔴 Open
**Severity:** Low (the driver fails loud on a missing tool by design — a usability gap, not a
correctness bug)
**Discovered:** 2026-09-02 — `docs/WORK_ORDER_protocol_faithful_traffic.md` §3.4/§5.2, at build time.

**Symptom:** `dig` (`dnsutils`), `curl`, and `ntpdate`/`chrony` are not present in the
`debian-12-cloud@v1` base image the shipped catalog clones from. A `command`-driven persona for
`dns`/`http`/`https`/`ntp` fails loud at start (`PersonaDriverError` naming the missing binary + its
Debian package) on any twin built from that image; only `icmp` (via `ping`, present by default) works
out of the box.

**Impact:** `drive: command` is unusable for 4 of its 5 v1 protos on any twin deployed from the stock
image, and adding the tools via a standing twin's `install:` list trips `--update`'s mutation refusal
(see GAP-054 above and CHANGELOG 2026-09-02 "Content-only reapply..."). The work order's own §5.2
already names the workaround (bake into the image, don't retrofit a standing twin) but no image
manifest change has landed yet.

**Fix:** extend `docs/IMAGE_BUILD.md`'s (or `docs/design/10-images.md`'s) bake recipe for
`debian-12-cloud` (or a new variant) to preinstall `dnsutils`, `curl`, and `ntpdate`/`chrony`, so a
freshly-built twin gets `drive: command` support for free. Until then, any live-verify or real use of
the driver needs a purpose-built image.

**Related:** `docs/WORK_ORDER_protocol_faithful_traffic.md` §3.4/§5.2, CHANGELOG 2026-09-02
"`drive: command`...".

---

## GAP-057 — `scripts/doc-stats.py`'s counter commands are cwd-relative (`.venv/bin/python`); a missing `.venv` in a worktree silently SKIPS the counter instead of failing, hiding real drift

**Status:** 🔴 Open
**Severity:** Medium (a doc-drift gate that can silently stop policing anything is worse than no gate,
in the specific worktree-without-venv case)
**Discovered:** 2026-09-02 — the `drive: command` build's own control run (commit `a48d4e5d`'s
message) found 2 of 3 "pre-existing failures on this branch tip" were `tests/test_pipeline_scripts.py`
tests failing in a `.venv`-less worktree; independently reproduced by code inspection this pass.

**Symptom:** `.claude-doc-stats.json`'s `counters.{ops,rules,tests}.command` all begin with the
literal argv element `.venv/bin/python`, executed via `subprocess.run(..., cwd=REPO)`. When `REPO`
resolves to a git worktree with no `.venv` of its own (a normal state — `.venv` is gitignored and
per-checkout), the command raises `FileNotFoundError`/`OSError`; `doc-stats.py`'s `_compute_one`
catches that, prints `"counter 'X' did not run (...) — not checked"` to stderr, and returns `None`.
`check()`'s claim loop then does `if actual is None: continue` — the counter's claims are **silently
skipped**, not flagged as a failure, so `--check` can print "No counter drift in the policed docs."
while having verified nothing. `tests/test_pipeline_scripts.py`'s
`test_configured_ops_counter_matches_op_registry` and `test_configured_rules_counter_matches_validator`
(which call `doc_stats.compute` directly, bypassing the skip-on-`None` swallow) fail instead, with an
`AssertionError: None != 40`-shaped message that gives no hint the real cause is a missing `.venv`.

**Impact:** every `/dev` pipeline stage that operates in an isolated worktree without its own `.venv`
set up either (a) gets two confusing, unrelated-looking test failures, or (b) if it runs
`doc-stats.py --check` directly rather than through pytest, gets a **false "no drift" pass** that has
silently checked nothing.

**Fix:** belongs upstream, in the workflow-template's generic `scripts/doc-stats.py`, not as a local
patch — either resolve `.venv/bin/python` relative to a properly-set-up repo root, or, more robustly,
treat a counter that "did not run" as a hard `--check` **failure** rather than a silent skip
(mirroring `doc-stats.py`'s own stated philosophy for a non-zero-exit counter: "a failed command
still prints, and a partial count is worse than none" — the same logic applies to a command that
couldn't run at all). Until fixed, any pipeline stage running in an isolated worktree should set up
(or symlink) a `.venv` before invoking `doc-stats.py`.

**Related:** CHANGELOG 2026-09-02 "`drive: command`..." (commit `a48d4e5d`'s control-run note), GAP-058
(a related but distinct cwd/worktree-resolution defect, in the commit-guard hook rather than the
counter commands).

---

## GAP-058 — The commit-guard hook resolves the repo via `CLAUDE_PROJECT_DIR` before the invoking tree's own `git rev-parse`, so a worktree-based pipeline stage is gated on a DIFFERENT tree's doc counters

**Status:** 🔴 Open
**Severity:** Medium (blocks a legitimate commit in an isolated worktree with a misleading error
message; does not corrupt any state)
**Discovered:** 2026-09-03 — reported by the orchestrator: a `code-writer` subagent working in
`.../scratchpad/wt/option-a-reapply` (a worktree whose own tree was internally consistent) was
hard-blocked by "CLAUDE.md claims 1956 pytest tests, source says 2021" — a drift that existed in the
MAIN tree (`/home/rocky/axRange`), not the worktree; reproduced by running `doc-stats.py --check`
directly from the main tree and getting the identical message.

**Symptom:** `scripts/hooks.py`'s `_resolve_repo()` (and `scripts/doc-stats.py`'s own copy of the same
function) checks `CLAUDE_PROJECT_DIR` **first** and only falls back to
`git rev-parse --show-toplevel` from cwd if that env var is unset or not a directory. Claude Code sets
`CLAUDE_PROJECT_DIR` to the harness's notion of "the project" — the MAIN checkout — for every subagent
regardless of which git worktree that subagent is actually cwd'd into and committing from. So a
`git commit` PreToolUse hook firing inside an isolated worktree evaluates `doc-stats.py --check` (and
any other repo-relative logic in the hook) against the **main tree's** files, not the worktree's own.

**Impact:** every worktree-based pipeline stage (the `/dev` pipeline's per-branch isolated worktrees)
inherits whatever drift/state the main tree happens to be in at commit time, unrelated to the
worktree's own correctness — a worktree that is internally clean can be blocked, and (the more
dangerous direction, not observed but implied by the same mechanism) a worktree that itself has drift
could conceivably be waved through if the main tree happens to be clean at that moment. The error
message names a counter mismatch with no indication the tree being checked isn't the tree being
committed, so the failure mode is confusing to debug from inside the blocked subagent.

**Fix:** in `scripts/hooks.py`'s (and `doc-stats.py`'s) `_resolve_repo()`, check whether cwd is inside
a git tree (`git rev-parse --show-toplevel`) **first**, and use `CLAUDE_PROJECT_DIR` only as the
fallback for when cwd is not inside any git tree at all (e.g. a hook invoked outside a repo). This is
upstream workflow-template code, not local to axRange — see `docs/LESSONS.md` LES-209 for the general
rule.

**Related:** `docs/LESSONS.md` LES-209, GAP-057 (a related but distinct cwd/worktree-resolution
defect, in the counter commands rather than the repo-resolution helper).

---

## GAP-059 — A twin-config param declared with an int default rejects a float `--param` override ("not a valid int"); the deploy gate had to work around it per-key

**Status:** 🔴 Open
**Severity:** Low (loud failure, never silent; a one-character edit to the config is the workaround —
but the error names a type, not the cause, and the correct fix is per-param rather than general)
**Discovered:** 2026-09-04 — during the deploy-gate build (CHANGELOG "Deploy gate BUILT"), when a cast
config declaring the perfectly natural `DEPLOY_MIN_ROLE_CONFIDENCE: 0` ("off") made
`--param DEPLOY_MIN_ROLE_CONFIDENCE=0.9` fail.

**Symptom:** `axrange/twinconfig/params.py`'s `_coerce_like` types a `--param` override against the
**declared default's Python type**. A config that writes a ratio-valued param as `0` or `1` (an int in
YAML) therefore makes every fractional override of it fail with a "not a valid int"-shaped
`TwinConfigParamError`. The message names the coercion, not the reason, so the operator's natural
reading is "this param does not take 0.9", when the real fix is to write the *default* as `0.0`.

**Impact:** narrow today, and the deploy gate is not affected — `gate.normalize_declared_thresholds`
coerces `DEPLOY_MIN_OCCUPANCY`/`DEPLOY_MIN_ROLE_CONFIDENCE` to `float` before `resolve_params` runs.
That workaround is deliberately **per-key**: widening `_coerce_like` to accept a float wherever an int
is declared would silently re-type every int param a config declares, and `N_WORKSTATIONS: 25` must
keep rejecting `=2.5` (a fractional VM count is a content bug, not an override). So the trap is still
live for **any future fraction-valued param** whose default happens to be authored as a whole number,
and each one has to be remembered individually.

**Fix (candidate, not taken):** let a param declare its type explicitly (`params: {X: {default: 0,
type: float}}`) rather than inferring it from the default's literal form; or keep the inference but
special-case an int default overridden by a float **only when the declared default is 0 or 1**, which
is where the ambiguity actually lives. Either is a twin-config vocabulary change and wants its own
decision; until then, author a ratio-valued default as `0.0`/`1.0` and add it to
`normalize_declared_thresholds`-style normalization if it is read by engine code.

**Related:** `axrange/twinconfig/params.py` (`_coerce_like`, `coerce_scalar`),
`axrange/twinconfig/gate.py` (`normalize_declared_thresholds`), CHANGELOG 2026-09-04 "Deploy gate
BUILT", `docs/WORK_ORDER_deploy_gate.md` §4.1.

---

## GAP-060 — `StateStore.__init__` CREATES the db file (and its parent directory) at whatever path it is handed, so a read-only verb pointed at a typo'd `--db` silently scores an empty store instead of failing

**Status:** 🔴 Open
**Severity:** Low (polish; the wrong-path outcome is a confusing empty result, not data loss or a
wrong deploy — and every write path is unaffected, since a deploy legitimately creates its store)
**Discovered:** 2026-09-05 — during the fidelity-scorecard build (CHANGELOG "Fidelity scorecard
BUILT"), the first purely **read-only** consumer of the state store. Pre-existing behavior, dating
from the store's original design; recorded here because the scorecard is where it first bites.

**Symptom:** `axrange/planner/state.py`'s `StateStore.__init__` runs
`self.db_path.parent.mkdir(parents=True, exist_ok=True)` and then `sqlite3.connect(...)`, which
creates the file if absent, then stamps a fresh schema into it. A read-only verb —
`te scorecard`, `te inventory`, `te status`, `list-deploys` — handed a mistyped or
wrong-directory `--db` therefore **succeeds against a brand-new empty store** rather than reporting
"no such state store". The user-visible result is "no deploys recorded" (or, for an explicit deploy
id, a not-found), which reads as *the deploy is gone* rather than *you are looking in the wrong
place*. The trap is sharpened by this project's actual layout: the real store is `~/.axrange/state.db`
while `_DEFAULT_DB_PATH` and most documentation examples show a relative `state.db`, so an operator
running from the repo root can create a stray empty `./state.db` and never notice.

**Impact:** narrow but recurring. It costs a diagnostic cycle each time ("the twin is gone?") and it
leaves stray empty `state.db` files around the filesystem. Nothing is corrupted and no live resource
is touched — the wrong-path store is simply empty. It is a *diagnosis* cost, not a correctness one,
which is why this is Low.

**Fix (candidate, not taken):** give `StateStore` an explicit `create: bool = True` and have every
read-only caller pass `create=False`, raising a typed `StateStoreMissingError` naming the path when
the file is absent (the deploy path keeps today's behavior — creating its store is correct). A
cheaper interim: have the read-only CLI verbs `os.path.exists()`-check the resolved db path before
constructing the store and exit with "no state store at <path>". Either is a small change with a
wide blast radius across call sites, so it wants its own pass rather than riding on a feature build.

**Related:** `axrange/planner/state.py` (`StateStore.__init__`, `_check_schema_version`),
`docs/LESSONS.md` LES-031 (the schema-version guard on every open — the same constructor),
`docs/WORK_ORDER_fidelity_scorecard.md` §7 preflight (the runbook now names the real path
explicitly because of this), CHANGELOG 2026-09-05 "Fidelity scorecard BUILT".

---

## GAP-061 — QUIC (443/udp) is observed but the twin serves no real HTTP/3, so it can never answer a protocol-aware probe

**Status:** 🔴 Open (accepted for v1 — **D7 (a)**, `docs/WORK_ORDER_protocol_faithful_services.md` §10)
**Severity:** Low (honest, reported gap — it is excluded from row 5's denominator with a reason,
never scored as a failure; and it is overwhelmingly an *external* listen, so it lands on the
aximprint sink rather than on a twin host)
**Discovered:** 2026-09-04 — writing the server half of row 5
(`docs/WORK_ORDER_protocol_faithful_services.md` §4). Recorded on landing S1/S3/S5, 2026-09-05.

**Symptom:** the CorpAPCN export observes **34** `443/udp` listens, **33 of them external**. Nothing
axRange deploys speaks HTTP/3: nginx in Debian main is built without it, so serving QUIC means a
bespoke image bake (`docs/IMAGE_BUILD.md` §5.1) for one port. The twin therefore cannot return a
protocol-valid QUIC response, and a client that dials 443/udp gets nothing back.

**Impact:** none on the deployable set today — the shipped cast places **no local `443/udp`
listener** (verified against `twin-configs-cast/corpapcn-twin/corpapcn-twin.topology.json`: the 34
facts are all on non-local entities). It becomes visible the moment a source's own web tier is
observed serving HTTP/3, or when the aximprint sink starts reproducing external services faithfully
(§7.4, aximprint's call). Reproducing 443/**tcp** is unaffected and is real (nginx + a self-signed
cert).

**How it is reported, not hidden:** `axrange.fidelity.probe.classify_listen_port` classifies
`443/udp` as `no protocol-aware probe` and row 5 publishes it in `excluded_facts` with that reason
and a count. That is the whole point of the exclusion machinery: an unexplained filter is as
dishonest as a vacuous pass (LES-101), and an *unexcluded* QUIC fact would sit permanently
`inconclusive` and block row 5 from ever passing for a reason that has nothing to do with the twin.

**Fix (candidate, not taken):** bake an HTTP/3-capable nginx (quiche/BoringSSL) into a dedicated
web-tier image and add a `quic` probe method built on a stdlib-expressible Initial packet. Both
halves are real work for one port; neither is on the first golden's path.

**Related:** `docs/WORK_ORDER_protocol_faithful_services.md` §4 (the protocol→server table), **D7**;
`axrange/fidelity/probe.py` (`_EXCLUSIONS`, `classify_listen_port`);
`docs/design/13-golden-twin-fidelity.md` §3.1 row 5; GAP-056 (the client-side sibling: the tools a
`drive: command` persona needs are also an image-bake question).

---

## GAP-062 — 9997/tcp (Splunk universal forwarder) has no free real server and no stdlib-expressible probe

**Status:** 🔴 Open (accepted — **D6 (a)**, `docs/WORK_ORDER_protocol_faithful_services.md` §10)
**Severity:** Low (one observed host, one port; reported with its reason, never failed)
**Discovered:** 2026-09-04 — the protocol inventory of
`docs/WORK_ORDER_protocol_faithful_services.md` §4. Recorded on landing S1/S3/S5, 2026-09-05.

**Symptom:** `10.20.194.240` (inferred `monitoring-collector`) is observed listening on `9997/tcp`.
That is Splunk's forwarder receiver: a **proprietary** framing with no free server implementation and
no published handshake a stdlib probe could send. The twin's current stand-in was an **echo**
responder — precisely the synthetic filler §8.2's ruling removes, and useless anyway, since a
protocol-aware probe of an echo gets its own bytes back.

**Impact:** one `(host, port)` fact out of the observed local set is permanently unreproducible as a
*real* service. Recording it honestly is strictly better than the alternatives: an echo makes the
port answer while making the protocol wrong (and a detection rule matches protocol semantics, never a
port number), and silence draws an ICMP unreachable that Zeek scores as a fabricated reverse local
edge (**D8**'s measured reason the echo survives only where no real server exists).

**Related, same category:** `3389/udp` is also classified `no protocol-aware probe`, for a different
reason — RDP-UDP is a session-acceleration channel negotiated *inside* an already-established TCP
session, so there is no standalone datagram handshake to send. The RDP fact is carried by `3389/tcp`,
which the twin does reproduce for real (S3's `enable-rdp.ps1`).

**Fix (candidate, not taken):** none that is honest. A Splunk forwarder receiver would need Splunk;
substituting a different daemon on 9997 would reproduce the port number and not the protocol, which
is the thing this work order exists to stop doing. If a source ever depends on it, the answer is a
reference-ingest fact ("this host runs Splunk"), not a fake listener.

**Related:** `docs/WORK_ORDER_protocol_faithful_services.md` §4/§10 (**D6**, **D8**);
`axrange/fidelity/probe.py` (`_EXCLUSIONS`); aximprint's `scripts/check_role_fixed_point.py`, which
already carries 9997 as a permanent declared exception.

---

## GAP-063 — 8006/tcp is reproduced as plain HTTPS, not as Proxmox; protocol-valid, application-unfaithful

**Status:** 🔴 Open (accepted — **D6 (a)**, `docs/WORK_ORDER_protocol_faithful_services.md` §10)
**Severity:** Low (row 5 passes on it *correctly* — its rule is protocol-**valid**, not
application-equivalent — but a reader who sees "8006 answers" may infer more than is true)
**Discovered:** 2026-09-04 — the protocol inventory of
`docs/WORK_ORDER_protocol_faithful_services.md` §4. Recorded on landing S1/S3/S5, 2026-09-05.

**Symptom:** `10.80.76.108` (inferred `hypervisor`) is observed listening on `8006/tcp`, which in the
source is the **Proxmox VE web/API port**. The twin serves that port with **nginx over TLS**: a real
HTTPS listener with a self-signed lab certificate, which is genuinely HTTPS (`8006/tcp` maps to the
`https` probe method) but is *not* a Proxmox API. Its certificate, its paths, its API surface and its
responses are all different.

**Impact:** row 5 records a protocol-valid response and passes the fact, which is the correct
verdict under its own rule — but the twin does not reproduce what a Proxmox-aware detection rule or
an API-shaped client would see. This is the general boundary spec §9 already draws (payload content
out of scope, certificates always lab-synthetic); it is recorded here because 8006 is the one
observed port where the gap between "the protocol is right" and "the application is right" is wide
enough to mislead.

**Fix (candidate, not taken):** none proposed for v1. Reproducing a Proxmox API surface would mean
either running PVE inside the twin (a nested-virt scope explosion) or hand-writing an API mock, which
is synthetic generation of exactly the kind §8.2's ruling removes. The honest improvement is a
reference-ingest fact naming the product, plus (later) an `application`-level clause on row 5 that
scores the *service identity* separately from the protocol.

**Related:** `docs/WORK_ORDER_protocol_faithful_services.md` §4/§10 (**D6**), §11 ("TLS certificates
are always self-signed lab certs"); `axrange/fidelity/probe.py` (`_TCP_METHODS[8006] = "https"`);
`docs/design/13-golden-twin-fidelity.md` §9.

---

## GAP-064 — Ephemeral/dynamic observed ports are sockets, not services; the twin never reproduces them and must never be scored for it

**Status:** 🔴 Open (accepted, by design — `docs/WORK_ORDER_protocol_faithful_services.md` §4's last
row: "observed-but-not-reproduced; report in that state, never fake a listener")
**Severity:** Low (fully handled by the scorecard's denominator classification; recorded so the
*reason* is written down rather than living only in a code comment)
**Discovered:** 2026-09-04 — the observed listen inventory of
`docs/WORK_ORDER_protocol_faithful_services.md` §2. Recorded on landing S1/S3/S5, 2026-09-05.

**Symptom:** the observed local listen set includes `49667/tcp`, `49669/tcp`, `49682/tcp`,
`55390/tcp`, `57086/tcp`, `63153/tcp` (Windows RPC endpoint-mapper allocations and other ephemeral
sockets, all ≥ 49152, the IANA dynamic/private floor) and `7680/tcp` (Windows Delivery Optimization's
peer-to-peer update cache). None of them is a *service* in any sense the twin could declare or
reproduce: the RPC ports are allocated per-boot by the endpoint mapper, and a capture that sees one
is recording an artefact of its own window.

**Impact:** none, provided nothing tries to reproduce them. The failure mode this entry guards
against is the opposite one — the retired echo responder bound whatever port list it was handed, so a
twin could be made to "answer" `57086/tcp` and *look* more faithful while being less so. A twin that
serves AD DS genuinely does open dynamic RPC ports, from the same allocator the source used; they
simply will not be the same numbers, and matching numbers would be fabrication.

**How it is reported, not hidden:** `axrange.fidelity.probe.classify_listen_port` classifies every
port ≥ `EPHEMERAL_PORT_FLOOR` (49152) and `7680/tcp` as `not a service`, and row 5 publishes each
excluded fact with its reason and a count. The S3 fixture's DC declares its ten *service* ports and
deliberately omits `49669`/`49682` for the same reason.

**Fix (candidate, not taken):** nothing to fix — this is the correct behavior. The one improvement
worth considering later is a row-5 sub-clause that asserts a DC has *some* dynamic RPC port open
(the endpoint mapper is working) without asserting *which*, which is the only part of this that is
actually reproducible.

**Related:** `docs/WORK_ORDER_protocol_faithful_services.md` §2/§4 (**D8**);
`axrange/fidelity/probe.py` (`EPHEMERAL_PORT_FLOOR`, `_EXCLUSIONS`, `classify_listen_port`);
`docs/design/13-golden-twin-fidelity.md` §3.1 row 5's "as built 2026-09-05" note; LES-101.

---

## GAP-065 — An app bound to two hosts is provisioned on only the FIRST; the other host silently gets nothing, and three shipped apps do exactly this

**Status:** 🔴 Open
**Severity:** Medium (silent — the deploy reports success and the second host simply never receives
the app; three shipped `target-environments/` apps are affected today)
**Discovered:** 2026-09-05 — while adding the declared-listen set
(`docs/WORK_ORDER_protocol_faithful_services.md` S1). A first cut refused multi-bound apps outright
and was caught immediately by `target-environments/enterprise-multisite/scenario.yaml`, which
plans in the suite (LES-185: shipped content found what no fixture would have).

**Symptom:** `axrange/planner/pass1_emit.py`'s `_emit_apps` resolves an app's host with
`next((b.host for b in bindings if b.app == app_id and b.host), None)` — the **first** matching
binding. Every later `{app: X, host: Y}` entry for the same app is discarded with no error, no
warning and no op. The deploy completes green and `Y` has none of `X`'s packages or scripts.

**Live in shipped content, and deliberately authored:**
`target-environments/enterprise-multisite/scenario.yaml` binds `db-engine` to `db-prod` **and**
`db-dw`, `etl-runner` to `etl01` **and** `etl02`, and `branch-file` to `br1-fs` **and** `br2-fs` —
each with an author comment stating the two-host binding is intended ("used by BOTH the production
OLTP DB and the data warehouse"). So `db-dw` has no postgresql, `etl02` has no python/ETL tooling,
and `br2-fs` has no samba, and none of that has ever been visible.

**Impact:** a scenario silently deploys a subset of what it declares. It is worse than an ordinary
missing feature because the IR *reads* as if the app is on both hosts, so the gap is invisible in
review as well as at runtime — and now also in the fidelity scorecard's row 3, which compares a
cast group's declared installs against op rows and would score the second host as unreproduced
without being able to say why.

**Partially fenced 2026-09-05:** `_emit_apps` now **refuses** a multi-bound app that declares
`listen:` (`App.listen`, S1). That case is not merely incomplete but wrong: the declared listen set
is joined over every binding (`_declared_listen_by_host`), so the twin's published inventory would
claim a service on a host that installs nothing. The general silent-drop is deliberately left
untouched — refusing it outright makes a shipped scenario un-plannable, which is a bigger change
than the build that found it.

**Fix — a DECISION FOR THE USER, not an implementation call, and it is queued as one**
(CHANGELOG 2026-09-05 "Protocol-faithful services BUILT", "Decisions queued"). Two options, and
whichever is chosen changes shipped content's identity:

- **(a) Fan out** — emit the app's ops for every bound host, which is what the content plainly means
  and what `twinconfig.expand` already does per node. `db-dw`, `etl02` and `br2-fs` gain the
  packages their author intended. Adds new op ids to `enterprise-multisite`'s plan.
- **(b) Refuse** the multi-bound binding outright and split the three shipped apps into one entry per
  host. Honest and explicit, but it rewrites the scenario's IR, so its `ir_hash`/`plan_id` moves —
  and `plan_id` keys VMIDs, VM names, zone, vnets and the underlay bridge, and is how a standing
  deploy is found (LES-126). Any live deploy of that scenario is orphaned.

Neither is a drive-by, which is why the build that found it fenced only the case it made *wrong*
(a multi-bound app declaring `listen:`) and left the general silent drop untouched. A third,
non-exclusive step worth taking either way: make the drop **visible** — a validator warning or an
emission-time notice naming the discarded bindings — so the gap stops being invisible while the
decision is pending.

**Related:** `axrange/planner/pass1_emit.py` (`_emit_apps`, `_declared_listen_by_host`),
`target-environments/enterprise-multisite/scenario.yaml`, `docs/LESSONS.md` LES-001/LES-185,
`docs/WORK_ORDER_protocol_faithful_services.md` S1 (review S4).

## GAP-066 — `axr-topology/1.4`'s new payload (`services[].app`, `observations`, per-listen evidence class) is published by the producer and consumed by nothing; the scorecard still infers protocol from a port table

**Status:** 🔴 Open
**Severity:** Low (an **unconsumed seam, not a breakage** — nothing regresses, and the probe keeps
working off `listen_ports`, which 1.4 retains; the cost is accuracy left on the table and a stale
`unclassified` evidence bucket)
**Discovered:** 2026-09-05 — installing the first golden's candidate cast
(`twin-configs-cast/corpapcn-golden/`, CHANGELOG 2026-09-05 (later)) and verifying it read-only
against `axrange/fidelity/`.

**What the producer now publishes** (aximprint's services build, CHANGELOG 2026-09-05
"Protocol-faithful services BUILT"), measured on the installed golden bundle:

- **`services[]` per node** — 403 rows across **365 of 786 nodes**, each `{port, proto, app,
  evidence_class, app_evidence_class}`, with the `app` token deliberately spelled in the **sensor's**
  vocabulary (`dns`, `ntp`, `krb_tcp`, `ldap_tcp`, `ldap_udp`, `smb`, `ssl`, `rdp`, `http`, `ssh`,
  `dce_rpc`, `dhcp`) so it joins against `edges[].proto`.
- **A top-level `observations` block** — 354 DNS names and 11 identity principals.
- **A per-listen `evidence_class`** on every `services[]` row.

**What axRange consumes: none of it.** `axrange/fidelity/reference.py` reports the version key
(`bundle_schema`, published as of the in-flight Console S1 rider — reporting a version is not
consuming a payload) and reads `listen_ports` for row 5's denominator, exactly as it did against
`1.3`. Two concrete consequences, both visible in run 1's document shape:

1. **The probe picks its method from a port table** (`probe.classify_listen_port`) when the bundle
   now carries the observed **application per listen**. A service on a non-standard port is
   mis-methoded or excluded as *no protocol-aware probe*, and the bundle already knows better.
2. **Row 5's evidence mix counts every fact `unclassified`** — `rows.py` explicitly notes that
   "schema_version 1.3 carries no per-listen evidence class", which stopped being true the moment
   1.4 landed. The producer now labels each listen `observed` and the scorecard declines to say so.

**Why it is filed rather than fixed:** the fix touches row 5's method selection and its evidence
mix — the two things scorecard run 1 has just been measured against — so changing them in the same
window would make run 1 and its successor incomparable for a reason unrelated to the twin. It is
also entangled with **S4** (zones/vhosts from observed names), whose own consumer is gated on
**aximprint GAP-019** (a carrier for observed TLS/HTTP/SMB), and with the scorecard's **S5**.
Sequence it with those.

**Fix sketch:** read `services[]` when `bundle_schema` ≥ `axr-topology/1.4`, prefer its `app` over
the port table for method selection (falling back to the table for `app: null` rows — 12 of the 403),
carry the per-listen `evidence_class` into row 5's mix instead of `unclassified`, and keep the
`1.3` path as the fallback so an older bundle still scores. Cross-check `observations.dns` /
`observations.identity` only when a consumer needs them (S4) — publishing them is not a reason to
read them.

**Related:** `axrange/fidelity/{reference,rows,probe}.py`,
`docs/WORK_ORDER_protocol_faithful_services.md` §6.1/§11/§12,
`docs/WORK_ORDER_fidelity_scorecard.md` §8.5/§7.1, `docs/LESSONS.md` LES-222/LES-228, aximprint
GAP-019.

## GAP-067 — A standing O2 Suricata sensor's capture is not restart-persistent (bare `-D`, no unit), and there is no supported way to re-assert a standing surface

**Status:** 🔴 Open
**Severity:** **High** (a standing sensor can be silently dead for weeks — measured at **40 days**
here — and the only repair path today is "stand up a new one")
**Discovered:** 2026-09-06 — preflight for the services §9.2 live-verify on `dev45`
(CHANGELOG 2026-09-06 "Services §9.2 gated `dev45` live-verify PASSED").

**Symptom.** The standing `o2-suricata` sensor (VM 883610) was up, `o2 status` reported it present,
and **Suricata had been dead since 2026-07-27 06:05** ("signal received"). `eve.json` was frozen to
the nanosecond; only the Loki shipper survived. Nothing anywhere reported a problem.

**Root cause, two halves.**

1. **No supervision.** `deploy-sensor`'s render launches Suricata as a bare `-D` daemon. The
   2026-07-17 supervision overhaul (CHANGELOG) moved **tcpdump and zeek** to named transient
   `systemd-run --unit=` units with a fail-loud `systemctl is-active` start check **and never
   covered Suricata**, whose own start-check bug had been fixed separately in 2026-06-23. So the
   one sensor type the standing surface actually runs is the one with no restart path.
2. **No re-assert path.** `o2control.control.stand_up_surface` **returns early on a `completed`
   deploy**, which is correct for idempotence and leaves an operator with a dead process no
   supported command can restart. The workaround used here was to stand up a **second, dedicated**
   surface — which worked (15/15 ops, 1.21 min) but is not a repair.

**Recurrence (2026-09-21, protocol-engine live-verify on dev45).** Found dead again — silent since
the same 2026-07-27 date as the original discovery, i.e. it has never run since, and no fix has
landed against this entry. On-sensor verification during that run hit a second, separate finding:
the standing `o2-suricata` sensor's own image ships **no `python3` and no `tcpdump`**, so confirming
what the sensor was or wasn't seeing needed an install on the sensor itself before any check could
run at all. That is a gap in the sensor image, not the supervision fix below, and is unresolved
independently of it — track it alongside (a).

**Fix sketch.** (a) Launch Suricata as `systemd-run --unit=axr-sensor-<safe> --collect` with the
same fail-loud start check tcpdump/zeek already use — a small, well-precedented change, and the
one that removes the failure mode entirely. (b) Add a freshness signal to `o2 status`: the newest
`eve.json`/pcap record timestamp, or a byte counter that must advance across two reads, so
"present" stops being mistakable for "capturing" (LES-247). (c) Decide and build a re-assert verb
(`o2 deploy --reassert`, or a per-op force) — see GAP-070 for why re-running the *whole* surface
against current code is not safe today.

**Related:** `axrange/config_exec/render.py` (`deploy-sensor`), `axrange/o2control/control.py`,
CHANGELOG 2026-07-17 (the supervision fix), CHANGELOG 2026-06-23 (Suricata's start-check fixes),
`docs/LESSONS.md` LES-247/LES-079/LES-086, GAP-070.

## GAP-068 — A standing surface's manifest is persisted nowhere; it can only be reconstructed from op inputs

**Status:** 🔴 Open
**Severity:** **Medium** (recoverable by hand, but the recovery is archaeology and the reconstructed
manifest is a guess until a plan-id match confirms it)
**Discovered:** 2026-09-06 — services §9.2 live-verify on `dev45`, while trying to re-assert the
standing surface.

**Symptom.** The manifest that produced `o2-surface-025c0709` (the standing surface: which sensors,
which transport CIDR, which export settings) exists in **no** checked-in file and in **no** state
store column. Recovering it required reading the deploy's persisted op inputs back out of the DB and
then **brute-forcing the plan id** until it matched (`o2-surface-acd8d24a-o2`).

**Why it matters beyond inconvenience.** The surface is the longest-lived thing on the cluster —
the twins around it are disposable by design — so it is precisely the artifact whose definition
should outlive any one operator session. A `te` twin has its config checked in under `twin-configs/`;
a `tap` plan and a `tfc` profile are files; the **surface alone** is a command someone once typed.

**Fix sketch.** Either persist the expanded manifest with the deploy row (an `o2` sibling of the
`ir_hash`/`plan_id` provenance the twin tiers already carry), or ship the surface manifest as
checked-in content like every other tier's spec (`o2-configs/<surface>/…`) with the deploy row
naming the file it came from. The first is a state-store change; the second is a convention plus a
CLI flag, and matches how the rest of the product already works.

**Related:** `axrange/o2control/control.py`, `axrange/planner/state.py`, GAP-067, GAP-070.

## GAP-069 — Sensor resolution is state-DB-local, and the error text sends the operator to a command that will contradict it

**Status:** 🔴 Open
**Severity:** **Medium** (a hard stop with a misleading message; no data loss, but it cost the
live-verify its whole planned tap path)
**Discovered:** 2026-09-06 — services §9.2 live-verify on `dev45`.

**Symptom.** `tap deploy` failed at resolve with:

```
unknown sensor 'o2-suricata' -- not in the standing O2 inventory (axrange o2 status)
```

The sensor **exists**, is a live VM, and `axrange o2 status` **confirms it** — when run against the
state DB the surface was deployed into (`/home/rocky/axr-product/deploy-dev45/state.db`). The tap
was being applied from a different (scratch) DB, and sensor resolution reads the **o2 deploy row**,
not the cluster. So the message points at a command that, run the obvious way, proves the message
wrong.

**Root cause.** The standing O2 inventory is a **state-store** concept, not a cluster concept, and
every tier's `--db` is independent. Nothing in the error names the store it searched.

**Fix sketch.** Name the store in the message (`… not in the standing O2 inventory in
<path/to/state.db> (axrange o2 status --db <that path>)`), which turns a contradiction into an
instruction. Longer term this is the same question as GAP-068: if a surface's identity lived in a
file rather than only in one DB, cross-DB resolution would be possible at all.

**Related:** `axrange/o2control/taps.py` (sensor resolve), `axrange/o2control/control.py`,
`docs/LESSONS.md` LES-246, GAP-068, GAP-073.

## GAP-070 — A standing deploy predating the 2026-07-27 vnet-naming change cannot be re-asserted, and the attempt silently builds parallel SDN infrastructure

**Status:** 🔴 Open
**Severity:** **High** (a re-assert that gets *further* than this one did would move a standing
sensor's NIC onto an address-less bridge — a silent capture outage on the component whose whole job
is not to have one)
**Discovered:** 2026-09-06 — services §9.2 live-verify on `dev45`, during the surface re-assert
that failed for an unrelated reason (GAP-071/GAP-072) after its SDN ops had already run.

**Symptom.** Five SDN ops completed and **created a second vnet `o2vb904f`** in the existing shared
zone `o2zbe2d1`, with **the same CIDR** as the standing surface's vnet. The re-assert adopted the
zone (whose name is stable) and adopted **nothing** of the vnet.

**Root cause.** Commit `7d33854e` (2026-07-27) made vnet names **plan-unique**. The standing surface
was deployed **one day earlier**, so its vnet carries the old name shape and current code cannot
compute it — Model C's check-then-act looks for a name that will never be there, finds nothing, and
correctly creates. Every adoption path that keys on a deterministic name has this property: a naming
change is retroactively un-adoptable.

**What nearly happened.** The run stopped at `clone-vm`. Had the image binding resolved, the next
ops would have attached the sensor VM to the **new** vnet — an address-less bridge with no subnet —
silently ending the standing sensor's capture.

**Fix sketch.** A **duplicate-CIDR-in-zone check at `create-vnet`** would have caught this
immediately and is the cheapest guard (two vnets with one CIDR in one zone is never intentional).
Beyond that: record the resource's actual name with the op row so adoption can key on **what was
created** rather than on what the current code *would* create — the general answer, and the one
that survives the next naming change.

**Residue on `dev45`:** the stray vnet is inert (no IP, no subnet, no VM) and awaits a manual
`pvesh delete /cluster/sdn/vnets/o2vb904f` + `pvesh set /cluster/sdn`. The failed deploy row
`o2-surface-5274be37` is **deliberately not destroyed**, because its rows include the **adopted
shared zone** and a destroy would sweep it out from under the standing surface.

**Related:** `axrange/adapter_proxmox/naming.py`, `axrange/adapter_proxmox/rest.py`
(`op_create_vnet`), `axrange/planner/teardown.py`, commit `7d33854e`, `docs/LESSONS.md` LES-248,
GAP-067, GAP-068.

## GAP-071 — `clone-vm` validates `template_vmid` BEFORE its adoption check, so adopting an existing VM requires a binding it never uses

**Status:** 🔴 Open
**Severity:** **Medium** (blocks every re-assert/resume path over a VM that already exists, for a
value the op is about to not need)
**Discovered:** 2026-09-06 — services §9.2 live-verify on `dev45`, in the failed surface re-assert.

**Symptom.** The re-assert failed with `template_vmid missing — check image binding` on a host whose
VM **already existed and would have been adopted**. Model C's whole point is check-then-act; here
the check is unreachable because a precondition for the *act* is enforced first.

**Root cause.** `axrange/adapter_proxmox/rest.py` (~lines 668–679) resolves and validates the image
binding's `template_vmid` at the top of `op_clone_vm`, before the "does a VM with this deterministic
name already exist?" branch.

**Fix.** Move the adoption check above the binding resolution; resolve `template_vmid` only on the
create path. Small and local, but it changes an op's failure surface, so it wants its own test:
adopt-an-existing-VM-with-no-binding must succeed, create-with-no-binding must still fail loudly.

**Related:** `axrange/adapter_proxmox/rest.py` `op_clone_vm`, `docs/ADAPTER.md` §3 (Model C),
GAP-070, GAP-072.

## GAP-072 — A missing image binding is silent at plan time and only fails on the cluster, after real infrastructure has been created

**Status:** 🔴 Open
**Severity:** **High** (the failure lands mid-deploy with cluster state already changed — here five
SDN ops and a stray vnet, GAP-070 — and the same shape has been open as GAP-001 since 2026-06-26)
**Discovered:** 2026-09-06 — services §9.2 live-verify on `dev45`.

**Symptom.** An images root with no binding for a referenced image plans **cleanly**: the op's
`template_vmid` input is persisted as `null` and nothing objects. The deploy then runs every
preceding op — SDN zone, vnet, subnet, bridge — and dies at `clone-vm` with `template_vmid missing`.

**Root cause.** Nothing between the manifest loader and `clone-vm` treats an unresolvable binding as
an error. `None` flows through pass-1 emission into the persisted inputs, so the state store now
records an op whose inputs are known-unusable.

**Fix sketch.** Refuse at **plan** time: a `null` `template_vmid` for any emitted `clone-vm` fails
the plan, naming the image, the node and the images root searched. This is the narrow, immediately
buildable half of **GAP-001**'s item 1 (pre-flight template validation) — that gap asks for cluster
verification too; this one only asks that a binding which does not exist locally never reach the
cluster.

**Related:** `axrange/manifest/`, `axrange/planner/pass1_emit.py`,
`axrange/adapter_proxmox/rest.py`, **GAP-001** (the same failure class, cluster-side),
`docs/LESSONS.md` LES-248/LES-139.

## GAP-073 — `tap validate` returns PASS against a `failed` surface deploy whose sensor has no vmid

**Status:** 🔴 Open
**Severity:** **Medium** (a green validate directly ahead of a deploy that cannot work — the exact
shape LES-177 exists to warn about)
**Discovered:** 2026-09-06 — services §9.2 live-verify on `dev45`, after the failed surface
re-assert left a `failed` `o2` deploy row in the scratch store.

**Symptom.** With the scratch DB holding a **failed** surface deploy (`o2-surface-5274be37`) whose
sensor never reached `clone-vm` and therefore has **no vmid**, `tap validate` reports **PASS**. The
subsequent `tap deploy` cannot bind anything.

**Root cause.** Tap validation checks the plan's shape and the *presence* of a named sensor in the
inventory; it does not check the sensor's **deploy state** or that it resolved to a vmid.

**Fix sketch.** Make the sensor-resolution check part of validate, not only of deploy: refuse (or
warn loudly, if validate must stay non-cluster-touching) when the referenced sensor's owning deploy
is not `completed`, or when its resolved `vmid`/`transport_ip` is absent — and name which. Pairs
with GAP-069's error-text fix, since both are the same "resolve the sensor properly, and say what
you found" work.

**Related:** `axrange/o2control/taps.py`, `docs/LESSONS.md` LES-177/LES-246, GAP-069.

## GAP-074 — A Kerberos client needs realm configuration no twin ships, so no automated consumer can obtain a ticket

**Status:** 🔴 Open
**Severity:** **Medium** (blocks unattended Kerberos traffic entirely; the operator workaround is a
documented, disclosed guest write)
**Discovered:** 2026-09-06 — services §9.2 live-verify on `dev45`, step 3f.

**Symptom.** `kinit alice@CORP.AXR.LAB` cannot work on a fresh Linux client: `krb5-user` ships no
realm configuration, the twin's own resolver serves no `_kerberos._tcp` SRV records, and nothing
points the client at the DC. The verify proceeded by writing `/etc/krb5.conf` on `probe-client` by
hand — prescribed by the README, **disclosed in the run record**, and still a guest write axRange
did not make.

**Consequence.** A `drive: command` persona (or any future automated consumer) can produce DNS,
HTTP, NTP and ICMP unattended but **cannot produce Kerberos**, because the precondition is
client-side state no tier authors. Row 5's Kerberos evidence is therefore operator-driven today.

**Fix sketch.** Three candidates, in increasing generality: (a) a `run-script` in the twin content
that writes a minimal `krb5.conf` from the twin's own realm + DC address — cheap, and it makes the
fact twin content rather than tribal knowledge; (b) have the DC's DNS zone serve the standard
Kerberos SRV records, which is what a real domain does and removes the client-side file entirely;
(c) bake `krb5-user` + a realm template into the client image (which GAP-056 already wants for the
tool set). (b) is the faithful one and belongs with S4's observed-DNS work.

**Related:** `twin-configs/svc-verify/README.md` step 3f,
`docs/WORK_ORDER_protocol_faithful_services.md` §9.2, GAP-056, `docs/design/13-golden-twin-fidelity.md`
row 5.

## GAP-075 — LLMNR on a stock Debian cloud image defeats the listen-set exclusivity criterion

**Status:** 🔴 Open
**Severity:** **Low** (an unachievable criterion, not a broken twin — but it makes a stated pass
condition unusable as written)
**Discovered:** 2026-09-06 — services §9.2 live-verify on `dev45`, step 2.

**Symptom.** `svc-web` serves exactly its declared `443/tcp` **and also** binds LLMNR on
`5355/tcp+udp` on the **wildcard** address, put there by the image's `systemd-resolved`. §9.2's
criterion — *"the non-loopback listener set contains nothing beyond the declared set + `tcp/22`"* —
therefore cannot hold on a stock Debian cloud image, for a service the twin never asked for. (The
two bind9 hosts are unaffected: their recipes displace `systemd-resolved` outright.)

**Why it is not just a doc fix.** The same criterion is what a fidelity row would use to say "this
host serves exactly what was observed". An image-supplied listener is the image's, not the twin's,
and scoring it against the twin is a category error (LES-250).

**Fix sketch.** Two honest options, and a third that is not: (a) publish a per-image **listener
baseline** (measured once at image-build time) and state exclusivity as *declared ∪ baseline*;
(b) disable LLMNR in the image bake (`systemd-resolved`'s `LLMNR=no`), which also removes a real
LLMNR-poisoning surface from every Linux host — note that `ad-domain-lab`'s LLMNR content depends on
Windows hosts, not these; (c) hand-maintaining per-twin exceptions, which the next image update
breaks. Prefer (a), since it generalizes to every image-supplied socket.

**Related:** `docs/WORK_ORDER_protocol_faithful_services.md` §9.2 step 2,
`twin-configs/svc-verify/README.md`, `axrange/fidelity/rows.py` rows 4/5,
`docs/LESSONS.md` LES-250.

## GAP-076 — Suricata's `tls` eve-log type produces no records on a mirrored sensor although it is enabled

**Status:** 🔴 Open
**Severity:** **Medium** (the flow is classified `app_proto tls`, so the protocol IS identified —
but SNI, subject, issuer and JA3 all live in the missing record, and those are the fields any
TLS-aware detection or fidelity check needs)
**Discovered:** 2026-09-06 — services §9.2 live-verify on `dev45`, step 4.

**Symptom.** On the dedicated `svc-suricata` sensor (Suricata **7.0.10**), with `tls` enabled in
`eve-log.types` and `extended: yes`, nine flows for a real, completed TLS 1.3 handshake are recorded
as `flow` events with `app_proto: tls` — and **zero `tls` event records** are emitted. Every other
enabled type behaved (dns 30, smb 15, krb5 5, dcerpc 3).

**Hypothesis (labelled as such, not a cause).** The tap scopes mirror **rx-only** per port, by
design, so a client-side and a server-side port each contribute one direction; if the two halves of
the handshake arrive on different tap points, Suricata's TLS parser may never see a complete
bidirectional handshake on a single flow even though the flow itself is classified. This is a
hypothesis with a cheap test: bind a single `port`-scope tap with `both` direction to one host and
re-check.

**Why it matters.** Row 5 scored TLS a pass on substance here (the app_proto is definitively not
bare `tcp`), but every richer claim — "the twin reproduced the observed SNI" — needs the record that
is not being written. S4 (observed TLS names) has no evidence channel without it.

**Fix sketch.** Run the single-port `both`-direction test to confirm or kill the hypothesis; if
confirmed, either document it as an inherent property of rx-only segment mirroring (and say which
tap scope to use when TLS detail is wanted) or reconsider the direction derivation for the ports
that carry TLS. Check the sensor's `eve-log` `tls` sub-options too (`session-resumption`,
`custom:`), which the render sets.

**Related:** `axrange/config_exec/render.py` (the suricata render's `eve-log.types`),
`docs/design/06-taps-sensors.md` (scope → direction derivation),
`docs/WORK_ORDER_protocol_faithful_services.md` §9.2 step 4, GAP-077.

## GAP-077 — The sensor render's `eve-log.types` list omits `ntp`

**Status:** 🔴 Open
**Severity:** **Low** (a one-line fix; the protocol is still visible as `flow` `app_proto ntp`, and
the live-verify passed NTP through exactly that documented fallback)
**Discovered:** 2026-09-06 — services §9.2 live-verify on `dev45`, step 4.

**Symptom.** No `ntp` event records appear for real NTP traffic because `ntp` is not in the
`eve-log.types` list the suricata render writes. The README's version-caveat fallback (`flow` with
`app_proto: ntp`) carried the step, but "check the fallback" is now a permanent instruction for a
protocol Suricata 7.x fully supports.

**Fix.** Add `ntp` to the rendered `eve-log.types`. Worth doing as one pass over the whole list
against what the sensor's Suricata version actually supports, rather than one protocol at a time —
and worth a test that pins the rendered type list, so the next omission is caught at render rather
than on a cluster.

**Related:** `axrange/config_exec/render.py` (suricata `eve-log.types`),
`docs/WORK_ORDER_protocol_faithful_services.md` §9.2 step 4, GAP-076.

## GAP-078 — An explicit `ip:` on a lone router leg suppresses the segment's auto-gateway, and validation still passes

**Status:** 🔴 Open
**Severity:** **Medium** (a silently gateway-less segment; every host on it loses egress and the
failure surfaces later, as an `apt` timeout on an unrelated op)
**Discovered:** 2026-09-06 — reported by the author of the `twin-configs/svc-verify/` bundle
(commit `fbe70f87`) while pinning every host IP in the config.

**Symptom.** A twin config that pins the router's own leg address (`nodes[gw].nets[i].ip:`) on a
segment with exactly one router leg **suppresses that segment's auto-assigned gateway**. Validation
passes, the plan builds, and the segment ends up with no gateway — discovered only by reading the
emitted IR, or later by a host that cannot reach anything.

**Root cause (as reported).** The auto-gateway is derived where a leg carries no explicit address;
an explicit `ip:` takes the branch that skips it (`axrange/twinconfig/expand.py`, and
`axrange/planner/pass1_emit.py:717` on the emission side). No rule cross-checks *"this segment has
router legs but no gateway"*.

**Workaround used.** The `svc-verify` bundle leaves the `gw` legs' `ip:` unset and lets the
auto-gateway assign `.1` on both segments — which is why every host address in that README is
literal but the router's is described rather than pinned.

**Fix sketch.** Either honor an explicit router-leg `ip:` **as** the gateway (the least surprising
reading of the author's intent), or add a validator rule that fails when a segment carrying a
`kind: router` leg resolves to no gateway, naming the leg that suppressed it. The second is the
smaller change and catches the general case; the first is what an author would expect.

**Related:** `axrange/twinconfig/expand.py`, `axrange/planner/pass1_emit.py:717`,
`twin-configs/svc-verify/svc-verify.yaml`, commit `fbe70f87`.

## GAP-079 — The Console cannot draw conduit lanes for a deployed twin: the `CastSummary` API carries a conduit count, not the roster

**Status:** ✅ Resolved 2026-09-11 — `docs/WORK_ORDER_console_level0_rendering.md` **R5**, CHANGELOG
2026-09-11 "Console level-0 R5 + R7 BUILT". `CastSummaryDTO` now publishes `conduit_roster`
(`{from, to}` per conduit, the count field `conduits` unchanged beside it) and `routers`
(`{name, segments}` per **expanded** router, named as deployed — `gw01`, `gw02` — read from the
expander's own ids), the SPA mirrors both, and `views/useGraph.ts`'s hard-coded empty
`LIVE_CONDUITS` is gone.
**Severity:** **Low** (a missing view element in the read-only Console; the layout is correct and
proven, only the wire it reads is short a field — the app degrades honestly rather than breaking)
**Discovered:** 2026-09-09 — Console S3 build (`docs/WORK_ORDER_console_canvas.md` §14.5,
CHANGELOG 2026-09-09 "Console S3 BUILT").

**Symptom.** The Console's level-0 map draws segment tiles, infrastructure devices and the matrix
live against a real cast bundle, but the **east-west conduit band is empty** in the co-served app,
under an in-UI note ("conduit list absent from this API response"). An operator looking at a
deployed twin cannot see which segments are connected by conduits, even though the twin config
declares them.

**Root cause.** The conduit lanes are **computed and golden-proven** in
`o2/console/src/layout/lanes.ts` (22 lanes on the standing `twin-configs-cast/corpapcn-twin/`
bundle), but the S1 `CastSummary` DTO returned by `GET /api/imprints/{id}/cast` publishes only a
conduit **count**, not the `{from, to}` pairs. No API response the SPA reaches carries the roster,
so `buildGraph` has nothing to feed the pure layout. The SPA is built to light the band up **with no
view change** the moment a roster arrives (`o2/console/src/views/useGraph.ts` reads conduits where
present) — this is a missing backend field, not a frontend defect.

**Workaround.** None needed for correctness; the tiles, devices and matrix are all live. The lane
computation is exercised by `test/layout.real.test.ts` (the 22-lane count against the real bundle)
and `test/layout/lanes*.golden.json`, so the feature is proven off-cluster.

**Fix sketch.** A small **additive `CastSummaryDTO.conduits` backend slice** — publish the
`{from, to}` conduit roster (the compiler already knows it; it is in the bundle's `<id>.yaml`
`conduits:` block) on the existing `GET …/cast` route, mirror the field in
`o2/console/src/api/types.ts` (the hand-written mirror, LES-258), and `buildGraph` starts drawing
lanes with no further UI change. Belongs with Console S4 or the next time the imprint API is
touched; it is **not** an S4 blocker.

**Related:** `o2/console/src/layout/lanes.ts`, `o2/console/src/views/useGraph.ts`,
`axrange/api/dtos.py` (`CastSummaryDTO`), `axrange/api/imprints.py`,
`docs/WORK_ORDER_console_canvas.md` §14.5.

**Resolved 2026-09-11 by R5, with two field-name corrections to the sketch above** (implementation
calls, recorded because the sketch named different fields): the roster ships as **`conduit_roster`**,
not `conduits` — that name is already the shipped **count** and reusing it would have been a breaking
change to a published field — and the router legs ship as **`routers: [{name, segments}]`**, one
entry per **expanded** router named as deployed, not a flat `router_segments`, because a twin with two
gateways has two distinct leg sets and a flat list cannot say which is which. A server too old to
send either field omits it, and the SPA keeps the distinction (`graph.castOmits`) so the UI says "this
server did not publish the roster" rather than "the twin declares none". Note the 2026-09-11 arcs
ruling (that order's §4.1): the roster currently draws through `lanes.ts`, which **R4 retires** in
favour of volume-weighted arcs — the fix sketch's "starts drawing lanes with no further UI change"
describes what shipped today, not the end state.

**End state reached 2026-09-15 (level-0 R4, that order's §13; CHANGELOG 2026-09-15).** `lanes.ts` is
**deleted** and the roster now draws through `o2/console/src/layout/arcs.ts` as volume-weighted arcs
anchored at each site group's bottom edge, with **every asserted conduit** drawn in the edit colour
and dashed when the pair carried no observed traffic. The two `lanes.ts` references in **Related**
above are therefore historical — read them as `layout/arcs.ts`. Nothing about this gap's resolution
changed: the roster is published and consumed; only the geometry that renders it did.

## GAP-080 — Console pin-key validation is asymmetric with the SPA's tile/site keys: a loose segment CIDR and a `/`-rejecting site regex both silently drop a valid pin

**Status:** 🔴 Open
**Severity:** **Low** (a latent server↔client key-shape mismatch, not reachable with today's
all-`/24` RFC1918 content; the pin optimistically reverts rather than corrupting anything)
**Discovered:** 2026-09-10 — Console S4 (pins) build (`docs/WORK_ORDER_console_canvas.md` §15.4,
CHANGELOG 2026-09-10 "Console S4 BUILT"). Pre-existing in `axrange/api/console_files.py`, but S4 is
the first code to actually emit `site:`/`segment:` pin keys, so it is the first slice that can hit it.

**Symptom.** Two shapes a `PUT …/layout` can carry are handled inconsistently with the shapes the
SPA mints, so a legitimate pin is silently lost:
- A **`segment:<cidr>`** key is accepted by the server against a **loose** CIDR
  (`ip_network(..., strict=False)`), but a pinned tile is matched in the UI on its **strict**
  observed network form (`applyPins` keys on `segment:${tile.segment.observedCidr}`). A key whose
  CIDR is not already the strict network form validates on the server but then **matches no tile**
  when the layout applies it.
- A **`site:raw:<cidr>`** key — which `o2/console/src/layout/sites.ts` mints for a **non-RFC1918**
  segment that has no two-octet bucket (`bucketKeyFor` → `raw:<cidr>`, then `site:${key}`) — is
  **400-refused** by the server because `_SITE_KEY_RE = ^site:[0-9A-Za-z._:-]+$` has no `/`. The
  optimistic pin then rolls back and the drag appears to do nothing.

**Root cause.** `_validate_pin_key`'s `segment:` branch is more permissive than the SPA's tile key
(loose vs strict CIDR), and `_SITE_KEY_RE` was written for the two-octet `site:10.80` form and never
extended for the `site:raw:<cidr>` fallback the SPA can produce. Neither path is exercised by the
current content, which is entirely RFC1918 `/24`s (so every site key is two-octet and every segment
CIDR is already a strict network), which is why it shipped latent.

**Workaround.** None needed today — no shipped or cast twin is non-RFC1918, and every minted
`segment:` key is the strict observed form. A non-RFC1918 twin, or a hand-authored `layout.json`
with a non-canonical segment CIDR, would hit it.

**Fix sketch.** Normalize both sides to one canonical shape: validate `segment:` keys against the
**strict** network form (matching the tile key), and either teach `_SITE_KEY_RE` to accept the
`site:raw:<cidr>` shape the SPA mints (allow `/` after a `raw:` marker) or change `sites.ts` to mint
a `/`-free site key (e.g. hyphenated CIDR, the same escape the URL grammar uses for
`/segments/10.80.20.0-24`). A round-trip test — mint a key in `sites.ts`/`keys.ts`, feed it to
`_validate_pin_key` — would pin the symmetry.

**Related:** `axrange/api/console_files.py` (`_validate_pin_key`, `_SITE_KEY_RE`),
`o2/console/src/layout/sites.ts` (`bucketKeyFor`), `o2/console/src/model/keys.ts`,
`o2/console/src/layout/level0.ts` (`applyPins`), `docs/WORK_ORDER_console_canvas.md` §15.4.

## GAP-081 — A pending Console `suppress` re-staged over an already-suppressed key can flip to `applied` on the next provenance load, hiding the unsaved indicator

**Status:** 🔴 Open
**Severity:** **Low** (a narrow, self-correcting UI-state edge — no data is written or lost; the
unsaved-edit indicator under-counts for one interaction, and a reload or an explicit save corrects it)
**Discovered:** 2026-09-10 — Console S5 (editing and save) build (`docs/WORK_ORDER_console_canvas.md`
§16.4 "N3", CHANGELOG 2026-09-10 "Console S5 BUILT"). It is the "N3" honest limit that build recorded
going in.

**Symptom.** An operator stages a *pending* `suppress` on a host key that the **standing cast already
suppresses**. On the next provenance load the edit store's *applied* match (by block + key, against
`GET …/provenance`'s `population.suppressed[]`) sees the key already honored and marks the pending
record *applied* — so `hasUnsavedEdits`, which counts only *pending* entries, drops the unsaved
indicator even though the record was **never written** to `<imprint>.assertions.yaml`. The edit is
not lost; it simply stops signalling that it is unsaved.

**Root cause.** `hasUnsavedEdits` counts *pending* entries only, and the pending → applied match does
not first exclude a record the operator has staged but not yet saved. A record can therefore be
*pending* (browser only) **and** match an already-applied cast key at the same time, and the applied
match wins the display, masking the "you have unsaved work" signal.

**Workaround.** None needed operationally — the state is self-correcting: an explicit Save writes the
record and a reload re-reads the file, either of which restores an accurate indicator. It matters
only in the fleeting window between staging a redundant suppress and the next load.

**Fix sketch.** The clean fix is in the S2 edit reducer (`o2/console/src/edit/store.ts`): a *pending*
record must count as unsaved regardless of whether its key already matches an applied cast key — an
applied match must not mask a pending, unwritten record. That reducer was **deliberately untouched**
by S5 (editing built the record + view layer over S2's store), so the change was held rather than
made mid-slice. An effect-level alternative (recompute the indicator in a view effect) was rejected:
it has a stale-read / re-render-loop problem. A unit test staging a suppress over an already-suppressed
key and asserting `hasUnsavedEdits` stays true until save would pin it.

**Related:** `o2/console/src/edit/store.ts` (`hasUnsavedEdits`, the applied match),
`docs/WORK_ORDER_console_canvas.md` §16.4, CHANGELOG 2026-09-10 "Console S5 BUILT". Sibling latent
Console gap to GAP-080 (the pin-key shape asymmetry); both are cheap same-area fixes to fold in when
the console files / edit store are next touched.

## GAP-082 — `GET /api/imprints/{id}` refuses a cast bundle whose export bakes a different `imprint_id` than its directory, while `/topology` and `/cast` serve it

**Status:** ✅ Resolved 2026-09-11 — `docs/WORK_ORDER_console_level0_rendering.md` **R7** under
decision **D3 "tolerate on read"** (Authority: user), CHANGELOG 2026-09-11 "Console level-0 R5 + R7
BUILT".
**Severity:** **Medium** (the catalog card and anything built on the summary route refused a real cast
bundle; the Console canvas worked only because it bypasses the route — and "deploy as twin" was dead,
since its pre-check ran the summary)
**Discovered:** 2026-09-11 — Console level-0 rendering review (`docs/WORK_ORDER_console_level0_rendering.md` §2 F9, §5 D3).

**Two corrections to the record below, made when the fix was built:** the refusal was a **400, not a
500** (the level-0 order's F9 says 500), and it hit **every** cast bundle, not only one whose export
bakes a differing id — because the check being applied is the *normalized* sidecar's `imprint_id`
rule, and a **canonical** sidecar (which every real cast bundle carries) declares no imprint id at
all, so the comparison read `None` and refused. The heading's framing ("whose export bakes a
different `imprint_id`") describes the symptom that was noticed first, not the population affected.

**Symptom.** `GET /api/imprints/pit-395a61f04e-1780405200` returns 400 with
`imprint 'pit-395a61f04e-1780405200': directory name (canonical) disagrees with provenance.imprint_id=None`,
while `GET …/topology`, `…/cast`, `…/provenance`, `…/assertions` and `…/layout` for the same id all
succeed. The bundle's topology export bakes `imprint_id: corpapcn-disco` / `pit_id:
pit-395a61f04e-1780405200`; the directory (canonical) is `pit-395a61f04e-1780405200`.

**Root cause.** `axrange/api/imprints.py` `_cross_check_ids` requires the sidecar/export id to agree
with the directory name. On a *cast* bundle the export legitimately addresses the observed PIT, which
can differ from the canonical id (the same argument `o2/console/src/model/graph.ts` `CastInput.imprintId`
already records for the SPA side), and the canonical sidecar shape this bundle carries has no
`imprint_id` at the location the check reads (`=None`).

**Workaround.** Read `/cast` for the summary numbers, as the Console does.

**Fix sketch.** `docs/WORK_ORDER_console_level0_rendering.md` **R7** / decision **D3**: either the
check tolerates a differing baked export id on a cast bundle (warning in the response, not a
refusal) and reads the canonical sidecar's id field where it actually lives, or aximprint's
`install-cast` rewrites the export's `imprint_id` to the canonical id. v1 default: tolerate on read.
The three routes must agree on what an id is.

**Related:** `axrange/api/imprints.py` (`_cross_check_ids`, `load_imprint_summary`),
`docs/WORK_ORDER_console_canvas.md` §14.6 (the SPA-side canonical-id fix, same root),
`docs/LESSONS.md` LES-196.

**How it was fixed (2026-09-11).** Every id-shaped field is now classified by one question — *may a
legitimate producer write a value different from the addressing id?* — and the answer decides refuse
vs warn (`docs/LESSONS.md` LES-278; the classification is in `axrange/api/imprints.py`'s module
docstring, field by field). The canonical id (directory name = `twin_config.id` = URL id) is the one
addressing id and is enforced by **one guard on every imprint route**, including `/cast`,
`/provenance`, `/topology`, `/assertions`, `/layout`, apply and diff — so a bundle one route refuses,
every route refuses. A **normalized** sidecar's own `imprint_id` still **refuses** when it names a
different imprint (LES-195's copy-paste protection) and warns when missing; a canonical sidecar
declares none; the topology export's baked id and any `pit_id` are published and warned about, never
compared. **Lifting the refusal exposed the next failure** (LES-276): every real cast bundle then
failed at *plan build*, which is GAP-054's neighbour and is recorded with the fix in the CHANGELOG —
see also `docs/LESSONS.md` LES-275.

## GAP-083 — The Grafana plugin's Imprints screen renders unmeasured values as measured zeros on cast bundles

**Status:** ✅ **Resolved (built)** 2026-09-16 — Console **S7** executed decision **D5** (Authority:
user, 2026-09-11): the screen is **deleted**, along with its topology panel, the `initialView` panel
option that opened the dashboard on it, and the imprint reads and types that existed only for them; a
sweep of the whole `o2/grafana/` tree (provisioning and dashboards included) confirms no reference
survives (`docs/WORK_ORDER_console_canvas.md` §21; CHANGELOG 2026-09-16 "Console S7 BUILT and D5
EXECUTED").

**The closure is stronger than "no surface renders a placeholder as a zero."** The route this entry is
about, `GET /api/imprints/{id}`, now has **no rendering consumer at all**: the Console SPA declares
`getImprint` but calls it only from the opt-in `AXRANGE_LIVE_API` drift test, and the plugin's
surviving **"Deploy as twin"** action renders **no belief-store measurement in any state** — picked,
previewed or deployed — its only numbers coming from a plan that was actually built. The DTO's
non-null fields keep their wire contract and their "read the pair, never the float" rule
(`axrange/api/dtos.py`, comments updated in the same slice to record that the consumer they were owed
to is gone), so the rule stands for whatever reads it next.

**Original status, kept for the trail:** 🔴 Open (until Console **S7**, where decision **D5** retires
this screen — Authority: user, 2026-09-11)
**Severity:** **Medium** (a confident wrong number on a shipped screen; strictly worse than the error
it replaced, because an error is legible and a `0` is a claim)
**Discovered:** 2026-09-11 — Console level-0 **R7** build, immediately after the GAP-082 refusal was
lifted (`docs/WORK_ORDER_console_level0_rendering.md` R7; CHANGELOG 2026-09-11).

**Symptom.** The Grafana control-panel plugin's Track C **Imprints** screen now loads for real cast
bundles (it used to show an error, because the summary route refused them) and renders:
**Services / Personas / Out-of-vocab as `0`** for canonical, absent **and** unrecognized sidecars;
**Mean conf `0.00`** for absent and unrecognized; a canonical provenance row whose record carries no
confidence as **`0.00`**; and it never renders the response's `warnings` at all. Nothing on the screen
distinguishes "measured zero" from "this bundle publishes no such measurement".

**Root cause.** The API's tolerant read publishes a **placeholder plus a qualifier**
(`sidecar_shape` / `evidence_source` / `warnings`), which is the right wire contract — but the plugin
screen predates those qualifier fields and renders only the values (LES-277). The regression was
*created* by relaxing the refusal, not by the plugin changing.

**Workaround.** Read `GET /api/imprints/{id}/provenance` directly (it publishes `sidecar_shape`,
`evidence_source` and `warnings`), or use the Console SPA, which reads the qualifiers.

**Fix sketch.** Do **not** fix the screen: decision **D5** (Authority: user, 2026-09-11,
`docs/WORK_ORDER_console_canvas.md` §8.1) retires the plugin's Imprints screen at **S7** and keeps
only a slim **"Deploy as twin"** plugin action. Until S7 lands this stays open and known. If S7 slips,
the minimum honest patch is to render `—` (not `0`) whenever `sidecar_shape` is not `normalized`, and
to surface `warnings`.

**Related:** `o2/grafana/plugins/axellio-axrange-control-panel/` (the Imprints screen),
`axrange/api/imprints.py` (`sidecar_shape`, `evidence_source`), `docs/LESSONS.md` LES-277,
`docs/WORK_ORDER_console_canvas.md` §3 S7 / §8.1 D5.

## GAP-084 — The standing `axrange serve` on `:8080` is an unauthenticated write surface, and it now reaches the planner

**Status:** 🔴 Open
**Severity:** **High** (unauthenticated writes from any host that can reach the port; the reachable
verbs now include deploy and `--update --allow-destroy` against a standing twin)
**Discovered:** 2026-09-11 — Console level-0 R5+R7 build, reasoning about what the M2 plan-build fix
makes reachable (CHANGELOG 2026-09-11).

**Symptom.** The standing development server runs
`axrange serve --host 0.0.0.0 --port 8080 …` with **no `AXRANGE_API_TOKEN` set**, so the write-token
check is inert and every mutating route is open to anything that can reach the port. Until 2026-09-11
this was bounded in practice by a *bug*: the relative-`scripts/` path defect (the M2 fix, LES-275)
made plan and deploy of every real cast bundle fail at plan build. With that fixed, an unauthenticated
`POST /api/imprints/{id}/deploy` — or `POST /api/deploys {"config": …}` — reaches the planner, and
`{"update": true, "allow_destroy": true}` reaches `te deploy --update --allow-destroy` against a
standing twin **with no confirmation prompt** (the CLI's `--yes` gate does not exist on the API path).

**Root cause.** Two safe-by-themselves choices compose badly: the API treats reads as open on a
trusted bind and gates writes on a token that is **optional** (absent ⇒ no check, so a deployment that
forgets it is silently open), and the convenience bind is `0.0.0.0` on a lab network. The planner-side
guards that do exist (name guards, tier protection, the destroy blast-radius guard) bound the *damage
shape*, not the *authorization*.

**Workaround.** Set `AXRANGE_API_TOKEN` before starting any long-lived serve, or bind `127.0.0.1` and
reach it over SSH port-forwarding. The running process (pid 280286 at the time of writing) still runs
pre-M2 code and therefore still fails at plan build — **restarting it is what makes the write path
live**, so the restart is a deliberate decision, not housekeeping.

**Fix sketch.** Make the token **required** whenever the bind is not loopback (refuse to start
otherwise, naming the variable), and require an explicit confirmation field on the destructive API
shapes (`allow_destroy`) rather than inheriting the CLI's interactive gate as a no-op. A read-only
serve mode (`--read-only`) would cover the Console-viewing case that motivates the open bind.

**Related:** `axrange/api/app.py` (the write-token dependency), `axrange/cli/main.py` (`serve`),
`axrange/api/service.py` (`create_deploy`'s `update`/`allow_destroy`), `docs/design/11-api-ui.md`.

**Status note, 2026-09-15 17:30 UTC — still 🔴 Open.** The standing `:8080` serve was **restarted** by
the orchestrator on the user's choice (token on `0.0.0.0`, serving the HEAD build): it now runs with
`AXRANGE_API_TOKEN` set — the token held in `~/.config/axrange/api-token` (mode 0600, never printed) —
in tmux session `axrange-serve` (pid 2938185), with the same flags as before, serving a build of HEAD
`b717c616` (level-0 R6). Reads stay open; **writes answer 401 without the token**, and the Grafana
plugin's datasource needs an `Authorization: Bearer` header for its "Deploy as twin" writes. That
closes the exposure **for this one process only**. The product gap is unchanged: `axrange/api/serve.py`
still only **warns** when it binds a non-loopback host with no token (the fix sketch's refusal is
unbuilt), and the API's `allow_destroy` still carries no confirmation field — the next serve started
without the variable is as open as the old one was.

**Status note, 2026-09-29 (Unified Console pass) — still 🔴 Open, unaffected in kind, widened in
surface.** `axrange serve --targets <file>` (`docs/design/16-unified-console.md` §7) can now serve
several clusters' writes from one process, and the per-host persona apply
(`PUT /api/deploys/{id}/hosts/{host}/personas`) is a new mutating route — both inherit the same
optional-token posture this gap describes rather than fixing it. No new exposure beyond what an
additional write route and additional targets imply; the fix sketch (require a token whenever the
bind is non-loopback) is still unbuilt.

**Status note, 2026-09-30 — the standing `:8080` now carries both a token AND a protected-deploys
list, but the underlying gap (an optional token) is still unbuilt.** The fix rounds added
`AXRANGE_PROTECTED_DEPLOYS` (`423` + `override_protection=<id>` on any write against a listed
deploy — `docs/design/16-unified-console.md` §5) and the standing serve
(`~/.axrange/serve-8080.log`) now runs with `AXRANGE_API_TOKEN` set AND
`AXRANGE_PROTECTED_DEPLOYS=corp-twin-07cfcdb2,svc-engines-24131435` — both standing twins are
protected on this one process, by operator choice, not by this gap being closed. Protection is a
per-deploy allowlist an operator must remember to set, same as the token; a `serve` invocation that
forgets either is exactly as open as before. The fix sketch (require a token whenever the bind is
non-loopback) remains unbuilt.

## GAP-085 — The CLI `--config` path leaks its materialized twin-config tempfile

**Status:** 🔴 Open
**Severity:** **Low** (a file per invocation in the temp directory; no correctness impact)
**Discovered:** 2026-09-11 — Console level-0 R5+R7 build, while fixing the API's sibling of the same
code (CHANGELOG 2026-09-11). Pre-existing.

**Symptom.** `axrange te {validate,plan,deploy} --config <id>` expands the twin config to a temporary
IR file and never deletes it (`axrange/cli/main.py:1063`). Long-lived shells and CI accumulate one
materialized IR per invocation — measured: **7 leftovers per full pytest run**.

**Root cause.** The materialize step writes with `NamedTemporaryFile(delete=False)` and hands the path
onward; nothing owns the file's lifetime. The API's two resolvers had the same shape and were fixed in
this pass (the plan and deploy paths now delete the tempfile); the CLI's was left alone deliberately,
because the CLI's exit ends the process and the cost is a stale file rather than a leak within a run.

**Workaround.** None needed; the OS reclaims the temp directory on reboot.

**Fix sketch.** Wrap the materialize-and-use sequence in a `try/finally` (or a context manager shared
with the API resolvers, alongside `twin_config_source_dir` — the same "one shared helper" argument as
LES-275).

**Related:** `axrange/cli/main.py` (`--config` materialization), `axrange/api/service.py`,
`axrange/twinconfig/registry.py`, `docs/LESSONS.md` LES-275.

## GAP-086 — Sibling readers of one bundle disagree about the same document: unbounded `census`, an out-of-range `windows`, and two spellings of a reference directory

**Status:** 🔴 Open
**Severity:** **Low** (three narrow, individually-defensible inconsistencies; each surprises an
operator who moves between two surfaces on one bundle)
**Discovered:** 2026-09-11 — Console level-0 R5+R7 build, recorded as honest limits of the
number-bounding pass (CHANGELOG 2026-09-11).

**Symptom.** Three places where two readers of one artifact answer differently:
1. `GET …/cast` republishes the export's **`census`** numbers **unbounded**, while `GET …/topology`
   refuses the same out-of-range values with a 400 naming the field.
2. A bundle whose `windows` exceeds `2**53 - 1` **lists** (the listing is deliberately tolerant, so a
   broken bundle is visible rather than invisible) but its `/cast` **refuses** — the same bundle is
   simultaneously "there" and "unreadable", with no single response saying so.
3. The fidelity CLI's `--reference <dir>` still accepts a **compiler-named** `twin_config.yaml`, while
   the imprint API requires the canonical `<id>.yaml`. A directory that works for
   `axrange te scorecard --reference` can 404 through the API.

**Root cause.** Tolerant-listing vs strict-read is a deliberate split (LES-240's family: a listing
should not hide a broken bundle), and `/cast`'s census passthrough simply predates `axrange/jsonnum.py`.
The reference-directory spelling is an older convention the imprint routes standardized away from
without migrating the fidelity reader.

**Workaround.** Read `/topology` for census numbers you intend to trust; for (3) rename or symlink the
file to `<id>.yaml`, which both readers accept.

**Fix sketch.** Route `/cast`'s census through `axrange.jsonnum` like its siblings; publish a per-entry
`readable: false` + reason on the listing so tolerance does not require a second request to interpret;
and either teach the imprint reader the compiler-named spelling or teach the fidelity reader the
canonical one — one of the two, written down, rather than both surfaces guessing.

**Related:** `axrange/api/imprints.py`, `axrange/api/topology_export.py`, `axrange/jsonnum.py`,
`axrange/fidelity/reference.py` (`resolve_bundle_dir`), `docs/LESSONS.md` LES-279/LES-280.

## GAP-087 — A compiler upgrade rides along silently on the first apply: a recompile is pinned to a bundle's flags, not to a compiler version

**Status:** ✅ **Resolved (built)** — 2026-09-19, iteration 11. **As built:** `compare_compiler_builds`
(`axrange/api/recompile.py`) reads `cast_invocation.code_fingerprint` before/after and reports a
three-valued `compiler_changed` (`true`/`false`/`null`) + `compiler_note` on the apply response;
reported, not refused. The Console renders a distinct outcome only on `=== true`, its own
could-not-tell sentence on `null`, and nothing on an absent field. See CHANGELOG 2026-09-19.

**Status (as discovered):** 🔴 Open
**Severity:** **High** (the operator authors one suppression and receives an unrelated population
change; on a **deployed** twin the renamed groups would replace VMs on `te deploy --update`)
**Discovered:** 2026-09-11 — the Console **S6 acceptance proof** (`docs/WORK_ORDER_console_canvas.md`
§17.5, CHANGELOG 2026-09-11 "Console S6 acceptance CLOSED").

**Symptom.** The first apply on `twin-configs-cast/corpapcn-golden` suppressed exactly one host, and
the recompiled bundle differed by **22 hosts added / 23 removed** — because aximprint HEAD had renamed
11 node groups since the bundle was cast. The Console reported **"1 element changed"**, correctly for
what it joins on (observed identity), which is exactly why the population change was invisible: the
map's join hides a twin-side rename by design.

**Root cause.** `SubprocessRecompile` re-runs the producer from the bundle's own `cast_invocation`
block, which pins the **flags** of the original cast but not the **compiler build** that ran it. Any
upgrade to the sibling repo between cast and apply is applied silently on the operator's next edit.
`code_fingerprint_before/after` in the apply response would be the natural tell and cannot serve —
see GAP-088.

**Workaround.** Before an apply on a bundle you care about, compare the bundle's
`cast_invocation.code_fingerprint` with the current compiler's, or re-cast deliberately.

**Fix sketch.** Read the *new* build's `cast_invocation.code_fingerprint` from the recompiled topology
export, compare it with the pre-apply one, and surface a **distinct outcome** ("the compiler changed
between cast and apply: N hosts added / M removed, unrelated to your edit") — the same
"third honest outcome" shape as LES-273. Options beyond reporting: refuse the apply unless the
operator opts in (`?allow_compiler_change=1`), or pin the compiler revision in `cast_invocation`.
Scheduled as **iteration 11** of the 2026-09-11 Console run.

**Related:** `axrange/api/recompile.py`, `axrange/api/imprints.py` (apply), aximprint's
`cast_invocation`, `docs/WORK_ORDER_console_canvas.md` §17.5, GAP-088.

## GAP-088 — The apply response's `code_fingerprint_before/after` cannot reveal a compiler change: it is copied from the PIT row, and its docstrings say otherwise

**Status:** ✅ **Resolved (built)** — 2026-09-19, iteration 11 (**BREAKING API field rename, no
aliases**). **As built:** the old pair is renamed to `pit_code_fingerprint_before/after`; a new
`cast_code_fingerprint_before/after` pair carries `cast_invocation.code_fingerprint`, the value
that actually moves with a compiler upgrade. Both docstrings corrected. A repo-wide check found no
consumer of the old name outside the changed files. See CHANGELOG 2026-09-19.

**Status (as discovered):** 🔴 Open
**Severity:** **Medium** (a field two docstrings call "the compiler's build hash" that provably does
not move when the compiler does — the field an operator would reach for to detect GAP-087)
**Discovered:** 2026-09-11 — Console S6 acceptance proof (CHANGELOG 2026-09-11).

**Symptom.** Across an apply in which the compiler genuinely changed (11 node groups renamed),
`code_fingerprint_before` and `code_fingerprint_after` in the apply response were **identical**
(`3f5015bd85894407`), while the real build hash — `cast_invocation.code_fingerprint` in the topology
export — **did** move.

**Root cause.** The response's `code_fingerprint_*` pair is copied from the **PIT row** (the capture's
identity), not from the build invocation. `axrange/api/dtos.py` and `axrange/api/recompile.py`
describe it as the compiler's build hash, which contradicts the data.

**Workaround.** Read `cast_invocation.code_fingerprint` from the bundle's topology export before and
after.

**Fix sketch.** Either populate the pair from `cast_invocation.code_fingerprint` (and keep the PIT
value under its own name), or rename the field to what it is (`pit_code_fingerprint`) and add the
build hash beside it. Fix the two docstrings either way — a contradicting docstring is worse than an
absent one. Iteration 11.

**Related:** `axrange/api/dtos.py` (`ImprintApplyDTO`), `axrange/api/recompile.py`, GAP-087.

## GAP-089 — `GET …/diff`'s `since` parameter description names a value that 404s

**Status:** ✅ **Resolved (built)** — 2026-09-19, iteration 11. **As built:** the description names
`fingerprint_before` via one constant, `DIFF_SINCE_FIELD` in `axrange/api/app.py`;
`tests/test_api_console.py` drives the route with that constant so the prose and the working value
cannot drift apart again. See CHANGELOG 2026-09-19.

**Status (as discovered):** 🔴 Open
**Severity:** **Low** (a wrong parameter description on a working route; the caller who follows it
gets a clean 404 rather than a wrong answer)
**Discovered:** 2026-09-11 — Console S6 acceptance proof (CHANGELOG 2026-09-11).

**Symptom.** `axrange/api/app.py` describes `since` in a way that reads as "the apply response's
`code_fingerprint_before`". Passing that value **404s**; the value that works is the apply response's
**`fingerprint_before`**. Proven both ways in the acceptance run's evidence (files `10-` and `11-`).

**Root cause.** The description was written against the wrong one of two similarly-named fields on the
same response object (see GAP-088 for why the two exist).

**Workaround.** Pass `fingerprint_before`.

**Fix sketch.** Correct the parameter description, and add a test that drives the route with the value
the description names — a doc string is testable when it names a field. Iteration 11.

**Related:** `axrange/api/app.py` (the diff route), `axrange/api/imprints.py`, GAP-088.

## GAP-090 — The server's no-op apply warning is ambiguous ("either … or …")

**Status:** ✅ **Resolved (built)** — 2026-09-19, iteration 11. **As built:** the warning names the
specific case the install return code and the cast's own record counts already decide — e.g. "the
recompile produced no change to the cast (install returned 0) … compiled to the same bytes" —
without claiming each record took effect; the disjunction survives only for the genuinely-unknown
case. See CHANGELOG 2026-09-19.

**Status (as discovered):** 🔴 Open
**Severity:** **Low** (a legible but unhelpful message; the SPA's own branch is correct)
**Discovered:** 2026-09-11 — Console S6 acceptance proof (CHANGELOG 2026-09-11).

**Symptom.** A byte-identical recompile returns a warning phrased as "either the assertions changed
nothing **or** …", leaving the operator to pick. The server knows which: it holds the record counts,
the before/after fingerprints and the install return code.

**Root cause.** The message was written before the response carried enough to distinguish the cases,
and was not revisited when it did.

**Workaround.** Read `record_counts` and the fingerprint pair in the response.

**Fix sketch.** Emit the specific case ("the recompile produced no change to the cast: your N
assertion records compiled to the same bytes"), naming what byte-identity actually shows rather than
that each record took effect (a record naming an entity the cast no longer has changes nothing
either), and keep the disjunction only for the genuinely-unknown case. Iteration 11.

**Related:** `axrange/api/recompile.py`, `axrange/api/imprints.py`, `docs/LESSONS.md` LES-273 (the
neighbouring "don't-know" outcome the SPA already renders honestly).

## GAP-091 — (aximprint) `install-cast` never deletes stale files, so superseded scripts and personas accumulate in a bundle

**Status:** 🔴 Open · **Owner: aximprint** (recorded here because axRange's apply path is the consumer
that hits it; the fix belongs in the sibling repo's register too)
**Severity:** **Medium** (a bundle grows files no current cast produces; for a *tracked* bundle they
would be committed, and a stale `scripts/*.sh` is content a deploy can still reference)
**Discovered:** 2026-09-11 — Console S6 acceptance proof (CHANGELOG 2026-09-11).

**Symptom.** Applying (recompiling) a bundle writes the new cast's files over the old ones but removes
nothing, so files the new cast no longer produces — superseded `scripts/serve-*.sh`, `personas/*.yaml`
for groups that were renamed away — stay in the bundle directory indefinitely.

**Root cause.** `install-cast` is a copy-in step with no reconciliation of the destination against the
files the cast actually emitted.

**Workaround.** Cast into a clean directory when file-set accuracy matters, and diff the bundle's file
list against the new cast's before committing a tracked bundle.

**Fix sketch.** Make `install-cast` reconcile: write the new set, then remove destination files the
cast owns and no longer emits (never touching the operator-owned `<id>.assertions.yaml`,
`layout.json`, `.history/`). A manifest of emitted files in the bundle would make the ownership
boundary explicit.

**Related:** aximprint `install_cast_as_twin.py`, `axrange/api/recompile.py`,
`docs/WORK_ORDER_console_canvas.md` §17.5.

## GAP-092 — (aximprint) the provenance sidecar records the assertions file's ABSOLUTE path, so two checkouts produce different bytes and fingerprints

**Status:** 🔴 Open · **Owner: aximprint** (recorded here because it defeats axRange's determinism
check, which is the consumer)
**Severity:** **Medium** (breaks byte-identity of a recompile across machines/checkouts — the property
`14` §3's determinism rule and the Console's "no change" outcome both rest on)
**Discovered:** 2026-09-11 — Console S6 acceptance proof (CHANGELOG 2026-09-11).

**Symptom.** The new sidecar records the assertions file by **absolute** path. Two checkouts applying
**identical** assertions therefore produce sidecars that differ in bytes, and any fingerprint over the
bundle differs with them — so "the same inputs produce the same cast" holds only within one working
directory. (Within one directory it held exactly: the acceptance run's second identical apply was
byte-identical at `1de327650032c7ce`.)

**Root cause.** The path is captured as given rather than relativized to the bundle directory it is
written into.

**Workaround.** Compare casts only from the same checkout path; do not treat a cross-machine byte
difference as a real change.

**Fix sketch.** Record the assertions file **relative to the bundle directory** (or record only its
content hash, which is what a consumer actually needs). Either makes the determinism property machine-
independent.

**Related:** aximprint's sidecar writer, `axrange/api/recompile.py` (the determinism check),
`docs/design/14-operator-assertions.md` §3, `docs/WORK_ORDER_console_canvas.md` §17.5.

## GAP-093 — (aximprint) An `add: segment` on an observed CIDR the cast does not carry, with no host placed on it, is a silent no-op — and the compiler's conduit remedy text points at that same gesture

**Status:** 🔴 Open · **Owner: aximprint** (recorded here because the Console is the consumer that
surfaces it; the fix, and the lesson below, belong in the sibling repo's own register too)
**Severity:** **Medium** (a well-formed record is accepted, reported as having matched, and changes
nothing in the cast — and the compiler's own advice for a stranded conduit sends the operator to that
same ineffective record)
**Discovered:** 2026-09-15 — Console S5.1.a review, settled by running aximprint's real
`compile_twin` (CHANGELOG 2026-09-15 "Console S5.1.a BUILT"; `docs/WORK_ORDER_console_canvas.md`
§18.2 call 1).

**Symptom.** Take an observed segment the installed cast does not carry — pruned by an older compiler,
or genuinely left with no surviving host. An `add: segment` keyed on its CIDR, with no `add: host` on
it, compiles to a cast that still has no such segment; the sidecar lists the record in neither
`ineffective` nor `unmatched`, so nothing tells the operator their assertion did nothing. Separately, a
conduit to such a segment is reported ineffective with the remedy *"segment(s) [...] are not in this
twin — add them with `kind: segment`, or correct the CIDRs"* — and for an **observed** CIDR, adding it
with `kind: segment` is exactly the record that does nothing.

**Root cause.** In the asserted-segment pass (aximprint `src/aximprint/synth/compiler.py:1097-1113`,
at `ca5165c`) `aview.mark("add", "segment", cidr)` runs **before** `if cidr in observed_cidrs: continue`,
so every add is counted matched; only an **unobserved** CIDR joins `asserted_cidrs`, the set that
survives the emptiness prune (`live_cidrs | asserted_cidrs`). An observed segment therefore stays in
the twin only if a node sits on it, and an added host is what can put one there (hosts resolve against
`known_cidrs` = observed ∪ added, `:882`). The conduit branch's remedy string (`:1999-2002`) was
written for the unobserved case and is wrong for the observed one. "Matched" here means *the record's
key was found*, not *the record changed or confirmed anything*.

**Reachability today.** On a real PIT the **current** compiler prunes no observed segment — it emits
the whole observed population (the deployment-scope change) — so the no-op needs a cast from an older
compiler (`corpapcn-twin`'s six "pruned" segments, which any apply recompiles back regardless, GAP-087's
class) or a PIT with a genuinely host-less observed segment. The Console already works around it: it
allows such an add but says truthfully that *"the compiler keeps a segment only when a host is placed
on it: select it and use Add host here"* (`o2/console/src/edit/allocate.ts` `KEEPS_ONLY_WITH_A_HOST`).

**Workaround.** Place a host on the segment (`add: host` with `segment: <cidr>`); the `add: segment`
then sets its name and zone type.

**Fix sketch (aximprint).** Either keep an added observed segment the way an added unobserved one is kept
(join `live_cidrs` — if a host-less segment is acceptable downstream; the compiler's own comment at
`:1067-1071` notes axRange's expander errors on a conduit whose segment has no router leg), **or** report
it `ineffective` with the real reason ("observed, but no host survives on it — place one with `add:
host`") and correct the conduit remedy to name the host gesture. Aximprint's own candidate lesson from
this — *"'matched' must mean the record changed or confirmed something"* — belongs in **aximprint's**
`docs/LESSONS.md`, not here.

**Related:** aximprint `src/aximprint/synth/compiler.py` (`AssertionView.mark`/`ineffective`/`unmatched`,
the asserted-segment and asserted-conduit passes); aximprint `docs/WORK_ORDER_reference_ingest.md` §8,
finding 1; `docs/design/14-operator-assertions.md` §3 (the dated correction); `docs/LESSONS.md` LES-228.

## GAP-094 — The Console discards pending edits on every way out of an imprint except Escape: there is no draft persistence

**Status:** ✅ **Resolved (built)** 2026-09-16 — Console **S5.1.d**
(`docs/WORK_ORDER_console_canvas.md` §20; CHANGELOG 2026-09-16 "Console S5.1.d–e BUILT").
`o2/console/src/edit/draft.ts` persists an imprint's **pending** records (with their gesture tags) in
`localStorage` under one key per imprint, and they now survive a **reload**, an **imprint switch**, the
**breadcrumb**, the **outline's imprint list** and the browser's **Back**; a save discards the draft.
Restoring is **reconciliation, never a merge** (LES-251 holds): a record the file now holds identically
is dropped as redundant, one held *differently* under the same key is reported as a divergence, the
restore runs the same shape and cross-record refusals the staging paths run (LES-335), and a moved
assertions etag or `code_fingerprint` — including a bundle whose build **could not be read** — is
reported beside the restored set (LES-273's third outcome). A blocked or full store is said once and
never takes the app down; the API **write token is still never persisted** (proven by a test that greps
every storage write in the source).

**What it does NOT cover, deliberately:** the browser's **Back is not intercepted** (nothing is lost —
the work is in the draft — and the substitutes are the leave guard's own copy, a **destination notice**
naming the imprint left behind with its count and the way back, and the **catalog's** "N imprints on
this browser have unsaved drafts" for a cold start); and a **kept** draft — one held because the world
refused it, so the operator can resolve the conflict — is replaced by the next pending write in that
imprint, since the stored draft mirrors what is pending. The discard offer stands wherever a notice is
shown. One residual of the same slice is registered separately as **GAP-096** (a placement pin is
written to the server at stage time).

**Original entry, kept for the trail:**
**Status (until 2026-09-16):** 🔴 Open · **Owner: axRange** — planned for **S5.1.d** (iteration 9 of the Console run)
**Severity:** **Medium** (unsaved edits are lost with no prompt on an ordinary navigation; Save before
leaving is the only protection)
**Discovered:** 2026-09-15 — Console S5.1.a build, fix round 2's T5 (CHANGELOG 2026-09-15 "Console
S5.1.a BUILT"; `docs/WORK_ORDER_console_canvas.md` §18.5).

**Symptom.** Pending edits (staged in the browser, not yet saved) vanish without warning when the
operator clicks the **breadcrumb**, clicks another imprint in the **outline**, uses the browser's
**Back**, or **reloads**. S5.1.a made **Escape** refuse to leave an imprint with pending edits — it
announces *"N unsaved edits — save or discard them before leaving"* — but that is the only guarded exit.

**Root cause.** The edit store lives in the per-imprint workspace, which is keyed by imprint so that
switching imprints can never carry one imprint's edits into another's file (LES-259) — and pending
entries are React state only. **`o2/console/src/edit/store.ts` has no draft mechanism**, although
`docs/WORK_ORDER_console_canvas.md` §3 S5.1's acceptance text describes one ("survives a reload of the
same URL (pending edits persist in the store's draft, `edit/store.ts`)"), which reads as if it existed.

**Workaround.** Save (or Apply) before leaving an imprint; what is saved is in the assertions file.

**Fix sketch (S5.1.d).** A per-imprint draft keyed on the **canonical** imprint id (LES-263) and the
assertions etag it was staged against — rehydrated on mount, and on an etag mismatch **reported and
refused**, never silently merged (LES-251's rule) — plus a guard on every in-app way out of an imprint
that holds pending edits (a router blocker naming the count) and a `beforeunload` prompt for a reload or
a closed tab. The draft is edit content, not a credential, so unlike the write token it may be stored.

**Related:** `o2/console/src/App.tsx` (the Escape guard; `ImprintWorkspace` keyed by imprint),
`o2/console/src/edit/store.ts`, `docs/WORK_ORDER_console_canvas.md` §3 S5.1.d and §18.5,
`docs/design/15-console-canvas.md` §7, `docs/LESSONS.md` LES-259, LES-324.

## GAP-095 — A persona emit has no served-versus-dialed direction, so a `real-service` persona's ports cannot be told apart — and every consumer that derives a listen set from the catalog inherits the ambiguity

**Status:** 🔴 Open · **Owner: axRange** (the persona catalog, `axrange/personas/catalog/*.yaml`, and
`axrange/personas/schema.py`)
**Severity:** **Medium** (no runtime defect today — the drivers do not read direction — but every
*derivation* from the catalog is ambiguous, and the first consumer had to ship a disclosure instead of
an answer)
**Discovered:** 2026-09-16 — Console S5.1.c, building the per-role default listen set
(`scripts/gen_role_listen.py`; CHANGELOG 2026-09-16 "Console S5.1.b–c BUILT", call 3;
`docs/WORK_ORDER_console_canvas.md` §19.2).

**Symptom.** The persona library is the one place this repo records which ports a role is reached on,
so the Console generates each new host's `listen` set from it. Who listens depends on the persona's
`drive` (`docs/design/07-traffic-personas.md`): a `scripted`/`command`/`replay` persona **dials**, so
its emit's `peer_role` is the listener; a `real-service` persona **serves**, so its own host is. The
second half is where it breaks down: **a `real-service` persona's `emits[]` are not split into what
its daemon answers and what its host calls out to**, so a port the role *dials* is filed under the
role itself. Five catalog roles hold such a port today, each measured and named:

| role | ports it DIALS, filed as its own | what it really is |
|---|---|---|
| `web-tier` | 3306/tcp | its backend call to the db tier |
| `db-replica` | 3306/tcp, 5432/tcp | replication it PULLS from `db-tier` — its whole emit list |
| `protocol-gateway` | 502/tcp, 4840/tcp | southbound polls to `plc` and `historian` |
| `egress-proxy` | 53/udp (and 443/tcp northbound) | the proxy's own upstream calls |
| `monitoring-collector` | 9100/tcp, 161/udp | scraped ON `corp-workstation`; only 514 and 2055 arrive |

**Root cause.** `Emit` (`axrange/personas/schema.py`) carries `proto`, `ports`, `peer_role`,
`transport`, `cadence`, `volume`, `peer_scope`, `optional` — and a `direction` field that means
something else entirely (`"EW"`/`"NS"`, a flow's east-west/north-south shape, "descriptive only").
There is no field for *which end of this emit listens*, and for a dialing persona the answer is
implied by the drive while for a serving one it is not. **A future fix must not overload the existing
`direction` field**, whose meaning is unrelated and already documented.

**Today's mitigation (the Console's, not a fix).** `scripts/gen_role_listen.py` generates the table
drive-aware, names the five roles in `_DIALED_BY_A_SERVING_PERSONA`, exports them to the SPA as
`ROLES_WITH_DIALED_EMITS`, and `check_dialed_caveat()` fails loudly if one of them stops being a
`real-service` persona of the catalog. The Console repeats the caveat **where it offers the prefill**
— naming the typed role first when it is one of the five — so a prefill reads as a starting point to
prune, never as a claim that the host serves the port. Load-generator personas (`loadgen-*`) are
excluded outright: their netflow emits describe capture-throughput scaffolding, not a host's listen
set.

**Fix sketch, and why it needs its own decision.** The clean shape is a per-emit direction (e.g.
`serves: true` / `role: server|client`, or a per-persona `serves:` port list) that a `real-service`
persona's emits carry, with the dialing drives keeping today's meaning. That touches a
**version-locked** contract: `docs/VALIDATOR.md`'s PSN rules over `emits[]` (`PSN-040`..`PSN-110`)
would gain one, and every shipped catalog file would need auditing for which of its ports are served.
Additive and non-breaking is achievable (an omitted value reproduces today's behaviour exactly, the
`transport`/`peer_scope` precedent), but it is a spec change with a rule-count bump and belongs in a
work order of its own rather than in a UI slice. Until then, every consumer deriving a listen set from
the catalog must carry the same disclosure — and a consumer that silently picks one interpretation is
the failure this entry exists to prevent.

**Related:** `axrange/personas/schema.py` (`Emit`), `axrange/personas/catalog/*.yaml`,
`scripts/gen_role_listen.py`, `o2/console/src/model/roleListen.generated.ts`,
`o2/console/src/model/roles.ts` (`defaultListenFor`), `docs/design/07-traffic-personas.md`,
`docs/VALIDATOR.md` §5.12, `docs/design/15-console-canvas.md` §8,
`docs/WORK_ORDER_console_canvas.md` §19.2 call 3.

## GAP-096 — A Console placement pin is written to the server at STAGE time, while its segment is still only pending

**Status:** 🔴 Open · **Owner: axRange** — the cleaner option is recorded below; S5.1's slices are done,
so this is a follow-up rather than a scheduled item
**Severity:** **Low** (an inert record in an operator-owned file; nothing deploys from it, and the
ordinary paths already retract it)
**Discovered:** 2026-09-16 — Console S5.1.d (`docs/WORK_ORDER_console_canvas.md` §20.5; CHANGELOG
2026-09-16 "Console S5.1.d–e BUILT").

**Symptom.** Staging a clone commits its placement immediately as an `axr-layout/1` pin
(`segment:<cidr>`) through `PUT …/layout` — a **server** write — while the segment itself is only a
**pending** record that the change list correctly labels *pending (browser only)*. The two states
disagree for as long as the edit is unsaved. If the operator then abandons the draft (or clears browser
storage) without undoing, the pin stays behind naming a segment that never existed; and an undo that
removes the last pin leaves an **empty** `layout.json` rather than removing the file.

**Root cause.** A clone's drop position is committed through the same S4 writer a drag uses, which is
what makes the plane-stamp rule hold (LES-296: one gesture authors exactly one pin) — but S4's writer
was built for pins on **observed** tiles, which always exist. Nothing in that path knows the pinned
segment is itself an unsaved assertion.

**What already limits it.** Whole-gesture **undo drops the pin** with the records, and **discarding a
restored draft drops its pins too** (both proven by tests in `s51d.ui.test.tsx`). So the gap needs an
operator who abandons the work by a route that is not undo or discard.

**Fix sketch.** Carry a pending segment's placement **in the draft** rather than in `layout.json`, and
write it to the server when the record that adds the segment is **saved** — the same moment the segment
stops being browser-only. An orphan sweep is the cheaper alternative (drop a `segment:` pin whose CIDR
no record adds and no export observes, on load), but it is a write the operator did not ask for, so it
should be offered rather than performed. Either way, delete the layout document when the last pin goes
instead of leaving `{"pins": {}}` behind.

**Related:** `o2/console/src/App.tsx` (the stage-time pin commit and `dropPinsOf`),
`o2/console/src/edit/draft.ts`, `axrange/api/console_files.py` (`axr-layout/1`),
`docs/WORK_ORDER_console_canvas.md` §20.5, `docs/LESSONS.md` LES-289 (a persisted override needs an
owner for its subject's death — this is that rule's other end: an override created before its subject
exists).

## GAP-097 — The topology export's `schema_version` is rendered UNCHECKED by both consumers, where the seam says a consumer must refuse a major version it does not know

**Status:** 🔴 Open · **Owner: axRange** — owed by the SPA (the one design note 15 §9 seam 1 names);
narrowed from "both surfaces" when the Grafana control-panel plugin (the other consumer named below)
was deleted 2026-10-01 (`docs/reference/ui-consolidation/05-deletion-candidates.md`)
**Severity:** **Medium** (a future producer bump would be drawn as a partial graph with full
confidence, instead of being refused — the failure mode the seam rule exists to prevent)
**Discovered:** 2026-09-16 — Console S7 build, while deleting the plugin's imprints screen
(`docs/WORK_ORDER_console_canvas.md` §21.5; CHANGELOG 2026-09-16 "Console S7 BUILT and D5 EXECUTED").

**Symptom.** `TopologyExportDTO.schema_version` (the export's own `"1.3"` / `"1.4"` / `"1.5"`) is
**displayed** by the plugin's `TopologyExportView` and **mirrored but never read** by the SPA
(`o2/console/src/api/types.ts` carries the field; nothing consumes it). Neither consumer branches on
it. A producer that bumps the **major** version — dropping a field, changing what one means, adding a
required block — would therefore be drawn by both surfaces as whatever they can still parse: a partial
graph, rendered exactly as confidently as a complete one.

**Root cause.** `docs/design/15-console-canvas.md` §9's **seam 1** states the rule (a consumer refuses a
major version it does not know rather than drawing a partial graph), and both consumers were built
against the versions that existed, with the field treated as display/provenance data. Nothing enforces
the rule at the boundary, and the version has only ever moved within a major so far — which is exactly
when such a check is cheap to add and impossible to miss the absence of.

**Workaround.** None needed today (every shipped bundle is `1.x` and both consumers understand it); the
exposure begins at the first `2.x` export.

**Fix sketch.** One shared predicate per consumer — parse the major, compare against the highest this
build knows, and **refuse with the version pair in the message** ("this bundle is `axr-topology/2.0`;
this Console understands 1.x — update the Console, or read the bundle with a matching build") rather
than rendering. The SPA is the one the seam names, and is now the only surface left to fix (the
plugin's `TopologyExportView` was deleted 2026-10-01 along with the rest of the plugin). Pin it with a
fixture carrying a future major, asserting the refusal — the only way the check can be shown to exist
before the day it matters.

**Related:** `o2/console/src/api/types.ts`, `o2/console/src/model/graph.ts` (`buildGraph`, the consumer
that would draw the partial graph), `o2/grafana/plugins/axellio-axrange-control-panel/src/components/
TopologyExportView.tsx`, `axrange/api/dtos.py` (`TopologyExportDTO`), `docs/design/15-console-canvas.md`
§9 seam 1, `docs/LESSONS.md` LES-240 (a snapshot a later release will diff against needs a schema
version from day one — this is the reader's half of the same contract).

## GAP-098 — The plan an operator confirms is not PINNED at deploy: the server re-plans, and agreement rests on identical inputs plus a deterministic planner

**Status:** ✅ Resolved for twins and imprints — 2026-09-30 (Unified Console fix round 1, `71d01491`).
`POST /api/plan`/`POST /api/validate` publish `plan_id`/`fingerprint`; `POST /api/deploys` and
`POST /api/imprints/{id}/deploy` accept the matching `expect_plan_id` and refuse
`409 {"code": "plan-changed", "plan_id": "<actual>"}` when the server's own re-plan disagrees,
exactly the fix sketch below — live-verified 2026-09-30 (stale-plan 409 reproduced on the disposable
`sme-flat` write-path run). The plain-scenario `POST /api/deploys` path (no `config`/`imprint`) is
unaffected — same divergence window as before, lower blast-radius since a raw scenario has no
on-disk bundle an operator edits between preview and confirm.
**Severity:** **Medium** (the divergence window is narrow and needs an on-disk edit to open, but the
screen's entire job is to state the blast radius before real VMs are created) — resolved for the
config/imprint paths this gap was raised against.
**Discovered:** 2026-09-16 — Console S7 build, wiring the plugin's "Deploy as twin" action
(`docs/WORK_ORDER_console_canvas.md` §21.5; CHANGELOG 2026-09-16 "Console S7 BUILT and D5 EXECUTED").

**Symptom.** The action's gate is *preview → confirm → deploy*: `POST /api/imprints/{id}/plan` builds
the whole DAG (pure — no `state.db` write, no cluster call) and the confirmation restates its **op
count** and the destination's `api_url`. But `POST /api/imprints/{id}/deploy` **accepts no `plan_id`**
and its response returns none, so the server **re-plans** from the bundle on disk. What the operator
confirmed and what is deployed agree because the inputs are the same and the planner is deterministic —
not because the deploy was told which plan to run.

**Root cause.** The two routes were built independently: plan is a preview endpoint and deploy owns its
own planning, which is right for a CLI-shaped caller and leaves a UI with no way to say *this* plan. The
divergence window is any change to the bundle on disk — a re-cast, an `install-cast`, a hand edit,
another operator's apply — **between** the preview and the confirmation.

**Workaround.** Re-preview immediately before confirming (the action already retracts the preview
whenever the imprint or the target changes, so the shown plan always matches the current selection —
just not necessarily the current bundle).

**Fix sketch.** Publish the `plan_id` in the plan response, accept an **optional** `plan_id` on deploy,
and **refuse with both values** when the id the caller pins is not the id the server would build now —
the same "report, never adopt" shape the assertions etag uses (LES-251). That makes preview↔deploy
agreement a *checked* property rather than an inherited one, and it costs a caller that does not care
nothing. A weaker alternative — returning the plan's `ir_hash` in the deploy response so a UI can
compare after the fact — tells the operator only once it is too late.

**Related:** `axrange/api/service.py` (`imprint_plan`, `imprint_deploy`), `axrange/api/app.py` (the two
routes), `o2/grafana/plugins/axellio-axrange-control-panel/src/components/deploy/DeployTwinAction.tsx`,
`docs/design/11-api-ui.md`, `docs/LESSONS.md` LES-251.

## GAP-099 — `xfreerdp` fails with rc=1 and NO output at all against an open 3389, even at `/log-level:INFO`

**Status:** 🔴 Open · **Owner: axRange** — undiagnosed
**Severity:** **Medium** (the `rdp` protocol engine produces no session; what reaches the sensor is a
TCP connection and nothing that labels as `rdp`, so the emit proves nothing)
**Discovered:** 2026-09-21 — the LIVE protocol-engine run on dev45 (twin `svc-engines`, deploy
`svc-engines-24131435`), by hand from `linux-client` against the joined Windows member `win-member`.

**Symptom.** The `rdp` template's invocation returns **exit 1 and prints nothing whatsoever** — not an
error line, not a banner, not a connection attempt — against a port that is demonstrably open. Raising
verbosity with `/log-level:INFO` changed nothing: still no output. A tool that fails silently gives the
operator no thread to pull, which is why this is recorded rather than guessed at.

**What was tried, exactly.**

```sh
# the rendered argv (command._build_rdp), with the password on stdin
echo "$PW" | xfreerdp /v:10.66.20.21:3389 +auth-only /sec:nla /cert-ignore \
    /u:alice /timeout:5000 /d:corp /from-stdin
#   -> rc=1, no stdout, no stderr
echo "$PW" | xfreerdp ... /log-level:INFO      #   -> rc=1, still no output
```

* The port **is open**: `enable-rdp.ps1` ran on `win-member`, and 3389/tcp answers.
* The credential path is the platform's: `DEFAULT_PASSWORD` on **stdin** via `/from-stdin`, the same
  secret `alice` was created with (`svc-engines.yaml` authors no explicit password precisely so the
  two agree), and the same one `kinit alice@CORP.AXR.LAB` accepts on the same host.
* The client is Debian's `freerdp2-x11`.

**Not diagnosed.** Candidates worth eliminating in order, none tested: `/from-stdin` consuming the
piped password differently than expected (it may prompt on the TTY rather than read stdin, which on a
non-interactive run would explain both the instant failure and the silence); a `+auth-only` path that
exits before any logging is initialized; FreeRDP 2.x's `/log-level` needing `/log-filters` or
`WLOG_LEVEL=` in the environment to take effect; NLA/CredSSP failing against a 2022 member in a way
FreeRDP 2 reports only at `TRACE`. The first step is simply to get the tool to **say something** —
`WLOG_LEVEL=TRACE`, `/log-file:`, or `strace -f` on the invocation — before changing any argv.

**Workaround.** None. The emit stays authored (it is real work when it works) and its failure is
visible in the persona's `ok=`/`fails=` counters rather than hidden.

**Related:** `axrange/personas/drivers/command.py` (`_build_rdp`, the `rdp` template row),
`twin-configs/svc-engines/personas/svc-engines-mgmt-client.yaml`,
`twin-configs/svc-engines/scripts/enable-rdp.ps1`, `twin-configs/svc-engines/README.md` step 3b,
`docs/WORK_ORDER_protocol_engines.md` §5 E5.

## GAP-100 — The any-IP sink's acceptance path does not deliver to its own listening sockets

**Status:** 🟠 Open · **Owner: axRange** — **root cause FOUND and fixed in the recipe 2026-09-23;
live verification NOT achieved, and a SECOND symptom appeared underneath it** (see "2026-09-23"
below)
**Severity:** **High** (blocks E2's own acceptance criterion — the sink is the keystone of the
containment triangle; with the measure phase on, external destinations are correctly contained
but the sink does not yet answer for them, so nothing behind that boundary can be exercised)
**Discovered:** 2026-09-21 — the LIVE protocol-engine run on dev45 (twin `svc-engines`, deploy
`svc-engines-24131435`), during the E9 live-verify.

**2026-09-23 — root cause, the fix, and what is still not proven.**

*Cause (measured).* The any-IP route lets the kernel ACCEPT a packet addressed to someone else's
address, but it does not make that address usable as a SOURCE. Building the SYN-ACK the kernel asks
for a route out of `140.82.113.3`, finds it is not a local address, and gives up. The SYN is then
dropped at the listening socket — visible ONLY as `nstat TcpExtListenDrops` climbing (1316 of them)
while `tcpdump` shows the SYN arriving and nothing going back. The sink's own fallback responders
always worked, for the reason that is the fix: they are reached by a dstnat REDIRECT, which
rewrites the destination BEFORE the routing decision, so the socket is reached the ordinary way and
the reply is sourced from the host's own address; conntrack rewrites it back so the client still
sees the address it dialled. The SERVED ports (22/53/80/443, udp 53/123/443) were the only ones
excluded from that redirect and relying on any-IP alone.

*Fix.* `twin-configs/_shared/scripts/serve-sink.sh` now redirects the served ports to themselves for
a foreign destination (committed). Applied live, the rules are present and the sink's services are
healthy, and **`TcpExtListenDrops` stopped climbing** — the drop mechanism above is closed.

*Not proven, and a second symptom.* With the router back in `measure`, a client TLS connection to
`140.82.113.3:443` still does not complete — but now **no packet reaches the sink at all** (a
`tcpdump` on the sink's `eth0` filtered to that host captured **zero** packets during the attempt),
where the same test on 2026-09-21 showed SYNs arriving. State checked at that moment: the client's
route is `140.82.113.3 via 10.66.20.1` (correct); the router carries rules 999/1000/1001 and
`table 210 default via 10.66.30.10` (correct); its WAN drop counters are **0 packets**, so nothing
is being dropped there either. So the packet leaves the client, is not dropped by the router, and
does not arrive at the sink. **Next step: capture on the ROUTER's `eth1` (ingress) and `eth2`
(egress toward the sink) during one attempt** — that single measurement says whether the router
forwards it, and is the one thing not yet measured.

*Also note.* `axr-egress-phase.sh`'s own assertion `ip route get <dst> iif <lan> from <lan-ip>`
returns `RTNETLINK answers: Invalid argument` on this kernel, so `measure` reports "applied, but the
live state does not assert" even when the policy IS correctly applied. E1 predicted this exact risk.
That assertion needs a kernel-portable form, or the operator reads a real failure as a false alarm.
**Fixed 2026-09-28** (corp-twin devlab live run 2, `WORK_ORDER_corp_twin.md` §11.D): the cause
is the source, not the kernel version — `from <the router's own address> iif <leg>` is a martian
source on input (EINVAL on any Linux with `accept_local=0`). The probe now uses a non-local host
address on each leg and asserts every LAN leg (`egress_render.axr_probe_src`).

**Symptom.** From a guest, a TLS connection to an **external** observed name (`github.com` ->
`140.82.113.3:443`, after the router's `measure`-phase policy correctly points default egress at
the sink) never completes: no SYN-ACK is ever returned. Every layer checked along the path looks
correct in isolation:

* the router's measure-phase policy is applied (nft rules 999/1000/1001; policy-routing table 210
  `default via 10.66.30.10`; WAN-forward drop counters read **0 packets** — i.e. nothing is being
  silently dropped upstream of the sink);
* `rp_filter` is `0` on both the router and the sink (a strict reverse-path filter would explain a
  silent drop, and it is ruled out);
* the SYN packet **arrives** at the sink's `eth0` (tcpdump-proven, addressed to `140.82.113.3:443`,
  a foreign address the sink's own interface does not own);
* `ip route get 140.82.113.3 iif eth0 from 10.66.20.11` on the sink answers `local … dev lo table
  100` — the any-IP `local 0.0.0.0/0 dev lo` route IS being matched;
* the nft `prerouting` chain's TPROXY/redirect rules return early for `tcp dport {22,53,80,443}` —
  i.e. the intended fast-path for the ports Caddy listens on;
* Caddy is confirmed listening on `*:443`.

**And no SYN-ACK is ever sent.** Adding `local default dev lo` to the `local` table directly (a
narrower, more explicit route than the `0.0.0.0/0` one already there) did not change the outcome.
Undiagnosed beyond the checklist above — every individually-verified piece should add up to a
locally-terminated connection, and it does not.

**Consequence.** Measured live: with the measure phase ON, external destinations are contained
(they reach nothing on the real internet) but the sink does not answer them either, so E2's DNS/TLS
responder path is unproven for anything outside the twin's own internal names. `docs/
WORK_ORDER_protocol_engines.md` E1's own risk list predicted trouble at exactly this seam ("the
riskiest unknown: the sink's protocol detection against real client diversity").

**Not diagnosed / next steps.** Candidates worth eliminating in order, none tested: a `conntrack`
state left over from before the `local` route existed (loopback-routed traffic uses a different
conntrack path than a normal forward, and an established-looking but wrong-direction entry would
silently eat the SYN); a missing or wrong `ip rule` entry so the `local` table lookup never actually
triggers for a **new** connection the way `ip route get`'s explicit query does; a TPROXY mark not
being set on the packet before the local-delivery route decision, so the kernel does look up `local`
but has no listening socket bound the way TPROXY's non-local-bind requires (`IP_TRANSPARENT` on the
Caddy listener, not just on the nft mark). `strace`/`ss -K` on the actual packet at delivery time,
or a `conntrack -L` immediately after a probe SYN, is the next concrete step, not another
routing-table read (four of those already agree and still don't explain the silence).

**Related:** `docs/WORK_ORDER_protocol_engines.md` §5 E2/E9, `twin-configs/svc-engines/scripts/
provision-sink.sh`, `axrange/adapter_proxmox/egress_render.py`, GAP-101.

## GAP-101 — `egress:` (the twin's provision/measure phase) cannot be authored in a twin config, so containment depends on a manual step nothing enforces

**Status:** ✅ **Resolved 2026-09-23** (commit `6b8cc50b`, "a twin declares its containment, and
`te info` says whether it has it") — landed **before** `wip/corp-twin` branched and only now
recorded closed in this register during the corp-twin doc pass. `TwinConfig` gained an `egress:`
block (`TwinConfigEgress`: `phase`, `sink`/`sink_ip`, `mode`/`cidrs`) that expands to the IR's
top-level `egress:` and from there onto every `kind: router` node's `configure-router` input —
exactly the fix sketch below, at the twin level rather than per-node (so a two-router twin can't
declare two postures). `corp-twin`'s own `twin.yaml` containment is derived from this block (WO
consolidation §7 D3), and 2026-09-26's `axrange te egress` (LES-348) reads/flips it live. Old text
kept below for the trail.
**Status (superseded, kept for the trail):** 🔴 Open · **Owner: axRange** — needs an `IR_SCHEMA`/`TwinConfigNode` extension
**Severity:** **High** (a twin deployed and left alone reaches the real internet — measured, not
theoretical: see below)
**Discovered:** 2026-09-21 — the LIVE protocol-engine run on dev45, during the E9 live-verify.

**Symptom.** `docs/WORK_ORDER_protocol_engines.md` E1 built a router `provision`/`measure` phase
input, fail-safe default CLOSED — but there is nowhere in a twin config to *ask* for it.
`TwinConfigNode` forbids extra keys, so an `egress:` block has no schema slot to land in, and the
phase has to be flipped by hand against the deployed router after the fact.

**Consequence, measured live.** Because the switch is a manual, easy-to-forget step, it *was*
forgotten for part of this round's run: before `measure` was applied, the router's default route
went out the WAN to the lab network, which itself has real internet access — so external traffic
from the twin reached the **real internet**, not the sink. Proven, not inferred: a TLS handshake to
`github.com` presented a real **Sectigo** certificate, not the lab CA `provision-sink.sh` issues.
The gap was caught and closed (the measure phase now drops WAN egress and the containment breach
is over for this deploy) precisely **because** someone happened to check the certificate; nothing
in the product would have surfaced it otherwise.

**Root cause.** E1 built the mechanism (the router-side phase toggle) without a way to declare or
default it at the IR/twin-config level — "fail-safe default CLOSED" describes what happens to an
*unphased* router only in the sense that the mechanism, once invoked, defaults closed; it says
nothing about a twin that never invokes the mechanism at all, which is every twin authored today.

**Fix sketch.** Add an `egress:` key to `TwinConfigNode` (router-scoped: `phase: provision |
measure`, defaulting to `measure` so an *authored-but-unset* twin is the safe case too, matching
the router's own default-closed rule at every layer) so the phase is declared where the twin is
declared, not applied out-of-band; `te deploy`/`te deploy --update` then compiles it the same way
any other router config lands. Until this lands, **any twin standing on a network with real
internet access must have its egress phase set by hand before it is trusted for containment**, and
that step needs to be a named, checked item in every deploy runbook — not tribal knowledge.

**Related:** `docs/WORK_ORDER_protocol_engines.md` §5 E1, `axrange/twin_config/schema.py`
(`TwinConfigNode`), `axrange/adapter_proxmox/egress_render.py`, GAP-100.

## GAP-102 — Suricata 7.0.10 has no LDAP parser and logs no `ntp`, so its verdict on those protocols measures the sensor, not the engine

**Status:** 🔴 Open · **Owner: axRange** — accepted sensor limitation for v1, tracked so a future
scorecard read does not mistake it for an engine defect
**Severity:** **Low** (honest, reported limit — the engine-side evidence for both protocols is
independently strong; this is purely about what the *sensor* can see)
**Discovered:** 2026-09-21 — the LIVE protocol-engine run on dev45 (twin `svc-engines`), reading
`scripts/verify_wire_protocols.py`'s sensor-side verdict against the persona's own counters.

**Symptom.** Two protocols score worse at the sensor than the engine's own results justify:

* **LDAP reads ABSENT.** Suricata **7.0.10** ships no LDAP protocol parser at all — the parser
  arrives in Suricata **8.0** — so `eve.json` can never contain an `ldap` event type regardless of
  how real the traffic is. The engine side is not in question: `ldapsearch -Y GSSAPI` completed a
  real SASL bind (SSF 256, `alice@CORP.AXR.LAB`) once the reverse-DNS/SPN fix landed (E3's finding).
* **Kerberos reads PARTIAL, not PASS.** The KDC answered a TGS request with `KRB_ERR_RESPONSE_TOO_BIG`
  (a large TGS-REP, expected with AD's PAC), the client correctly retried over TCP per RFC 4120 —
  and Suricata logged that TCP leg as an unclassified flow rather than continuing to label it
  `krb5`, so the sensor's flow-classification count takes a hit for behavior that is protocol-correct.
  The engine's own counters (`kinit`+`kvno` succeeding **4/4**) are unaffected and are the trustworthy
  read here. Suricata also emits **no `ntp` event type** at all in this build, for the same reason
  as LDAP — a coverage gap, not a traffic defect.

**Why this belongs in the register rather than being fixed in the read.** `scripts/
verify_wire_protocols.py` and any future scorecard consumer must **not** average a sensor coverage
gap into a protocol-fidelity verdict the way a real classification failure would be scored — the two
have different remedies (upgrade Suricata vs. fix the client). This entry exists so that distinction
is written down once instead of re-derived on every read.

**Fix sketch.** Upgrade the standing/dedicated sensor to Suricata **8.0+** for LDAP parsing (bookworm-
backports or a newer base image); until then, `verify_wire_protocols.py` should label an ABSENT/PARTIAL
verdict on `ldap`/`ntp`/`kerberos`-over-TCP-retry as **sensor-limited**, distinctly from a genuine
classification miss, the same way GAP-076 already carves out TLS's missing `tls` event record.

**Related:** GAP-076 (the sibling TLS `tls`-event gap on the same sensor version), `scripts/
verify_wire_protocols.py`, `docs/WORK_ORDER_protocol_engines.md` §5 E9,
`docs/WORK_ORDER_protocol_faithful_services.md` §9.2.

## GAP-103 — corp-twin: WAN2's Meraki-uplink failover behavior is not modelled

**Status:** 🟡 Open · **Owner: axRange** — no engine mechanism for a second, idle WAN leg
**Severity:** Low (cosmetic on the wire; the real network's failover behavior is not exercised)
**Discovered:** 2026-09-26 — `docs/WORK_ORDER_corp_twin.md` §8 G6, corp-twin's device-parity round.

**Symptom.** corp-twin's `wan2-isp` leg is present but permanently idle — a Meraki MX uplink
failover (active WAN1, standby WAN2, health-probe-driven cutover) has no analogue in the engine;
`hq-mx` never routes over it.

**Fix sketch.** Out of scope for a v1 route-based egress model; would need a policy-routing health
probe + failover rule on `configure-router`, a real second-WAN scenario to justify it.

**Related:** `docs/WORK_ORDER_corp_twin.md` §8 (fidelity gaps table, G6).

## GAP-104 — corp-twin: `hq-mx`'s Proxmox 32-NIC ceiling is not validated at low `DEPLOY_MIN_OCCUPANCY`

**Status:** 🟡 Open · **Owner: axRange** — no plan-time NIC-count guard against the adapter's limit
**Severity:** Medium (a plan can pass validation and fail at deploy against a real cluster limit)
**Discovered:** 2026-09-26 — `docs/WORK_ORDER_corp_twin.md` §6, §8 G10.

**Symptom.** Every off-map/HQ leg lands on `hq-mx` today (one router carries every leg), so a low
`DEPLOY_MIN_OCCUPANCY` overflows PVE's 32-NIC-per-VM ceiling: 27 NICs at occupancy 0.8, 33 at
occupancy 0. There is no plan-time check that refuses this before a live deploy hits the adapter's
own limit.

**Fix sketch.** A validator/planner check counting a router's legs against the adapter's known NIC
ceiling (32 for PVE), refusing at plan time with the occupancy value that would fix it — the same
"name the fix in the refusal" shape LES-098 already uses elsewhere.

**Related:** `docs/WORK_ORDER_corp_twin.md` §6 (addressing/relocation), §8 G10.

## GAP-105 — The API/serve path does not add a twin folder's `personas/` to the persona search path

**Status:** ✅ Resolved in full — 2026-09-30 (Unified Console fix round 1, `71d01491`).
`GET /api/personas?twin=<id>` and the per-host persona apply path (`POST .../personas/plan`,
`PUT .../personas`) resolve a twin-local persona overlay per request, never mutating the
process-global registry (`docs/reference/ui-consolidation/04-api-contract.md` "GET /api/personas").
The whole-profile route (`POST /api/tfc`) now does the same (`axrange/api/service.py`
`_tfc_twin_overlay`) — verified by `tests/test_unified_console_fixes.py`, and the doc-writer's
"not yet confirmed" line from the 2026-09-29 pass is closed out: the round's tests pass. The CLI
path (`--twin`) was never affected.
**Severity:** Medium (a twin deployed through the API/serve surface silently missed its own personas;
the CLI path was unaffected) — resolved
**Discovered:** 2026-08 (S0's original scope, LES-275) — restated 2026-09-26 as WO corp-twin §8 G15
and WO twin-consolidation Phase 2+3 record ("not small — left open").

**Symptom.** `resolve_twin_config_path`/S0's loader adds a twin folder's `personas/` to the
persona-resolution path only through the CLI's `--twin` flow. The API takes inline traffic profiles
against a process-global persona registry, so a twin-local persona is invisible to `POST` deploys
through `axrange serve`.

**Fix sketch.** A per-request persona overlay scoped to the twin id in the request, layered onto the
process-global registry for the duration of that request/resolution — not a global mutation (would
race concurrent requests for different twins).

**Related:** `docs/WORK_ORDER_corp_twin.md` §8 G15; `docs/WORK_ORDER_twin_consolidation.md` Phase 2+3
record ("Phase 3 still open"); LES-275.

## GAP-106 — PVE's boot-time handling of an address-less (no-`ipconfigN`) NIC is assumed, not verified

**Status:** 🟡 Open · **Owner: axRange** — needs a live boot check against real PVE cloud-init
**Severity:** Medium (if wrong, every L2-leg boot on a real cluster could stall on a DHCP timeout
per port instead of coming up link-down as assumed)
**Discovered:** 2026-09-26 — `docs/WORK_ORDER_corp_twin.md` §8 G18; earmarked for live-verify §11 D1.

**Symptom.** A NIC with no `ipconfigN` is assumed to be left unconfigured by PVE's cloud-init network
render (no DHCP attempt), so a bridge port boots link-down and the device recipe brings it up
itself. This is a hypervisor-side behavior that cannot be checked offline; if PVE instead emits a
DHCP attempt for an address-less NIC, boot waits on a DHCP timeout per such port instead.

**Fix sketch.** Live-verify runbook §11 D1 (`journalctl -b | grep -i dhcp` on a device VM with
address-less legs) settles this the first time `corp-twin-devlab` actually boots on `dev45`; record
the answer here once run.

**Related:** `docs/WORK_ORDER_corp_twin.md` §8 G18, §11 D1.

## GAP-107 — axRange's SDN VLAN-zone vnets cannot author real/explicit VLAN tags or a trunk (multi-VLAN) link

**Status:** 🟡 Open · **Owner: axRange** — needs `IR_SCHEMA`/planner/adapter work, not scoped
**Severity:** Medium (every segment is its own auto-tagged, single-VLAN, untagged-to-authors vnet;
a twin that needs a real 802.1Q trunk between two devices has no engine primitive for it)
**Discovered:** 2026-09-26 — `docs/WORK_ORDER_corp_twin.md` §4 (finding 5) and §8 G9.

**Symptom.** A Proxmox VLAN zone auto-assigns its own tag per vnet (`expand.py`'s `_VLAN_BASE`); an
author cannot pin a specific VLAN ID, and there is no IR-level trunk (tagged multi-VLAN) link
primitive — every segment is its own untagged-to-the-author vnet. corp-twin's device-parity round
worked around this entirely at the recipe layer (the Linux-bridge switch VMs tag real 802.1Q IDs on
top of untagged SDN vnets), which proves the wire behavior but does not give the *engine* a way to
author a real tagged trunk or a pinned VLAN ID directly on a segment.

**Fix sketch.** Not scoped. Would need an IR-level `vlan_id:`/`trunk:` segment property, an
`OP_CATALOG` op the adapter compiles it through (802.1Q on a Proxmox VLAN-zone vnet, or a bridge
device dedicated to the purpose), and a decision on whether tags should be author-pinned at all
(collision risk across scenarios on one cluster — see GAP-006's cross-scenario VLAN-ID class).

**Related:** `docs/WORK_ORDER_corp_twin.md` §4 finding 5, §8 G9; GAP-006 (cross-scenario VLAN
collision, a sibling concern for the same primitive).

## GAP-108 — An internal (no-WAN) VyOS device needs `IR_SCHEMA`/`OP_CATALOG` changes axRange doesn't have yet

**Status:** 🔴 Open · **Owner: axRange / user** — a locked-contract change, gated on the user's
WO §12 D1 call
**Severity:** Medium (blocks the letter of the user's "VyOS VMs" device-parity ruling; the wire
behavior is delivered today by a reversible Debian-host substitution, WO §4.1)
**Discovered:** 2026-09-26 — `docs/WORK_ORDER_corp_twin.md` §4.1, §12 D1.

**Symptom.** VyOS ships no QGA/cloud-init in the generic image, so it cannot run a `run-script`
recipe the way every other device-parity device does; a real internal VyOS **router** additionally
needs a no-WAN mode and bridge/VRRP inputs on `configure-router` that don't exist — both are
`IR_SCHEMA` + `OP_CATALOG` changes, i.e. version-locked contract work, not a twin-config-level fix.
corp-twin's device-parity round substitutes Debian `kind: host` VMs with role recipes instead
(CHANGELOG 2026-09-26 "corp-twin device parity…", reversible), explicitly as a stand-in for this gap.

**Fix sketch.** Scope a work order for: a `no_wan: true` (or equivalent) input on `configure-router`;
bridge/VRRP-shaped inputs the VyOS `vbash` engine can consume; and a way to run VyOS's own config
engine without QGA (vbash over SSH, as the WAN-facing router already does). Gated on the user's
choice between accepting the Debian substitution as parity or ordering this work (WO §12 D1).

**Related:** `docs/WORK_ORDER_corp_twin.md` §4.1, §12 D1; CHANGELOG 2026-09-26 "corp-twin device
parity as Debian hosts…".

## GAP-109 — `apt-daily-upgrade.timer` needs an engine- or image-level fix, not a per-recipe quiesce

**Status:** 🟡 Open · **Owner: axRange** — needs a cloud-init userdata mask or an image-bake change
**Severity:** Medium (every twin's `provision-app` apt step races apt-daily the same way; this round's
corp-twin devlab run won its race only by timing, not by a fix)
**Discovered:** 2026-09-28 — `docs/WORK_ORDER_corp_twin.md` §11.D live run 1 (LES-353).

**Symptom.** corp-twin's sink recipe now quiesces `apt-daily{,-upgrade}.timer` before its own first
package step (`axr_apt_quiesce`), but every OTHER twin's `provision-app` ansible apt step still
races the same timer on every deploy — this round happened to win the race by timing, not by design.

**Fix sketch.** Mask `apt-daily.timer`/`apt-daily-upgrade.timer` in the cloud-init userdata (every
deploy) or in the image bake (once, per LES-024's "bake fragile tooling" rule) rather than
per-recipe; retire the per-recipe quiesce once the general fix lands.

**Recurrence (2026-10-05, corp-twin redeploy):** `run-script[9k02-app]` (`serve-vrrp-l3.sh`) lost attempt 1 to the dpkg lock (exit 100) and passed on retry — the race is not sink-only.

**Related:** `docs/WORK_ORDER_corp_twin.md` §11.D; LES-353; `twins/_shared/scripts/serve-sink.sh`.

## GAP-110 — `twins/svc-engines/scripts/provision-sink.sh` still embeds the pre-fix shared sink recipe

**Status:** 🟡 Open · **Owner: axRange** — rebuild before svc-engines' next deploy
**Severity:** Medium (the standing `svc-engines-24131435` twin's sink was deployed with the buggy
heredoc; not itself known to have misfired, but the fix has not been applied to it)
**Discovered:** 2026-09-28 — `docs/WORK_ORDER_corp_twin.md` §11.D live run 1.

**Symptom.** The unquoted-heredoc fix (LES-351) landed in `twins/_shared/scripts/serve-sink.sh` and
was folded into corp-twin's own `provision-sink.sh` via `build-sink-payload.sh`, but
`twins/svc-engines/scripts/provision-sink.sh` was left as-built (it backs the live
`svc-engines-24131435` deploy) and is explicitly excluded from `tests/test_twin_recipe_heredocs.py`
for that reason.

**Fix sketch.** Run `sh twins/svc-engines/scripts/build-sink-payload.sh` to rebuild it from the fixed
shared recipe before svc-engines' next deploy, then drop its heredoc-test exclusion.

**Related:** `docs/WORK_ORDER_corp_twin.md` §11.D; LES-351; `twins/svc-engines/scripts/provision-sink.sh`.

## GAP-111 — sme-flat and ics-ot bind the Windows `corp-workstation` persona onto Debian hosts

**Status:** 🟡 Open · **Owner: content** — pick a Linux workstation persona or a Windows image per twin
**Severity:** Medium (each twin's `tfc deploy` would fail every `corp-workstation` instance with
`Failed to execute child process`; `tfc validate`/`tfc deploy` now refuse it up front)
**Discovered:** 2026-09-28 — the os-compatibility repo guard added for `docs/WORK_ORDER_corp_twin.md`
§11 live run 3 (`tests/test_tfc_persona_os.py`).

**Symptom.** `twins/sme-flat/tfc/default.yaml` (`role: corp-workstation`) and
`twins/ics-ot/tfc/default.yaml` (`role: corp-ws`) assign the catalog `corp-workstation` persona
(`os_family: windows`, PowerShell scripted client) to `debian-12-cloud@v1` hosts. Before the
Windows scripted driver existed the persona ran the Linux path regardless of `os_family`, so these
profiles predate the break.

**Fix sketch.** Per twin: either add a twin-local Linux workstation persona (the same emits,
`os_family: linux`) and assign it, or move the workstation hosts to a Windows image. Then drop the
twin's entry from `_KNOWN_MISMATCHES` in `tests/test_tfc_persona_os.py` (the guard is strict).

**Related:** `docs/WORK_ORDER_corp_twin.md` §11 live run 3; `axrange/o2control/traffic.py`
`persona_os_mismatch`.

---

## GAP-112 — The transport vnet is still 1500 MTU; full-size mirrored frames are fragmented, not carried whole

**Status:** 🟡 Open · loss fixed, cost remains
**Severity:** Low (no frames lost since 2026-09-28; the cost is ~2x transport packets for full-size
frames, plus reassembly work on the sensor, which matters only at multi-Gbit/s mirror rates)
**Discovered:** 2026-09-28 — corp-twin live run 3 mirror-loss diagnosis
(`docs/WORK_ORDER_corp_twin.md` §11, "Live run 3 — mirror loss").

**Symptom.** A full-size mirrored frame encapsulates to 1542 B (1546 B with an 802.1Q tag), but
every transport vnet on dev45 (`o2v7dc98`, the sensor's `net1`) is 1500. Before the fix the tunnel
dropped those frames silently; since `nopmtudisc ignore-df` (CHANGELOG 2026-09-28) it fragments
them and the sensor reassembles, visible as host `IpFragCreates`/`FragOKs` and sensor `IpReasmOKs`.

**Root cause.** `docs/ARCHITECTURE.md` §3's ratified preferred option ("bump `vnet-transport` MTU")
was never implemented: no op sets an MTU on the transport vnet, and an external sensor's NIC MTU is
operator-owned (`net1: virtio=…,bridge=o2v7dc98` without `mtu=`).

**Fix sketch.** The o2/sensor-transport create path sets the SDN vnet MTU to ≥ 1546 (1550), owned
sensors get `mtu=1550` on their transport NIC plus the guest link MTU; `EXTERNAL_SENSORS` docs tell
the operator to do the same (`qm set <vmid> -net1 virtio=<mac>,bridge=<transport>,mtu=1550` and
the in-guest NIC MTU). A Linux bridge takes its ports' minimum MTU, so every port must move
together. Keep `nopmtudisc ignore-df` as the floor for a sensor that has not been raised.

**Related:** `axrange/adapter_proxmox/ssh.py` `_GRETAP_PMTU_OPTS`; a separate, pre-existing loss
point is the sensor VM's own virtio ring (`tap104i1` `tx_dropped`, 4.39M cumulative at diagnosis,
0 in the 20 s sample) — the sensor not draining bursts, not the tunnel.

**Standing twins do not self-heal.** The `nopmtudisc ignore-df` fix only takes effect on a tap
apply (create or adopt-path changelink) — it does not retroactively touch a gretap nobody has
re-applied. Measured 2026-09-28: corp-twin's own gretaps read 0 `tx_errors`/0 `tx_dropped` after
its live run 3/4 re-applies, but the standing `svc-engines-24131435` twin's two gretaps
(`tapg_84e687`, `tapg_d1b207`) still lose ~1.49M frames/min each (`tx_errors`) — pre-fix, never
re-applied. Healing it needs `axrange tap deploy` against that standing twin (every tap apply
force-resets its ops to `pending`, so the changelink runs); left for the user, since it also
carries the stale sink recipe (GAP-110) and the twin is otherwise live.

**This fix sketch, validated 2026-10-01.** corp-twin's own capture-rate run proved raising the
transport MTU removes the fragmentation/reorder cost with no throughput loss — but only as a
twin-local, runtime-only workaround (`twins/corp-twin/scripts/mirror-mtu-path.sh`), not the op-level
fix this gap calls for. See GAP-119 for what is still missing.

## GAP-113 — The Unified Console ruling explicitly leaves several run-time capabilities unbuilt

**Status:** 🟡 Open · **Owner: axRange** — scoped out of this round by `docs/reference/ui-consolidation/00-design-ruling.md` §3.4, not forgotten
**Severity:** Low (each is a known, named absence rather than a discovered defect)
**Discovered:** 2026-09-29 — Unified Console design ruling §3.4.

**Symptom.** The Console (`docs/design/16-unified-console.md`) has no push updates — every screen
polls (`GET /api/deploys/{id}`, `GET /api/deploys/{id}/hosts`), same as the deprecated Grafana
plugin did; a large tfc apply (84+ ops on corp-twin) is watched by polling, not a live log/event
stream. It also has no pcap download UI (`GET /api/pcaps` stays a stub), no exercise (`exr`) run UI,
no multi-node placement UI, and a per-host persona edit is export-only — it is never written back
into a twin's own `twins/<id>/tfc/*.yaml` (`docs/design/16-unified-console.md` §5's "export, not
write-back"). The Design area's known column-wrapping gap (`docs/design/15-console-canvas.md` §12,
the map not fitting a 1600 px window) is unchanged by this round.

**Fix sketch.** Each is independently scoped work, not a single fix: SSE/websocket for progress: a
job/event model in the API; pcap download: implement the stub; exercise run UI: a Console screen
over `/api/exr`; multi-node placement: an engine capability first (see GAP-011); write-back: a
deliberate "save this host edit into the twin" action, distinct from the current read-only export.

**Related:** `docs/reference/ui-consolidation/00-design-ruling.md` §3.4; `docs/design/16-unified-console.md`
§10; `docs/design/15-console-canvas.md` §12; GAP-011 (no multi-node placement primitive).

## GAP-114 — A per-host persona apply accepts `intensity` but never persists or applies it

**Status:** 🟡 Open · **Owner: axRange** — same limitation as the pre-existing whole-profile apply, now visible on a second route
**Severity:** Low (the field is silently a no-op, not silently wrong — but nothing tells the caller)
**Discovered:** 2026-09-29 — Unified Console API-contract review
(`docs/reference/ui-consolidation/04-api-contract.md` "Per-host persona request body").

**Symptom.** Both the per-host persona body and a whole traffic-profile assignment accept an
`intensity` field, but the engine has never recorded or driven on it — `GET /api/deploys/{id}/hosts`
always reports a persona instance's `intensity` as `null`. This was already true of `POST /api/tfc`
before this round; the per-host apply (`PUT /api/deploys/{id}/hosts/{host}/personas`) inherits it
rather than fixing it, and an operator setting intensity in the Console's persona-edit form gets no
feedback that it did nothing.

**Fix sketch.** Either add an `intensity` column/field to the persisted op inputs and thread it
through the drivers, or refuse the field with a clear message until it is real, rather than
silently accepting and dropping it. A Console-side disclosure (grey out the control, or a note) is
a partial mitigation available without an engine change.

**Related:** `axrange/o2control/traffic.py`; `axrange/api/dtos.py`;
`docs/reference/ui-consolidation/04-api-contract.md`.

## GAP-115 — The Unified Console's live-read paths are unverified against a real cluster

**Status:** ✅ Resolved — 2026-09-30. The build order's live-verify step ran in two parts: Part A
(2026-09-29, read-only) against the standing `corp-twin-07cfcdb2` — all 106 hosts, 84 persona
instances and 80 tap bindings matched the CLI, live VM power state matched `pvesh`, per-host
heartbeat matched `systemctl is-active` on 2 sampled hosts, previews wrote nothing, six pages loaded
with 0 console errors (0.71 s for the 106-host Twin page); Part B (2026-09-30, write-path) against a
disposable `sme-flat` deploy proved the per-host apply's isolation guarantee live (two untouched
hosts' systemd units byte-identical before/after), the plan-fingerprint pin, protected-deploy 423,
os-mismatch and role refusals, the whole-profile revert guard, and destroy — full record:
`docs/CHANGELOG.md` 2026-09-30 "Unified Console live verification";
`docs/archive/work_orders/WORK_ORDER_unified_console.built.md`; screenshots
`docs/reference/ui-consolidation/shots/`. Two defects (D2, D3) surfaced by the run and were fixed
same day (fix round 4).
**Severity (at discovery):** Medium until verified (every live claim in this round — cluster
reachability, VM power state, QGA reachability, persona heartbeat — was implemented and
unit/API-tested, but none had been proven against a real Proxmox cluster as of the 2026-09-29 doc
pass).
**Discovered:** 2026-09-29 — Unified Console doc pass, ahead of its own live-verify step.

**Symptom (at discovery).** `GET /api/range`'s reachability probe, `GET /api/deploys/{id}/health`'s
cluster sweep and per-host QGA/heartbeat reads, and the per-host apply's post-start heartbeat were
all new code paths whose correctness against dev45/pxtwin1 had not yet been demonstrated live —
only their contract and (presumably) their test coverage.

**Related:** `docs/reference/ui-consolidation/00-design-ruling.md` §6–§7; `docs/design/16-unified-console.md` §4/§6.

## GAP-116 — `sme-flat`'s default traffic profile is unresolvable

**Status:** 🟡 Open · **Owner: content** — same family as GAP-111, different failure
**Severity:** Medium (blocks `tfc deploy` with no `--profile` override; the write-path live-verify
run worked around it with a substitute profile, so the twin itself was never exercised through its
own default)
**Discovered:** 2026-09-30 — Unified Console write-path live-verify (Part B), preparing a disposable
`sme-flat` deploy.

**Symptom.** `twins/sme-flat/tfc/default.yaml` cannot be applied as-is: it assigns the Windows
catalog `corp-workstation` persona onto the twin's Debian hosts (GAP-111 — `tfc validate`/`tfc
deploy` correctly refuse it), **and separately** a `db-tier` role in the same profile `requires:`
a `web-tier` role the twin does not declare at all — a second, independent reason the default
profile cannot resolve, uncovered only once GAP-111's persona swap was worked around. The
live-verify run used a hand-built substitute profile (one persona, `N_WORKSTATIONS=1`) to reach the
write paths instead.

**Fix sketch.** Once GAP-111 is fixed for `sme-flat` (a Linux workstation persona or a Windows
image), audit `twins/sme-flat/tfc/default.yaml`'s `requires:` graph against the twin's actual role
set and either add the missing `web-tier` host/role or drop the `db-tier` → `web-tier` requirement.

**Related:** GAP-111 (the persona/OS mismatch this profile also has); `twins/sme-flat/tfc/default.yaml`.

## GAP-117 — A whole-profile `tfc deploy` restarts every unit even when a host's assignment is unchanged

**Status:** 🟡 Open · **Owner: axRange** — pre-existing behavior, not introduced by the Unified Console
**Severity:** Low (correct but wasteful; makes a whole-profile apply non-idempotent in the
operationally relevant sense — re-running it against no change still bounces every persona)
**Discovered:** 2026-09-30 — Unified Console write-path live-verify (Part B), observed while
proving the whole-profile revert guard.

**Symptom.** `POST /api/tfc` / `axrange tfc deploy` resets and re-drives every persona instance's
ops on a whole-profile apply, regardless of whether that host's assignment actually changed from
its last completed apply. The per-host persona apply added by this consolidation is scoped
correctly (only the target host's rows are touched — proven live, see `docs/CHANGELOG.md`
2026-09-30 "Unified Console live verification"); the pre-existing whole-profile path was not
in scope for that fix and still restarts everything.

**Fix sketch.** Diff the desired assignment against the tfc deploy's last completed rows per host
(the same comparison the per-host apply already does to compute start/stop/unchanged) and skip
`force_reset_ops` for a host whose resolved assignment is unchanged — turning a repeat
`tfc deploy` into a true no-op.

**Related:** `axrange/o2control/traffic.py`; `docs/design/16-unified-console.md` §5.

## GAP-118 — `docs/reference/ui-consolidation/07-live-verification.md` could not be written from the pipeline

**Status:** 🟢 Not a defect — recorded so the missing file is not mistaken for a missed step
**Severity:** None (informational)
**Discovered:** 2026-09-30 — doc-writer pass, attempting the file the build order (§6/§7 of
`00-design-ruling.md`) names for the live-verify record.

**Symptom.** The doc-writer stage's write of `docs/reference/ui-consolidation/07-live-verification.md`
was denied by the permission system mid-pipeline. The live-run facts it would have held are not
lost: they are recorded in `docs/CHANGELOG.md` 2026-09-30 "Unified Console live verification" and
`docs/archive/work_orders/WORK_ORDER_unified_console.built.md`, and the screenshots that would have
illustrated it are committed at `docs/reference/ui-consolidation/shots/`.

**Fix sketch.** None needed for the record (it already exists elsewhere). If a `07-*.md` write is
wanted for parity with `00`–`06`, it needs the user's explicit go-ahead for that specific file
before a future pipeline pass attempts it again.

**Related:** `docs/reference/ui-consolidation/00-design-ruling.md` §6–§7; `docs/CHANGELOG.md`
2026-09-30 "Unified Console live verification".

## GAP-119 — The unfragmented mirror path is a runtime-only host state, not an op; it does not survive a reboot or a gretap re-create

**Status:** 🔴 Open
**Severity:** Medium (the fix is proven and cheap to re-apply, but nothing re-applies it
automatically, and the twin that needs it is live on dev45 right now)
**Discovered:** 2026-10-01 — corp-twin capture-rate ceiling run
(`docs/reference/capture-rate/2026-10-01-corp-twin-ladder.md`, rung R1/R2).

**Symptom.** `twins/corp-twin/scripts/mirror-mtu-path.sh apply` gives only corp-twin's 24 gretaps a
1600-byte path to XPress-SW-A (`fwmark 0x5ca1` → policy-routing table 251, sensor transport leg
`tap104i1` raised to MTU 1600) so full-size mirrored frames stop fragmenting into two outer IP
packets — this took captured TCP out of wire order from 5–6 % down to 0 with no throughput cost.
None of it is persisted: a dev45 reboot, a VM-104 NIC replug, or a gretap re-create (`tap
destroy`/`tap deploy`, not a plain re-apply — that adopts the existing netdev and keeps the
changelink) all drop it silently, and nothing re-applies it or even reports it is gone.

**Recurrence (2026-10-05):** after the dev45 reboot the path was gone as predicted and had to be hand-re-applied on the redeployed twin; the 24 gretap names happened to match, so the script's hardcoded list held (its comment still names the old tap id).

**Root cause.** `docs/ARCHITECTURE.md` §3's ratified fix (bump the transport vnet's own MTU,
tracked as GAP-112) was never built; this script is a corp-twin-local, hand-run workaround on top
of GAP-112's `nopmtudisc ignore-df` floor, proven to work but outside any op the adapter or tap
tier owns.

**Fix sketch.** Fold the validated shape (fwmark + policy route + transport-leg MTU, or GAP-112's
broader "raise the SDN vnet MTU") into the tap op/adapter so `axrange tap deploy` asserts it on
every apply (create AND adopt-path changelink), the same way `nopmtudisc ignore-df` already is
(LES-356) — not a twin-local shell script an operator must remember to re-run.

**Related:** GAP-112 (the transport-MTU root cause this works around), `twins/corp-twin/scripts/
mirror-mtu-path.sh`, `axrange/adapter_proxmox/ssh.py` `_GRETAP_PMTU_OPTS`, `docs/LESSONS.md`
LES-356, LES-360.

## GAP-120 — A traffic persona's filler stream is reset by the real server after the first window; "success" on the wire is not success at the application layer

**Status:** 🔴 Open
**Severity:** Medium (the twin's protocol-health metrics read clean; the actual data transfer the
persona is supposed to perform does not happen past the first window)
**Discovered:** 2026-10-01 — corp-twin capture-rate ceiling run
(`docs/reference/capture-rate/2026-10-01-corp-twin-ladder.md`).

**Symptom.** caddy (the external sink) and smbd (the file server) both reset the `scripted`
driver's filler stream after the first window, so raising a persona's message size does nothing
for offered load — only more/faster connections do. The engine's own counters show
`corp-file-share-client` with **494,783 failed messages** recorded since 2026-09-28: run 4
(`docs/WORK_ORDER_corp_twin.md` §11) had confirmed SMB traffic was "real" at the connection/socket
layer (smbd owns 445 and drains the client's stream), but that is not the same as the intended
transfer completing. Separately, the `https` personas send tagged filler bytes, never a real TLS
ClientHello, so "TLS handshake completion" is unmeasurable by construction (always 0), not merely
unmeasured.

**Update (2026-10-05, redeploy):** `corp-file-share-client` is now 100 % failed with 0 bytes (4352/4352, smbd `ECONNRESET` from 10.180.64.21:445); heavy generic endpoints fail 44-48 % (caddy resets). Twin-wide 14.1 % of messages failed (34,935 of 248,510).

**Root cause.** The `scripted` persona driver (`axrange/personas/drivers/scripted.py`) emits
connectivity shape, by its own module docstring ("v1 fidelity note, deferred, not silently
omitted") — the real fix path, a `drive: command` driver backed by real client tools, is already
built (`docs/WORK_ORDER_protocol_faithful_traffic.md`, CHANGELOG 2026-09-02) but no shipped
persona uses it yet, and it has never been applied to corp-twin.

**Fix sketch.** Move corp-twin's file-share and TLS-bearing personas onto `drive: command` (the
work order's §5.1 aximprint companion — the sink becoming real services — is the stated
precondition); judge success from the engine's own failed-message/application-layer counters, not
from wire-level shape (`docs/LESSONS.md` LES-347).

**Related:** `docs/WORK_ORDER_protocol_faithful_traffic.md`, GAP-056 (no shipped persona uses
`drive: command` yet), `docs/LESSONS.md` LES-347 (judge success at the artifact, not the exit
code/wire shape).

## GAP-121 — corp-twin's single external sink (2 vCPU) caps the twin's offered traffic at ~30 kpps

**Status:** 🔴 Open
**Severity:** Low (a capacity ceiling, not a correctness bug — measured, not inferred)
**Discovered:** 2026-10-01 — corp-twin capture-rate ceiling run
(`docs/reference/capture-rate/2026-10-01-corp-twin-ladder.md`, rungs R6/R7/F1).

**Symptom.** Scaling corp-twin's mix past ×6 of run 4's offered rate (29,945 pps / 244.2 Mbit/s)
made the single external sink (2 vCPU caddy, load average ~5 at ×8) start retransmitting on
client ↔ sink:443 — ×8 failed 1 of 3 correctness samples, ×12 and ×16 were worse. This is the
twin's own ceiling for this mix: the capture/mirror chain itself held clean to 218 kpps / 1.96
Gbit/s with probe traffic over the same path, 7× past where the sink gives out.

**Root cause.** corp-twin's cast was rewritten (`docs/WORK_ORDER_corp_twin.md` §12 D3) from
aximprint's four-sink shape (`internet-sink-{1,2,4,8}`) down to one `sink` role, a twin-local
persona edit; the compiler upstream still emits four sinks.

**Fix sketch.** Either give the sink more vCPUs, or restore the cast's four-sink shape (D3) so
load spreads across multiple backends — either raises the ceiling without touching capture or the
mirror path, which already have ~7× headroom.

**Related:** `docs/WORK_ORDER_corp_twin.md` §12 D3 (now has a measured reason), GAP-122 (the
capture chain's own, much higher ceiling).

## GAP-122 — pxswcapture runs a single AF_XDP queue; capture itself first loses packets at ~305 kpps

**Status:** 🟡 Open — informational ceiling, not yet hit by any real mix
**Severity:** Low (7× above anything corp-twin's current sink can offer; matters only if GAP-121
is fixed or a different twin drives more traffic at this sensor)
**Discovered:** 2026-10-01 — corp-twin capture-rate ceiling run, probe traffic P1–P3
(`docs/reference/capture-rate/2026-10-01-corp-twin-ladder.md`, "Capture chain itself").

**Symptom.** Probe traffic (not a corp-twin mix candidate) held the capture chain clean at 218
kpps / 1.96 Gbit/s for 10 minutes (131 M packets, 0 drops); at ~305 kpps / 2.7 Gbit/s pxswcapture
started dropping (`queue`, 7,141 in one 10 s interval), then `tap104i1` tx_drop (40). Sensor facts
read at baseline: `rx_queues_bound 1` — pxswcapture runs `afxdp-copy` generic-XDP on `gre-cap`
through a single XSK socket/queue, and that queue (`afxdp_q0`, pinned to one core at 100 % busy-poll
at every load level, not a saturation signal by itself) is what saturates first.

**Root cause.** The sensor's capture appliance (`o2/tcpdump-sensor/`, pxswcapture) binds one AF_XDP
queue regardless of the NIC's available combined queues (`ens19` has 8).

**Fix sketch.** Multi-queue AF_XDP (bind one XSK socket per combined queue, RSS-hash the incoming
GRE traffic across them) would move this ceiling roughly linearly with queue count; out of scope
for any twin currently deployed (corp-twin's own sink ceiling, GAP-121, is ~10× below this one).

**Related:** GAP-121, `o2/tcpdump-sensor/README.md` (sensor facts and the GRO fix, written by the
2026-10-01 run).

## GAP-123 — svc-engines' two Windows VMs powered themselves off on 2026-10-01; cause unknown

**Status:** 🔴 Open — needs triage
**Severity:** Medium (two VMs on a standing, otherwise-live twin went dark with no PVE task and no
OOM line — an unexplained guest-initiated shutdown is itself a signal worth chasing)
**Discovered:** 2026-10-01 — noticed during the corp-twin capture-rate ceiling run; **not caused by
it** (the run's writes were limited to corp-twin's own 24 gretaps, `tap104i1`, one policy-routing
rule/table, and corp-twin's traffic profile — nothing of svc-engines' was touched, per the run's
own hard limit, `docs/reference/capture-rate/2026-10-01-corp-twin-ladder.md`).

**Symptom.** `864812` (`te-cbd9a1-dc`) powered off at 07:45:31 UTC and `506017`
(`te-cbd9a1-win-member`) at 07:57:04 UTC — both guest-initiated (no PVE task recorded, no OOM
line; the systemd `.scope` deactivated normally and `qmeventd` ran its ordinary cleanup). Left
stopped, since the authorization's hard limit forbade touching svc-engines. VM 102 `o2-services`
is also stopped, as of an unknown time, unrelated to this discovery.

**Root cause.** Unknown — not investigated (out of this run's scope). svc-engines is already a
known-unhealed standing twin (GAP-110's stale sink recipe, GAP-112's unfixed gretap loss on
`tapg_84e687`/`tapg_d1b207`), so a guest-side fault on it is plausible but unconfirmed.

**Fix sketch.** Triage from inside the guests (Windows Event Log: System, for the shutdown reason
and initiator) before any restart; if it recurs, treat it alongside the existing svc-engines heal
item (`sh twins/svc-engines/scripts/build-sink-payload.sh` then `tap deploy` + provision-sink
re-apply, GAP-110/GAP-112) rather than as a one-off.

**Related:** GAP-110, GAP-112, `docs/SESSION_STATE.md` (carried as an item for the user).

## GAP-124 — Twins carry no IPv6: the cast drops it at the segmenter, and the engine has no dual-stack segment

**Status:** 🔴 Open — work order opened (`docs/WORK_ORDER_dual_stack_segments.md`, Authority:
user, 2026-10-06)
**Severity:** Medium (invisible to mix/capture-rate work at 0.1 % of bytes, but any defense keyed on
dual-stack behaviour — ICMPv6/NDP/RA, v6 DNS, QUIC over v6 — sees none of it)
**Discovered:** 2026-10-06 — the user asked why corp-twin carries no IPv6 when its source captures
carry plenty; measured live and in the source bundle the same day.

**Symptom.** On the standing `corp-twin-3dae3188` (dev45), 20 s of mirrored frames on three gretaps
(vlan20, sfp25 trunk, wan1; ~248,000 frames) held exactly **one** IPv6 frame — an ICMPv6 router
solicitation (`fe80::… > ff02::2`) on the trunk. IPv6 is effectively absent on the wire.

**Present in the source.** The cast bundle for the same as-of
(`twins/pit-395a61f04e-1780405200/generated/…topology.json`): 218 of 2,641 nodes carry an IPv6
address and 291 of 4,157 edges touch one (1.9 % of flows, 0.1 % of bytes; other 187, icmp 56,
quic 31, dns 12, smb 2, rdp/rpc/krb 1 each). By class: 55 link-local inside hosts (RS, DHCPv6
solicit, mDNS, LLMNR — IPv6 stacks up across the network), **13 on the corp's own global
`2603:300b:64a:ba00::/64`**, 27 on a remote `2001:489a::/32` (Microsoft `.us` cloud endpoints, a
destination), 117 other internet peers, 6 multicast. Two of the 13 in-house globals tie by DNS name
to the domain controllers on `10.80.20.0/24` (corp-twin's `mx-vlan20`), so the real network
advertises one /64, on VLAN20; MAC correlation finds nothing (nodes carry no MAC).

**Root cause — lost at the cast's segmenter.** None of the bundle's 39 segments has an IPv6 cidr and
every IPv6 node has `segment: null`: aximprint infers IPv4 broadcast domains only (and tags the
corp's own v6 hosts `external`), and the cast builds the twin from placed nodes only — so the IPv6
side of every dual-stack host is an unplaced node. Upstream dependency:
`docs/reference/GAP_ANALYSIS_aximprint_corp_discovery.md` G-03 (IPv6 RA prefixes as unused segment
evidence); `docs/JOURNAL_axquery_discovery_loop.md` treated the "external IPv6 /64s" as noise.

**Faithfully reproduced downstream.** `twins/corp-twin/te.yaml` segments are IPv4-only; `hq-mx` has
no v6 legs, RA or DHCPv6 (`configure-router` enables `net.ipv4.ip_forward` only); the expander and
planner allocate from one `cidr` per segment; `ipconfigN` carries no `ip6=`; persona emits name no
address family, the tfc driver resolves peers to IPv4 (`_host_ip`), and the sink serves IPv4 only.
The IR and validator already *accept* an IPv6 `cidr` (`IR_SCHEMA` §3.1, NET-020) but no network can
hold both families. Nothing in CHANGELOG/OPEN_QUESTIONS/LESSONS ever ruled IPv6 out — it slipped
through. Unchecked: whether the Windows templates have IPv6 disabled (Windows was silent where it is
normally chatty). The egress engine's rule tables are `table ip` (IPv4-only); a router WAN NIC
holding an RA-learned v6 address would leak its own v6 uncounted in `measure`. **Verified absent
2026-10-06** on hq-mx (vmid 305605): no global v6, no v6 default route, `all.forwarding=0` — dev45's
vmbr0 advertises no prefix. That is the uplink's doing, not the engine's, so the dual-stack build
adds an `ip6` table before any segment gets a prefix, and the check repeats whenever the uplink
changes (WO §4, S0a).

**Fix sketch — two pieces.** (1) **Dual-stack segments** — an optional `cidr6` per segment, router
RA, host addressing, v6 containment proven as a gate: `docs/WORK_ORDER_dual_stack_segments.md`.
(2) **IPv6 persona emits** (v6 peers, a v6-serving sink, AAAA content) — a **separate future work
order**, not yet opened. Upstream: aximprint G-03 + v6 locality.

**Related:** `docs/WORK_ORDER_corp_twin.md` (the first adopter), GAP-120 (the other open
corp-twin traffic-fidelity gap).
