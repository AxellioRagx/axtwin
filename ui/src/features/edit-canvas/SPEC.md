# edit-canvas — the twin design canvas

## What it is

The network-design surface of the console: a pan/zoom canvas on which a twin
is drawn as segments and the hosts and routers attached to them, first to
**view** a discovered or authored twin (C2) and then to **edit** it (C3) —
place hosts on segments, add segments, duplicate sections, annotate — writing
the result back as a twin-config. It serves the **view & customize** step of
the loop.

It is a canvas over axRange's own model, not an emulator's. A twin has no link
objects: a node attaches to one or more L2 segments (`net` / `nets[]`), and
the API already serves that as a bipartite host↔segment graph. The editing
gesture is therefore "drop a host onto a segment", never "wire interface to
interface". Exporters to emulator formats (containerlab, GNS3, CORE) are a
separate, later feature over the expanded IR and are not this canvas.

## Seam

A consumer of the control-plane API only (`ui/Spec.md`); it defines no
endpoint and holds no orchestration logic. Mounted as the `features/edit-canvas`
route inside the twins area.

| Reads | Route |
|---|---|
| authored twin graph | `GET /api/scenarios/{id}/topology` (`TopologyDTO`: nodes typed `host\|router\|switch\|network`, edges host→network with `ip`) |
| cast twin graph with observed evidence | `GET /api/twins/{id}/topology` (`TopologyExportDTO`: weighted `src/dst/proto/bytes/flows/evidence_class` edges, census) |
| palette | `GET /api/twin-configs` images and node groups, `GET /api/personas` |
| positions | the layout document (`axr-layout`), extended with `host:<name>` pins beside the existing `site:` and `segment:` pins |

| Writes | Route |
|---|---|
| undeployed config: Save / Save-As | A3 `POST /api/twin-configs`, `PUT /api/twin-configs/{id}` |
| deployed twin: working copy, Apply / Reset / Persist | A2 `GET\|PUT /api/deploys/{id}/config`, `config/apply`, `config/reset` |
| every mutation, before Apply or Save is enabled | `POST /api/validate` |
| positions and annotations | `PUT` of the layout document, ETag-guarded |

The twin config never carries presentation. Positions, free text and shapes
live only in the layout side-car (the pattern containerlab's TopoViewer uses
with its annotations file); shapes are stored as raw positioned SVG snippets
there, the way GNS3 stores drawings.

**Stack:** `@xyflow/react` (React Flow, MIT) is the single dependency added to
`ui/`; it supplies pan/zoom, selection, drag, connection handles, a minimap
and DOM-rendered custom nodes. Segments render as compound (group) nodes,
hosts as chips with role badge and evidence ring, routers as nodes spanning
the segments they join, observed traffic as arcs with `log(bytes)` width. The
frozen console's pure `o2/console/src/layout/` and `model/` modules are lifted
for deterministic initial placement and the evidence/zoom model; its `views/`
and `edit/` (which edit imprint assertions, not the twin config) are not.
React Flow ships neither auto-layout nor undo/redo: initial layout comes from
the lifted tiered engine, undo/redo from a command stack over the working copy.

**Modes.** Edit mode when the twin is undeployed or the working copy is open;
view mode, canvas locked except annotations, while its deploy is running —
the same two-mode discipline CORE's GUI uses.

**Two kinds of edit, classified by the API, shown by the canvas.** An
attribute edit (persona, intensity, schedule, cadence, tap binding, a role
change that keeps the image) applies live through the tier's playout; a
topology edit (segment, node, attachment, count, image) requires a re-plan.
The canvas does not decide this: it reads the per-change classification A2
reports and shows each pending edit's blast radius — *live* or *re-plan* —
before Apply, and Apply is enabled only when every pending change is live.
A working copy with a topology change offers Persist / Save-As and a
"redeploy from baseline" action instead.

**Two layers, one rule.** The canvas edits the twin-config (the cast). When
an operator is correcting discovery rather than designing — a host's role,
two segments that are one VLAN — the edit is offered a **promote to
assertion** action that writes it through the imprint's assertions seam,
re-casts, and re-opens the working copy; the canvas never writes assertions
directly and never edits the imprint in place. A twin designed from scratch
starts from an empty twin-config and has no imprint and no promote action.

## Config

None of its own. The telemetry base URL and everything else come from
`ui/Spec.md`'s single served config; the canvas reads what the API enumerates
and names no image, persona or platform in code.

## Acceptance

`ui/Spec.md`'s C2 and C3 tests are the acceptance; this feature adds no
criteria of its own beyond them.

- **Automated (Vitest, the node gate):** C2 — the authored and cast graphs
  render with every segment, host and router present and positions stable
  across two renders of the same layout document. C3.1–C3.6 as written in
  `ui/Spec.md`: add node and segment, duplicate a section onto a segment with
  segment-appropriate addressing (the lifted `allocate.ts`), Save-As from both
  states, invalid edits blocked with the validator's reason, dirty marker on a
  deployed twin, Reset to baseline across two open consoles. Undo returns the
  working copy byte-equivalent to the pre-edit document. A persona change on
  a deployed twin shows *live* and Apply is enabled; adding a segment shows
  *re-plan* and Apply is disabled while Persist and Save-As stay enabled;
  promote-to-assertion on a cast twin re-opens a working copy whose
  baseline carries the change.
- **Live (eyes):** the largest cast twin in the catalog renders and pans at
  interactive rate; the element budgets below are measured, not assumed.

## Notes

- Prerequisites are the A3 then A2 seams and the `host:` pin kind in the
  layout document. A canvas with nothing to save into repeats the frozen
  console's fate; read-only C2 may land first, editing waits for A3.
- Element budgets: the frozen console's tile/band/matrix caps stay as the
  guard against DOM-node blow-up; beyond them the observed census falls back
  to the matrix view, not to a denser canvas.
- Do not import emulator UI code: EVE-NG's UI is proprietary, GNS3's is
  GPLv3; both are incompatible with this repository's license. Their UX ideas
  (palette, interface picker, drawings, edit/run lock) are reimplemented, not
  copied.
- The old console's edit path writes imprint assertions; this feature writes
  twin-configs. Keep the two apart.
