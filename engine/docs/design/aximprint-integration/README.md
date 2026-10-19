# aximprint ↔ axRange integration — design canvas + plan

Design-of-record for wiring **aximprint** (learns a network's imprint from sensor metadata and casts
it into deployable twin config) into the **axRange** twin pipeline — "the missing front door."

- **Canvas (Artifact):** https://claude.ai/code/artifact/761461cf-2300-4097-b6bc-843391a84c12
  — three pages: **Architecture** (the closed loop + per-seam contracts), **Console** (the operator
  deploy screen), **Rollout** (the three-track parallelization plan).
- **Plan:** [`WORK_ORDERS.md`](WORK_ORDERS.md) — the cross-repo ticket set (§0 schema pin + Tracks
  A/B/C + the Phase-3 tail). The routable axRange ticket is `docs/WORK_ORDER_aximprint_console.md`
  (Track C).

## Artboard sources

`Main.dc.html` · `Contracts.dc.html` · `Console.dc.html` · `Rollout.dc.html` + `canvas.json`.
These are the canvas seed sources — edit them and re-seed to update the Artifact. The generated
`aximprint-axrange-integration.html` (~2.5 MB, the published editor payload) is a byproduct and is
gitignored.
