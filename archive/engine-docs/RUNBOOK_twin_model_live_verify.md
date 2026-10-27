# Runbook — staged twin-model live-verify (dev45)

> **Purpose:** clear the five accumulated **gated** `dev45` live-verifies for the twin-model arc
> (`tap_points`, `persona_library`, `traffic_tier`, `twin_configs`, `exercise_tier`) as **one batched
> session, bottom-up on a standing surface**, reusing state between stages. Modeled on
> `docs/RUNBOOK_o2_live_verify.md`.
>
> **Every** `axrange o2 deploy|destroy`, `te deploy|destroy`, `tap deploy|destroy`, `tfc deploy|destroy`,
> `exr run|destroy`, `image build`, `docker compose`, and any `state.db` re-init is a **GATE** — the
> live-verifier stops and asks before each. Reads / `validate` / `plan` / `status` / `info` run unattended.
>
> **Status: Stages 1–5 PASSED on dev45 (2026-07-25, CHANGELOG "Twin-model arc: dev45 live-verify
> Stages 1–4 PASSED" + "…Stage 5 (`exercise_tier` capstone) PASSED").** Stage 6 (`sme-flat`
> twin-config) remains open for a follow-on session. Seven code fixes were surfaced and driven by
> Stages 1–4 — see the inline notes on each stage below, and the CHANGELOG entry for full detail.
> Stage 5 drove no code fixes (content + cluster ops only) but surfaced a significant twin-config
> limitation — see the Stage-5 note below and `docs/PRODUCT_GAPS.md` GAP-040/GAP-041. Six commits
> landed (`105d0de` … `70fd847`) before this run; code arc complete.

## Standing state to reuse (already on dev45)

- **O2 server stack** — VM 102 `o2-services`: Loki (`LOKI_URL=http://10.80.72.37:3100`), Grafana,
  Prometheus, Caldera, Wazuh-manager (2026-07-15 live-verify PASSED). **Confirm still up in Stage 0.**
- **2-sensor surface spec** — `deploy-dev45/surfaces/two-sensor.yaml` (A2 fan-out needs ≥2).
- **External sensor** — `XPress-SW` (vmid 103, `10.255.251.20`) via `adapter.conf` `EXTERNAL_SENSORS`.
- **Images: ALL PREBUILT on the cluster** (user, 2026-07-24) — the templates for `ubuntu-server-22`,
  `windows-server-2022`, `win11-finance`, `vyos` already exist on dev45. **No `image build` step.**
  Manifests + Proxmox bindings exist in `tests/fixtures/images/`; the only prep is ensuring the images
  root the deploy uses carries the correct per-cluster **`template_vmid`** for each (GAP-031 collapses
  to a binding-wiring confirm, not a build).

## Path: FULL CAPSTONE (user, 2026-07-24) — no builds

Run all six stages including `corp-enterprise` (Windows DC + `win11-finance` clients), the real **AD
kill chain**, and both `twin_configs`. GAP-031 is resolved by confirming the image bindings resolve to
the prebuilt templates (Stage 0), **not** by building anything.

---

## Stage 0 — Preflight (no gate)

- [ ] Full suite green (baseline **1423 passed / 12 skipped / 0 xfailed**), `doc-stats --check` clean.
- [ ] `adapter.conf` targets dev45 (`PROXMOX_API_URL=…108`, `DEFAULT_NODE=dev45`), `LOKI_URL` set.
- [ ] O2 server stack reachable (Loki `/ready`, Grafana, Caldera, Wazuh) — the A3/A4 backends.
- [ ] `state.db`: `tfc`/`exr` are free-form `deploys.tier` values (no `SCHEMA_VERSION` bump this arc) —
      **no re-init expected**; confirm it opens without `StateStoreSchemaError`.
- [ ] **Image bindings (GAP-031 collapse):** confirm the images root used for the deploy carries
      bindings with the correct dev45 `template_vmid` for every image `corp-enterprise` references
      (`debian-12-cloud`, `ubuntu-server-22`, `windows-server-2022`, `win11-finance`, and `vyos` if the
      router is VyOS). Images are prebuilt — this is a binding-wiring check; if a `template_vmid` is
      missing, ask the user for it. **No `image build`.**
- [ ] `axrange te validate --config corp-enterprise --images <dev45-root>` — expect PASS (REF-120 here
      means a binding still lacks its `template_vmid`, resolved above — not a missing image).

## Stage 1 — Lab up (`o2`) — GATE — **PASSED 2026-07-25**

- [x] **[GATE] `axrange o2 deploy`** the **2-sensor** surface (`deploy-dev45/surfaces/two-sensor.yaml`)
      — 24/24 ops.
- [x] `axrange o2 status` — 2 `o2_`-tagged sensors standing (`o2-tcpdump`/`o2-suricata`), each with a
      transport IP in inventory.
- [x] Blast-radius check: throwaway deploy/destroy churn during the run left the `o2_` surface +
      `img-*` untouched.
