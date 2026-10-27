# Slice design — `OP-ACT` activity execution (the traffic/attack path)

> **Trigger moved to the Exercise tier (2026-07-24, `docs/WORK_ORDER_exercise_tier.md`, CHANGELOG
> "Exercise tier (`exr`) BUILT").** Everything below (the OP-ACT machinery — render, ConfigBackend
> ops, the inline-steps engine, the AD kill chain content) is **unchanged**. What changed is *who
> emits it*: a `te` (engine) deploy's own DAG no longer contains `_emit_activity` at all — the
> standalone, ephemeral `exr` playout (`axrange/o2control/exercise.py`) composes `OP-ACT-010/020/030`
> by hand against an already-deployed twin instead. `REF-260` (new, WARNING) fires when a `te`-tier
> IR still declares `activity:`, so the archetypes below (`soc-detection-lab`, `ad-domain-lab`,
> `purple-team-range`) keep validating — they're now `exr`-consumed content, not edited.
>
> **STATUS: ✅ IMPLEMENTED + LIVE-VERIFIED on dev45 (2026-06-12, inline-steps MVP).**
> The §2 Option A design below shipped: render + ConfigBackend ops + planner
> `_emit_activity`, 238 tests green, `validate`/`plan` PASS on soc-detection-lab.
> Live on dev45 (PVE 9.2): the activity trio ran in order, the runner executed
> in-guest (QGA-confirmed), and the generated ICMP + TCP-SYN traffic was captured
> at the sensor (done-criterion #2 met). See CHANGELOG 2026-06-12.
>
> **AD ATTACK CHAIN (OP-ACT content) LANDED + LIVE-VERIFIED on dev45 2026-06-24.**
> The corpus AD kill chain (recon → LDAP/SMB enum → AS-REP roast → Kerberoast →
> DCSync) is authored as inline-steps activities on `ad-domain-lab` +
> `purple-team-range` (run from the LAN attacker against the corp.lab domain). A
> focused verify (`ad-attack-verify`: router-egress + responding `windows-server-2022`
> DC + attacker + `tcpdump-sensor`) ran the chain against a LIVE DC and the sensor
> captured **543 attacker↔DC packets** across the full AD surface — Kerberos 88
> (20 AS-REQ/TGS-REQ payload pkts), LDAP 389/636, SMB 445, GC 3268, and **131
> DRSUAPI/RPC packets from a SUCCESSFUL DCSync** (krbtgt hash extracted with a
> Domain Admins cred). This is the §9 follow-on #2 (domain-actor activities) — built
> on the now-shipped identity overlay. CHANGELOG 2026-06-24.
> **Authority:** design proposal (Claude Code, 2026-06-11). The project-wide
> scope-boundary gates were removed 2026-06-11 (CHANGELOG; Authority: user), and
> per-archetype `OP-ACT` activity is delegated to Claude Code with no chat
> round-trip required. The earlier "ratify in a chat/design session before
> implementing" gate this doc was written under (the one `docs/SLICE_CONFIG.md`
> passed on 2026-06-02) **no longer applies** — the §2 pivotal decision (Option A,
> inline-steps MVP) and the §3 scope are Claude Code's to land directly, with a
> CHANGELOG entry (`**Authority:** implementation (Claude Code)`) recording what
> was decided at build time.
>
> Companion to `docs/OP_CATALOG.md` §5.6 (the OP-ACT op signatures — already
> ratified), `docs/IR_SCHEMA.md` §5 (the `layers.activity` surface — already
> ratified + validated), `docs/PLANNER.md` §4.3/§5 (config idempotency, barriers),
> and `docs/SLICE_CONFIG.md` (the config-class slice this one extends).

---

## 1. What this slice is, and why it's next

The shipped slices stand up infrastructure, capture, images, and **install software**
(`provision-app` / `run-script`). What no slice does yet is **make anything happen** in
a deployed range: recon, exploitation, lateral movement, ransomware/exfil, OT Modbus
manipulation, benign user load. That is the **`OP-ACT` activity plane** — "run a behavior
over time, on a host, against targets."

