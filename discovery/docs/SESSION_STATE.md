# Session State

<!-- Cross-session handoff. COMMITTED; update at wrap (doc-writer owns it in Pipeline mode). KEEP UNDER
  120 LINES — SessionStart truncates past CLAUDE_HANDOFF_CONTEXT_LINES (120) and the gotchas and
  environment sections, at the end, are what silently fall off. -->

**Last updated:** 2026-09-05 (a correction pass on a 2026-09-03 entry — see the block directly
below; the narrative under "What this session did" is still iteration 19's and is left as written).
Branch `wip/persona-fidelity` (contains `wip/axrange-integration` in
full — `git merge-base` against `master` is `master`'s own tip, `585bd71`). This entry replaces the
2026-08-27 `wip/axrange-integration` narrative and the 2026-09-02 stopgap pointing at axRange's
`SESSION_STATE.md` — both are superseded history now folded into `docs/CHANGELOG.md`; this file
describes the CURRENT thread. **The CorpAPCN fidelity loop CLOSED at iteration 19**
(`docs/JOURNAL_corpapcn_fidelity.md`) — its convergence **objective** is retired
(`docs/CHANGELOG.md` 2026-09-03), its measurements are not.

## Since this entry was written (2026-09-04/05) — read this before the plan below

`wip/golden-twin` is open in both repos and **S0, S1 and S2 of
`docs/WORK_ORDER_reference_ingest.md` have landed** — the evidence model + precedence resolver, and
the operator assertions channel (`axr-assertions/1`, `cast --assertions`, a reference-ingest plugin
kind). **S3-S5 remain open.** Two claims below are superseded, and corrected here rather than
deleted because both were load-bearing: reference ingest does **not** land under `referenced` (that
means second-hand evidence *from inside the capture*, weaker than `observed`) — `EVIDENCE_CLASSES`
is five in **precedence order**, `asserted` > `imported` > `observed` > `referenced` > `imputed`
(IR `1.4`); and the work order is written, so "do not write it pre-emptively" is spent.

## What this session did

Ran iteration 19 (`docs/WORK_ORDER_proto_weight_rows.md`, STATUS now DONE; ruled 2026-09-02,
Authority: user): **D1 = (a)+(b), D2 = no seam.** **(a)** `proto_weights`
(`src/aximprint/synth/personas.py:406`) now ranks emit ports by edge ROWS, not `ir_edge.flows` —
the two columns order the same six groups oppositely (DNS wins on flows every time, 443 wins on
rows in five of six). **(b)** `roles.yaml`'s `web-tier` (443/tcp) row now precedes `dns-resolver`
(53/udp) on their specificity tie, so the twin's internet sink infers `web-tier` instead of
`dns-resolver`. **Option (c)** (require `dns-resolver` to match `53/udp` AND `53/tcp`) was built,
measured against the real source, and REJECTED: 13 of the source's 15 `dns-resolver` hosts are
UDP-only, and 8 of the twin's 15 sink instances would still read `dns-resolver` even under it — the
work order's own "16 of 18" estimate did not reproduce (closest: 15/16 hosts, 107/114
entity-windows). Also rewrote `scripts/check_role_fixed_point.py` so a could-not-run path (no
`ruamel` in this venv) exits **2**, distinct from **0** (clean) and **1** (a finding) — it used to
`skip:`-and-exit-**0**, a vacuous green that made the loop's own fixed-point criterion satisfiable
by a check that had never actually run. Suite 1872 -> **1884** (+12). Fixed an unrelated
`verify_tier.sh` red (`ruff`, import order in `acquire/cli.py` — landed as a separate chore commit).

## The re-score, the live run, and the verdict

Full numbers in `docs/CHANGELOG.md` 2026-09-03; the short version: a re-score projected
`role_edges` 0.878 -> **0.411** and the live run measured **0.406**, so the **target was hit and it
cost a trade** — `role_edges_local` 0.533 -> **0.593** and `external_dst_edge_share` 0.8641 ->
**0.8153**, moving away from the source's 0.8476. A resolver-census check found only 1 of the twin's
4 real resolvers kept `dns-resolver` (the rest fell to `generic-endpoint`, matched on dial rather
than listen evidence) — a labelled HYPOTHESIS, not a measured cause; query pointer
`ir_entity where role='dns-resolver'` in `iter19/twin-ir-iter19.duckdb`.

## The direction decision (Authority: user, 2026-09-03) — read before touching the loop again

**The loop's OBJECTIVE is retired; its measurements are not.** Six iterations (14-19) moved the
deciding axis, `role_edges_local`, through a 0.533-0.742 band with no trend. Root cause: **12 of the
source's 18 local role-edge keys are structurally absent from the twin** (the Windows DC answers
nothing; the source carries no workstation-to-workstation traffic), so every re-weighting of the six
keys that DO exist trades one axis against another — this iteration is the sixth instance of exactly
that. Also: shape distance is not the platform's actual goal and only loosely proxies what the goal
(attack/defense experiments against the twin) needs — real protocols, real identity, matching
security posture, matching configuration. **Replacement: fidelity to a closed ten-dimension spec
("golden twin")** — asset / topology / configuration / state / protocol / performance / security /
dependency / temporal / behavioral (+ a visual row, for operator trust) — each tagged by observation
channel and evidence class, the behavioral row being this loop's fingerprint, kept, as a TOLERANCE
rather than a convergence target. The operational half (configuration, state, posture) becomes a
second **"reference ingest"** evidence channel (directory export first) under the existing
`referenced` evidence class, feeding the existing `ir_identity`/`ir_unseen` tables — an observation
gap to fill, not a synthesis problem to keep tuning. **Branch plan:** close out this branch, then
fast-forward both repos' `master` (user call, not automatic), then open `wip/golden-twin` — same
branch name, deliberately, in BOTH repos — for the design pass. **New work named but NOT yet
written — do not write it pre-emptively:** a cross-repo fidelity spec
(`docs/design/13-golden-twin-fidelity.md`, canonical in axRange), aximprint's
`WORK_ORDER_reference_ingest.md`, a fidelity scorecard (axRange), a firewall-policy IR extension
(axRange).

