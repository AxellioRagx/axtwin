# WORK_ORDER_corp_twin — built record (archive)

Moved here **verbatim** from [`docs/WORK_ORDER_corp_twin.md`](../../WORK_ORDER_corp_twin.md) §10 on
2026-09-28, per the work-order size rule in `docs/CONVENTIONS.md` §3 (WO over its 40 KB warn
budget, `scripts/doc-budget.py`). Only the **finished** S0/S1 slice records move here — the order is
not fully built (S2's live-verify is not run, WO §12 D1 is still open, and P0–P2/D's own
review-round records stay in the live file, which is still the active spec for the remaining VyOS
path and the live-verify runbook). Headings are unchanged, so a citation like "§10 S0" resolves
here.

<!-- original lines 310-324 of docs/WORK_ORDER_corp_twin.md, under its "## 10. Slices" heading -->

- **S0 — loader (BUILT).** `axrange/twinconfig/manifest.py` (`twin.yaml` model + validation +
  tier-file resolution); `registry.py` resolves `<root>/<id>/twin.yaml → tiers.te.default`, then
  `<id>/te.yaml`, ahead of the old `<id>/<id>.yaml`/`<id>.yaml`; the default catalog search path is
  `twin-configs/` **then** `twins/` (later wins an id collision); `tap|tfc validate|plan|deploy`
  accept `--twin ID [--variant NAME] [--twins-dir DIR]` instead of the positional, and any tier file
  under a `twins/<id>/` folder gets that twin's `personas/` on the persona path (the env var
  still overrides) and defaults `--target` to the twin id when the file declares none.
  *Acceptance:* `tests/test_twin_manifest.py` green; `te validate --config corp-twin` finds
  `twins/corp-twin/te.yaml`; every old `--config` id still resolves. The tap positional's
  "required" moved from argparse to dispatch (exit 2, "a tap file path is required (or --twin)";
  `tests/test_cli_lifecycle.py` updated).
- **S1 — the twin (BUILT).** `twins/corp-twin/{twin.yaml, te.yaml, tap/, tfc/, personas/,
  scripts/, generated/}`. *Acceptance:* `te validate` + `te plan --config corp-twin --images
  deploy-dev45` clean; `tap validate` / `tfc validate` static-clean on every variant.