It is the **highest-value remaining gap** because the corpus is overwhelmingly *activity*:
across the 324-video Stage-2 corpus the dominant content is attack/exercise behavior
(lateral movement, phishing, Kerberoasting, Modbus manipulation, the purple-team kill
chain), and **all seven Stage-2 archetypes already carry a designed-but-deferred OP-ACT
spec** (`scenario_ai_research/stage2/CORPUS_DIGEST.md` §5). Those specs are the concrete targets this
slice implements against — e.g. the `soc-detection-lab` chain *recon → exploit web → lateral
to workstation → exfil*, staged today as inert target-list cribs on each attacker host.

Activity is also what makes the **capture plane meaningful**: today tapped hosts mirror to
the sensor, but nothing generates the traffic worth capturing. OP-ACT closes that loop —
which is why `start-activity` structurally gates on `mirror-ready` (capture live *before*
traffic).

## 2. The pivotal decision — the behavior agent + behavior definition

`OP_CATALOG.md` §5.6 fully specifies the *three ops* (`deploy-behavior-agent` →
`start-activity` → `wait-actor-ready`) and their wiring. What it deliberately leaves open
(§5.6 note + OPEN_QUESTIONS Q8): **what the behavior agent *is* and what a behavior
*does*.** `office-workday@v2` and a full behavior-agent runtime belong to the **AI core
(Q8 — OPEN but unbuilt).** So the slice must pick a v1-minimal agent that makes
activity executable *without* the not-yet-built behavior library or AI roles.

| Option | The "agent" + "behavior" | Assessment |
|---|---|---|
| **A. Inline-steps MVP** *(recommended)* | No agent runtime, no library. Support only `inline: { steps: [...] }`; render the steps to a script and run them in-guest over the **existing QGA config channel** (the run-script mechanism). A step is a concrete invocation (`{tool: nmap, target, args}` or a `{run: <shell>}` escape). | ✅ Smallest thing that makes activity real. Reuses the shipped `config_exec` + QGA path verbatim. Sidesteps the Q8 behavior library *and* the AI core. Matches the corpus + the seven archetypes (their OP-ACT specs are concrete tool sequences) and the cribs already staged on the attacker hosts. `behavior:` library refs stay a clear "deferred" error — exactly how `config`/`role`/`container` were gated out of the config slice. |
| **B. Minimal built-in library** | A small registry of hand-written behaviors (`attacker-recon`, `office-workday`) resolved by id. | ⚠️ Pulls in the behavior-library design (the REF-140/150 / ACT-020/030 registry stubs) and a rendering contract — a second pivotal decision. More than the MVP needs; do it as a follow-on once inline steps prove the path. |
| **C. Resident behavior agent** | A long-running in-guest daemon honoring `schedule:` windows, health endpoint, etc. (the §5.6 vision). | ❌ Heaviest; the agent internals are Q8. Defer. `wait-actor-ready` degrades to "the step script started/completed" under Option A. |

**Sub-decision — one-shot vs. scheduled.** Under Option A, `start-activity` runs the rendered
steps **once** (fire-and-forget or run-to-completion); `schedule:` windows (`start`/`end`/
`days`/`once`) are **deferred** in the MVP (the catalog already delegates scheduling to the agent
— there is no agent here yet). One-shot-at-deploy is enough to generate and capture an attack
chain. The full schedule executor (windows + seeded jittered `interval` + multiple-per-node) is
**designed in §11**.

**Recommendation: Option A (inline-steps MVP), one-shot.** It honors the ratified op catalog
(places + starts the "agent"), reuses the proven QGA channel, and defers every Q8-coupled piece.

## 3. Scope

**In:**
- `pass1_emit` extension: replace the current `activity`-reject guard (`pass1_emit.py` ~L294)
  with a `_emit_activity` that, **per `activity` binding**, emits `deploy-behavior-agent[$act]`
  → `start-activity[$act]` → `wait-actor-ready[$act]`, wired `after host-ready[$host]` and (for
  `start-activity`) `after mirror-ready[$sensor]` when the scenario has capture.
- A renderer for **`inline.steps` only**: a small, closed step vocabulary plus a `{run: <shell>}`
  escape, rendered to one opaque script delivered + executed over the **existing** QGA channel.
- `config_exec` extension: `deploy-behavior-agent` (write the rendered step-runner into the
  guest, idempotent) and `start-activity` (exec it). `wait-actor-ready` is a **wait** op that
  polls completion/health. Routed automatically — these ops are `config`-class, and the executor
  already dispatches config-class ops to `config_exec` by `idempotency_class` (`executor.py`
  `_backend_for`).