## Next steps (ordered)

1. Get this branch reviewed and merged; fast-forward both repos' `master`s.
2. Open `wip/golden-twin` in both repos; write the cross-repo fidelity spec first — the reference
   ingest work, the scorecard, and the IR extension all depend on its ten-dimension list and
   evidence-channel design being settled first.
3. `WORK_ORDER_reference_ingest.md` (aximprint) — **S0/S1/S2 done; resume at S3.** Note the order
   changed on ratification: **operator assertions came FIRST**, the directory export second. S3 is
   the LDIF parser + its three IR tables; S4 the identity compile step, which is what actually
   closes the domain-controller gap `docs/WORK_ORDER_windows_responder.md` (SUPERSEDED in approach)
   named, via identity injection rather than a responder.
4. axRange side: the fidelity scorecard and the firewall-policy IR extension.
5. `docs/WORK_ORDER_source_locality_filters.md` (PARKED) — revisit only if the golden-twin spec's
   behavioral-row tolerance fails; its pre-delegated ruling was withdrawn, not answered.

## Gotchas

- **axRange's own fix for the responder-drift problem is NOT on this branch.** The standing twin's
  19 `run-script` guests run responder text that differs from their persisted `script_content`
  (iteration 17's finding, still true after this iteration's re-install). axRange's **Option A**
  (`te deploy --update --reapply`) is committed on a branch over there, pending review/merge — do
  not assume it has landed.
- **`check_role_fixed_point.py` needs an interpreter that can import axRange** (for its persona
  catalog, which pulls in `ruamel`) — this repo's own `.venv` deliberately does not carry it. Run it
  as `/home/rocky/axRange/.venv/bin/python scripts/check_role_fixed_point.py` (or set
  `AXRANGE_ROOT`); under this repo's bare `.venv/bin/python` it now exits **2** (could-not-look)
  rather than the old silent false-green **0**.
- **Never run `/home/rocky/corpapcn-fidelity-artifacts/measure.sh`** — an old scratchpad script from
  an earlier iteration; it does not target the current standing twin. Use the per-run
  `iterNN/measure.log` capture instead.
- **The standing twin (`corpapcn-twin-0e4f7d58`) and its tap
  (`tap-corpapcn-twin-0e4f7d58-e4b517fe`) stay UP** — it is the test bed for whatever measures the
  golden-twin spec's behavioral row next. Don't destroy it without a reason.
- **Durable artifacts** (outside both repos on purpose, `LES-003`; not backed up):
  `/home/rocky/corpapcn-fidelity-artifacts/` — `src-ir-rematerialized-20260902.duckdb` (source,
  current code — differs from the archived `capcn-ir6.duckdb` by one external IPv6 host, a
  code-build difference, not a network one), `iter19/twin-ir-iter19.duckdb`,
  `iter19/twin-ir-iter18-rescored.duckdb`, `iter19/measure.log`, plus every prior
  `twin-ir-iter14..18.duckdb`.
