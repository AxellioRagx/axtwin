# axRange — cyber-range digital-twin instrument

A personal instrument (not a shipped product) for one loop:

> feed a pcap / sensor → **discover** a network → view & **customize** the twin →
> **deploy** it to Proxmox → drive **realistic traffic** → **inject an attack** →
> **confirm** the O2 observability surface caught it.

The engine and control-plane API behind this loop are real and have been
live-verified on two Proxmox clusters (`dev45` PVE 9.2, `pxtwin1` PVE 8.4). The
work ahead is wiring the two unwired seams (live discovery; attack-inject +
detection-confirm) behind the API, then a single UI over all of it.

## Layout — a monorepo of two packages

| Dir | Package | Role | Import rule |
|---|---|---|---|
| `engine/` | `axrange` (`engine/src/axrange`) | twin plan→deploy→destroy, traffic (`tfc`), taps, exercises (`exr`), O2 surface, control-plane API (`axrange serve`) | must **not** import `aximprint` |
| `discovery/` | `aximprint` (`discovery/src/aximprint`) | sensor metadata → discovered network → cast to a twin config | may import `axrange` |

Both packages are src-layout: `src/`, `tests/`, `scripts/`, `docs/`. Content
lives beside them at the root, not inside a package:

| Dir | What |
|---|---|
| `catalog/` | every twin config — `twins/` (live bundles), `twin-configs/` (legacy), `target-environments/` (scenario IR), `demo/` — and `personas/`, the persona library (see its README) |
| `ai-crafted/` | the agentic twin-config experiment (corpus + pipeline tree; not read by code) |
| `reports/` | rendered reports |
| `ui/` | the operator console spec (and, later, the SPA) |
| `archive/` | the pre-consolidation design ledgers, reference only |

The one-way boundary (`discovery` → `engine`, never back) is the real design
constraint. Keep it enforced as a layering test, the way `discovery` already
checks its own internal layers.

Install both into one venv:

```
python -m venv .venv && . .venv/bin/activate
pip install -e ./engine[api,dev] -e ./discovery[dev,acquire]
```

Run the loop (CLI, today): `axrange te deploy --config corp-enterprise …` →
`axrange tfc deploy …` → `axrange tap deploy …` → `axrange exr run …`.
First target: `catalog/twins/corp-enterprise/` (README has the sequence).

## Architecture: plugin seams (this is the project's shape — expandable by design)

The platform is a **plugin architecture**: the core defines a few stable
*contracts*, and functionality lives in *plugins* that self-register into
registries the UI and runner read generically. Adding a capability means adding a
plugin, not growing the core — this is how the project stays expandable without
this file ever growing. The extension points (seams):

- **Discovery adapters** — `IngestPlugin` (`discovery/`): pcap, PacketXpress/axapi,
  axQuery, Zeek, Suricata, … each with its own config.
- **Deploy platforms** — the `Adapter` protocol (`engine/`): Proxmox (built),
  VMware and others as added plugins.
- **Sensors**, **personas**, and **attack frameworks** — registries (`engine/`):
  each type self-registers; the UI lists whatever is installed.

Rules that keep this flat:

- The core holds **contracts**; plugins hold **functionality**. This file names
  the **seams**, never the individual plugins — plugins are discovered at runtime
  (the registry) and by convention (`**/SPEC.md`), so the filesystem is the index.
- Every plugin/feature carries, **beside its code**, its own `SPEC.md` (contract,
  config schema, and an `## Acceptance` section) and its own tests / `verify()`.
  A feature whose validation differs from the capture-flow gate **ships its own
  verifier** through the same seam — the core never hard-codes which checks exist.
  Start new specs from `SPEC.template.md`.
- This is **forward-looking**: existing code is not rearranged into feature
  folders. New plugins/features follow the convention; an existing one gets a
  co-located `SPEC.md` only when you are already editing it for a real reason.

## The critical path (do these in order — each is a thin seam over working code)

1. **Wire discovery into the API.** Replace the fixture imprint reader
   (`engine/src/axrange/api/imprints.py`) with a real call into `aximprint`
   (pcap → `ingest`/`materialize`/`cast` → imprint). This is step 1 of the loop
   and the whole reason the two packages now live together.
2. **Wire attack-inject + confirm as one action.** `POST /api/exr` exists; add the
   read-back of what O2 caught so inject and confirm are one motion.
3. **Add an edit/save-twin endpoint** so "customize the twin" is real (today the
   UI can view and deploy, not edit).
