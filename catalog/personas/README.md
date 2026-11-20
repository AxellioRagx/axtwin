# personas — the shipped persona library

One file per persona, `personas/<id>.yaml`, loaded by
`axrange.personas.registry` (`*.yaml` only; this README is not read). A
persona says WHAT traffic a role emits (`emits[]`) and HOW it is driven
(`drive`); the schema is `engine/src/axrange/personas/schema.py`, the
semantic rules are the validator's PSN-0xx catalog. `AXRANGE_PERSONAS_DIR`
adds extra directories; a twin's own `personas/` folder overlays these.

## Display metadata (optional, 2026-11-20)

Three optional keys the canvas's Add panel shows in its role picker
(`docs/design/canvas-add-panel.md` §4.2, D1). The engine does not branch on
them; a file without them loads and drives exactly as before.

| Key | What | When absent |
|---|---|---|
| `category` | the role group: `identity`, `business`, `network`, `endpoint`, `ot`, `traffic`, `other` | `traffic` for an id starting `loadgen-` or `xseg-`, else `other` |
| `label` | the human name ("File server") | the id |
| `blurb` | one plain sentence on what it does | the canvas builds one from `emits[]` |

`GET /api/personas` serves all three with the fallback applied. The canvas
picks the card from the category (`endpoint` → Workstation, `ot` → OT device,
the rest → Server) and filters by `os_family`; `traffic` personas appear only
under the popover's Advanced.

```yaml
persona:
  id: file-server
  category: business
  label: "File server"
  blurb: "Serves SMB/NFS shares to workstations; bursty in business hours."
  os_family: linux
  ...
```