- [x] **Left UP** for Stages 2–4. The standing-sensor capture-supervision fix (LES-085/LES-086) held
      up live under real capture across the whole run.
- **Pass =** 2 sensors stand, transport IPs published, unrelated te destroy left them intact. **PASS.**

## Stage 2 — Base twin (`te`) — GATE — **PASSED 2026-07-25**

- [x] **[GATE] `axrange te deploy --config corp-enterprise --images <dev45-root>`** — the capstone twin
      (Windows DC + `win11-finance` clients + router), 401/401 after 3 resumes (two transient
      first-boot apt-churn failures + fix #1, the dataset-guard raise 100 MiB → 2 GiB).
- [x] `axrange te inventory <deploy> --json` — all 32 `hosts[]` carry **`role`** and `tap_points[]` are
      present. Confirms the `role:` field + twin-config expander + inventory, end-to-end, for the
      first time on real content.
- [x] Left UP for Stages 3–4.
- **Pass =** twin stands, inventory publishes roles + tap points. **PASS.** **Content finding (not a
  fix): `corp-workstation01..25` NetBIOS-truncate into ONE AD computer object — GAP-036, not a code bug.**

## Stage 3 — `tap_points` — GATE — **PASSED 2026-07-25**

- [x] **[GATE] `axrange tap deploy`** a plan binding a **`segment`** tap point → `sensor-a` and a
      **`boundary`** tap point → `sensor-b` — the first real boundary tap point on a real twin.
- [x] `verify-capture` / inspect each sensor's pcap: `sensor-a` has the segment's traffic, `sensor-b`
      the boundary's — and **not** each other's. Heartbeats confirmed from **both** a Linux AND a
      Windows injector (fix #3 — the Windows PowerShell `Ping.Send` sentinel).
- [x] **Re-shard:** moved the segment tap point to a third sensor; capture followed **in place**
      without redeploying the twin. **This re-shard is what surfaced fix #7** (the gretap de-scope
      over-deletion — LES-118) — a live capture-plane data-loss bug, root-caused and fixed in this
      session, not left standing.
- [x] `tap destroy` touched only `tap_` (twin + `o2_` intact).
- **Pass =** each place's traffic lands at its bound sensor; re-shard re-points without a twin
  redeploy. **PASS** (after fix #7 landed — the re-shard initially exposed the bug, then re-verified
  clean post-fix). Fix #2 (boundary sentinel off the router, onto a leaf) also drove out of this stage.
  *(Closes `tap_points`. The "background traffic kept running through the re-shard" half confirmed in
  Stage 4.)*

## Stage 4 — `traffic_tier` (+ `persona_library` drivers) — GATE — **PASSED 2026-07-25**

- [x] **[GATE] `axrange tfc deploy`** a busy-corp profile binding personas onto the twin's roles —
      28/28. **Replay** leg still deferred (GAP-033 — no curated pcap ships); scripted + real-service
      personas ran out of the box (after fix #5, the Windows persona drivers).
- [x] On a twin host: Windows persona **Scheduled Task** units (fix #5,
      `-ExecutionTimeLimit 0` — LES-120) survived the launching QGA session, same guarantee as the
      Linux `systemd-run --unit=` transients.
- [x] At a Stage-3 tap: real busy-corp SMB sessions from Windows workstations to the DC/file-server
      captured on the wire.
- [x] **Independence:** confirmed a `tap` re-apply (re-shard, Stage 3) did not disturb the running
      `tfc` traffic (the §3 orthogonality claim) — verified in both directions once fix #7 landed.
- [x] `tfc destroy` touched only `tfc_`.
- **Pass =** persona units survive the QGA session + emit the expected protocols; tap re-shard leaves
  traffic undisturbed. **PASS.** *(Closes `traffic_tier` + `persona_library` (drivers, both Linux and
  Windows, scripted + real-service), and Stage 3's deferred half.)* Fixes #4 (persona optional
  emit-peers) and #6 (schedule override wiring) also drove out of this stage.

## Stage 5 — `exercise_tier` capstone — GATE — **PASSED 2026-07-25**

> Stages 1–4 left the Lab/twin/`tap`/`tfc` state standing as of 2026-07-25; Stage 5 ran against that
> standing state on `corp-enterprise-f525eed4` rather than restanding anything.

- [x] **[GATE] `axrange exr run`** the **AD kill chain** against the instrumented twin (recon → LLMNR →
      SMB/LDAP enum → AS-REP → Kerberoast → DCSync). Ran against the **live `tfc` background**.
      Attacker actions ran from the twin's existing `egress-proxy` host — no dedicated attacker node
      was deployed, because editing the twin config to add one would trigger a whole new deploy rather
      than extending the standing one (GAP-040); impacket + the vulnerable AD accounts (`svc-sql`,
      `jsmith`) were hand-provisioned onto `egress-proxy`/the DC over QGA as a one-time workaround.
- [x] Attack succeeded through **DCSync (T1003.006): real `krbtgt` hash + AES keys dumped over
      DRSUAPI**, plus AS-REP roast (`T1558.004`) and Kerberoast (`T1558.003`) along the way. Truth
      markers (`[axr-truth] run=… technique=… host=egress-proxy ts=…`) confirmed landed in Loki,
      joinable by technique+host+window. **LLMNR poisoning stayed attempt-only** — no real hash
      captured, for lack of a script-staging primitive for a persistent listener (GAP-041); detections
      (Suricata/Wazuh) join was not separately re-confirmed this stage (truth-marker half PASSED, the
      detection-join half rides the already-live-verified A4 pipeline from earlier work).
- [x] **`exr destroy`** left twin + `tfc` + `tap` **up** — exr went `destroyed`, the other three tiers
      stayed `completed` (ephemeral, cascaded nothing but its own).
- [x] **Replay:** re-ran the same spec+seed → **identical `playout_hash 2be5d3ae0008bec7`.** The
      defense-tuning-then-delta half was **not** exercised this stage (no tuning was applied between
      the run and its replay) — replay-identity is proven, the measurable-delta claim is not yet.
- [x] Same-host overlap guard: a second concurrent `exr run` on the same host was **refused**
      ("destroy the prior run first").
- **Pass =** attack measured against realistic background; replay identical. **PASS** (AD-specific
  done-criteria: recon/AS-REP/Kerberoast/DCSync all succeeded and were truth-marker-measured; the
  post-tuning delta and real LLMNR-hash-capture done-criteria remain open — see GAP-040/GAP-041).
  *(Closes `exercise_tier`'s gated live-verify.)*

## Stage 6 — `twin_configs` (`sme-flat` remainder) — GATE — **OPEN (not yet run)**

- [ ] `corp-enterprise` is already deploy-verified as the Stage-2 substrate (2026-07-25, 401/401,
      inventory publishing roles + tap points). Cover the second config:
      **[GATE] `te deploy --config sme-flat --images <dev45-root>`** (`debian-12-cloud` + `ubuntu-server-22`).
- [ ] It publishes its inventory (hosts + tap points); a `tap` binds to it; `te destroy` clean.
- **Pass =** both configs (`corp-enterprise` in Stage 2, `sme-flat` here) expand → deploy → publish
  inventory → bind → destroy clean.

## Teardown — GATE

- [ ] **[GATE] `axrange te destroy`** the twin — cascades its `tfc` + `tap` (+ any `exr`); confirm the
      `o2_` surface + `img-*` templates + unrelated VMs are **untouched** (the tier guard).
- [ ] **[GATE] `axrange o2 destroy`** last (only if the standing surface should come down).
- [ ] `list-deploys` clean; idempotent re-destroy is a clean no-op.

## Gates at a glance

**Stop and ask before each:** `image build` · `state.db` re-init · `o2 deploy|destroy` · `te deploy|destroy`
· `tap deploy|destroy` · `tfc deploy|destroy` · `exr run|destroy` · `docker compose`.
**Unattended:** every `validate` / `plan` / `status` / `info` / `inventory` / pcap read.

## After the run (doc-writer)

Flip each `WORK_ORDER_*` live-verify PENDING → PASSED (or note what deferred + why), update
`docs/design/02-twin-model.md` if the run changed current-state facts, resolve/annotate GAP-031/033
as exercised, add a CHANGELOG "twin-model arc live-verified on dev45" entry, and record any LES the
run earns.

**Done for Stages 1–4 (2026-07-25):** `WORK_ORDER_{tap_points,persona_library,traffic_tier}.md`
flipped PENDING → PASSED; `WORK_ORDER_twin_configs.md` flipped to partial (`corp-enterprise`
PASSED, `sme-flat` still open pending Stage 6); the twin-model design doc's built-status updated;
GAP-031 resolved (dev45-specific, with a substitution caveat), GAP-032 resolved; GAP-036..039 added
(NetBIOS truncation, Windows clock skew, skip-warning dedup, scripted-persona protocol-fidelity note);
CHANGELOG "Twin-model arc: dev45 live-verify Stages 1–4 PASSED" entry added; LES-118..125 recorded.

**Done for Stage 5 (2026-07-25):** `WORK_ORDER_exercise_tier.md` flipped PENDING → PASSED (AD kill
chain incl. real DCSync `krbtgt` dump, truth markers in Loki, clean ephemeral `exr destroy`, identical
replay `playout_hash`; egress-proxy/incremental-add caveat noted); the twin-model design doc's
built-status extended to Stage 5 PASS (only Stage 6 remains); GAP-040 (twin-config incremental-add)
and GAP-041 (no script-staging primitive for real hash-capture tools) added; CHANGELOG "Twin-model
arc: dev45 live-verify Stage 5 (`exercise_tier` capstone) PASSED" entry added; LES-126/LES-127
recorded. Stage 5 drove no code fixes and no counter change (1529/12/0, 1541 collected, unchanged).
**Still open for the next session:** Stage 6 (`sme-flat`), plus the final Teardown gate (deliberately
left standing at the end of this session for Stage 6 to reuse).