- **Actors: implicit-local + external only.** The agent host comes from the `activity→host`
  binding; the actor is a local user on that host (or `external: true`). No domain identity.
- Step **content read at emit time** (the guest is isolated), embedded in op inputs — exactly
  the `run-script` content-threading pattern (`SLICE_CONFIG.md` §11).
- A `valid_activity` fixture, unit tests, and a live `pxtwin1`/dev45 smoke (an inline recon
  activity whose traffic is captured at the sensor).

**Out (explicit; follow-on slices):**
- **`behavior:` library refs and `overrides:`** — deferred (the behavior library / AI core, Q8).
  A clear "library activities are not yet supported" error, mirroring config/role/container.
- **`schedule:` windows** — one-shot only in the MVP; the full schedule executor (windows +
  seeded jittered cadence + multiple-per-node) is **designed AND BUILT — see §11**.
- **Domain-actor activities** — need `create-domain-user` / `join-host-to-domain`, which are
  OP-OVL/identity (deferred). External + local actors only.
- **The OP-OVL blue-team half** — defense overlays that *score* whether the activity was
  detected. OP-ACT generates; OP-OVL observes. Separate slice.
- **Host-side telemetry / the AI red-team driver** (Q8/Q9).

## 4. Op flow

For an activity `act-recon` (actor `attacker`, inline steps), bound to host `attacker`, in a
scenario with capture:

```
host-ready[attacker] ─► deploy-behavior-agent[act-recon] ─►┐
                                                           ├─► start-activity[act-recon] ─► wait-actor-ready[act-recon]
mirror-ready[$sensor] ─────────────────────────────────────┘
```

`deploy-behavior-agent` can land early (right after the host is ready); `start-activity` waits
on **both** the agent deploy and `mirror-ready`, so no packet is generated before capture is
live. Activities are **leaves** of the DAG (`OP_CATALOG.md` §5.6) — nothing depends on them but
the scenario-level "ready" assertion. `mirror-ready[$sensor]` is **already emitted today**
(`pass1_emit.py` L412-417), so the gate exists.

## 5. Components (grounded in the current tree)

| File | Change |
|---|---|
| `axrange/planner/pass1_emit.py` | Replace the `ir.layers.activity` reject (~L294) with `_emit_activity`; add `deploy-behavior-agent`/`start-activity`/`wait-actor-ready` to `_OP_CATALOG_IDS`; resolve `activity→host` (+ `activity→targets`) bindings; wire `mirror-ready`. |
| `axrange/config_exec/backend.py` | Add `op_name` cases `deploy-behavior-agent` (write step-runner) and `start-activity` (exec it), beside the existing `provision-app`/`run-script`. Reuse `GuestAgent`. |
| `axrange/config_exec/render.py` | A step renderer: closed `tool:` vocabulary (e.g. `nmap`, `curl`, `hydra`, `script`) + `{run: <shell>}` escape → one shell script. `behavior:`/`overrides:` → deferred error. |
| `axrange/planner/executor.py` | **No change** — config-class routing + the `wait` polling loop already exist; `wait-actor-ready` is a wait op like `wait-os-ready`. |
| validator | **No change** — `layers.activity` is already structurally + semantically validated (IR §5, Pass 3). Only execution is missing — same position the config slice started from. |
| `tests/` | `test_activity_exec.py`; extend `test_planner_dag.py` for the activity DAG; a `valid_activity` fixture (1 host + 1 inline recon activity, gated on mirror-ready). |

## 6. Open questions (resolve by building, per the `ADAPTER.md` §7 pattern)

- **Step vocabulary.** The closed set of `tool:` kinds for v1 and how each renders to shell;
  how the `{run: <shell>}` escape is bounded. (Lean small: `nmap`/`curl`/`hydra`/`script` + `run`.)
- **`activity→targets` threading.** How the targets binding flows into step args (explicit
  `target:` in the step vs. injected from the binding). The archetypes' cribs use explicit targets.