4. **Build the UI** — a standalone SPA served by `axrange serve`
   (`AXRANGE_SPA_DIR` co-serve is already wired), designed around the 7-step loop,
   embedding Grafana panels for the telemetry (confirm) step. Not a Grafana plugin.

A UI is only as seamless as the API beneath it — hence 1–3 before 4.

## Acceptance test (the only definition of "working")

The loop runs end to end: a twin deploys, drives traffic, and **its tapped
traffic reaches a sensor** — either a real sensor if one is present, or a dummy
`tcpdump` node stood up as the tap target, confirmed by climbing capture metrics
(packets / bytes / rate). O2 is set up and stood up manually and out of band, so
it is **not** part of this gate; "the capture is flowing" is. If a change breaks
that, it isn't done. No per-slice "done" substitutes for this.

Each plugin/feature may also carry its **own** acceptance check (see below); the
runner executes every active plugin's verifier, this capture-flow check being the
default for the deploy loop.

## Frozen — off the path, do not build on or polish

Kept in-tree for reference, but **not** maintained and **not** on the critical
path: the golden-twin / fidelity-scorecard direction, the AI-scenario-research
pipeline (dropped from this baseline; recover from old history if ever needed),
the CorpAPCN fidelity loop. If work starts generating new tiers, fidelity
sub-questions, or work-orders, that is the old sprawl returning — stop.

## History

This repo is a clean baseline consolidated from the former `axRange` and
`aximprint` repos. The full historical design ledger (124 KB CLAUDE.md,
CHANGELOG, WORK_ORDERs, LESSONS) lives under `archive/` and in each package's
own `docs/`. Treat those as reference, not as live instructions — this file is
the live orientation. The version-locked contract specs (`engine/docs/IR_SCHEMA.md`,
`PLANNER.md`, `OP_CATALOG.md`, `VALIDATOR.md`, `ADAPTER.md`, `IMAGE_MANIFEST.md`)
remain the authority for their respective layers.

## Scope discipline (why the previous build bloated, and the rules that prevent it)

The prior version of this project grew to 500 MB and a 124 KB orientation file
because the workflow let scope expand on its own. These rules invert that. They
are binding on every session.

1. **No new scope without explicit human sign-off.** Implement against the
   current goal only. Do **not** introduce a new tier, abstraction, schema,
   "direction," or work-order on your own initiative. When you see a tempting
   expansion, name it in one line and stop for the user's call. Scope expansion
   is the gated action — not the default. (This reverses the old "advance design
   by default and record it," which was the root cause.)
2. **The definition of done is fixed and the user owns it** — the acceptance
   test above (the `corp-enterprise` loop), plus the 7-step UI loop in the goal
   at the top. Every task must name which step it advances. If it advances none,
   it does not happen. The goal does not move mid-session.
3. **Docs match reality; they do not accumulate.** Keep this file ≤ 2 pages,
   hard cap. Git history is the changelog — do not reintroduce a narrative
   CHANGELOG, a `WORK_ORDER_*` sprawl, an auto-injected LESSONS store, or
   doc-counter commit hooks. If this file is growing, that is the warning sign.
4. **One task, one branch, finish before starting.** No second `wip/*` branch
   until the current one merges or is explicitly abandoned.
5. **Plateau stop-rule.** If a metric does not improve for ~2 iterations, stop
   and bring it to the user. A plateau is a reason to question the goal, never to
   "try another axis."
6. **No vanity metrics.** Test counts, rule counts, op-type counts, persona
   counts are not progress — they rise with accretion. The only progress question
   is: does more of the loop run than before?

## Working rules

- Build toward the acceptance test, not toward completeness. One well-scoped
  change at a time; re-run the loop.
- Don't refactor the large modules (`o2control/taps.py`, `cli/main.py`,
  `api/service.py`) for aesthetics — they carry hard-won cluster fixes. Change
  them only when a seam on the critical path requires it.
- Git: outbound push on explicit request; inbound sync (fetch/pull/clone) is the
  user's own manual step.

## Live-deploy data is not project data

Deploy logs, planner state DBs, screenshots, pcaps and any other artifact of a
real deployment live **with that deployment's state DB** (the default is
`~/.axrange/`), never inside this checkout. The old `engine/deploy-dev45/`
scratch workspace was deleted on 2026-10-20 for that reason; pass `--images`
and `--db` paths explicitly.
