*The AI scenario research subsystem — a two-stage pipeline that turns open cyber-range knowledge
into axRange's scenario content — and the AI core roles it feeds toward.*

## Two stages, one contract

`scenario_ai_research/` is a top-level component, separate from the engine, that builds axRange's
scenario content in two deliberately different stages:

- **Stage 1 — knowledge ingestion.** An open-source, entirely **local** pipeline that discovers
  cyber-range-relevant public video content, acquires a transcript for each (captions first,
  falling back to a local Whisper model only when no captions exist), and summarizes it with a
  free local model (Ollama by default). The output is a structured, **provenance-tagged knowledge
  corpus** — one record per source with its summary, key points, extracted entities (tools,
  techniques, defensive controls, scenario patterns), and the transcript/model provenance that
  produced it, plus a consolidated index.
- **Stage 2 — scenario design.** A Claude Code session reads that corpus and uses it to design
  and build axRange's upper layers: the apps-and-tools and activity content, the identity/data/
  defense overlays, and full target-environment archetypes — ultimately emitting **Scenario IR**
  that flows through the standard `axrange te {validate,plan,deploy}` pipeline like any other
  scenario (see [03-engine.md](./03-engine.md)).

The **knowledge corpus is the contract** between the two stages. Stage 1 never reasons about
axRange's schema; Stage 2 never touches raw video. This keeps the expensive, judgment-heavy work
(turning scattered public knowledge into a correct, deployable target-environment design) on the
side that can reason about it well, while the bulk transcription and first-pass summarization —
high-volume, mechanical work — runs entirely on free local models with no API cost.

