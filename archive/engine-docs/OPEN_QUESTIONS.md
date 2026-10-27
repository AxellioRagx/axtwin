# Open Design Questions

Topics whose design isn't yet locked into the formal specs (`docs/ARCHITECTURE.md` / `docs/PLANNER.md` / `docs/VALIDATOR.md` / etc.). Each entry has a **Status** line indicating where it stands. As of 2026-06-11 the project-wide scope-boundary gates have been removed (CHANGELOG 2026-06-11) — these topics are no longer doc-flagged as "do not implement against." The user routes work between Claude (chat) and Claude Code on a per-topic basis; pick up whichever is useful when a thread needs it.

Order is rough priority (top = most likely to need attention soonest).

---

## Q1. Canvas UX and serialization

**Status:** 🟢 **SUPERSEDED 2026-09-29.** The free-hand layered canvas this question was about was
never built (`docs/reference/ui-consolidation/03-ui-history-and-decisions.md` §1 row 0,
§3 "Layered free-hand IR canvas"). The operator UI that was built instead is the axRange Console
(`docs/design/16-unified-console.md`): a design-time Design area (the assertions editor of
`docs/design/15-console-canvas.md`, over an aximprint cast, not a free-hand drag-and-drop canvas)
plus run-time Range/Twin/Catalog areas. The layer/overlay model stays an IR concept
(`docs/IR_SCHEMA.md`), not a UI commitment. See `docs/CHANGELOG.md` 2026-09-29. Original text below
kept for the historical record.

**Status (as ratified, pre-2026-06-11):** 🟡 **OPEN — user-directed (not in active build as of 2026-06-11).** v1 ships hand-written IR + the CLI deploy/capture thread; the canvas remains designed-conceptually. The IR parser the canvas would emit into already exists (`axrange/ir/`). As of CHANGELOG 2026-06-11, the project-wide scope gates are removed: canvas work can pick up whenever a thread needs it. See historical CHANGELOG entry "v1 scope: canvas (Q1) and AI core (Q8) declared out of v1" for the original 2026-05-28 decision; that gate is now retired but the design notes below remain a useful starting point.

**What we agreed (still valid for the future iteration):**
- Three primary layers + optional Identity/Data/Defense overlays.
- Canvas is "just an editor"; its only output is the Scenario IR.
- Every property visible in the canvas maps to an IR field — no hidden canvas state.

**Still open:**
- What's the rendering tech? (HTML+SVG, React Flow, something else? Web or desktop?)
- Per-layer visibility model — show/hide layers independently, or always show all three with overlays toggled?
- Snap-to-grid? Auto-layout? Manual only?
- How are layer-to-layer bindings displayed visually (Layer 2 app "on" Layer 1 host)?
- Versioning UX — does the canvas show a per-element history? Or is version control purely git-mediated?
- How does the user toggle overlays? Side panel? Layer drawer?
- Export format — is the canvas state the IR directly, or is there an intermediate visual-layout file separate from the IR?

**Why this needs more discussion:** the canvas is the highest-touch user surface. Getting it wrong costs the most rework. We have not even decided whether v1 has a canvas at all or starts with hand-written IR + a viewer.

---

## Q2. Scenario IR schema — concrete shape

**Status:** ✅ **RESOLVED 2026-05-21.** See CHANGELOG entries "IR concrete schema: structural shape and section field model", "IR capability taxonomy v1: closed 16-keyword set", and "Image manifest + binding: two-tier model". Formal specs in `docs/IR_SCHEMA.md` and `docs/IMAGE_MANIFEST.md`.

