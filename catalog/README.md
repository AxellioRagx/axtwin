# catalog — twin and persona content

Every twin config the instrument can deploy, in one place, out of the package
tree. Code reads these through `axrange.paths` (unset-default) or an explicit
`--twins-dir` / `AXRANGE_TWINS_DIR`.

| Folder | Format | Status |
|---|---|---|
| `twins/` | `twins/<id>/twin.yaml` bundle + `te.yaml`, `tap/`, `tfc/`, `exr/` | **live catalog** — hand-authored twins and aximprint casts share it; `_shared/` is never a twin |
| `twin-configs/` | legacy `twin-configs/<id>/<id>.yaml` (+ tap/tfc overlays) | compat reader only: searched, never written (`archive/engine-docs/WORK_ORDER_twin_consolidation.md`) |
| `target-environments/` | `target-environments/<id>/scenario.yaml` (raw IR) | scenario library; corpus-derived samples (see its README) |
| `demo/` | single-file te / tap / tfc / surface samples for `web-lab` | hand-authored demo set |
| `personas/` | `personas/<id>.yaml`, one traffic persona each | the shipped persona library (`axrange.personas.registry`); `AXRANGE_PERSONAS_DIR` adds extra dirs; see its README |
| `image-profiles.yaml` | `profiles: {<name>: {image}}` | the image the canvas's Add panel writes from card + OS (`docs/design/canvas-add-panel.md` §4.3); served read-only by `GET /api/image-profiles` |

First target for the loop: `twins/corp-enterprise/` (its README has the sequence).
