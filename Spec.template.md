# <feature or plugin name>

> Copy this file to `SPEC.md` beside the code it describes (e.g.
> `engine/axrange/adapter_vmware/SPEC.md`, `ui/src/features/edit-canvas/SPEC.md`).
> Keep it short and about the **present** — git history is the record of change,
> not this file. Delete this quote block.

## What it is

One or two sentences. Which step of the loop (see root `CLAUDE.md`) it serves.
If it serves none, it should not be getting built.

## Seam

Which registry/contract it plugs into, and the type it implements — e.g.
`IngestPlugin`, `Adapter`, sensor/persona/attack registry entry. State the
methods it must provide and how it registers.

## Config

The plugin's own configuration schema (keys, types, defaults). Where config is
read from. Fail-loud on an unrecognized shape — no silent defaults.

## Acceptance

How this feature is validated — the criteria, not the history. Two kinds:

- **Automated:** the tests beside this code that assert the criteria (name them).
  These run in the normal `pytest` gate.
- **Verifier (if it needs live/hardware/eyes):** the check this plugin registers
  (`verify()` / a `verify` command) that the runner executes, OR a short numbered
  manual checklist here with expected observations.

If the capture-flow gate in root `CLAUDE.md` is sufficient, say so and stop.

## Notes

Known limits, and anything a future editor must not break. No changelog here.