**Resolved points (kept here as a quick index; full detail in the formal specs):**
- File layout: single canonical `scenario.yaml` with strict-no-override `includes:`.
- Top-level sections: `scenario:` header, `layers:` (infra/apps/activity), `bindings:`, `overlays:` (identity/data/defense, optional).
- Bindings: explicit top-level block (not `on:` inline shorthand). Typed dicts: `app→host`, `activity→host`, `activity→targets`, overlay bindings.
- Identifiers: human-meaningful strings; validator catches dangling refs; optional `aliases:` for rename continuity.
- Versioning: two distinct fields — `scenario_schema_version` (the IR spec) and `scenario.version` (per-scenario monotonic).
- Capability taxonomy: closed 16-keyword set (3 OS-family + 7 `host:*` + 6 `kernel:*`). No implicit inheritance. CHANGELOG-gated additions.
- Image model: two-tier — platform-independent manifest + per-platform binding. Images declare `provides:` from the same keyword set. Validator checks `provides:` ⊇ `requires:`; planner unions IR-node `host:*` requirements with binding `requires_host_caps:`.
- `install:` action kinds: closed set of six (`pkg`, `pkg@version`, `config`, `role`, `container`, `script`). First-boot only.
- `reproducible: true` scenario flag promotes unpinned image/behavior refs and missing `seed:` from warnings to errors.
- Activity DSL: three forms — library reference, library + `overrides:`, or pure `inline:`.
- Defense overlay binding: `observes:` (network mode) or `installed_on:` (host-agent mode), or both.
- Seeds and determinism: single top-level `seed:`; per-actor seeds derive deterministically.
- Routers and switches are `nodes:` with `kind: router` / `kind: switch`; no separate top-level list.

**Open follow-ups deferred from this resolution** (CHANGELOG-able additions when needed):
- `requires-preset:` bundles for common groupings (e.g. `modern-windows` → `[windows, host:uefi, host:secure-boot, host:tpm]`).
- Capability negation (`!host:tpm`) — for "must NOT have" requirements in adversary scenarios.
- Confidential-compute keywords (`host:sev`, `host:tdx`, `host:sgx`).
- `behavior-agent:*` capability prefix or separate `installed_agents:` field for declaring pre-installed activity-simulator agents on images.
- Phases as first-class concept (bindings already gestured at `only_in_phase:`).
- ~~Deploy-time parameters (`${N_FIN_WORKSTATIONS:5}`).~~ ✅ **Addressed 2026-07-24** by
  `twin_configs` (`docs/WORK_ORDER_twin_configs.md`, CHANGELOG 2026-07-24 "Twin configs BUILT") —
  `axrange/twinconfig/params.py`'s `resolve_params`/`substitute` implement `${NAME:default}`
  exactly as sketched here, resolved entirely **before** a twin config's dict becomes a Scenario
  IR (`axrange/twinconfig/expand.py`), so this stays a template-authoring convenience, not an
  `IR_SCHEMA` change — the Scenario IR itself still carries no template syntax and needed no
  version bump.
- Manifest signing / supply-chain integrity.
- Image deltas / patching across versions (current model is fully self-described per version).

---

## Q3. Validator: full check list

**Status:** ✅ **RESOLVED 2026-05-26.** See CHANGELOG entry "Validator: rule catalog v1". Formal spec in `docs/VALIDATOR.md`.