```
┌─────────────── Stage 1: local ingestion (scenario_ai_research/research/) ──────────────┐
│ discover videos → acquire transcript (captions-first, Whisper fallback)                │
│                 → summarize (local LLM, e.g. Ollama)                                    │
│                 → write provenance-tagged corpus records + a consolidated index         │
└───────────────────────────────────┬──────────────────────────────────────────────────────┘
                                    │ knowledge corpus (JSON + Markdown, provider-agnostic)
                                    ▼
┌─────────────── Stage 2: scenario design (a Claude Code session) ───────────────────────┐
│ reads the corpus → designs apps/activity/overlays → authors target-environment IR        │
│                 → gated through axrange validate → plan → deploy, same as any scenario    │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

## Why the corpus is a firewall

Every piece of content Stage 2 produces still has to pass the validator before it is deployable
— the trust boundary the whole platform relies on is unchanged (see
[03-engine.md](./03-engine.md)). Nothing a local model summarizes reaches a live cluster
ungated: the corpus only ever informs design; it never substitutes for the validate/plan gate.
This is also why Stage 1 stays deliberately dumb and mechanical — it does not need to be trusted
with correctness, only with faithfully capturing and attributing what a source said.

## What Stage 1 produces

Each corpus record carries the fields a Claude Code session needs to use it responsibly:

- identifying/provenance fields (source, transcript path taken, the model and version that
  produced the summary, a run timestamp);
- a summary and a list of key points;
- extracted entities — tools, techniques, defensive controls, and scenario patterns mentioned;
- a consolidated index ranking and cross-referencing every record, meant to be the first thing a
  Stage-2 session reads.

The corpus format has no axRange-specific schema baked into it and no dependency on any
particular model provider — it is plain JSON and Markdown, so the mapping from "what a video
said" to "what axRange builds" happens entirely in Stage 2, not in the corpus format itself.

**A practical caveat, not a design gap:** bulk video download runs against YouTube's terms of
service; the captions-first default exists specifically to minimize that (an `--captions-only`
mode avoids downloading media at all). Stage 1 is a local, operator-run research tool, not a
hosted service — the operator controls how it's used, and the choice is deliberate rather than
overlooked. Heavy dependencies (`yt-dlp`, `faster-whisper`, an Ollama client) are an optional
install extra, never part of axRange's core dependency set, and every backend behind the
`Summarizer`/transcript interfaces is swappable and fake-tested so the pipeline's unit tests never
need network access or a real model.

## The coupling rule

Coupling between the two stages — and between this subsystem and the engine — runs in one
direction only:

> `scenario_ai_research/` (and the `target-environments/` Scenario IRs it produces) may import
> axRange's validator, planner, and op catalog, the same way the CLI does. The engine
> (`axrange/`) must never import anything from `scenario_ai_research/`.

This keeps the engine standalone regardless of how the research/design side evolves, and it is
the same "no leaking platform details up the stack" discipline the rest of the codebase follows
(see [03-engine.md](./03-engine.md)).

## What Stage 2 produces

The `target-environments/` library — the deployable Scenario IR archetypes covering domains like
SOC detection, AD-domain attack/defense, CTF-style ranges, ICS/OT, DFIR hunting, vulnerability
management, and purple-team exercises — is Stage 2's output. Each archetype is ordinary Scenario
IR: it validates, plans, and deploys through the same pipeline as any hand-authored scenario, and
it carries the same layered content and overlay structure described in
[09-content-overlays.md](./09-content-overlays.md). Stage 2 is where a corpus entity like "this
technique, this tool, this detection pattern" becomes a concrete node, activity step, or overlay
entry in a real IR.

## The AI core: five roles this feeds toward

Beyond scenario design, the platform's intended AI core is five roles, each with a different job,
different inputs, and a different output:

| Role | Purpose | Inputs | Outputs |
|---|---|---|---|
| Scenario planner | Generate scenarios from a natural-language brief | A brief, plus the knowledge corpus | Scenario IR |
| Red-team agent | Adaptive attack execution | The twin's IR, current state, ATT&CK context | Attack actions and observations |
| Blue-team agent | Defense synthesis | Telemetry, defense-overlay state | Detection rules and configuration changes |
| Telemetry analyst | Hunt and summarize | Captured telemetry, alerts | Timelines, anomaly reports, after-action reports |
| Traffic generator | Benign baseline activity | The twin's IR, its host roles | Realistic background traffic so an attack has somewhere to hide |

Two of these roles are realized today by **non-AI mechanisms** that do the same job by design
rather than by automation, with AI-driven automation as the future layer on top of them:

- **Scenario planner** → this subsystem (Stage 1 corpus + Stage 2 Claude-Code design), producing
  the `target-environments/` archetypes.
- **Traffic generator** → the persona library and Traffic tier
  (see [07-traffic-personas.md](./07-traffic-personas.md)), which populates a deployed twin with
  realistic background behavior without any AI in the loop.

The remaining roles map onto components that exist as **operator-driven** mechanisms today, with
AI-driven playout as the direction they are heading:

- **Red-team / Blue-team** → the Caldera attack server and the Wazuh detection/response manager
  standing in the O2 surface, driven by an operator through the Exercise tier
  (see [05-o2-surface.md](./05-o2-surface.md), [08-exercise-measurement.md](./08-exercise-measurement.md)).
- **Telemetry analyst** → the ATT&CK-technique coverage measurement built on top of O2's telemetry
  stores (see [08-exercise-measurement.md](./08-exercise-measurement.md)).

## Where this fits

- [03-engine.md](./03-engine.md) — the validate → plan → deploy pipeline every Stage-2 IR is
  gated through, and the "no leaking platform details up the stack" discipline the coupling rule
  follows.
- [09-content-overlays.md](./09-content-overlays.md) — the layered content and overlay model
  Stage-2 archetypes are built from, and the archetype library this subsystem populates.
- [07-traffic-personas.md](./07-traffic-personas.md) and
  [08-exercise-measurement.md](./08-exercise-measurement.md) — the traffic-generator and red/blue/
  telemetry-analyst roles as they exist today, ahead of full AI-driven automation.
- [11-api-ui.md](./11-api-ui.md) — the control-plane API these AI-core roles will eventually drive
  programmatically, the same way the console drives it today.

For the AI-core role sketch this subsystem feeds toward, see `docs/ARCHITECTURE.md` §6; for the
design reasoning trail, see `docs/CHANGELOG.md` and `docs/design/README.md`'s retired-brief
mapping table.
