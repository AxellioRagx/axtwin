# WORK ORDER — aximprint side of the axRange integration (Track B + Track A live round-trip)

**STATUS:** **Track B COMPLETE (2026-08-25, unratified — landed on `wip/axrange-integration`,
committed as `495818b`/`5d468b0` and rebased onto `master` 2026-08-26 after the axquery arc merged).
Track A near-complete, its remainder cluster-gated and deliberately not started.** The document as a
whole remains a proposal.
Companion to axRange's `docs/WORK_ORDER_aximprint_console.md` (Track C)
and the cross-repo plan + design canvas at `axRange:docs/design/aximprint-integration/`
(`WORK_ORDERS.md`).
**Authority:** proposal (Claude Code), for user review — not ratified.
**Route:** Pipeline (`/dev`) for Track B; the Track A remainder is a cluster-gated verify item.
**Coupling:** aximprint MAY import axRange's validator / planner / op-catalog to validate what it
emits; axRange imports nothing from aximprint (unchanged).

## Contract to hold (the integration's one serialization point)

Target axRange twin-tier schema **`scenario_schema_version` 3** (`twin_config` + `tfc` + `tap`);
provenance stays a **sidecar** `<name>.provenance.json` (their vocabulary is `extra="forbid"`).
`tap.sensors` binding is left unfilled — an axRange `o2`/`tap`-tier fact.

## Track A — synth → deployable (seam ②): NEAR-COMPLETE

`WORK_ORDER_synth_v1.md` + `WORK_ORDER_network_ir.md` step 4 landed `synth/compiler.py` +
`aximprint cast`; **axRange's own expander already accepts the output — 38 networks / 197 nodes /
0 warnings.**
- **Remaining (cluster-gated):** the **live deploy round-trip** — take a synthesized
  `twin_config`+`tfc`+`tap` all the way through `axrange validate → plan → deploy` on a cluster
  (not just expander-accept) and confirm the twin stands up. Queues with axRange's Phase-3
  live-verify; not fresh parallel work.
- Still open in `synth_v1`: D3 node-count cap; §8 tap-placement recommendation (feeds seam ④).

## Track B — ingest breadth (seam ①): **COMPLETE 2026-08-25**

Zeek ingest is done; the packetXpress read side is covered by `xtract`/`axapi`
(`docs/AXAPI_CONTRACT.md`, DONE). **Add the Suricata ingest plugin.**
- **Files:** `src/aximprint/ingest/` (plugin base + registry; new `ingest/suricata/`).
- **Do:** normalize Suricata `eve.json` (alert + flow records) into the same event model
  (`Flow / Identity / Service / Alert / SensorHealth`) the Zeek plugin produces; register it.
- **Done when:** a Suricata capture ingests and windows equivalently to a Zeek capture of the same
  traffic, `verify_determinism.py` green.

### RESULT — landed 2026-08-25 (CHANGELOG same day)

`src/aximprint/ingest/suricata/{__init__,plugin,sources}.py`, registered in `ingest/__init__.py`.
**3 ingest plugins** — `zeek`, `axquery` (2026-08-20, from `master`) and `suricata`. Tier 1
**1574 → 1722** tests (**148** added; the pre-rebase figures were `1519 → 1667`, same delta against
an older base); `bash scripts/verify_tier.sh` exit 0, re-run post-rebase 2026-08-26.

**One decision was ruled by the user, and it was this document's own self-contradiction.** The
`Do:` line above says "alert + flow records"; the `Done when:` line says "ingests and windows
equivalently to a Zeek capture". Those are not the same table. **The user ruled for parity with the
Zeek table**, folded into this build rather than deferred to a second dispatch. Everything else is
Claude Code's, recorded in the CHANGELOG for review.

- **18 parsed + 25 declined = 43** named `event_type` entries, **asserted disjoint at import**,
  covering all **35** of Suricata's own `eve-log: types:` list (the extra 8 are alternate spellings
  like `files`/`fileinfo`, or schema objects a stock build never emits). `Q24`'s no-fall-through
  discipline, applied to a third sensor — and note the **census unit differs per sensor** and the
  three are not interchangeable: a log stem for Zeek, an `event_type` field for Suricata, an
  endpoint for axquery.
- **Transport/semantics split mirrors Zeek's**; the sensor-neutral reorder machinery moved to
  `ingest/reorder.py` **below** every plugin, and the horizon constants deliberately did **not**
  move (`LES-119`).
- **Kind spellings reuse Zeek's** so the projection tables are inherited, not re-declared.
- **"Done when" is now a script, not a word.** `scripts/verify_suricata_equivalence.py` is the
  acceptance gate and `scripts/verify_pipeline_gates.sh` runs it as a **fourth** leg, with
  `verify_determinism.py --plugin suricata` for the per-plugin determinism half.

**The finding that matters more than the plugin.** The equivalence gate **passed while both sensors
were broken identically** — both rendered a Kerberos principal in a grammar `kerberos_identifier`
cannot read, both classified 20 machine accounts as users, both anchored nothing, and both agreed.
The two plugins differ only above `events/`; `windowing/` is one body of code serving both, so any
regression there moves both sides together and the gate prints a full page of ticks. Widening the
comparison key made it visible; only a **positive assertion anchored to the seeded plan** made it
fail. Every axis now carries one, and the gate is mutation-proven. `LES-123`.

**Opened by this work, all needing a ruling:** `Q32` (ARP's scope/confidence and the
proxy-ARP/VRRP guard — its cross-branch caveat is spent now that `wip/axquery-ingest` has merged and
parser and tables sit in one tree), `Q33` (`IdentityEvent.principal` has no declared grammar — the
root cause above), `Q34` (`s0_count`/`rej_count` keyed on Zeek-verbatim strings; pre-existing, and
`axquery` had been publishing a confident `0` for five days before this branch's gate exposed the
class — **CLOSED 2026-08-26 by deleting both counters**, `state_mix` already carrying the fact), `Q35` (a Suricata run has no sensor-loss signal at all; the one-measurement reopen condition
is recorded), `Q36` (does `FlowEvent.service` want cross-sensor normalization — three vocabularies
now, counting axquery's coarser `dns`/`tls`/`ssh`/`other`). Lessons `LES-123` through `LES-129`.

**Opened by the 2026-08-26 rebase reconciliation:** `Q37` — the acceptance gate this track built
compares **Zeek vs Suricata only**, and `axquery` has no automated equivalence check at all. Whether
every new sensor owes a ground-truth-anchored gate, or only sensors that read the same plane, is a
policy question this track's finding implies and does not settle. `LES-130` records the doc hazard
the rebase itself surfaced (an English ordinal is a counter nothing polices).

**Not done, deliberately:** `ruff check src/aximprint/ingest/` is **not** clean — 22 findings, none
in new code, all pre-dating the branch (net **−2**), and reaching clean needs an `I001` autofix that
reorders the plugin-registration imports in `__init__.py`. CLAUDE.md already scopes whole-repo lint
to a separate work order.

## Sequencing

Track B is independent and parallel-safe — it belonged in aximprint's own `/dev`, and it is
**done**. Track A's
remainder lands with the cluster round-trip on the axRange side. Neither blocks axRange Track C
(the Console), which builds against the already-existing output shape.