**Resolved points (summary index; full detail in the formal spec):**
- **91 IR-side rules** across 10 categories (STR 20, INC 5, REF 21, CAP 6, LIF 6, NET 11, OVR 4, ACT 5, DEF 5, FAB 8) + **7 cluster rules** (CLU) executed planner-side = **98 total**. (Count as of the 2026-06-29 fabric slice, which added the FAB-010..080 category; the per-category tables in `docs/VALIDATOR.md` §5 are authoritative. See CHANGELOG "Ratify doc corrections" for the original 2026-05-28 correction from "53".)
- **Rule schema (13 fields):** `id`, `category`, `severity`, `reproducible_promotes_to_error`, `pass`, `title`, `description`, `rationale`, `input`, `message_template`, `fixable`, `examples` (`fails:`/`passes:`), `related`.
- **Numbering:** `<CAT>-<NNN>` in tens with gaps. Stable across versions: never renumber, never reuse retired IDs.
- **Severity ladder:** `error` (blocks deploy) / `warning` (proceeds but flags) / `info` (observational, suppressed in CI). Per-rule `reproducible_promotes_to_error: bool` promotes severity by one step when scenario has `reproducible: true`.
- **Four-pass execution:** Pass 1 (structural + include) halt-on-error → Pass 2 (reference) halt-on-error → Pass 3 (capability, lifecycle, network, overlay, activity, defense) accumulate → Pass 4 (cluster) planner-side only.
- **Code structure:** shared library with four entry points (`validate_structural`, `validate_full`, `validate_with_cluster`, `explain_rule`). No dependency on planner internals; planner depends on validator. Same rule code path runs in CLI, canvas, CI, and deploy.
- **Two-stage handoff:** `axrange validate` runs Passes 1–3 (no cluster access needed); `axrange deploy` runs Passes 1–4 via the planner. DAG-shape errors (cycle, reachability, type consistency among ops) stay in the planner per `docs/PLANNER.md` §3.5.
- **Forward-compatible placeholders:** `REF-190` (phases) and `OVR-040` (data classification) are dormant in v1; activate when their respective IR features ratify.
- **Operator UX:** errors first, warnings second, info last; each finding shows rule ID + templated message + see-also/fix links. Exit code 0 for PASS or PASS-WITH-WARNINGS, non-zero for FAIL. `axrange validate --explain <id>` prints the full rule definition.

**Open follow-ups deferred from this resolution** (CHANGELOG-able additions when needed):
- Rule-by-rule severity re-tuning based on real operator experience — re-tuning is CHANGELOG-able and does not require new rule IDs.
- Severity escalation in CI mode (`axrange validate --strict` treating warnings as errors).
- Rule-disable mechanism (`axrange validate --disable-rule CAP-040`) for advisory rules that prove noisy.
- Validator caching (re-validate unchanged IR quickly) — implementation optimization, not spec.
- Localization of error messages — English-only in v1.
- Cross-scenario static analysis beyond `NET-060` (e.g. shared overlays, shared identity domains).

---

## Q4. Planner DAG: exact operation primitives

**Status:** ✅ **RESOLVED 2026-05-21.** See CHANGELOG entry "Planner: DAG construction + runtime + concurrency + re-planning". Formal spec in `docs/PLANNER.md`.

**Resolved points (summary index; full detail in the formal spec):**
- **Op model:** fine granularity (one op per externally-observable side effect); three kinds (`action`, `wait`, `barrier`); typed inputs/outputs; explicit `after:` annotations for non-data-flow ordering; synthetic `*-ready` barriers for cross-entity dependencies.
- **DAG construction:** three-pass deterministic build (emit + barriers, data-flow edges, ordering edges). Same IR + manifests + cluster snapshot → same DAG, hashable.
- **Placement:** union of IR `host:*` requirements + binding `requires_host_caps:`; least-loaded heuristic with alphabetic tiebreak; recorded in state store for stable re-deploys.
- **Idempotency:** two-layer model. `infra` ops use Model C (deterministic naming + check-then-act + cleanup-on-partial); `config` ops use Ansible-native idempotency.
- **`cleanup_strategy: redo | escalate`** on op definitions.
- **Failure semantics:** 3 attempts, exponential backoff 5s→15s→45s + jitter. Auto-escalate once, halt subtree on second escalate-failure. Halt scope = failing subtree + transitive dependents; independent subtrees continue. All operator-overridable via CLI.
- **Runtime:** ephemeral process per deploy. `--detach` for long deploys; state store is authoritative; lease/lock per scenario prevents concurrent invocations.
- **State store:** SQLite file on orchestrator. Tables: `deploys`, `leases`, `ops`, `op_attempts`. Atomic op-completion + output-persistence.
- **Concurrency:** semaphore-per-resource (`proxmox-cluster: 16`, `proxmox-node:<name>: 4`, `ovs:<name>: 1`, `vm:<vmid>: 1`, `ansible-pool: 8`, global `max-ops-in-flight: 32`). Operator-tunable.
- **Re-planning:** plan-diff against prior state store record. Per-entity change classification (Unchanged/Mutated/Added/Removed) with per-field redo rules. Default: show diff, require confirmation. Removals gated behind `--allow-destroy`.