- **Re-run semantics.** Traffic generation is **not** idempotent (re-deploy re-sends). The
  catalog marks deploy/start `redo`; confirm "re-running re-fires the activity" is the accepted
  v1 semantics (it is for one-shot attack replay — the project's whole point).
- **`wait-actor-ready` under Option A.** With no resident agent, what does it poll — step-runner
  exit status / a completion marker file written by the runner? (Likely the latter.)
- **`mgmt_ip` vestigial?** As with provision-app, the backend reaches the guest by `vmid` over
  QGA — confirm at build time.

## 7. Dependencies & tie-ins

- ✅ **`mirror-ready` barrier** — already emitted (`pass1_emit.py`); the gate exists.
- ✅ **Execution channel** — `config_exec` + `GuestAgent` (QGA) ships and routes config-class ops.
- ✅ **Op signatures** — `OP_CATALOG.md` §5.6 is ratified; this slice implements, not redesigns.
- ✅ **No escalate-walk-back needed** — deploy/start are `redo` (not `escalate`), so the deferred
  planner machinery (`PLANNER.md` §4.4/§8) is **not** a blocker (unlike `run-script`).
- ⛔ **Identity ops** (`create-domain-user`/`join-host-to-domain`) are deferred → local/external
  actors only this slice.
- 🎯 **Concrete targets** — the seven Stage-2 archetypes' §5 OP-ACT specs (`CORPUS_DIGEST.md`).
  First target: an `inline` recon activity on `soc-detection-lab`/`purple-team-range`'s attacker.

## 8. Done-criteria (proposed)

1. `axrange validate | plan | deploy` of a 1-host + 1-inline-activity scenario; `plan` shows
   `deploy-behavior-agent` → `start-activity` (gated on `mirror-ready`) → `wait-actor-ready`.
2. Live on dev45: deploy runs the inline steps in-guest (verified via QGA exec output), and the
   generated traffic **appears in the sensor's pcap** (closing the OP-ACT ↔ capture loop).
3. `start-activity` does **not** fire before `mirror-ready` (assert DAG ordering).
4. `behavior:`/`overrides:`/`schedule:`/domain-actor activities each produce a clear "deferred"
   error, not a silent skip.
5. Full suite green (existing + new).

## 9. Follow-on slices (after this one)

1. **Behavior library** (Option B): the built-in behavior registry + `behavior:`/`overrides:`
   resolution (the REF-140/150 stubs) — benign user load (`office-workday`) and reusable
   attack chains.
2. **Domain-actor activities**: needs the identity overlay (`create-domain-user`,
   `join-host-to-domain`) — couples to an OP-OVL identity slice and `role: adds`.
3. **`schedule:` windows + resident agent** (Option C): daily windows, jitter, the §5.6 agent.
4. **OP-OVL defense overlays**: the blue-team half — observe + score whether the OP-ACT chain
   was detected (the purple-team loop), turning these archetypes from "attack runs" into
   "attack runs *and is measured*".
5. **AI red-team driver** (Q8): the AI core plans/varies the activity instead of fixed inline steps.

## 10. Implementation checklist (gate lifted 2026-06-11 — Claude Code drives)

The chat-ratification gate this section once held is **lifted**: the scope-boundary
gates were removed 2026-06-11 and OP-ACT is delegated to Claude Code. What remains is
the build-time acceptance Claude Code lands against:

- [x] §2 pivotal decision committed at build time: **Option A (inline-steps MVP),
      one-shot**, `behavior:` / schedule / domain-actor deferred.
- [x] Step vocabulary (§6) settled: closed `tool:` set (`nmap`/`curl`/`hydra`/`nc`/`ssh`)
      + `{run: <shell>}` escape; per-step brace-grouping; completion-marker poll.
- [x] Implementation lands against §5 + §8 (done-criteria 1, 3, 4, 5 met in tests;
      #2 — live traffic-at-sensor — is the remaining live-verify).
- [x] CHANGELOG entry added (`**Authority:** implementation (Claude Code)`, 2026-06-12).

## 11. Schedule executor — the `schedule:` piece (designed 2026-07-09, BUILT 2026-07-11)

The inline-steps MVP (§2 Option A) runs `start-activity` **once**. This section specs the
`schedule:` executor that lifts that, per the O2 activity model
(`docs/design/08-exercise-measurement.md`: `activity` = **behavior + schedule + tools**, benign
**and** scripted, engine-run). **Authority:** user, 2026-07-09 (design); build is
`implementation (Claude Code)`, 2026-07-11 (`WORK_ORDER_schedule.md`).

> **BUILT.** All three capabilities below shipped as designed, with one implementation correction
> to §11.3: the jittered scheduler is a **seeded Python sleep-loop**, not a systemd timer —
> `RandomizedDelaySec` isn't seedable, which would have broken the "same seed ⇒ same schedule"
> replay requirement this section exists for. `start-activity` writes a small bash **launcher**
> script and execs it (`ga.exec(vmid, ["bash", launch])`) rather than inlining
> `pkill -f "<scheduler>" ...; setsid python3 <scheduler> &` into one `sh -lc` string — the inline
> form's own argv contains the scheduler path substring, so its `pkill -f` would SIGTERM the
> invoking shell itself before the `setsid` relaunch ever ran (a real self-kill bug caught during
> build). Validator: `ACT-050` lifted for seeded schedules (warns only when there's no
> `schedule.interval` to seed); new `ACT-060` (interval shape) + `ACT-070` (seed-recommended).
> Unit/integration-verified (794 pytest tests); gated `dev45` live-verify PENDING.

### 11.1 Three capabilities

1. **Multiple activities per node.** `activity` is already a list, and **ACT-040 caps one
   host-binding *per activity*, not one activity per host** — so N activities can share a node.
   Each compiles to its **own runner keyed by activity id** (`act-<id>`), so two runners on one
   host never collide.
2. **Windowed scheduling.** The existing `Schedule` fields — `days` / `start` / `end` / `once` —
   bound *when* an activity may run (calendar window). `once: true` = today's one-shot behavior.
3. **Seeded jittered cadence (realism).** A new `schedule.interval: {min, max}` (durations —
   `"30s"`/`"5m"`/`"2h"`) fires the steps at `random(min, max)` intervals *within* the window, so
   activity reads as **organic, not synthetic**. Mutually exclusive with `once`.

### 11.2 Determinism — seed the randomness (load-bearing)

The range's value is the **replay loop** (run → measure → add defense → replay the *identical*
thing → measure again). Truly-random cadence breaks it: the replayed run's traffic differs, so a
detection delta can't be attributed to the defense change. So the jitter is drawn from a **seeded
RNG** using the `Activity.seed` field (today **reserved** — ACT-050 rejects it; this gives it its
purpose). **Same `seed` ⇒ same schedule.** Realistic *and* reproducible.

### 11.3 Compilation

Each scheduled activity → a **per-activity seeded runner** on the host (keyed `act-<id>`):
- **one-shot / windowed-`once`** → the current run-once script (unchanged).
- **jittered** → **as built:** a Python sleep-loop script (`render_jitter_scheduler`), NOT a systemd
  timer — `RandomizedDelaySec` isn't seedable, which would break "same seed ⇒ same schedule." While
  inside the window, it sleeps `seeded_random(min, max)` (`jitter_sequence()`, deterministic draw
  order) and re-runs the step script; the RNG is seeded from `Activity.seed`. Naming keys on
  activity id (`act-<id>`), so multiple loops coexist on one node.
- `start-activity` **installs** the loop via a written bash **launcher** script executed as
  `ga.exec(vmid, ["bash", launch])` — the pkill-then-relaunch logic must live in that file, not be
  inlined into the invoking `sh -lc` command, because the inline command's own argv would contain
  the scheduler path substring and self-match its own `pkill -f <scheduler>` (real bug, fixed during
  build). `wait-actor-ready` confirms the loop is armed/running (not "completed").

### 11.4 Schema delta (illustrative)

```yaml
activity:
  - id: web-browsing
    inline: { steps: [ ... ] }
    seed: 1337                              # makes the jitter reproducible
    schedule:
      days:  [Mon, Tue, Wed, Thu, Fri]      # window: WHEN it may run (existing)
      start: "09:00"
      end:   "17:00"
      interval: { min: "30s", max: "5m" }   # NEW: jittered cadence within the window
```

`Schedule` gains `interval: {min, max}` (duration strings); `Activity.seed` is **un-reserved**
(ACT-050 lifted for seeded schedules). **No op-catalog signature change** — this is a
render/backend feature plus the two schema fields.

### 11.5 Validator

- `interval` requires both `min` and `max`, `max >= min`, and is **incompatible with `once: true`**.
- a jittered/windowed activity ⇒ **`seed` recommended** (warn if absent — a non-seeded jittered
  activity is non-reproducible, defeating the replay loop).
