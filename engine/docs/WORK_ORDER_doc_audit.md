# Work Order — audit & refresh all `docs/*.md` to the current design

> **For a subagent Pipeline session (`/dev`), not the design session.** Authority: user
> (2026-07-10). Route: Pipeline mode (`docs/WORKFLOW.md`) — doc-writer-heavy (+ `doc-stats`);
> code-reviewer for cross-ref integrity. **Genre-aware, context-sensitive — NOT a blind sweep.**
> Template: `docs/WORK_ORDER_sink_rename.md` / `WORK_ORDER_resource_tags.md`. **Executed** as the
> 2026-07-10 doc audit this order describes; superseded as a genre reference by the 2026-07-27
> `docs/design/` consolidation (`docs/design/README.md`) — the design-brief genre and files this
> order's §2/§4 refer to no longer exist, and its own paths below are updated to their current
> `docs/design/` home rather than left to rot.

## 1. Goal

The design has moved (O2 redesign, tier tags, sink→capture, standing sensors, target-environments/,
scenario_ai_research, overlays +adversary, activity schedule). Several `docs/*.md` still reflect the
old design. Audit **all 40 `docs/*.md`**, update the stale ones to the current design, and produce a
**per-doc audit table** (verdict for every file) so nothing is silently skipped.

## 2. The authoritative "current design" reference set

Align docs to these (they are current — do not "correct" them):
`docs/ARCHITECTURE.md` §0 (the current top-level frame) · `docs/CONVENTIONS.md` (names, tier tags,
doc genres, scenario-vs-TE) · `docs/design/05-o2-surface.md` (the O2 surface — this work order's
original O2 design-brief reference set is retired and folded in here) · `docs/SLICE_ACTIVITY.md`
§11 · `CLAUDE.md`. When a doc disagrees with these, the doc is stale.

## 3. Drift checklist (what to look for — grounded in a 2026-07-10 probe)

1. **Retired `axr` resource prefix / `is_axr_*`** (32 docs) → tier tags: `te<6hex>` zone · `tev<5hex>`
   vnet · `tebr_*` bridge · `te-<6hex>-<host>` VM · `tapg_<6hex>` gretap · `tapm-<vmid>` mirror ·
   `o2*` · `img-<slug>`; predicates `is_te_*`/`is_tap_*`/`is_o2_*`/`is_img_*` (`docs/CONVENTIONS.md` §2).
2. **`sink` / `sidecar`** — retired (sink→capture/sensor/tcpdump-sensor; sidecar→network-sensor/
   research). The dedicated work orders swept these — **verify no residue** re-introduced.
3. **Paths:** `observability/`→`o2/` · `sink/`→`o2/tcpdump-sensor/` · `docs/sink/`→`docs/tcpdump-sensor/`
   · `scenarios/`→`target-environments/` (4 docs) · `aiscenario/`→`scenario_ai_research/` ·
   the former ai-scenario "sidecar"-named design brief → `_research.md` (itself now retired and
   folded into `docs/design/12-ai-scenario-research.md`) · `SIDECAR_HANDOFF.md`→`RESEARCH_HANDOFF.md`.
4. **In-scenario sensor model** (6 docs) → **standing `o2_` sensors** (`docs/ARCHITECTURE.md` §0;
   `docs/design/05-o2-surface.md`). Where a doc describes the superseded model, **add a
   current-state pointer** (do not erase the trail).
5. **Overlays list** → `identity + data + **defense + adversary**` (many docs list only identity/data/
   defense). Defense/adversary declare host agents pairing with O2 Wazuh/Caldera servers (A3).
6. **Attack/defense model** → A3 (servers in O2, agents in substrate, dual-mode attack, manual playout,
   A4 coverage). Briefs assuming "emit into the deploy DAG" get a pointer to A3 (as the overlay briefs did).
7. **Stale counters** (doc-stats polices some, NOT all — e.g. "100 pytest tests", "53 rules" survive):
   reconcile op-type count (37), catalog-rule count (98), pytest count (current baseline), per-category
   validator rule counts against source. Run `scripts/doc-stats.py --check`; fix un-policed numbers by hand.
8. **Stale "not built / deferred" claims** for shipped work: tier tags, sink rename, `axrange destroy`,
   standing/multi-sensor, router+fabric slices, etc. — mark them shipped.
9. **Cross-ref integrity:** every `docs/X.md` / path / filename reference must resolve to an existing
   file/dir (renamed briefs, moved dirs).

## 4. Genre-aware rules (per `docs/CONVENTIONS.md` §3)

| Genre | Files | Rule |
|---|---|---|
| **Formal spec** | `ADAPTER`, `IMAGE_BUILD`, `IMAGE_MANIFEST`, `IR_SCHEMA`, `OP_CATALOG`, `PLANNER`, `VALIDATOR`, `ARCHITECTURE` (done) | Update stale **prose + counters + retired terms**. **NO** schema-field / op-signature / rule-catalog *content* changes (version-locked → separate effort). Prose-only. |
| **Design of record** *(was: design brief, retired 2026-07-27)* | `docs/design/*.md` | Current at the time this order ran: fix terms/paths only. As of 2026-07-27 this genre is `docs/design/*.md` — the per-topic brief files this row originally named are deleted; see `docs/design/README.md`'s mapping table. |
| **Slice** | `SLICE_*` | Update to current state (retired terms, standing sensors, tiers, overlays). |
| **Living register** | `CHANGELOG` (**never rewrite history**), `LESSONS` (append-only; only fix Scope-line paths that moved), `OPEN_QUESTIONS` / `PRODUCT_GAPS` (mark resolved items), `WORKFLOW` (check) | Per each register's own rule. |
| **Work order** | `WORK_ORDER_*` (incl. this one) | Leave; their DONE/OPEN status is intentional. Permanent grep carve-outs (they name retired terms by design). |
| **Not tracked** | `SESSION_STATE.md` | gitignored — skip. |

## 5. Method & deliverable

Per doc: read against §2, apply §3 through the §4 genre rule, and record a **verdict**. Deliverable =
an **audit table** (in the CHANGELOG entry or a scratch note) with one row per `docs/*.md`:
`CURRENT` (no change) · `UPDATED` (stale content fixed) · `POINTER-ADDED` (superseded brief, trail kept)
· `HISTORY-PRESERVED` (register, untouched). No file silently skipped.

## 6. Validation

- `python scripts/doc-stats.py --check` clean.
- **Grep gates** (outside `docs/CHANGELOG.md` history + the `WORK_ORDER_*` carve-outs + intentional
  `**Supersedes:**`/pointer notes): no live `axr<hex>` / `is_axr_` resource refs; no `sink` / `sidecar`;
  no `scenarios/` path / `aiscenario`.
- **Cross-ref integrity:** every referenced `docs/*.md` / path resolves (a small link-check script or grep).
- Counters reconcile to source.

## 7. Coordination / out of scope

- **`OP_CATALOG.md` is being actively edited** (a `.swp` was present 2026-07-10) — coordinate / re-check
  before editing it.
- **No schema/op-signature/rule-content changes** — those are version-locked, separate efforts.
- **Do not erase the design trail** in superseded briefs — add pointers.
- CHANGELOG **history** is never rewritten; each batch lands its own CHANGELOG entry.
- Suggested batching (own call): (a) formal specs, (b) design briefs, (c) slices, (d) registers.