**Sibling deliverables deferred to future sessions** (CHANGELOG-able when written):
- **Op catalog** — ✅ **RESOLVED 2026-05-27**, extended through 2026-06-29 (`create-subnet`, `configure-router`, `verify-capture`, `deploy-sensor`, `configure-routing` + `fabric-ready`). Closed set of **37 op types (31 action/wait + 6 barriers)** with full signatures. Spec in `docs/OP_CATALOG.md`; see CHANGELOG entry "Op catalog v1" and the later extension entries.
- **Redo rules table** — now a *derived view*: each op in `docs/OP_CATALOG.md` carries a `redo_on:` list of IR field paths, so the re-planner's redo table is generated by grouping ops by field path rather than authored separately. A standalone `docs/REDO_RULES.md` summary can be generated when convenient; the source of truth is the catalog.
- **Plan-diff confirmation UX details** — display format, interaction model, what's actionable on the diff.
- **Concurrency-limit empirical defaults** — current values are guesses. Real numbers come from operational experience; revisable via CHANGELOG. (Now includes `gretap:<sensor>: 1`.) **Cross-process scope clarified (2026-06-03):** §8's semaphores are in-process; the "correctness" categories (`ovs:<node>`, `vm:<vmid>`, `gretap:<sensor>`) now also take **DB-backed cross-process locks** so they serialize across concurrent deploy *processes* (`docs/PLANNER.md` §8.4 + `resource_locks` table). The throughput limit *numbers* here remain per-process guesses to tune. See CHANGELOG 2026-06-03 "cross-process resource locking."
- **Priority / op cost models** — for non-FIFO semaphore acquisition. Not needed for v1.

---

## Q5. Proxmox adapter: where it runs

**Status:** ✅ **design RESOLVED 2026-05-28.** See CHANGELOG entry "Q5 resolved; planner-execution + Proxmox-adapter slice authorized". Formal spec in `docs/ADAPTER.md`. The first implementation slice (planner execution layer + adapter, built together) is authorized — see `docs/ADAPTER.md` §6 and `CLAUDE.md` Immediate Next Actions. Remaining items are implementation (exact REST endpoints, `ovs-vsctl` call forms, VMID derivation), answered by building the slice rather than in design chat.

