# WORK ORDER — Console: empty scenario selector, and stale deploys in the Topology view

> **STATUS: DONE — commit `fa0520d4` (2026-09-02).** Code was written and verified against a live
> API the same day it was reported (2026-08-27, two CHANGELOG entries dated that day — "Console
> selector unstuck..." and "Logical/Physical semantics restored...") but sat uncommitted for a
> week; `fa0520d4` commits it, and also completes S3/S9 (the two "Not done this pass" items the
> second of those entries recorded), per the correction added there 2026-09-03. See
> `docs/LESSONS.md` LES-197 for the staging-discipline finding this delay surfaced.
> **Genre:** work order (`docs/CONVENTIONS.md` §3). **Authority:** user, 2026-08-27 — found while
> using the Grafana control panel against a live twin.
> **Branch:** `wip/persona-fidelity` (current) or a fresh `wip/console-fixes`.

## Symptom, as reported

> "the topology & twin views in both expect a scenario to be specified — which looks to be a
> hardcoded path where nothing is listed. Also, Topology tab shows stale deployed twins, maybe
> due to stale stateDb entries."

## What was measured, before any fix

```
GET /api/scenarios   -> {"scenarios": []}
GET /api/deploys     -> 43 deploys: 40 destroyed, 2 completed, 1 failed
```

Both reproduce against `axrange serve --adapter-config adapter.conf --images deploy-dev45`
with a live twin (`corpapcn-twin-580f4c94`) standing.

## Defect 1 — the selector can never list what this platform now deploys

Two independent causes; fix both, and do not conflate them.

**1a (operator error, no code change).** `serve` was launched with no `--scenarios-dir`, so the
listing had nothing to read. There are 10 scenario-shaped entries under `target-environments/`
(`<id>/scenario.yaml`), and pointing at it populates the list. Worth a doc line, not a patch.

**1b (real gap, and the one that matters).** `/api/scenarios` resolves ONLY
`<scenarios_dir>/<id>.yaml` or `<scenarios_dir>/<id>/scenario.yaml` (`api/settings.py`), and there
is **no twin-config route in the API at all** — `grep '"/api/...' api/routes.py` returns nothing
for twin configs. Yet every twin this project now deploys comes from a **twin config**
(`corp-enterprise`, `corpapcn-twin`, the five shipped archetypes), which the CLI has had since
`te deploy --config` landed. The Console therefore asks the operator to pick from a list that
structurally cannot contain the thing they deployed.

**Wanted.** A twin-config listing the panel can select from, alongside scenarios — not instead of
them. `axrange twin-config list|show` already exists in the CLI and `axrange.twinconfig` already
has the registry; this is an API surface over machinery that exists, not new discovery logic.
Mind `--twin-configs-dir`: cast output lives in `twin-configs-cast/`, deliberately outside the
shipped catalog, so the route must honour the configured dir(s) rather than hardcoding one.

## Defect 2 — `/api/deploys` returns destroyed deploys with no filter

40 of 43 rows are `destroyed`. Those are torn-down twins whose resources no longer exist; showing
them in a Topology view is not a stale *cache*, it is the API faithfully returning rows the caller
never wanted. The state store is correct — nothing to purge.

**Wanted.** A status filter on the endpoint (`?status=`, repeatable, or an `active=true` default
that excludes terminal states), with the DEFAULT chosen so an unfiltered Console shows only live
deploys. Keep destroyed rows reachable — deploy history is real and `list-deploys` shows it — but
they must not be the default answer to "what is deployed".

Decide deliberately and record it: is the default `active`, or does the panel filter? Prefer the
API, so every consumer inherits it rather than each re-implementing the same predicate.

## Done when

1. `GET /api/scenarios` (or a sibling twin-config route) lists the five shipped twin configs plus
   anything under a configured extra dir, against a `serve` started with the project's own dirs.
2. `GET /api/deploys` returns only non-terminal deploys by default; destroyed ones remain
   reachable through an explicit filter.
3. The two Grafana dashboards at `/d/axr-topology` and `/d/axr-imprints` populate their selector
   and show only the standing twin.
4. Unit tests for both endpoints, including the "destroyed is excluded by default" case, which is
   the one a future refactor will silently break.
5. `docs/CHANGELOG.md` entry, Authority-marked.

## Explicitly out of scope

Do not touch the deployed twin, the tap, or the traffic profile — a separate fidelity experiment
is running against them concurrently (`aximprint docs/JOURNAL_corpapcn_fidelity.md`). API and
plugin only.

---

## SCOPE EXPANSION — 2026-08-27, Authority: user

> "The topology view doesn't show anything in it. I was expecting it to show the logical, physical
> & deployed views for a given twin — be it a discovered and yet to cast IR or te config or
> deployed te." … "refer to `/home/rocky/apcn/topology` for the topology views I'm talking about.
> Expecting something similar."

Fixing the selector only lets an operator *pick* something; it does not make the panel *draw* it.

### The reference: `/home/rocky/apcn/topology`

A working, self-contained interactive topology built from a real capture. **Read
`GENERATOR_SPEC.md` and `README.md` there before designing anything.** What to take from it:

- **Overview (logical).** Hierarchical tiered diagram — Internet on top, the tap as a badge on the
  trunk it actually monitors, WAN edge/NAT, inferred infrastructure chips, then segments grouped
  by /16 + VLAN. **East-west segment-to-segment traffic is drawn as arcs in its own band below the
  segments, so it never crosses the north-south story.** Toggles for DNS overlay, tunnel overlay,
  volume labels, minor links.
- **Physical view** (`#/physical`). *What each address is plugged into*, rather than who it talked
  to: gateway chassis tiered edge → core → distribution, the broadcast domains they serve, NIC
  vendor census. For axRange the equivalents are concrete rather than inferred — vmid, PVE node,
  vnet/bridge, NIC index — all already published by `/api/deploys/{id}/inventory`.
- **Zone drill-down.** Click a segment → a crossing-free sankey: hosts left, segment spine, peer
  zones right, resolvers on top. Peer zones clickable to move sideways.
- **Addressable routes** — `#/`, `#/zone/<cidr>`, `#/physical` — so back/forward work and a view
  can be linked to.

### The architectural lesson, which matters more than the visuals

That page has **one JSON model and one renderer**. The renderer reads a single blob and nothing
else; the pipeline that produces the blob is swappable. Do the same here: define **one topology
model DTO** that all three sources can populate, and have the panel render that single shape.
Without this the panel grows three code paths that drift.

| source | route | can populate |
|---|---|---|
| discovered IR (not cast) | `/api/imprints/{id}` | logical only |
| te config (not deployed) | **no route — defect 1 above** | logical only |
| deployed te | `/api/deploys/{id}/inventory` | logical + physical + deployed |

`/api/scenarios/{id}/topology` already exists — check whether it already emits most of the logical
shape and is merely unreachable from the panel, before writing anything new.

### Constraints

1. **Say why a view is empty.** A blank canvas is indistinguishable from a broken panel — that is
   exactly how this bug presented. "Physical view needs a deployed twin; this is a discovered IR"
   is the required behaviour, not an empty draw.
2. **Never invent physical facts** for a discovered IR or an undeployed config. This project's
   whole provenance discipline is not presenting inference as observation.
3. Reuse published endpoints; do not add a parallel data path.
4. The reference ships an **audit** (`renderer/audit_jsdom.js`) checking routes, geometry *and
   completeness*, exiting non-zero. Something equivalent for the panel is expected — a render that
   silently draws nothing is the failure mode under repair.

### Done when (supersedes the earlier list, which it contains)

1. Selector lists all three source kinds; both dashboards populate.
2. `/api/deploys` excludes terminal deploys by default.
3. Any source renders a **logical** view; a deployed twin also renders **physical** and
   **deployed**.
4. A view that cannot apply says so in words.
5. Tests per endpoint, including empty-by-default and not-applicable, plus a render check that
   fails on a silently-blank canvas.
6. CHANGELOG entry, Authority-marked.