> **Networking substrate settled (2026-06-03).** The slice-MVP shipped a direct-bridge (`/nodes/<node>/network`) SDN bypass; that question (`docs/ADAPTER.md` §8.3) is now resolved as **route 1 — SDN-native** (auto-derived Simple/VLAN zones; `/cluster/sdn` + cluster-wide apply). The bypass is retired. See CHANGELOG 2026-06-03 "SDN-native networking ratified." Per-tap **sensor routing** (route any tap to any sensor) was ratified the same session — see Q10 and CHANGELOG 2026-06-03.
>
> ✅ **RESOLVED 2026-06-04 (Option 1).** The SDN-native-vs-capture conflict (PVE places the VM tap on a Linux vnet bridge, so the OVS mirror can't bind — VLAN path too, not just Simple; capture silently dead) is resolved by **keeping SDN-native and re-mechanizing capture onto a per-tap `tc` mirror on the Linux vnet bridge** (Option 1 of `docs/design/04-networking-capture.md`). SDN-native is **retained**. See CHANGELOG 2026-06-04 "SDN-native capture via per-tap `tc` mirror on the Linux vnet bridge"; mandatory live-verify gates pending on `pxtwin1`. (Prior finding: CHANGELOG 2026-06-03 "LIVE-VERIFY FINDING: SDN-native networking breaks the per-VM OVS mirror".)

**Resolved decisions (summary index; full detail in `docs/ADAPTER.md`):**
- **Transport split:** REST for everything the Proxmox API supports; SSH for the OVS/mirror plane (`ovs-vsctl`, hook install) since SDN does not expose mirrors via REST. Forced by `docs/ARCHITECTURE.md` §3.
- **One adapter interface, two backends** (`rest`, `ssh`); the planner does not know the transport per op.
- **Auth:** limited-role Proxmox API token (REST) + dedicated `ovs-operator` SSH key (SSH); credentials on the orchestrator host, never in the IR/state store. Rotation out of v1 scope.
- **Idempotency:** adapter signs up to PLANNER §4.2 Model C for every `infra` op.

**What was already known before this resolution (retained for context):**
- **The adapter is a library the planner imports**, not a separate process. Per `docs/PLANNER.md` §6.1, the planner runs as an ephemeral process per deploy (with `--detach` option); the adapter lives in that process's address space and shares its state-store handle.
- **The adapter's translation table is the per-platform binding** (`docs/IMAGE_MANIFEST.md`). For each IR-node + image-version pair, the binding specifies `storage` (template volid, vmid, node constraints), `clone_mode`, `cloud_init` parameters, `hardware` config, and `placement_constraints`. The adapter executes from those fields rather than from any embedded knowledge.
- **Idempotency contract is defined** (per `docs/PLANNER.md` §4): `infra` ops use deterministic resource naming from `{plan_id, op_id}` + check-then-act + cleanup-on-partial. The adapter signs up to this contract for every `infra` op it implements.
- **Failure recovery model is defined**: connection drops during a deploy are not special — the planner retries the failed op per the standard retry policy, and the op's `check-then-act` entry handles "resource is in unknown state from a partial prior attempt."

**Still open:**
- Does it talk to Proxmox via REST API only, or also SSH (for the `ovs-vsctl` mirror config that the API doesn't expose)? Per `docs/ARCHITECTURE.md` §3, the OVS mirror work is not API-reachable today, so SSH is unavoidable for at least the mirror plane. Likely: REST for everything the API supports + SSH for `ovs-vsctl` and any other adapter ops the API doesn't cover.
- How does it authenticate — API token, PAM user? Probably API token with limited role for REST; separate SSH key for the `ovs-vsctl` operator account. Credentials live where the planner can read them — orchestrator host, config file or env vars.
- Should the adapter abstract REST and SSH behind a common op interface, or maintain two distinct sub-modules? Probably one common interface with two backends; the planner doesn't care which an op uses.
- Where do credentials live and how are they rotated? Out of v1 scope but worth flagging.
- Op-by-op API mapping is enumeration work — surfaces during op catalog (see Q4's sibling deliverables).

---

## Q6. Image / template pipeline

**Status:** ✅ **RESOLVED 2026-06-01 (design).** See CHANGELOG entry "Q6 resolved: image-build pipeline is `axrange image build`". Formal spec in `docs/IMAGE_BUILD.md`. The first implementation slice (the `axrange/imagebuild/` submodule) is authorized — see `docs/IMAGE_BUILD.md` §8 and `CLAUDE.md` Immediate Next Actions. Remaining items are implementation (exact REST endpoints, probe forms, binding recipe schema), answered by building the slice rather than in design chat. **With Q6 resolved, the v1 design surface is fully closed.**

**Resolved decisions (summary index; full detail in `docs/IMAGE_BUILD.md`):**
- **The pipeline is a CLI verb** — `axrange image build <image>` in `axrange/imagebuild/`, run from the operator's workstation against the cluster's existing REST + SSH paths. **No CI / Jenkins / build host in v1** — single-lab + manual-cadence + ~5–10 images justifies a checked-in script, not standing infrastructure. CI is the door that opens for multi-operator, Windows, or scheduled rebuilds — all post-v1.
- **Linux-only for v1**, building on upstream distro cloud images (e.g. Debian 12 generic-cloud), not from ISO.
- **Conformance is two-layer, both required:** minimal per-capability probes for every `provides:` keyword, + an `axrange` smoke-test that deploys a throwaway one-host scenario through the real planner+adapter. Conformance failure fails the build.
- **Build recipe lives in the Proxmox binding**, not the manifest; the manifest stays the pure capability contract; conformance bridges them. Lifting the recipe to the manifest is deferred until a second platform ships.
- **Auth:** the build pipeline uses its own credential scope, broader than the deploy-time adapter token (VM create / disk import / VM destroy / template convert); same orchestrator-side discipline, never in manifest/binding/IR.
- **Lifecycle:** a successful build demotes the prior `template_vmid` to `deprecated` and promotes the new one to `current`; the operator commits the binding diff.
- **Cadence: manual.** No scheduled/triggered builds in v1.
- The pipeline **closes the operator-side `qemu-guest-agent` prerequisite** — QGA preinstall + auto-start becomes a bake step. The 2026-05-29 manual template-rebuild runbook remains the documented fallback and is exactly what the pipeline automates.

**Open follow-ups deferred from this resolution** (implementation, or CHANGELOG-able later):
- Exact REST endpoints for VM-create / disk-import / template-convert; exact per-capability probe forms; the build-time REST role delta; cloud-image source URLs + checksum sources.
- Ubuntu support (Debian is the slice target; should fall out, unverified).
- Concrete binding build-recipe field schema (lands in `docs/IMAGE_MANIFEST.md` §3 with the slice).
- Lifting the platform-independent build-recipe portion into the manifest when a second platform adapter ships.
- Full `install_support:` verification and `first_boot_budget`/`size_estimate` measurement as additional conformance layers (the v1 slice does `provides:` probes + smoke-test first).
- Image attestation / signing — remains out of v1 (also tracked in `docs/IMAGE_MANIFEST.md` §6).
- `--check-upstream` reporting of newer upstream point releases — a possible refinement, not a v1 commitment.

---

## Q7. Scenario state store

**Status:** ✅ **substantially RESOLVED 2026-05-21** via the planner ratification. See `docs/PLANNER.md` §7. Remaining items below are concrete-schema implementation work, not design questions.

**Resolved points:**
- **Backing:** SQLite file on the orchestrator host (default `/var/lib/axrange/state.db`). Single-writer by construction (enforced by the per-scenario lease). Migration path to PostgreSQL remains clean if v1 scale hits limits.
- **Schema sketched:** tables `deploys` (one row per `axrange deploy` invocation), `leases` (active per-scenario lock), `resource_locks` (cross-process correctness locks — added 2026-06-03, `docs/PLANNER.md` §7.2/§8.4), `ops` (per op instance with status, inputs, outputs, scope), `op_attempts` (per attempt with timing, result, error). Detail in `docs/PLANNER.md` §7.2.
- **Atomicity:** op completion + output persistence is atomic (single transaction). Lease takeover is atomic. Heartbeat updates are non-blocking.
- **Drift detection** is reframed: the planner detects drift between IR and prior deploy via `ir_hash` comparison; the result drives the plan-diff and re-planning behavior (`docs/PLANNER.md` §9). No separate drift-detection subsystem.
- **Multi-operator concurrency** resolved by the lease/lock model: one active deploy per scenario, refused-with-message for concurrent invocations, automatic lease expiry for crash recovery.
- **IR versioning** lives in git (per `docs/IR_SCHEMA.md` §1.5); the state store records `ir_hash` for matching prior deploys but doesn't store full IR snapshots.

**Still open (implementation, not design):**
- Concrete SQL DDL with column types, indices, and foreign-key constraints.
- Migration discipline (schema versioning, upgrade paths).
- Retention / archive policy for old `deploys` records — accumulating indefinitely is fine for a long time but eventually we'll want a "prune deploys older than N days" command.

---

## Q8. AI core: agent loops and tool wiring

**Status:** 🟡 **OPEN — user-directed (not in active build as of 2026-06-11).** v1 triggers attacks/traffic manually; the five AI roles are not built. The op catalog already places/starts behavior agents via `OP-ACT` for when this work picks up. As of CHANGELOG 2026-06-11, the project-wide scope gates are removed: AI-core work can pick up whenever a thread needs it. See historical CHANGELOG entry "v1 scope: canvas (Q1) and AI core (Q8) declared out of v1" for the original 2026-05-28 decision; that gate is now retired but the design notes below remain a useful starting point. **Note:** AI-driven scenario design is being approached via the **AI scenario research** (`docs/design/12-ai-scenario-research.md`, ratified 2026-06-11), a different shape from the original five-agent in-the-runtime framing here.

**What we agreed (still valid for the future iteration):**
- Five roles: scenario planner, red-team, blue-team, telemetry analyst, traffic generator.

**Still open:**
- Which model(s)? Same model across roles or per-role specialization?
- Agent framework — write our own loop, or sit on an existing framework?
- Tool wiring — how does the red-team agent invoke Caldera/Atomic? What's the safety envelope?
- How does the telemetry analyst access pcaps without ingesting them all into context? (BPF prefilter? Zeek-log-only with on-demand pcap slice?)
- Where does RAG content (MITRE, NIST, D3FEND) live, who updates it?
- Eval — how do we know the agents got better between iterations?

---

## Q9. Host-side telemetry

**Status:** 🟡 **OPEN — user-directed, first concrete instance shipped.** The general question (agent choice, transport, per-OS matrix) is still open, but `install-defense-agent` gained a **Windows host-audit branch** (2026-06-24, CHANGELOG) that enables the DC's native Security-log audit subcategories (4768/4769 Kerberos, 4662 DCSync via a domain-root SACL) post-promotion — live-verified capturing the AD attack chain in the DC's own event log. That's native Windows auditing, not Sysmon/osquery/Fluent Bit/Wazuh-agent telemetry, so the broader "still open" items below stand.

**What we agreed:**
- v1 started network-side only (capture appliance + pcaps); a first host-side signal (Windows Security-log audit) has since shipped for the AD identity increment (`docs/SLICE_OVERLAY.md`, `docs/SLICE_WINDOWS_AD.md`).

**Still open:**
- When (not if) we add host telemetry: agent choice (Sysmon? osquery? Fluent Bit? Wazuh?).
- Transport — log shipping over the management vnet to a separate log receiver? Same sensor as pcaps?
- Per-OS agent matrix.
- Privacy/blast-radius — host agents are deep access; what's the threat model for the agent itself?

---

## Q10. Multi-sensor scaling and HA

**Status:** 🟡 **OPEN — user-directed; partially addressed 2026-06-03.** Per-tap **sensor routing** (route any tap point to any sensor; multiple sensors as multiple endpoints on the shared transport vnet) is ratified — `docs/IR_SCHEMA.md` §6 `sensors:` + tap→sensor bindings, CHANGELOG 2026-06-03. That answers the **sharding** axis below (manual sharding of taps across sensors). **HA / standby / cross-site replication remain unbuilt** — picks up whenever a thread needs it. Note: this is capture *routing/sharding*, not per-scenario *correlation* — each sensor stays a merged black box (the no-correlation line holds per sensor).

**Still open:**
- When does a single sensor stop being enough? (Likely: more than ~2 Gbps aggregate, or >1 site.)
- ~~Sharding by zone (which zone tunnels to which sensor) vs by traffic type vs by hash~~ — superseded by per-tap sensor routing (operator-chosen mapping, finest granularity). 2026-06-03.
- Active-standby pattern — does the standby capture in shadow mode or stay cold?
- Cross-site replication of `/captures/` — out of scope or in?

---

## Q11. Operator interface for run management

**Status:** 🟢 **Answered 2026-09-29.** v1 has both: the CLI grid below, and a web UI, the axRange
Console (`docs/design/16-unified-console.md`) — run-time first, polling-based (no real-time log
tail; SSE/websocket is explicitly out of this round, `docs/design/16-unified-console.md` §10).
Progress on a long-running op is watched by polling `GET /api/deploys/{id}` (op tree + attempts),
the same shape the Twin area and the CLI both use. Snapshot/restore and pause/resume beyond cancel
remain open, as below.

**Status (as ratified, pre-2026-09-29):** CLI surface implied by planner ratification; web UI and other surfaces still open.

**What's now known:**
- **CLI commands** (per `docs/PLANNER.md` §6.3): `axrange deploy`, `axrange status`, `axrange list-deploys`, `axrange cancel`, `axrange validate`, `axrange plan`.
- **Deploy-time flags** (consolidated across Q4(a) and Q4(b)): `--detach`, `--dry-run`, `--max-retries`, `--retry-backoff`, `--no-auto-escalate`, `--halt-scope`, `--allow-destroy`, plus concurrency overrides (`--limit-cluster`, `--limit-per-node`, `--limit-ansible`, `--max-ops-in-flight`).
- **Tree view of deploy state** (status symbols ✓ ✗ ⊘ ⋯) is the standard output for both terminal-failure summary and `axrange status`.
- **State store is queryable** with standard SQLite tooling; the state store doubles as a diagnostics surface.

**Still open:**
- Does v1 have a web UI, or CLI-only? My current bias: CLI-only for v1; web UI in a later iteration once we have real operator usage data.
- How does the canvas (Q1) know which scenarios are deployed right now? It queries the state store.
- How are run logs surfaced beyond the tree view? Per-op logs in the state store, but the operator probably wants a way to follow a single op's logs in real time — design needed when implementation forces the question.
- Snapshot / restore semantics — not specified; a CHANGELOG-able addition when needed.
- Pause / resume beyond the binary "cancel" — not v1.

---

## Q12. Scenario library and reuse

**Status:** not discussed.

**Still open:**
- Is there a curated library of scenarios shipped with the platform? Examples in `target-environments/`?
- How are scenarios shared between operators / installations?
- Per-scenario licensing / classification (some scenarios may contain real CVE PoCs)?

---

## Q13. `config`-class execution slice (the Ansible path)

**Status:** ✅ **RATIFIED 2026-06-02 — slice AUTHORIZED.** The first `config`-class plane (`OP-APP-010 provision-app`: install software on a deployed host) is authorized. Full scope in `docs/SLICE_CONFIG.md`; ratified in `docs/CHANGELOG.md` 2026-06-02. **Claude Code: this slice is now implementable** (`pkg` / `pkg@version` / `script` only); overlays (`OP-OVL`), activity (`OP-ACT`), the other `install:` kinds, and the deferred planner machinery were out of this slice's scope at the time (all since implemented — see CHANGELOG; the project-wide scope gates were removed 2026-06-11).

**The open design decision is resolved:** the **execution channel** is **QGA-mediated Ansible** — the orchestrator pushes + runs the rendered playbook *in* the guest over the adapter's qemu-guest-agent exec channel (deployed guests are on isolated SDN nets, so the tech stack's original "pull-mode Ansible" hits the same reachability wall `wait-os-ready` did). Ansible is baked into the template via the imagebuild `provision:` path. Folded into `docs/PLANNER.md` §4.3 + `docs/ARCHITECTURE.md` §147.

---

## Topics deferred indefinitely (will revisit only when needed)

- **Scenario-state correlation in capture pipeline.** User explicitly chose to defer this — sensor stays a black box. Revisit when manual time-range queries become painful. See CHANGELOG 2026-05-21.
