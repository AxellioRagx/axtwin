# Direction Log

Dated "direction" notes and historical progress narrative that used to accumulate inline in
`CLAUDE.md`, moved out here verbatim on 2026-09-16 to keep `CLAUDE.md` small — it is loaded
into every session and every background agent's context (see `docs/CONVENTIONS.md` for the
budget this enforces and why). Nothing below is edited from how it read in `CLAUDE.md`:
entries are ordered newest first, and each is reproduced exactly as originally written —
including any date, counter, or `**Authority:**` marker that was correct as of the day the
entry was written, even where later entries (in this file or in `docs/CHANGELOG.md`) have
since superseded it. This is a historical record, not a statement of current state — for
what is true today, read `CLAUDE.md` or `docs/design/README.md`. The full pre-trim `CLAUDE.md`
(2026-09-19) is archived verbatim as the last section of this file.

---

## Tech-stack direction blockquotes

These sat under `CLAUDE.md`'s `## Tech Stack (Locked In So Far)` heading, directly after the
tech-stack table, before this move.

### 2026-09-03 → 2026-09-16 — Golden-twin fidelity direction (spec 13/14/15, scorecard, Console canvas build arc S1–S7)

> **Direction: "golden twin" fidelity supersedes traffic-shape fidelity as the CorpAPCN loop's
> goal — ratified 2026-09-03 (Authority: user); design record landing as
> `docs/design/13-golden-twin-fidelity.md` on a new branch `wip/golden-twin`, not yet written as of
> this entry (landed the same day, `97fd3346`; its §8 was ruled the same day — see the end of this
> blockquote).** The CorpAPCN twin-fidelity loop (aximprint `docs/JOURNAL_corpapcn_fidelity.md`)
> closed at **iteration 19**: its deciding axis, `role_edges_local`, plateaued in a 0.533–0.742 band
> across iterations 14–19 rather than converging, because 12 of 18 source local-edge keys are
> structurally absent from the twin (a Windows DC that answers nothing, no workstation-to-
> workstation traffic) — and the fingerprint measures traffic **shape**, which is neither this
> platform's purpose nor a reliable proxy for what that purpose depends on. New direction: fidelity
> against a **closed spec**, not one capture's traffic shape — **ten dimensions** (asset, topology,
> configuration, state, protocol, performance, security, dependency, temporal, behavioral) plus
> **visual**, each tagged with its observation channel (wire/reference/operator/console) and
> evidence class (observed/referenced/imputed), weighted by **experiment impact** (what the platform
> is actually for — attack/defense validation — not how measurable an axis happens to be from a
> capture alone). The operational half (config/state/dependency) is an observation gap the wire
> cannot fill; it is closed by a reference-ingest channel (directory exports first) on the aximprint
> side — posture injected, secrets never. **Work-order triage under the new direction:** Option A
> (`--reapply`, above) kept as the maintenance path; `drive: command` (above) promoted; the Console
> kept as the golden-twin UI; `docs/WORK_ORDER_o2_sensor_rules.md` promoted; **per-rule firewall
> policy** ("Immediate Next Actions" item 5, open since the router slice landed 2026-06-10) promoted
> — the one reproduction gap injection alone cannot fill, since routers ship permissive
> default-forward and the twin-config `conduits[].policy` field that once modeled per-rule policy
> was found inert and removed 2026-08-04. Three new work orders are scoped but not yet written: a
> fidelity scorecard (axRange), a firewall-policy IR extension (axRange), and reference ingest
> (aximprint). The standing `te corpapcn-twin-0e4f7d58` (138 VMs, dev45) stays up as the scorecard's
> first subject and Option A's live proof; both repos' `master` are fast-forwardable from this
> branch, ahead of the `wip/golden-twin` cut.
> **§8 RULED 2026-09-03 (Authority: user; CHANGELOG same date):** the first golden gates on the
> **structural + behavioral** rows (1 asset / 2 topology / 3 configuration / 8 dependency + 5
> protocol / 9 temporal / 10 behavioral); the operational rows (4 state / 6 performance / 7
> security) are deferred for it and 11 visual is reported; the standing twin is scorecard run 1 and
> a fresh cast is the first golden; row 3 relaxes to role-level install lists; row 5 is served by
> realistic protocol clients AND servers configured from observed data, not synthetic filler (the
> protocol-faithful traffic work order gains a server half); rows 6/8/9/10 tolerances stay
> provisional until run 1 and are fixed as spec v1.1; the directory export is an owner-run LDIF
> file drop with a supplied attribute allowlist (no access from our side), sequenced — with the
> firewall-policy extension — after the first golden, since both serve the deferred operational
> rows. Spec §3.3/§4/§8 carry the gate set and the verbatim rulings.
> **2026-09-04 (Authority: user; CHANGELOG same date):** a second design-of-record,
> `docs/design/14-operator-assertions.md`, adds the **operator channel** — a **six-class evidence
> model** in precedence order (`asserted` > `imported` > `observed` > `referenced` > `imputed` >
> `compiler`; `defaulted` is renamed `compiler` and `referenced` is split from out-of-band imports),
> an **assertions file** (`<imprint>.assertions.yaml`, add/override/suppress by stable entity key,
> the first reference ingest to build, ahead of the LDIF export) and the **Console canvas** as its
> author (display → edit → save → recompile → refresh; the YAML stays the contract). Same day:
> the fidelity **scorecard** and the row-5 **server-half** work orders were drafted (OPEN); the
> sidecar's real compiler shape is canonical (axRange adapts it for the Console); **`App.listen`
> is wired** as the twin's declared listen set (version-locked `IR_SCHEMA` touch, no bump); the
> **real DC is promoted under the lab realm `corp.axr.lab`** (the observed realm was offered and
> not chosen); row 1 scores at group granularity with OS family reported imputed. Scored today,
> the standing twin fails rows 1, 2 and 5 — **all three now measured on-cluster 2026-09-05, row 5
> included** (previously rows 1/2 measured and row 5 predicted; see the scorecard blockquote below). **Later the same day the §7 sequence was ruled:** the
> reference channel and firewall policy follow the first golden, and the fresh cast's rows 1 and 2
> are closed by a **deployment-scope change (spec §3.4)** — the cast emits its whole observed
> population with an observation block (role-less hosts as generic endpoints) and axRange's deploy
> gates on occupancy at `DEPLOY_MIN_OCCUPANCY` (default 0.8, row 1's bar); the canvas gets **save**
> (file only) and **apply** (save + recompile + refresh), drift is flag-only, and the first edit set
> includes suppress, image/sizing overrides and conduits/routes. **Evening:** three more work
> orders drafted (axRange `WORK_ORDER_deploy_gate.md`; aximprint `WORK_ORDER_reference_ingest.md` +
> `WORK_ORDER_deployment_scope.md`), with three rulings — the 0.8 default kept (~64 hosts vs 122
> today), role-less hosts as `unroled-endpoint`, a fixed occupancy ladder in the cast's group key —
> and one model addition: a declared deployment transformation (relocated CIDR, the lab realm) is not
> a precedence event. The **Console canvas** is in design as a clean-start SPA (`o2/console/`, not
> Grafana): semantic zoom over three levels, deterministic layout, edges in bundles and a matrix —
> **ratified the same evening as `docs/design/15-console-canvas.md`** (SPA starts clean;
> deterministic prefix-grouped layout with optional pins); its work order is separate and unwritten.
> **The deploy gate is BUILT 2026-09-04** (`docs/WORK_ORDER_deploy_gate.md` §11.2, CHANGELOG
> "Deploy gate BUILT (spec 13 §3.4, the axRange half)"; S1–S3 + S4 partial, the inventory key
> deferred): `axrange/twinconfig/gate.py` gates a cast's `observation:` blocks at
> `DEPLOY_MIN_OCCUPANCY` (default 0.8) and records the effective thresholds in `scenario.labels` —
> `Scenario.labels`' first producer, and the expander `warnings` channel's first since 2026-08-04 —
> so recovering a standing twin's threshold is an `ir_hash` proof, not a guess. Two cascade rules
> the design did not have and real content demanded (Authority: implementation (Claude Code)): **a
> router follows its segments** (router legs are neither occupancy evidence nor a gated-out-from-
> under error, and are pruned with their segment — without it every real cast refused, since the
> compiler's single `gw` legs all 27–33 segments) and **the cascade removes a segment only when the
> gate emptied it** (router-only transit and uplink segments have no host occupant by construction;
> a bare emptiness test cost `dmz-perimeter` its internet uplink and `datacenter` its fabric link at
> every threshold). Nothing version-locked moved; all six shipped/cast `plan_id`s are pinned
> byte-identical.
> **The fidelity SCORECARD is BUILT 2026-09-05** — spec 13 §6's *certify* box, S1–S4
> (`docs/WORK_ORDER_fidelity_scorecard.md` §9.7, CHANGELOG "Fidelity scorecard BUILT"): a new leaf
> package `axrange/fidelity/` plus `axrange te scorecard [ID]` and `GET /api/deploys/{id}/scorecard`
> compute rows 1/2/3/5 under §3.3's gate set with `not-computed` a first-class verdict and
> `incomplete` never collapsing into `not-golden`; **run 1 on the standing `corpapcn-twin-0e4f7d58`
> was measured offline** — `incomplete`, row 1 fail naming five role-less hosts, row 2 fail on 6 of
> 32 observed segments, row 3 pass, row 5 `not-computed` over a 31-fact denominator with 10
> published exclusions — and **the gated `dev45` read-only run (including the in-guest probe) RAN
> 2026-09-05 and PASSED** (CHANGELOG "Scorecard run 1 RAN on `dev45`", `docs/WORK_ORDER_fidelity_scorecard.md`
> §7.1): all six steps, nothing deployed/updated/destroyed, step 1 reproducing the offline numbers
> exactly and step 2's probe taking row 5 to **fail** (5 of 31 passing) and the verdict to
> **`not-golden`** — three of the failures being the echo responder returning the probe's own NTP
> mode-3 request and LDAP `searchRequest`, i.e. an **open port that reproduces no service** (LES-229).
> Rows 6/8/9/10 came back `not-computed`, so spec §8.3's v1.1 tolerances wait on **S5** (rows 8/9/10
> behind the fingerprint seam, still open) plus a recapture, not on run 1. **The first golden's
> candidate cast is installed** as `twin-configs-cast/corpapcn-golden/` — produced from the same
> source PIT by the services-build compiler, the first bundle carrying its own provenance sidecar
> (so the scorecard reads evidence classes from the producer, `warnings: []`), validating and
> planning read-only (695 ops; 80 hosts on 24 segments at `DEPLOY_MIN_OCCUPANCY 0.8`) — with its
> **deploy still gated and not run**, and one unconsumed producer↔consumer seam registered as
> GAP-066 (`axr-topology/1.4`'s `services[].app` + `observations` are published and unread). One implementation
> refinement rides with it: row 5's denominator is the observed *service* listens on deployed hosts
> at or above the deploy threshold, every exclusion published with its reason.
> **Row 5's SERVER HALF is BUILT 2026-09-05 in BOTH repos** — `docs/WORK_ORDER_protocol_faithful_services.md`
> §10.2, CHANGELOG "Protocol-faithful services BUILT": axRange gained the **declared listen set**
> (`App.listen`'s first consumer ever — D2(b), Authority: user, no `IR_SCHEMA` bump; a twin config's
> `nodes[].listen:` → `App.listen` → `clone-vm`'s `node_listen` (emitted only when non-empty, or
> `--update` would refuse every standing twin) → `hosts[].listen` in `te inventory` / the inventory
> API, reported against the *observed* set in the scorecard's rows 4/5 as `measured` only, never a
> verdict) plus the `ad-cast` S3 fixture and the S5 gap register (GAP-061..065); aximprint replaced
> the echo responder with **14 real-daemon `serve-*.sh` recipes** (bind9/nginx/chrony/isc-dhcp/sshd,
> driven by the observed listen census, echo surviving only where no real server exists) behind
> `bundle_schema: axr-topology/1.4` with per-node `services[]` + an `observations` block, and
> authored the **domain controller as a real AD DS forest under the LAB realm `corp.axr.lab`** (D4 —
> the observed `AXELLIO.DOM` is recorded, not reproduced; the observed short hostname `axdc01` is
> kept). The cast plans through axRange's own validator + planner (695 ops, incl.
> `wait-domain-ready[axdc01-app@corp.axr.lab]`). **The gated `dev45` live-verify RAN and PASSED
> 2026-09-05/06** (CHANGELOG "Services §9.2 gated `dev45` live-verify PASSED";
> `docs/WORK_ORDER_protocol_faithful_services.md` §9.2.1) on the purpose-built throwaway
> `twin-configs/svc-verify/` twin, never the standing one: 82/82 op rows in 17.3 min, every declared
> listen pair present on a non-loopback address, **all six protocol probes passing** (`dig` NOERROR,
> a completed TLS handshake, an NTP offset, an LDAP rootDSE, an accepted anonymous SMB session, a
> real Kerberos TGT, `PartOfDomain: True`), and — the half that cannot be faked in-guest — the
> sensor recording `dns`/`krb5`/`smb` event types with `app_proto` `tls`/`ntp` on the rest, **no bare
> `tcp`/`udp` on any classifiable flow**, unattended from a `drive: command` persona's own cadence
> as well as from the operator's probes; teardown clean, the standing twin untouched. Twelve gaps
> came out of it (`docs/PRODUCT_GAPS.md` **GAP-067..GAP-078**), most about the **standing O2
> surface** rather than this build — headline: the standing `o2-suricata`'s Suricata had been dead
> since 2026-07-27 with `o2 status` reporting nothing for 40 days (GAP-067, LES-247), so the run
> stood up a dedicated scratch sensor instead. **S4** (zones/vhosts from observed names) still waits
> on an aximprint IR carrier for TLS/HTTP/SMB (aximprint GAP-019, a decision queued for the user)
> and now also on **GAP-076** (Suricata wrote no `tls` records, so SNI has no evidence channel).
> **The CONSOLE's S1 API slice is BUILT 2026-09-05/06** (`docs/WORK_ORDER_console_canvas.md` §12,
> CHANGELOG 2026-09-06 "Console S1 BUILT") — the backend half of design note 15's operator canvas:
> eight read/write routes under `/api/imprints/{id}` (topology alias, cast summary, assertions and
> layout with content-hash etags, a **synchronous** two-stage `apply` that recompiles the bundle
> through aximprint's own CLI, and a fingerprint-keyed `diff`), with the `axr-assertions/1` schema
> **vendored** from aximprint and its closed sets derived from it rather than restated. The apply
> path is proven against the **real compiler**, which is what found four defects three green unit
> rounds had certified (LES-230..LES-243). **S2 — the SPA scaffold — is BUILT 2026-09-08**
> (`docs/WORK_ORDER_console_canvas.md` §13, CHANGELOG 2026-09-08 "Console S2 BUILT"): a new
> component directory **`o2/console/`** (Vite 8 + React 18 + TypeScript strict, clean-typecheck from
> zero) holding the three-pane shell, **one** pure URL grammar in `model/zoom.ts`
> (`/imprints/<id>/segments/10.80.20.0-24` — a CIDR's prefix length written with a hyphen, never
> `%2F` — with every unusable filter value reported rather than dropped), a typed client over the
> eight S1 routes, and the pending/saved/applied edit store whose 412 rule is *report the server's
> etag, never adopt it, reload before retry* (LES-251) — **wired end to end and drawing nothing
> yet**, which is the slice's point. It runs **co-served** from `axrange serve --spa-dir
> o2/console/dist --imprints-dir twin-configs-cast` (one origin, no CORS, behind a new history
> fallback in `axrange/api/app.py` — headless is byte-identical when `--spa-dir` is unset), or from
> `npm run dev` on 127.0.0.1:5173 proxying `/api`. The same slice settled S1's §12.6 residual as
> **`ImprintDiffDTO.suppressed_keys`**, a *set delta* of authored keys, **refusing** the recommended
> per-removal `authored_key` column because the producer's accounting cannot support that join
> (LES-255). **S3 (draw), S4 (pins), S5 (editing + save) and S6 (apply + refresh) are BUILT and
> committed** (`§14`/`§15`/`§16`/`§17`; CHANGELOG 2026-09-09/10), and **S6's §17.5 acceptance residual
> is CLOSED 2026-09-11** — a real-compiler apply through the SPA's own HTTP path proved the compiler
> writes an authored key into the new sidecar (five checks; a byte-identical second apply; six
> findings registered as `docs/PRODUCT_GAPS.md` GAP-087..GAP-092, four of them axRange's).
> **S7 (the Grafana embed) is the last slice of that order, and D5 is RULED (Authority: user,
> 2026-09-11): the Grafana plugin's Imprints screen retires at S7, with "Deploy as twin" kept as a
> slim plugin action.** Two post-build follow-ons are open beside it: `§3 S5.1` (selection-driven
> editing) and **`docs/WORK_ORDER_console_level0_rendering.md`** (R1–R7 — the live level 0 is a grid
> with no trunk, rails or width). **R5 + R7 of that order are BUILT 2026-09-11** (CHANGELOG, its §9):
> `GET …/cast` now publishes `conduit_roster` + `routers` (closing GAP-079), and **one addressing id
> per bundle** — canonical directory name, enforced by one guard on every imprint route, with every
> other id-shaped field warned about rather than compared (closing GAP-082, which had refused **every**
> real cast bundle with a 400). Lifting that refusal exposed the bug behind it: the API resolved a twin
> config's relative `scripts/…` paths against the materialized tempfile, so plan build failed on every
> real cast bundle and **"deploy as twin" was dead on real content** — now one shared
> `twin_config_source_dir()` used by the CLI and both API resolvers (plan previews pinned at 695 /
> 1077 / 1227 ops). New leaf module `axrange/jsonnum.py` holds the one bounded-number rule for
> republished artifact values. **R1 of that order is BUILT 2026-09-12** (CHANGELOG, its §10) —
> frontend-only, no counter moved: level 0 is now **drawn at 1 layout unit = 1 CSS px and scrolls**
> rather than being fitted to the pane (a `fit width` control never goes below **0.8**, and the
> readout states how much is off-screen), the rule living in one pure module
> `o2/console/src/layout/viewport.ts`; **tier rails** label the left edge from `Level0Layout.rails`
> with **content-derived heights**, so a cast with no router draws no WAN EDGE rail rather than
> labelling an empty band; and the summary strip and outline pane both collapse. One deferred
> decision rides to R2: a pin is stored as an **absolute** coordinate, so R1's +128 rail shift
> re-points any stored pin (blast radius zero today — the only `layout.json` holds `"pins": {}`), and
> **R2 adds a plane/epoch stamp to `axr-layout/1`** rather than migrating four times as R2/R3/R4/R6
> each move the plane again. **R2 of that order is BUILT 2026-09-14** (CHANGELOG, its §11) — level 0
> now reads as a **path**: a pure `layout/trunk.ts` draws Internet → **tap** → WAN edge → a bus →
> **one leg per site group** (each leg ending at its group header's nearest edge and entering no box,
> checked as computed geometry by `noConnectorCrossesATile` on every level-0 golden); the WAN box is
> keyed on a new `graph.wanEdge` that is **declared** by a published router or **inferred** from
> observed crossing traffic (labelled *egress (inferred from observed traffic)*, never "gateway");
> the tap badge is labelled **verbatim** from the export's `vantage`/`windows`; and stroke width
> carries north-south volume through a shared `layout/scale.ts` normalised over **the drawn set's own**
> log range (a deliberate trade — width is relative within one drawing, and R4's arcs inherit it).
> R1's deferred decision landed with it: `axr-layout/1` gains an **opaque `plane` stamp** the API
> round-trips and never interprets, and the stamp advances only when a pin set is **wholly**
> re-authored — identified by value, so one drag can never re-certify the pins it did not move
> (LES-296). The pytest counter moves **3101 → 3112** (the additive `plane` field and its tests are
> R2's only Python). **R3 of that order is BUILT 2026-09-14** (CHANGELOG, its §12) — frontend-only,
> no counter moved: a new pure `layout/infra.ts` puts each infrastructure device over **the site
> column holding its segment** and reaches the group with a published orthogonal **stub** (the
> diagonal tie-lines that crossed tiles are gone; the stubs join `noConnectorCrossesATile`), and the
> tile's unlabeled histogram bar becomes up to three **role badges** — an infrastructure role
> reserved a slot, then by count, because a pure count ranking hides both domain controllers behind
> unfingerprinted endpoints on **both** real bundles (a recorded departure from the slice's own
> wording, whose cost is measured: it changes 4 of 39 / 3 of 32 segments and displaces a
> non-endpoint role exactly once). Because the badges are deliberately not a ranking, the tile
> discloses the projection on hover **and** in its `aria-label`. `PLANE_EPOCH` goes **1 → 2** — the
> infrastructure band's height is now content-derived, and a hash over constants cannot see a rule
> that reads the data, which is the rule R6 now inherits. **R4 of that order is BUILT 2026-09-15**
> (CHANGELOG, its §13) — frontend-only, no counter moved: the east-west band becomes
> **volume-weighted arcs** (`layout/arcs.ts`; `lanes.ts` **deleted**), each anchored at its site
> group's **bottom edge** so nothing can cross a tile by construction, with the band's height bounded
> by a depth clamp rather than by the pair count, and **every asserted conduit drawn in the edit
> colour, dashed when the pair carried no observed traffic** — so a declared-but-silent conduit is
> visibly distinct from an observed-but-unasserted flow (the one property the retired lanes had, kept
> for the set where it means something). Two recorded departures from the slice's own sketch, both
> forced by measurement: an arc **drops into the band before it curves** (a cubic reaches only ¾ of
> the way toward its control points, so the literal shape sagged through tiles — 1 crossing at K=12,
> 6 with the budget lifted), and the depth is anchored at `max(bandTop, both ends)` so a pin cannot
> bulge a curve back up through its own column. The top-K budget is **inert on today's content**
> (both casts declare a conduit for nearly every pair they observed) — **ruled ACCEPT**, because
> budgeting by volume would hide declared-but-silent conduits *first* and the band is height-bounded
> anyway. **R6 of that order is BUILT 2026-09-15** (CHANGELOG, its §14) — frontend-only, no counter
> moved, and **with it every slice of that order (R1–R7) is built**: a new pure `layout/columns.ts`
> **ranks** a site group's tiles by placed-host count (`?order=cidr` the other order), draws at most
> **six** and collapses the rest behind a **`+N more`** tile (`?expand=<site key>`), and sizes the
> column from the shown count — taking the pit bundle's 19-segment group from seven tile rows to two
> and the plane's height **1282 → 834**. The tile now carries **`CIDR · hosts · north-south and
> east-west bytes · role badges · evidence ring`**, the byte figure **derived from `trafficPairs`**
> so a tile and an arc cannot disagree. **The slice's real lesson is disclosure, not the budget:**
> the ranking is by *hosts*, so the collapsed set can hold most of a group's *traffic* (measured:
> 75.1 % / 65.5 % / 99.9 % on the three real groups), and a collapsed segment has no tile to say so —
> the `+N more` tile therefore states the **hidden volume** on hover, in its accessible name and in
> the tier's note, from one helper (LES-319). Two defects measurement caught before ship: the band was
> measured from the lowest *tile* and overran its bound (201.8 against 140), and a collapsed
> segment's arcs would have **vanished** (`corpapcn-twin` keeping 4.6 % of its east-west bytes and
> losing its widest arc) — they now fold onto the group's `+N more` anchor and merge (LES-316).
> `PLANE_EPOCH` goes **2 → 3**, the case R3's rule reserved it for. **One follow-up is named rather
> than implied: column wrapping** — the map still does not fit at a 1600 px window (plane
> **2030 × 834**), and the residual is structural width, not height. **S5.1.a — selection drives
> editing — is BUILT 2026-09-15** (iteration 7; `docs/WORK_ORDER_console_canvas.md` §18, CHANGELOG
> 2026-09-15): the selected tile, spine or host carries its facts and actions in the inspector (the S5
> free-standing forms are gone), a segment is added from a `+` on its site prefilled with the next free
> `/24`, a pending or saved **added segment is selectable** before the map draws it, **Escape** clears
> a selection first and will not leave an imprint with unsaved edits, and the cast summary gains an
> additive **`segment_roster`** (`{name, cidr, zone_type}`) — the pytest counter's **3112 → 3122**.
> **S5.1.b–c are BUILT 2026-09-16** (iteration 8; that order's §19, CHANGELOG 2026-09-16): a gesture
> may now stage a **batch** of records **all-or-nothing** (`edit/batch.ts`, a `stageBatch` reducer
> action, no new record kind), a segment can be **cloned with its hosts** by Alt-drag, keyboard or tap
> — its drop position committed as an ordinary S4 pin, its pending tile drawn hatched and reflowing
> nothing — and a fresh segment can be born with **generated hosts** numbered from `.10`, their listen
> set prefilled from a **generated, drift-tested** per-role table (`scripts/gen_role_listen.py`) that is
> **drive-aware** and discloses what it cannot know: a `real-service` persona's emits carry no
> served-versus-dialed direction, so five roles' defaults hold a port the role dials
> (`docs/PRODUCT_GAPS.md` **GAP-095**, owner axRange — a fix would touch the version-locked PSN rules).
> The pytest counter moves **3122 → 3131**. **S5.1.d–e are BUILT 2026-09-16** (iteration 9; that order's
> §20, CHANGELOG 2026-09-16), **completing the S5.1 amendment (a–e)**: pending work is drawn at both
> levels (a placed clone at its pin, an unplaced segment appended to its column or parked clear of
> everything, hatched chips at level 1), the change list shows **one entry per gesture** with a
> **whole-gesture undo** that drops the segment's pin with it, *Add host here* on a spine prefills the
> next free address, and a per-imprint **draft** (`localStorage`) keeps pending records across a reload,
> an imprint switch, the breadcrumb, the outline and Back — **closing `docs/PRODUCT_GAPS.md` GAP-094**.
> Restoring is reconciliation, never a merge: what the file now holds is dropped, the staging paths' own
> refusals run again, and a moved etag or compiler build is reported (including when the check could not
> be made). Frontend-only — no counter moved. One residual: a placement pin is written at stage time
> while its segment is still pending (**GAP-096**, Low). **S7 is BUILT 2026-09-16** (iteration 10; that
> order's §21, CHANGELOG 2026-09-16), **executing decision D5 (Authority: user, 2026-09-11)**: the
> Grafana panel now **frames the Console SPA at its own origin** — an iframe, not a shared React root,
> forced by the SPA's same-origin `/api` against a Grafana origin with no CORS — configured by a
> `consoleBaseUrl` panel option, claiming no load state a cross-origin frame cannot know and naming
> what to check when the frame stays blank. **The plugin's Track C Imprints screen is DELETED** with
> its `initialView` option, **closing `docs/PRODUCT_GAPS.md` GAP-083** — and the closure is stronger
> than the gap: `GET /api/imprints/{id}` now has no rendering consumer at all. **"Deploy as twin"
> survives** as pick → preview (the pure plan route) → an in-panel confirmation naming the
> destination's `api_url` → deploy, rendering **no belief-store measurement**. Nothing version-locked
> moved (the only Python edit is three DTO comments). Two new gaps: **GAP-097** (`schema_version`
> rendered unchecked by both the plugin and the SPA, against design note 15 §9 seam 1) and **GAP-098**
> (the previewed plan is not pinned at deploy — the server re-plans). **With S7,
> `docs/WORK_ORDER_console_canvas.md` has NO OPEN SLICE (S1–S7 and S5.1 a–e are all built);** what the
> embed needs next is a live Grafana, since none of it — the frame rendering, the SPA's `/api` from a
> cross-site frame, dashboard persistence, the datasource-proxy 401, a real deploy, plugin signing —
> is testable in this repo (that order's §21.5).

### 2026-09-02 — Protocol-faithful traffic: a `drive: command` persona driver

> **Protocol-faithful traffic: a `drive: command` persona driver — BUILT 2026-09-02
> (`docs/WORK_ORDER_protocol_faithful_traffic.md`, CHANGELOG 2026-09-02, Authority: user).** A
> fourth persona `drive:` kind alongside `scripted`/`replay`/`real-service`: at each emit's firing
> instant it runs a REAL protocol client tool (`dig`/`curl`/`ntpdate`/`ping`, five v1 protos —
> dns/http/https/ntp/icmp) instead of pushing tagged filler bytes at a port, so a capture records
> the protocol named in `proto:` rather than bare `tcp`/`udp`. Reuses the existing persona pipeline
> verbatim (peer resolution, the `systemd-run` launch primitive, the QGA delivery path, the
> `get_driver` dispatch — one new `_DRIVERS` entry is the entire execution-side wiring change).
> **Containment is structural**: every peer address is baked into the rendered client as an
> orchestrator-resolved literal, and rendering refuses any argv whose address is a bare hostname
> with no `--resolve`/`--connect-to` binding — the guest can never be made to resolve a name. Linux
> only (a Windows persona is a loud refusal, not a silent no-op); no shipped catalog persona uses it
> yet, since the tool set (`dnsutils`/`curl`/`ntpdate`) belongs in an image bake, not a standing
> twin's `install:` list (`docs/PRODUCT_GAPS.md` GAP-056). Version-locked: `VALIDATOR` `PSN-030`'s
> drive enum widens to include `command` — rule count unchanged at 114 (the `PSN-100`/`PSN-110`
> precedent for an additive enum widening inside a version-locked spec). No `IR_SCHEMA`/
> `OP_CATALOG`/`PLANNER`/`ADAPTER`/`IMAGE_MANIFEST` change; op count stays 40. **Why it matters more
> than the CorpAPCN fidelity loop's own axes:** detection rules match protocol semantics (a DNS
> query name, an RC4 TGS-REP), never a port number, so filler-byte traffic can never trigger one and
> therefore can never produce a realistic false-positive rate either — a precondition for
> `CLAUDE.md`'s stated primary purpose, independent of any one fidelity metric. **Gated `dev45`
> live-verify PENDING**, blocked on an aximprint companion (the internet sink becoming real
> services) and deliberately never run against the standing `corpapcn-twin` (adding the tool set
> there would itself trip `--update`'s mutation refusal).

### 2026-08-25 — aximprint↔axRange integration, Track C slices 2+3 ("deploy as twin" + Console UI)

> **aximprint↔axRange integration, Track C slices 2+3 ("deploy as twin" + Console UI) — BUILT
> 2026-08-25 (`docs/WORK_ORDER_aximprint_console.md`, CHANGELOG "…Track C slices 2+3").** Closes
> the other two of Track C's three scope items the slice-1 blockquote above left open. **PART A
> (backend):** `imprint` joins `scenario`/`config` as a THIRD mutually-exclusive `create_deploy`
> source (`axrange/api/service.py`) — an imprint's `<id>.yaml` IS a `twin_config` document, expanded
> through the SAME `materialize_twin_config` and handed to the SAME validate→plan→lease→executor
> sequence every source uses; `imprint_plan`/`imprint_deploy` are thin wrappers, not a second
> orchestration path. New `POST /api/imprints/{id}/plan` (open, pure preview) + `/deploy`
> (write-token gated). **PART B (UI):** the Grafana plugin gains an in-panel Topology/Imprints
> toggle (`initialView` option) and an Imprints browser screen (stat tiles, confidence-shaded
> learned-topology, provenance table, "Deploy as twin") that hands off back to Topology on the
> existing live op-tree. **Review-caught fix, generalized as new `docs/LESSONS.md` LES-196:** the
> deploy→Topology handoff first landed on a blank canvas because the topology endpoint resolves its
> base graph only from the scenarios catalog, never from a non-catalog id like an imprint's — fixed
> with `service._resolve_read_ir_path` falling back to the imprints catalog (covers both topology
> and node-detail). No op/rule/schema count moved. Unit/build-verified only (full pytest suite +
> plugin `npm run build`/`test:ci`) — **the gated `dev45` live-verify of "deploy as twin" has NOT
> run** (it stands up real VMs); Track C is code/UI-complete, not yet fully closed per the work
> order's §4.

### 2026-08-25 — aximprint↔axRange integration, Track C slice 1 (belief-store/provenance READ surface)

> **aximprint↔axRange integration, Track C slice 1 (belief-store/provenance READ surface) — BUILT
> 2026-08-25 (`docs/WORK_ORDER_aximprint_console.md`, CHANGELOG "aximprint integration — Track C
> slice 1").** First slice of a new, still-open integration track (design canvas + cross-repo plan
> at `docs/design/aximprint-integration/`, committed 2026-08-25). `axrange/api/imprints.py` reads a
> checked-in FIXTURE imprint (`axrange/api/fixtures/imprints/<id>/` — a genuine `twin_config` YAML +
> a `<id>.provenance.json` sidecar; the `aximprint` package itself is never imported) and serves it
> over three new read-only endpoints (`GET /api/imprints`, `/{id}`, `/{id}/provenance`). No new op
> type; no version-locked schema change. **Backend read surface only** — the Grafana Console UI and
> the "deploy as twin" action (the work order's other two Track C scope items) are separate,
> not-yet-built follow-on slices; do not read this as Track C being complete.

### 2026-08-13/14 — Max-capture-rate characterization + capture/traffic-generation fixes

> **Max-capture-rate characterization + three capture/traffic-generation fixes — dev45, 2026-08-13/14
> (CHANGELOG).** A ramp against the standing `corp-enterprise` twin found **~1.5 Gbit/s / ~132 kpps
> sustained lossless** capture (240 s / 31.7M packets) to the external `XPress-SW-A` packetXpress
> sink, then, after giving the sink NIC multiple queues (`queues=8`, Authority: user — eliminated
> 31,733 sink drops at ~2.6 Gbit/s entirely), found the NEW ceiling is that **all tapped ports mirror
> into one single-queue gretap netdev** (`tc mirred overlimits` past ~2.6 Gbit/s; twin generation
> itself tops out ~2.75 Gbit/s, limited by the `scripted` persona driver's single-threaded-per-instance
> design). Three defects the run surfaced were fixed the same window, each with its own CHANGELOG
> entry (2026-08-14): the mirror gretap inheriting the node's `fq_codel` qdisc (silently dropping
> fire-and-forget mirrored frames — fixed with an asserted `pfifo` FIFO, ~60% more lossless
> throughput); `provision-app` never refreshing a cloned guest's stale apt index before installing
> (LES-024's rule, previously implemented only on the shell-installer path); and the single-peer
> persona resolver (`peer_scope: one`) resolving a mesh persona to its OWN host over loopback — the
> `peer_scope: all` exclusion (LES-170) had never been mirrored onto the sibling function, which had
> silently defeated the shipped `domain-controller` persona's DC-replication emit on every single-DC
> twin. **Persona catalog grew 28 → 36** (8 new `loadgen-*` personas, chosen by load shape for this
> characterization work, not role) plus a `roles-loadgen.yaml`, a seven-rung `tfc-maxrate-l1..l7`
> ramp, and three tap plans of increasing width (`tap-maxrate-{dmz,wide,all}.yaml`) — all under
> `twin-configs/corp-enterprise/`, with a dated snapshot record at `twin-configs/
> corp-enterprise-maxrate/`. **Left standing on dev45 at the time:** `te corp-enterprise-890c29b2` +
> `tap tap-corp-enterprise-890c29b2-c9cab1c4` (4-segment/17-port capture) + `tfc
> tfc-corp-enterprise-890c29b2-b423b5cc` (parked at the clean rung-4 rate) — **all three are
> DESTROYED as of 2026-09-06** (corrected here; this had read as a present-tense claim long after
> the fact). The only standing content on `dev45` is `te corpapcn-twin-0e4f7d58` plus its `tap`/`tfc`
> siblings. No schema/op/rule change. **3131 pytest tests.**

### 2026-08-11 — Workflow-template v7 generic scripts adopted

> **Workflow-template v7 generic scripts adopted, superseding the 2026-08-07 "keep the bespoke
> scripts" decision — 2026-08-11 (Authority: user; CHANGELOG 2026-08-11).** The template upgrade
> replaces this project's bespoke `scripts/doc-stats.py`/`scripts/verify_tier.sh`-class tooling with
> generic, config-driven scripts; axRange's counters (pytest test count, op-type count, catalog-rule
> count) are now reproduced through a new checked-in `.claude-doc-stats.json` instead of hand-coded
> imports of `axrange.ops`/`axrange.validator`. Two consequences of the swap were repaired in the
> same pass: an over-matching regression in `scripts/inject_lessons.py` (bare tokens that are
> non-top-level directory names, e.g. `design`/`catalog`, still over-match against one subtree — a
> known-bounded residual, not fully fixed), and `tests/test_pipeline_scripts.py`, rewritten against
> the v7 API. **Supersedes:** CHANGELOG 2026-08-07 "bespoke pipeline scripts kept."

### 2026-08-11 — Incremental twin update: `axrange te deploy --update` (W2)

> **Incremental twin update: `axrange te deploy --update` (W2) — BUILT 2026-08-11
> (`docs/WORK_ORDER_twin_incremental_update.md`, CHANGELOG 2026-08-11); gated `dev45` live-verify
> PENDING.** Closes `docs/PRODUCT_GAPS.md` GAP-040's headline case: growing or shrinking a standing
> twin no longer requires a full redeploy. `--update [--allow-destroy] [--yes]` pins the standing
> deploy's `plan_id` (never derives a fresh one) and reconciles the edited IR against it via a new
> pure diff module (`axrange/planner/plandiff.py`) — a deliberately REDUCED subset of `docs/
> PLANNER.md` §9: whole-node **Added**/**Removed** (gated behind `--allow-destroy`, reusing a new
> scoped variant of the existing teardown sweep) run or clean up normally; **Unchanged** ops skip via
> the pre-existing resume path; any **Mutated** op (an existing node's own fields genuinely changed)
> is a hard refusal naming the op/field/old→new value — §9.2's per-field redo-rules table is NOT
> implemented ("a partially-correct redo table is worse than none"). Sibling `tap`/`tfc`/`exr`
> deploys left referencing a removed host are warned about, not auto-reconciled (an honest v1 limit).
> No version-locked schema change; op count and rule count unchanged. **It took three code rounds** —
> each review round found a real defect the previous round's green suite had certified, the sharpest
> a planner-internal roster (`configure-router`'s `served_hosts`) that was persisted where the diff
> could see it, hard-refusing `--update` on **100% of this repo's shipped router-bearing content**
> until moved to a new non-persisted `OpInstance.planner_hints` field — see `docs/LESSONS.md`
> LES-178 through LES-187 for the full defect trail (aggregate-commit-op re-evaluation, tombstone
> exclusion, exit-code ownership, auto-heal precision, guard placement, shipped-content validation,
> and a circular-test fix). **The gated `dev45` live-verify (mandatory — this feature deletes real
> VMs) PASSED 2026-08-14** (all four §6 proofs, on a router-bearing twin; CHANGELOG 2026-08-14) —
> and a second, larger-scale, incidental exercise of the removal sweep specifically ran 2026-08-31
> during the CorpAPCN fidelity loop (196 → 123 VMs, `rc=0`, 976 op rows tombstoned; CHANGELOG
> 2026-08-31). This paragraph previously read "has not yet run," which was stale by three weeks —
> corrected 2026-09-03. LES-126's
> "twin-configs have no incremental add" dead-end is now an OPT-IN limitation (the unflagged path)
> rather than the only option.
>
> **Narrow content-only carve-out added 2026-09-02** (`--update --reapply`,
> `docs/WORK_ORDER_responder_op_row_drift.md` option A, CHANGELOG 2026-09-02, Authority: user): a
> Mutated `run-script` (`OP-APP-030`) op whose ONLY differing resolved input is `script_content` may
> now be opted into re-application via `--reapply`, instead of always hard-refused — implements
> exactly one row of §9.2's redo-rules table (`apps[*].install`). Every other field, on
> `run-script` or any other op (including the `provision-app`/`pkg:` sibling, deliberately out of
> scope), remains the hard refusal described above. `docs/PRODUCT_GAPS.md` GAP-054 tracks the
> general defect this narrowly addresses — persisted op inputs can silently stop describing a
> standing twin's guests after an out-of-band content push, and `--reapply` only repairs the state
> store going forward, never retroactively.

### 2026-07-25 — Access facts & endpoint resolution

> **Access facts & endpoint resolution — BUILT 2026-07-25 (`docs/WORK_ORDER_access_facts.md`,
> CHANGELOG "Access facts & endpoint resolution").** `info` is now a self-service "how do I get in"
> sheet, closing the real-use pain of hand-copying IPs/passwords/endpoint URLs. **Part A — deploy-wide
> default password:** a new `AdapterConfig.default_password` (`DEFAULT_PASSWORD` in adapter.conf,
> built-in default `Axr4nge-Lab!` so an unset key is behavior-identical to before) replaces the four
> hardcoded Windows-password constants and now ALSO enables Linux SSH password login (`chpasswd` +
> `lock_passwd: false` + `ssh_pwauth: true`, previously key-only) — **security posture note:** this
> is a lab-wide posture change, live the moment the field exists even with the key unset; an explicit
> per-binding/per-identity password still wins over the default. **Part B — o2-services register +
> auto-resolve:** `O2_SERVICES_VMID`/`O2_SERVICES_NAME` (+ optional `O2_<SVC>_PORT`) in adapter.conf
> let `o2 info` resolve the docker-host VM's live IP and derive all five endpoint URLs itself, instead
> of an operator hand-copying each `O2_<SVC>_URL`; an explicit `O2_<SVC>_URL` still wins per-service.
> New `axrange/adapter_proxmox/resolve.py` holds every best-effort (short-timeout, never-raise) live
> lookup — kept structurally separate from the fail-loud, full-timeout client the teardown-protect
> guard and deploy ops use (LES-128). **Part C** — `o2 info`/`tap info` resolve each `EXTERNAL_SENSORS`
> entry's live name + mgmt IP. **Part D** — `te info` gained a per-node access block (ip / login user /
> effective password / a paste-ready `ssh`/`rdp` hint); `--no-secrets` masks passwords in text output
> (`--json` always carries the real value); a router with no `configure-cloud-init` op (VyOS) shows
> `(key auth)` with no password hint. No version-locked schema changed; no new op type. **Unit/suite-
> verified — 1587 passed / 12 skipped / 0 xfailed, 1599 collected; **dev45 live-verify PASSED
> 2026-07-25** (read-only `te info`/`o2 info` endpoint + `XPress-SW` resolution + graceful degradation,
> then a throwaway `te` deploy proving a real password-only SSH login into a fresh Linux node before a
> clean explicit-id destroy). `list-deploys` gained a `tier` column + dynamic-width alignment
> (2026-07-25 follow-up).**

### 2026-08-04 — Role and persona become `tfc`-owned (+ W1 build)

> **Role and persona become `tfc`-owned — DESIGNED 2026-08-04, two OPEN work orders; the inert-field
> sweep half is BUILT (CHANGELOG 2026-08-04).** Triggered by an operator finding that changing a
> deployed node's persona appeared to require a redeploy. Diagnosis: **personas were already a separate
> tier** (`tfc`, live-verified 2026-07-25) — three unrelated things made it look otherwise. (1) A twin
> config's `nodes[].persona:` is **inert** (read only by a catalog typo-warning, never written to the
> IR); (2) the published `hosts[].role` is baked into **`clone-vm`'s persisted inputs**
> (`pass1_emit.py:626` → `inventory.py:71`) — the literal reason a role change needs a re-clone; (3)
> a traffic profile's `twin:` is a **required literal deploy id** (`traffic.py:591`) with no name
> resolution and no `--target`, so a checked-in profile is welded to one minted deploy id — the exact
> trap tap plans had fixed 2026-07-25, one day after `tfc` was built.
> **`docs/WORK_ORDER_role_persona_tier.md` (W1, OPEN)** makes role and persona both `tfc`-owned and
> runtime-swappable: `twin_config.nodes[].role` splits into `name:` (id stem) + optional `role:`
> (baseline), a new `roles:` block in the traffic profile overrides it, `build_twin_inventory` resolves
> role as *tfc assignment → clone-vm baseline → none*, and profiles gain tap-parity targeting
> (optional `target:`, name resolution via the existing `taps.resolve_target_deploy`, `--target`).
> One version-locked bump: **`OP_CATALOG` 39 → 40** (`OP-TFC-020 assign-role`, pure metadata, no
> adapter call). **Decided, not deferred: no sixth `_VALID_TIERS` tier** — a role tier would create
> zero cluster resources and its destroyed state is incoherent (peer resolution breaks), so it follows
> `tap`'s precedent of not being promoted; map duplication across profiles is solved by the loader's
> existing YAML includes. **`docs/WORK_ORDER_twin_incremental_update.md` (W2, OPEN)** builds
> `te deploy --update` — add/remove nodes against a standing twin by **pinning the standing deploy's
> `plan_id`** (`vmid_for_op`/`vm_name_for_host`/zone/vnet/bridge are all plan-keyed, so a fresh
> plan_id would renumber the whole twin) and diffing op-id sets; **in-place mutation is refused loudly**
> (PLANNER §9.2's redo table is deferred). Closes GAP-040. **Landed already (CHANGELOG 2026-08-04):**
> the five inert twin-config fields with no replacement to ship — `segments[].purpose` (18 shipped
> uses, migrated to YAML comments), `nodes[].identity_role`, `conduits[].policy`, top-level
> `identity.groups`/`.ous` — plus `expand.py`'s `_reject_removed_keys` tables generalizing the old
> one-off `tap_points:` check. Proven inert: all 5 shipped configs produce byte-identical
> `plan_id`/`ir_hash` before and after. `nodes[].persona` is deliberately held for W1, which ships the
> `tfc-*.yaml` profiles carrying its intent. Two IR-side inert fields found in the same sweep
> (`Activity.adversary`, `Network.isis_area`) are **reported, not removed** — the IR is version-locked
> (`docs/PRODUCT_GAPS.md` GAP-049). See LES-172 for the audit method.
>
> **W1 BUILT 2026-08-04 (CHANGELOG "Role and persona become `tfc`-owned"); gated `dev45`
> live-verify PASSED 2026-08-04 (all 8 proofs; CHANGELOG "W1 … live-verify PASSED").** The
> headline held on-cluster: a role+persona swap on a running host touched **no VM** (worker vmid
> identical, uptime +29.3s monotonic, twin op-rows md5-identical, old persona unit gone/new
> running), personas put 336 real packets on the wire at `o2-suricata`, and teardown returned
> dev45 to its exact baseline. **The run also found and fixed FOUR bugs the 1731-test suite could
> not catch** — each hid behind the name-target / minimal-twin path no unit test exercised
> (LES-001's family): (1) a name-target spec rebuild dropped the new `roles` field
> (`dataclasses.replace()` at 6 sites, LES-173); (2) the persona launch died with no
> `/var/log/axrange` on a plain host (`mkdir -p` at the shared systemd primitive, LES-174); (3)
> `tfc`/`tap destroy --target <name>` never resolved the name (mirror the apply-path resolution on
> destroy, LES-175); (4) a duplicate cascade block in `cmd_destroy` left over from the LES-171
> move ran the cascade twice (dead block deleted, LES-176). One cosmetic finding logged as GAP-050
> (mirror-snippet teardown accounting). **3131 pytest tests** (+4 regression, each proven to fail
> without its fix). All of the above landed: `OP-TFC-020 assign-role` (**`OP_CATALOG`
> 39 → 40**, version-locked, Authority: user — pure metadata, no adapter call, and the only op
> with empty `resource_categories`); `build_twin_inventory(..., role_overrides=)` resolving
> *tfc assignment → clone-vm baseline → None* with `HostInfo.role_baseline` published
> alongside; the traffic profile's `roles:` block (applied BEFORE assignments — load-bearing
> ordering, test-pinned) with a `_retire_dropped_roles` de-scope pass; tap-parity targeting
> (optional `target:`/`twin:` alias, name resolution **delegating to**
> `taps.resolve_target_deploy` rather than a second copy, `--target` on validate/plan/deploy/
> info + API); `roles_from:` for sharing one role map across a twin's profiles; the twin-config
> `name:`/`role:` split; **`extra="forbid"`** on the twin-config models (Authority: user); and
> `persona_check.py` retired with the field it validated. **5 `tfc-default.yaml` profiles ship**
> beside the configs carrying the removed `persona:` intent, plus
> `corp-enterprise/{roles.yaml,tfc-busy.yaml}`. **All 5 shipped configs keep byte-identical
> `plan_id`/`ir_hash`** across the split — now a permanent regression pin, because `plan_id`
> keys VMIDs/VM names/zone/vnets/bridge and finds a standing deploy (LES-126). **1741 pytest
> tests.** One correction recorded in the work order rather than quietly fixed: §3.1's original
> "YAML includes already solve map duplication" rebuttal was **wrong** (includes run only in
> `parse_ir`, never for a profile), so the build added `roles_from:` to make the argument that
> justified not building a sixth tier actually true.

### 2026-07-28 — Persona volume lever + high-volume catalog wave

> **Persona volume lever + high-volume catalog wave — BUILT 2026-07-28 (CHANGELOG; Authority:
> implementation (Claude Code)).** The `scripted` persona driver's documented "v1 connectivity-shape
> emitter" deferral turned out to cap a whole persona **instance** near **~5 tiny TCP
> connections/second (~2 kbit/s)** — three constants compounding (`_VOLUME_MESSAGES` topping out at
> 24, a hardcoded 0.2 s inter-message gap, and a single-threaded loop over all emits, so the gap
> bounded the persona rather than one emit), with `volume:` controlling connection COUNT and never
> byte size. **Fixed in the driver, not the catalog:** a volume level now resolves message count +
> **bytes per message** + gap together (`scripted._VOLUME_PROFILE`) with a new **`bulk`**
> elephant-flow tier; emits gain **`transport:`** (tcp/udp, *derived from `proto`* when unset — so
> dns/ntp/snmp/syslog/netflow/ike/wireguard stop being driven over TCP), an explicit **`bytes:`**
> override, and **`peer_scope: all`** fan-out across every twin host holding a role (excluding the
> instance's own host, LES-170); `RESERVED_PEER_ROLES` gains **`broadcast`/`multicast`**, resolved
> by the driver to literal wire addresses so segment chatter is expressible at all. A **`burst:`
> cadence bug** was fixed in the same pass: it hardcoded `interval_s = 86400.0` regardless of the
> authored duration, so `corp-workstation`'s `burst:30m` print emit fired **twice a day**; both
> cadence kinds now mean "every `<dur>`" and differ in shape (burst = back-to-back, cyclic = paced).
> **Catalog 20 → 28 personas**, chosen by *load shape* rather than role: `voip-softphone` (the only
> constant-bitrate UDP), `streaming-media-client`, `ci-build-farm`, `db-replica`, `log-shipper` (high
> pps / low bytes-per-packet — the profile that overruns a capture ring), `broadcast-chatter` (the
> only broadcast/multicast persona — **and the benign LLMNR/NBNS substrate `ad-domain-lab`'s
> LLMNR-poisoning step never had to hide in**), `scanner-benign` (an *authorized* scan: a
> false-positive source that forces detection rules to be tuned on more than "saw a port sweep"), and
> `p2p-update-peer` (the only mesh — flow count grows with the square of the twin). **`VALIDATOR` 112
> → 114** (new `PSN-100` transport enum, `PSN-110` peer_scope enum; `PSN-070` widened for `bulk`) —
> a version-locked contract touch, additive and non-breaking. No IR/PLANNER/OP_CATALOG/ADAPTER/
> IMAGE_MANIFEST change; no new op type; no schema-version bump. **Verified by a functional wire test,
> not only render assertions** — the rendered client run against local sinks produced **1269
> datagrams / 1.77 MB in 4 s (~3.5 Mbit/s)** from one `volume: high` UDP emit; **3131 pytest tests
> pass**; all 28 personas validate clean. **`dev45` live-verify PENDING.** **Two honest limits going
> in:** TCP byte volume only materializes against a real listener (a refused connect is 2 packets, not
> 64 MiB — the guest log's new `fails=` counter is the tell), and emits targeting the reserved
> `internet`/`egress` tokens still put **nothing** on the wire (`docs/PRODUCT_GAPS.md` **GAP-048**,
> new — 11 shipped emits affected, incl. all of `egress-proxy`'s northbound traffic). See LES-169/
> LES-170 and `docs/design/07-traffic-personas.md`.

### 2026-07-28 — The twin substrate becomes tap-agnostic

> **The twin substrate becomes tap-agnostic — BUILT 2026-07-28 (`docs/WORK_ORDER_tap_eligibility.md`,
> decisions D1–D6 + `STR-200`, all **Authority: user**; CHANGELOG "…").** Closes the last te-side
> tap-authoring surface, superseding several of the tap-related claims in the two blockquotes above
> (pointers left in place there rather than rewritten, per this file's own "record, don't rewrite
> history" discipline). **No `tap:` field survives anywhere in the Scenario IR** — `nodes[].tap` /
> `nodes[].nets[].tap` / `networks[].tap` are all REMOVED, and a leftover one now fails validation
> loudly rather than silently vanishing (`STR-200`, new). **No named/labeled tap-point catalog** —
> `TwinConfigTapPoint`/`_apply_tap_points`/`TapPointMaterializationError` are removed; a twin
> config's `tap_points:` key now errors instead of silently doing nothing. **No published inventory
> `tap_points[]`** — `te inventory` / the API DTO / `twin-config show` publish only the twin's full
> `hosts[].nets[]` topology; `derive_default_tap_points` survives as *internal*, unpublished
> resolution machinery. **`OP-MIR-005 record-tap-intent` is REMOVED** (op count 40 → 39) — a `te`
> deploy now emits **zero** OP-MIR ops of any kind, not even a declarative bookkeeping one. **The
> tap plan is the sole authority on capture**: `scope`/`target`/`tap_point:`/`selector:` addressing
> (`selector:` drops `labels:` matching — D3 removes the field it matched on) resolves directly
> against the twin's full published topology — unconditional, every interface `attach-vm-to-vnet`
> ever attached, no allow-list — so `segment` finally means "every port on the net," including the
> WAN uplink (no longer a special case: `perimeter: wan` resolves the ordinary way, since `__wan__`
> is just another entry in the full topology now). A non-blocking capacity-guardrail warning fires
> at `tap plan`/`tap deploy` when a resolved plan's port count or fan-out crosses a large threshold,
> since full-topology eligibility makes "bind everything to one sensor" trivial to author. Also
> removes the **`tap_host`/`tap_net` → `sensor` binding kind** from `bindings:` (D6; `REF-200`
> retires with it, caught instead by `STR-100`'s closed binding-kind set). **Version-locked bumps,
> all Authority: user:** `scenario_schema_version` recognized set `{1,2}` → `{1,2,3}` (`STR-020`) —
> the one genuinely **breaking** removal in that set's history, unlike every prior additive bump;
> `OP_CATALOG` 40 → 40 op types; `VALIDATOR` 114 → 112 rules (retires `STR-190`/`NET-110`/`REF-200`,
> adds `STR-200`). **Content migration:** ~63 `tap:` flags stripped back out across the same 11
> `target-environments/` files the earlier blockquotes describe adding them to, and the
> `tap_points:` block removed from all 5 twin-configs; the shipped `corp-enterprise` tap plans
> (already `selector: {scope, target}`-only) are unaffected. **Closes `docs/PRODUCT_GAPS.md`
> GAP-028** (eligibility no longer needs hand-flagging at all — closed via full-topology access
> rather than the hand-flagged content the prior fix added, which this build then removed again)
> **and dissolves GAP-030** (the `labels[]` field it tracked as inert is gone, not merely unwired).
> **Unit/suite-verified — 1683 passed / 12 skipped / 0 failed, 1695 collected.** **Gated `dev45` live-verify
> PASSED 2026-07-28** — a fresh `tap-elig-verify` twin (required per LES-096: an already-`completed`
> `record-tap-intent` op on a pre-existing deploy can't retroactively vanish, so a `--fresh`
> redeploy, not a resume, was needed before the tap plan had full topology to resolve against rather
> than a stale recorded subset) confirmed `segment` resolves every port on the net including the
> router's own leg (rx-only), `perimeter: wan` resolves the WAN uplink, on-wire capture showed each
> frame exactly once with no duplication, and teardown was clean. Two pre-existing, unrelated bugs
> surfaced during the run — `docs/PRODUCT_GAPS.md` GAP-046/GAP-047 (neither blocked the deploy). See `docs/design/06-taps-sensors.md` for the
> current tap-eligibility model and `docs/WORK_ORDER_tap_eligibility.md` for the full decision record.

### 2026-07-23 → 2026-07-29 — Twin-centric re-frame (the five-tier twin model)

> **Twin-centric re-frame — design ratified 2026-07-23 (`docs/design/02-twin-model.md`), keystone
> BUILT the same day (`docs/WORK_ORDER_tier_split.md`, CHANGELOG "Tier split KEYSTONE").** The
> platform re-centers on a five-tier digital-twin pipeline (Lab `o2` → Twin `te` → Traffic `tfc` +
> Tap `tap` → Exercise `exr`); two new tier tags (`tfc`/`exr`) extend the existing `te`/`tap`/`o2`/`img`
> vocabulary (`docs/CONVENTIONS.md` §2). **What's built today:** the `tfc`/`exr` name-shape producers
> + predicates, `planner/teardown.py`'s generalized six-tier protect/cascade matrix, and a twin's
> published inventory (`axrange/planner/inventory.py`; `axrange te inventory [ID] [--json]` /
> `GET /api/deploys/{id}/inventory`) — substrate only, no schema-version bump, no live-verify gate
> needed. **`tap_points` BUILT 2026-07-24** (`docs/WORK_ORDER_tap_points.md`, CHANGELOG "Tap points
> BUILT"; unit/suite-verified, gated `dev45` live-verify pending): a new engine module
> `axrange/planner/tappoints.py` gives the `tap` tier a **semantic, topological tap-point
> vocabulary** — originally `segment`/`boundary`/`host`/`leg`-scoped named capture places (no
> `direction` field anywhere, per brief §4.1) — that `axrange/o2control/taps.py`'s tap-plan bindings can now
> address via `tap_point:`/`selector:` alongside the original mechanical `tap:` interface id
> (**the named/labeled catalog this introduced is itself RETIRED 2026-07-28** — see the "twin
> substrate becomes tap-agnostic" blockquote below; `tap_point:`/`selector:` addressing survives,
> now resolved as a derived id against the twin's full topology rather than a published catalog);
> **re-based on a SPAN model 2026-07-27 and BUILT the same day** (`docs/design/06-taps-sensors.md`,
> `docs/WORK_ORDER_tap_scopes.md`, CHANGELOG "Tap scopes BUILT"): the scope set is now **`port` /
> `segment` / `perimeter` / `boundary`** (`leg`→`port` rename, old `host` scope dropped, `perimeter`
> added — every `kind: router` port on a net, including `perimeter: wan` for the twin's external
> edge) and each scope now carries a **capture direction derived from the scope, never authored**
> (`rx` for multi-port sets, `both` for single-port) so a multi-port tap is no longer captured
> 2..N times. Closes the WAN-egress half of `docs/PRODUCT_GAPS.md` GAP-029 — **but only on a twin
> deployed fresh after 2026-07-27** (LES-096: an already-`completed` `record-tap-intent` op never
> republishes the new WAN-tappable input; needs `--fresh`). Gated `dev45` live-verify
> **PASSED 2026-07-28** — a fresh twin (`tap-elig-verify`) confirmed a `segment` tap point resolves
> every port on the net including the router's own leg (rx-only), `perimeter: wan` resolves the WAN
> uplink, and on-wire capture showed each frame exactly once with a clean teardown (CHANGELOG
> 2026-07-28 "Live-verify (dev45, 2026-07-28)").
> `mode: exclusive` is enforced across a whole plan's bindings, not just per-entry. Decided (not
> merely deferred): `tap` stays a resource name-shape tag, not a promoted `_VALID_TIERS` deploy
> tier (`docs/CONVENTIONS.md` §2, `docs/design/06-taps-sensors.md`). Eight `target-environments/`
> archetypes gained `tap: true` on their boundary-forming router/leaf/border nodes so `boundary`
> points (e.g. `ot-ics-range`'s IT/OT conduit) actually resolve against real content — closes
> GAP-028 *(superseded 2026-07-28: this hand-flagging approach and the `tap:` flags it added are
> themselves REMOVED — see below; GAP-028 stays closed via full-topology eligibility instead)*.
> **Tap plans are now predefinable (2026-07-25, `docs/WORK_ORDER_tap_target_resolution.md`,
> CHANGELOG; `docs/design/06-taps-sensors.md`):** a tap-plan's `target:` is OPTIONAL and, when
> given, may be a scenario/twin-config **name** (resolved to the newest live `te` deploy of that
> scenario) instead of a literal deploy id, and `tap validate`/`plan`/`deploy` all take a `--target`
> override — a plan file can now be checked into a library the way `twin-configs/` are, instead of
> being welded to one minted deploy id (partially closes the LES-072 staleness trap for name-authored
> plans; a plan hardcoding a literal deploy-id `target:` remains subject to it). **`persona_library` + `twin_configs` BOTH BUILT 2026-07-24** (parallel wave,
> `docs/WORK_ORDER_persona_library.md` + `docs/WORK_ORDER_twin_configs.md`, CHANGELOG "Persona
> library BUILT" + "Twin configs BUILT"; unit/suite-verified — **3131 pytest tests**; both gated
> `dev45` live-verifies **PASSED 2026-07-25** — persona drivers in full, twin-configs for
> `corp-enterprise` only (`sme-flat` still open — see the twin-model live-verify blockquote below)).
> **`axrange/personas/`** is a new engine-vocabulary registry
> (modeled on `axrange/sensors/`) — 20-persona starter catalog + three drivers
> (scripted/replay/real-service, all launching via detached transient `systemd-run` units, never
> `nohup`) + a new **`PSN-010..090`** validator category (**catalog rules 104 → 113**, catalog-side
> only, not part of the IR 4-pass pipeline — `docs/VALIDATOR.md` §5.12). **`axrange/twinconfig/`**
> is a parameterized-IR-template expander (`${NAME:default}` param substitution — closes the
> deferred `docs/OPEN_QUESTIONS.md` Q2 follow-up) + the five `twin-configs/<id>/<id>.yaml` starter
> configs (`sme-flat`/`corp-enterprise`/`datacenter`/`ics-ot`/`dmz-perimeter`) — reorganized
> 2026-07-26 into per-config subfolders, each holding its config + its portable name-targeted
> `tap-*.yaml` tap-plans (`corp-enterprise/` ships `tap-default`/`tap-reshard`/`tap-xpress`);
> discovery is recursive, flat layout still accepted — wired at `axrange te
> {validate,plan,deploy} --config <id>` + `axrange twin-config list|show`; expansion produces a
> plain Scenario-IR dict the unchanged validator/planner consume — **no `IR_SCHEMA` bump**. Neither
> build wired anything to a Scenario IR/binding at the time — that's now the **Traffic tier's** job.
> **`traffic_tier` BUILT 2026-07-24** (`docs/WORK_ORDER_traffic_tier.md`, CHANGELOG "Traffic tier
> (`tfc`) BUILT"; unit/suite-verified — 1370 passed / 12 skipped / 0 xfailed, 1382 collected; gated
> `dev45` live-verify pending): `axrange/o2control/traffic.py` (a third standalone playout,
> modeled on `taps.py`) binds a **traffic profile**'s persona→host-role assignments onto an
> already-deployed twin via the persona drivers, using the new `OP-TFC-010 drive-persona` op (40th
> op type) and a real, persona-typed `role:` IR field it depended on (`node.role`,
> `scenario_schema_version` 1→2, **Authority: user** — a version-locked schema bump) — **resolves
> `docs/PRODUCT_GAPS.md` GAP-027**, `hosts[].role` is no longer always `None`. Idempotent apply with
> a de-scope pass (LES-112/LES-113) so a narrowed re-apply or persona swap can't leave a stale
> instance running. CLI `axrange tfc {validate,plan,deploy,status,info,destroy}` +
> `POST/GET/DELETE /api/tfc`, cascaded by a `te` destroy. **Still inert regardless (at the time):** a
> twin config's `tap_points[].labels`/`id` were consumed only to set boolean IR `tap:` flags then
> dropped — label-based tap-plan *selection* (brief §4.1's own headline example) stayed inert
> (`docs/PRODUCT_GAPS.md` GAP-030) — `traffic_tier` was never that gap's consumer. **Dissolved, not
> merely closed, 2026-07-28:** the twin-config `tap_points:` block and the `tap:` flags it set are
> both REMOVED (`docs/WORK_ORDER_tap_eligibility.md` D3) — see the "twin substrate becomes
> tap-agnostic" blockquote below.
>
> **`exercise_tier` BUILT 2026-07-24 (`docs/WORK_ORDER_exercise_tier.md`, CHANGELOG "Exercise tier
> (`exr`) BUILT") — THE FIVE-TIER TWIN MODEL IS NOW COMPLETE, all six work orders code/docs-done.**
> `axrange/o2control/exercise.py` is a fourth standalone playout: a `tier="exr"` **ephemeral** deploy
> binding to an already-deployed `te` twin, reusing `run_deploy_executor`/`destroy_deploy` and the
> existing `OP-ACT-010/020/030` + `OP-OVL-070` ops (no new op type — 40 stays 40). **This is the
> scope shift:** a `te` deploy's own DAG no longer contains `_emit_activity` or the `overlays.
> adversary` install-agent sweep at all — `axrange deploy`/`axrange te deploy` **no longer runs
> attack playout, adversary agents, or measurement**, only the environment itself (infra/apps/
> identity/data/defense overlays, unchanged). Any earlier statement above describing
> `overlays.adversary`/`activity` as emitted "inside the `te` deploy" describes the
> **pre-2026-07-24** shape — see `docs/design/08-exercise-measurement.md` for the current
> Exercise-tier design. New validator **`REF-260`** (WARNING, 113→114) fires when a `te`
> IR still declares `activity:`/`overlays.adversary:`, so `ad-domain-lab`/`purple-team-range`/
> `soc-detection-lab` keep validating and become `exr`-consumed content, not edited. `playout_hash()`
> (the existing `ir_hash` column, no schema bump) makes replay-identity assertable. CLI `axrange exr
> {deploy|run, status, destroy}` + `POST/GET/DELETE /api/exr`, cascaded by a `te` destroy alongside
> `tap`/`tfc` (each sibling cascade now individually isolated, LES-117). **Cascade ORDER corrected
> 2026-07-29 (CHANGELOG, LES-171):** the three siblings run **before** the twin's own sweep, in
> reverse-of-create order `exr` → `tfc` → `tap` → `te`, and the sequence now lives in ONE shared
> helper (`axrange/o2control/cascade.py`) both the CLI and API call. They previously ran *after* it,
> so the tap tier's per-VM mirror de-scope and the traffic tier's in-guest persona stop were always
> issued against already-purged VMs — best-effort steps, so a dev45 `corp-enterprise` destroy
> reported `Result: DESTROYED` under 55 `Configuration file … does not exist` warnings rather than
> failing. `docs/design/02-twin-model.md` had codified the wrong order and is corrected. **Unit/suite-verified — 1423
> passed / 12 skipped / 0 xfailed, 1435 collected; gated `dev45` live-verify PASSED 2026-07-25** — the
> AD kill chain ran as an `exr` against the standing `corp-enterprise` twin, including a real DCSync
> `krbtgt` dump, truth markers joinable in Loki, a clean ephemeral `exr destroy`, and an identical-
> `playout_hash` replay (CHANGELOG "Twin-model arc: dev45 live-verify Stage 5 (`exercise_tier`
> capstone) PASSED"); the significant finding was that twin-configs have no incremental add
> (`docs/PRODUCT_GAPS.md` GAP-040, LES-126), worked around by hand-provisioning the attacker's tooling
> onto an already-deployed host rather than a dedicated attacker node. Two honestly documented limits
> going in, still open: the same-host overlap guard only serializes *sequential* re-apply, not true
> concurrency (`docs/PRODUCT_GAPS.md` GAP-034, LES-116); no auto-reap — a run persists until an
> explicit destroy or cascade (`docs/PRODUCT_GAPS.md` GAP-035). This framing is additive to, not a
> rewrite of, the O2/sensors/A1–A4 material above — see the design brief for the full picture.

### 2026-07-17 — Standing-O2-sensor capture supervision + retention fix

> **Standing-O2-sensor capture supervision + retention fix — 2026-07-17 (`docs/CHANGELOG.md`).**
> A dev45 investigation found the standing O2 `tcpdump-sensor` **not actually capturing**:
> `deploy-sensor`'s render (`axrange/config_exec/render.py`) launched tcpdump/zeek via
> `nohup … &`, which is **reaped the moment the QGA guest-exec session that launched it ends** —
> the same failure class as GAP-020 (fixed there for the Loki shipper + suricata's `-D`, never for
> tcpdump/zeek). **Fixed:** tcpdump + zeek now launch as detached, deterministically-named transient
> systemd units (`systemd-run --unit=axr-sensor-<safe> --collect`) with a fail-loud
> `systemctl is-active` start check. Paired with a **retention overhaul** (user requirement — pcaps
> must persist across tap removal *and* sensor restart, not reset): timestamped accumulating pcaps
> (`cap-%Y%m%d-%H%M%S.pcap`, `-G 3600 -C 100`, no `-W` ring) plus an in-guest disk-%/file-count prune
> watchdog (`axr-cap-watchdog-<safe>`, 60s timer) ported from the standing appliance's own
> `o2/tcpdump-sensor/bin/capture-watchdog`; new `adapter.conf` knobs
> `CAPTURE_HIGH_WATER_PCT`/`CAPTURE_LOW_WATER_PCT`/`CAPTURE_MAX_FILES` (85/70/2000 defaults).
> Two review-caught bugs fixed in the same pass (see `docs/LESSONS.md` LES-085/LES-086): the
> watchdog's prune glob had to be `cap-*`, not `cap-*.pcap` (tcpdump's `-C` size-rotation counter
> lands *after* the `.pcap` extension, so an extension-anchored glob silently missed most rotated
> files under real traffic); and the rotation interval had to move from 5min to hourly to stop an
> idle mirror from growing an unbounded small-file count a disk-%-only watchdog can't see. No
> IR/PLANNER/OP_CATALOG/VALIDATOR/ADAPTER/IMAGE_MANIFEST schema change. `verify-capture` is
> unaffected (reads the wire live, never pcaps). **Unit-verified (3131 pytest tests); dev45 live-verify
> pending** (`docs/PRODUCT_GAPS.md` GAP-025).

### 2026-07-11 → 2026-07-15 — Sensor-agnostic `te_` substrate

> **Sensor-agnostic `te_` substrate — BUILT 2026-07-11→2026-07-13 (`docs/WORK_ORDER_sensor_agnostic_te.md`,
> CHANGELOG 2026-07-13 "sensor-agnostic te" design + build entries); gated `dev45` live-verify PASSED
> 2026-07-15 (CHANGELOG 2026-07-15).** **Superseded in part 2026-07-28
> (`docs/WORK_ORDER_tap_eligibility.md`, Authority: user, see the dedicated blockquote below):** the
> `OP-MIR-005 record-tap-intent` op this entry introduces is itself REMOVED — a `te` deploy now
> emits **zero** OP-MIR ops of any kind (not even the declarative bookkeeping op below), and A2
> discovers the tappable set directly from the twin's full published topology instead of this op's
> persisted output. The rest of this entry's scope shift (no `attach-gre-tunnel`/`install-ovs-mirror`/
> `verify-capture`/`mirror-ready` emitted by a `te` deploy) remains accurate. Closes the last
> O2-direction gap: a `te_` deploy now stands up **only its own components** — it emitted **`OP-MIR-005
> record-tap-intent`** (declarative, no adapter call) instead of `attach-gre-tunnel` /
> `install-ovs-mirror` / `verify-capture`, and no longer emits its own `mirror-ready` barrier. All
> tap→sensor binding + capture verification is now the **A2 tap playout's** job alone (`axrange o2 tap
> apply`), which discovers the tappable set from `record-tap-intent`'s persisted output (instead of
> `install-ovs-mirror`'s, its pre-build discovery source) and the sensor's `transport_ip`/`vmid` from
> A1's standing inventory. `SENSOR_TRANSPORT_IP` / `SENSOR_<ID>_TRANSPORT_IP` / `SENSOR_SSH_*` /
> `SENSOR_CAPTURE_IFACE` and the `sensors` map are **removed** from `adapter_proxmox/config.py` +
> `adapter.conf.example`; `--sensor-ip`/`sensor_transport_ip` are dropped from the te deploy path.
> `NETWORK_SENSOR_*` (Option C, the appliance-adjacent network sensor) is unrelated and **stays**.
> **Closes GAP-012 — now Resolved (live-verified)**, not just Resolved (build): the dev45 run
> confirmed `verify-capture` passes over QGA with no `SENSOR_SSH_*`/`SENSOR_TRANSPORT_IP` set
> anywhere. **A code-review fix landed in the same build widened the scope**: Sensors-design Phase
> B/C in-scenario sensor VM synthesis (`sensors:` → a synthetic node + `deploy-sensor`,
> `docs/design/06-taps-sensors.md`) is now `tier == "o2"`-gated — it fires only for the standing-surface
> deploy A1 builds, never for an ordinary `te`. **40 op types (34 action/wait + 6 barriers); 104
> catalog rules (new `REF-250` WARNING when `sensors:` is declared but not stood up; `NET-110`
> retargeted); 3131 pytest tests.** **Content side-effect (bigger than a wiring detail):**
> `ad-domain-lab`/`purple-team-range` still declare `sensors:` in their IR, but a `te`-tier deploy of
> either now synthesizes **no sensor VM at all** — capture requires a standing O2 sensor (`axrange o2
> up`) plus `axrange o2 tap apply` post-deploy; those archetypes validate (warning only) but no
> longer self-capture from a bare `axrange deploy`.

### 2026-07-08 → 2026-07-15 — Surface-layer direction (O2) + the O2 build arc

> **Surface-layer direction — O2, documented 2026-07-08 (`docs/design/05-o2-surface.md`, TARGET/not-yet-built):**
> the standalone **engine** (`axrange/`) stands up only a scenario's own components; everything
> *around* it is pooled into **O2**, a **surface layer stood up separately on request**. O2's
> components sort into **three functional planes (refined 2026-07-25, `docs/design/05-o2-surface.md`):
> watch / act / steer** — **Observability** ("watch": network sensors + external
> `packetXpress` + host agents → the **Loki**/**Prometheus** stores), **Orchestration** ("act":
> **Caldera** attack, **Wazuh manager** defense, `o2control` playout), and a **Console** ("steer +
> see", spanning both: **Grafana** + the control-panel plugin + the API/CLI). Grafana is the
> *console*, not a telemetry store; **Wazuh** is dual-role (agent = watch, manager = act); only
> **Loki + Prometheus** are pure telemetry backends. This **supersedes-in-direction** the in-scenario sensor model *above* and the in-deploy
> tap emission (both remain the current implementation until the O2 refactor lands): sensors become
> **standing** components, and **taps become a separate config playout** (deep-dive deferred). A
> folder rename `scenarios/` → `target-environments/` (TE) is done. Branch: `wip/restructure`.
>
> **Blast-radius tiers — IMPLEMENTED 2026-07-08 (`docs/WORK_ORDER_resource_tags.md`; CHANGELOG
> 2026-07-08 "resource ownership tags").** Resources are tier-tagged in their deterministic names
> (`axrange/adapter_proxmox/naming.py`): **`te`** (target-env deploy — `te<6hex>` zone, `tev<5hex>`
> vnet, `tebr_*` bridge, `te-<6hex>-<host>` VM) / **`tap`** (mirror+source-gretap binding —
> `tapg_<6hex>` gretap, `tapm-<vmid>` mirror) / **`o2`** (standing surface — `o2v*`/`o2br_*`/`o2-*`,
> defined + protected but **not yet produced** by any live emission path — the O2 control layer
> creating them is separate, unbuilt work) / **`img`** (golden templates — `img-<image-slug>`).
> `planner/teardown.py`'s hard `_protected()` guard refuses + records a `guard-violation` on any
> `is_o2`/`is_img` match across every resource kind including the VM path, so a `te` destroy
> structurally cannot touch a standing surface component or a golden template. The former
> `is_axr_*` shape-matching predicates are retired. **Unit/suite-verified (3131 pytest tests) +
> LIVE-VERIFIED on `dev45` (2026-07-13, CHANGELOG):** a tapped `tap-verify` deploy produced correctly
> tier-tagged `te_`/`tap_` resources; `axrange destroy` swept all 8 while leaving `img-*` templates +
> unrelated VMs untouched (idempotent re-destroy clean). The `axr_*`→`img-*` template rename was a
> non-event — dev45's templates were already born `img-*` by `axrange image build`. `docs/WORK_ORDER_resource_tags.md`
> §5 is closed. (Two orthogonal non-tag findings logged: GAP-012 `verify-capture` needs `SENSOR_SSH_HOST`;
> GAP-015 the default-ON exporter agent assumes egress a no-router scenario lacks.)
>
> **O2 control component (`axrange/o2control/`) — DESIGNED 2026-07-08 (`docs/design/05-o2-surface.md`):**
> the linchpin that stands up the standing surface. Key call: it is a **thin engine *client***, not a
> second orchestrator — it **reuses the engine (planner+adapter+state) as a spec executor**, standing
> sensors up as a **persistent `o2_`-tagged deploy** (the engine already deploys sensors; O2 control
> just owns the trigger + standing lifecycle). Follow-on designs plug into its inventory: **A2** tap
> playout, **A3** attack/defense playout.
>
> **A3 attack/defense — DESIGNED 2026-07-09 (`docs/design/08-exercise-measurement.md`):** the TE
> substrate is a living, agent-instrumented **world**; the attack/defense/telemetry **servers live in
> O2**; red/blue **playout is manual** (operator-driven) for now. `overlays = identity + data +
> defense + adversary`; **defense retained** as blue **host** agents. Three default agent→server pairs:
> Prometheus exporter→Prometheus server, **Sandcat** (`overlays.adversary`)→**Caldera server**, **Wazuh
> agent** (`overlays.defense`)→**Wazuh server**. Attack is **dual-mode** — internal `activity`
> (engine-run; the AD kill chain stays here) or external Caldera (manual). Deferred: playout automation;
> **A4** measurement & scoring. **Superseded-in-direction 2026-07-24:** `activity`/`overlays.
> adversary` no longer run/emit from the TE substrate — they moved to the standalone Exercise tier
> (`exr`, see the "Twin-centric re-frame" blockquote below); this paragraph is kept as the historical
> A3 design record — see `docs/design/08-exercise-measurement.md` for the current Exercise tier design.
>
> **A4 measurement & scoring — DESIGNED 2026-07-09 (`docs/design/08-exercise-measurement.md`):** fills the
> explicitly-missing **Tier-4 detection scoring** — an **ATT&CK-technique coverage matrix** per attack
> `run-id`. Data spine: attack **truth markers** (`[axr-truth] run/technique/host/ts`, emitted by the
> activity runner — the one new stream) + O2 **detections** (Suricata/Wazuh/audit) both in **Loki**,
> joined by `technique + host + Δ-window`. **Manual Grafana coverage view** now; automated scorer
> (`o2control`) + the **replay-delta** (run1 vs run2) later. Only meaningful because **seeded activity**
> (SLICE_ACTIVITY §11) makes replays identical — A4 + seed = the "validate defenses" proof.
>
> **A2 tap config playout — DESIGNED 2026-07-10 (`docs/design/06-taps-sensors.md`):** the `tap_` tier's own
> lifecycle — a standalone **tap-plan** (`tap → [sensors]` bindings: **1:N fan-out** or **per-tap
> exclusivity**) binds a deployed TE's tappable interfaces to standing O2 sensors, reusing the OP-MIR ops
> via an O2-control playout against A1's inventory; idempotent `tap_`-scoped **apply/destroy** (the
> standalone `destroy taps` verb), independent of `te_`/`o2_`. VM sensor types (**packetXpress**) are
> interchangeable gretap endpoints; fan-out duplicates on the transport wire (a capacity note). Closes
> the O2 design arc (A1→A4 + A2).
>
> **Build handoff (2026-07-10):** the O2 arc is now five `/dev` Pipeline work-orders —
> `WORK_ORDER_o2_control.md` (**A1**, keystone) · `_taps.md` (A2) · `_attack_defense.md` (A3) ·
> `_measurement.md` (A4) · `_schedule.md` (activity scheduler). Sequence: `schedule` ∥ A1 → A2 ∥ A3 → A4.
> Still-gated live-verifies: `WORK_ORDER_{resource_tags,sink_rename}.md`. **packetXpress** VM sensor:
> its own design (image, in-scenario decode, downstream shipping), still deferred — **but a
> config-declared EXTERNAL sensor as a tap target (`docs/design/06-taps-sensors.md`, CHANGELOG
> 2026-07-21) partially un-defers it, and is now LIVE-VERIFIED END-TO-END on dev45 (2026-07-21,
> `docs/PRODUCT_GAPS.md` GAP-026, closed)**: a hand-provisioned packetXpress sink (`XPress-SW`) named
> in a tap plan via `adapter.conf`'s `EXTERNAL_SENSORS`, auto-protected from every tier's teardown,
> with `verify-capture` intentionally (and loudly) skipped since axRange's own tooling never reaches
> into it (QGA guest-exec disabled on the box; a manually-granted SSH credential is a separate,
> operator-driven channel used to fix a NetworkManager DHCP-profile gotcha, `docs/LESSONS.md`
> LES-092) — a real `tap-verify` deploy + tap-plan bind proved the mirror path end-to-end (44
> decapsulated frames on the sink's `gre-cap`) and a subsequent `tap destroy` + `te destroy` proved
> the sink is left completely untouched. **Follow-on work
> orders:** `WORK_ORDER_sensor_agnostic_te.md` (BUILT
> 2026-07-13, **gated live-verify PASSED 2026-07-15**) and, opened by that build,
> `WORK_ORDER_o2_sensor_rules.md` (OPEN — standing O2 sensors need per-scenario detection rulesets;
> the AD purple-team detection loop is dark until it lands).
>
> **O2 build arc — ALL FIVE BUILT (2026-07-11); gated `dev45` live-verify batch run 2026-07-15
> (CHANGELOG 2026-07-15).** The standing O2 surface layer now has: **A1** `axrange/o2control/`
> (stands up sensors as an `o2_`-tagged persistent deploy via `axrange o2 up|status|down` /
> `/api/o2/surface`; `tier` threaded through plan/deploy/naming/state/executor/teardown —
> `state.py` **SCHEMA_VERSION 2→3**, re-initialize any pre-2026-07-11 `state.db`) — **live-verify
> PASSED**; **A2** the tap config playout (`axrange/o2control/taps.py` — a standalone `tap-<te>`
> deploy binding a TE's taps to standing sensors, 1:N fan-out or per-tap exclusivity, `axrange o2 tap
> apply|destroy` / `/api/o2/taps`, cascaded by a `te` destroy) — **live-verify PASSED**; **A3**
> attack/defense — Stage A substrate default agents (`overlays.adversary`/`AdversaryEntry`,
> `OP-OVL-070 install-agent`, `scenario.default_agents` **default-ON — Authority: user, 2026-07-11**)
> + Stage B O2 servers (Caldera + Wazuh-manager under `o2/compose/`, human-gated) — **live-verify
> PASSED 2026-07-15** (dev45, dedicated O2-services VM 102: Sandcat enrolled in Caldera, a Wazuh
> agent enrolled in the manager; the Prometheus-exporter half of the three agent→server pairs was
> descoped, `docs/PRODUCT_GAPS.md` GAP-022); **A4** measurement & scoring (`technique:` labels,
> `[axr-truth]` Loki markers, `axrange/measurement/`, the Grafana attack-coverage dashboard) —
> **live-verify PASSED 2026-07-15** (both the truth-marker and suricata-detection streams landed in
> Loki, joinable by technique+host+window; two bugs found and fixed in the same run — GAP-020, a
> self-killing `pkill` + a QGA-reaped `nohup` in the shared `export.ship: loki` bootstrap, fixed with
> a named `systemd-run --unit=` transient; GAP-021, the truth shipper needs TE-host egress, worked
> around for the verify, open design question left standing); and the **activity schedule executor**
> (`schedule.interval` seeded jitter, `Schedule`/`Activity.seed`) — **live-verify PASSED** (with a
> determinism-check substitution, see CHANGELOG 2026-07-15). New validator rules REF-220/230/240 +
> ACT-060/070 (98→103). **Unit/integration-verified (3131 pytest tests); ALL SIX gated `dev45`
> live-verifies now PASSED (A1, A2, sensor-agnostic-te, SCHED, A3, A4) as of 2026-07-15** — the O2
> server stack (Prometheus/Loki/Grafana/Caldera/Wazuh) is standing on a dedicated rootful-docker VM
> (`docs/WORK_ORDER_o2_server_stack.md`, DONE); GAP-016/017/018 are Resolved (live-verified); the
> pre-existing `WORK_ORDER_{resource_tags,sink_rename}` live-migrations remain PENDING (unrelated).
> See CHANGELOG 2026-07-11, CHANGELOG 2026-07-15, and each design brief's built-status header for
> detail.

### 2026-06-23 — Capture-plane direction (sensors unify into one typed `sensor`)

> **Capture-plane direction — ratified 2026-06-23, not yet built (`docs/design/06-taps-sensors.md`):**
> the standing **capture appliance** + Suricata **network sensor** unify into one typed, in-scenario
> **`sensor`** (`tcpdump-sensor` / `suricata-sensor` / `zeek-sensor`, open registry), declared in
> a top-level IR `sensors:` block and **deployed as VM(s) with the scenario** (≥1 per scenario).
> The per-tap `tc`→keyed-gretap mirror framework is kept — only the gretap **`remote` re-points
> from `SENSOR_TRANSPORT_IP` to the deployed sensor's transport IP**. The "Mirror plane" / "Capture
> appliance" rows above describe the **current pre-refactor** plane; the sensors brief is the agreed
> next shape. Branch: `wip/scenario-sensors`.

---

## Immediate Next Actions — archived progress narrative

Three pieces of `CLAUDE.md`'s `## Immediate Next Actions for Claude Code` section, moved out here in the same reverse-chronological spirit (newest/most-recently-written first).

### The "Eight implementation slices have landed" preamble

This was the paragraph immediately under the `## Immediate Next Actions for Claude Code` heading, before the numbered active-thread list. The counters claim and the still-current facts at its end (Stage 6 open, the two-cluster live-verify, resume-safety) stayed in `CLAUDE.md`, quoted verbatim inside its replacement sentence; this is the full original paragraph.

Eight implementation slices have landed: the **capture appliance** (`o2/tcpdump-sensor/`), the **`axrange/` IR + validator + op-registry slice** (commit `47c86b0`), the **planner-execution + Proxmox-adapter slice** (with SDN-native infra, Option 1 capture via per-tap `tc` → keyed gretap → flow-based sensor, per-tap sensor routing, and cross-process resource locks all implemented + live-verified), the **imagebuild slice** (`axrange/imagebuild/`; + **Windows W0** — ISO/autounattend Windows-Server build, implemented + **live-verified on dev45** (template 102, 4/4 probes), `docs/SLICE_WINDOWS_AD.md`), the **config-class execution slice** (`axrange/config_exec/` — `OP-APP provision-app` + `run-script` via the QGA channel), the **router slice** (`docs/SLICE_ROUTER.md` — Phase 1 SDN-SNAT egress + Phase 2 router node (Debian/nftables) + VyOS fold-in via `vyos-ssh-set`), the **dynamic-tap slice** (`docs/design/06-taps-sensors.md` — originally declarative `tap:` at host/leg/segment, resolved into a tapped-interface set driving OP-MIR; **the `tap:` IR flags are retired 2026-07-28** (`docs/WORK_ORDER_tap_eligibility.md`) — a `te` deploy now emits zero OP-MIR ops, and the tap plan resolves directly against the twin's full published topology instead; + `OP-MIR-040 verify-capture`, the deploy-time capture heartbeat gating `mirror-ready`, now composed entirely by the A2 tap playout; **implemented + unit-verified, dev45 live-verify pending**), and the **fabric slice** (`docs/design/04-networking-capture.md` — VyOS dynamic routing + overlay (OSPF / IS-IS / iBGP / eBGP / BGP-EVPN / VXLAN / redistribution / BFD) on `kind: router` nodes via the top-level `fabric:` block + per-router `routing:`; `OP-HOST-100 configure-routing` + `OP-BAR-060 fabric-ready`; ported from the sibling dtDataCenter lab; **implemented + unit/plan-verified on `wip/fabric-slice`, live deploy verify pending**). On top of the slices, the **control-plane API** (`axrange/api/` + `axrange serve`, `docs/design/11-api-ui.md`) and **`axrange destroy`** (reverse-of-create teardown, CHANGELOG 2026-06-23) have landed. **40 op types (34 action/wait + 6 barriers); 114 catalog rules; 3131 pytest tests; `axrange {te,o2,tap,tfc} {validate,plan,deploy,status,info,destroy}` (+ `list-deploys [--tier]` | `serve` | `image build` outside the grid) all wired** — the CLI lifecycle unification (2026-07-16, `docs/WORK_ORDER_cli_lifecycle.md`, CHANGELOG) regrouped the former bare/`o2`/`tap` verb vocabularies into one grid + added the `info` verb; one-cycle deprecation shims cover the old spellings (`axrange deploy` → `te deploy`, `o2 up`/`down` → `o2 deploy`/`destroy`, `o2 tap apply`/`destroy` → `tap deploy`/`destroy`). The **Traffic tier (`tfc`, `docs/WORK_ORDER_traffic_tier.md`)** joined the grid 2026-07-24 — binds personas onto a deployed twin's published host roles (`node.role`, `scenario_schema_version` 1→2, Authority: user), a third standalone playout alongside `o2`/`tap` (`OP-TFC-010 drive-persona`; gated `dev45` live-verify **PASSED 2026-07-25**). The **Exercise tier (`exr`, `docs/WORK_ORDER_exercise_tier.md`)** landed the same day — `axrange exr {deploy|run, status, destroy}` + `POST/GET/DELETE /api/exr` (a reduced verb set, not the full six-verb grid, since it binds a spec to an already-deployed twin rather than validating/planning its own IR); a fourth standalone playout, and the scope shift that completes the five-tier twin model — a bare `te deploy` no longer emits attack/activity/adversary at all (gated `dev45` live-verify **PASSED 2026-07-25** — Stage 5 of the twin-model live-verify run: the AD kill chain, including a real DCSync `krbtgt` dump, ran as an `exr` against the standing twin, `[axr-truth]` markers landed in Loki, a clean ephemeral `exr destroy` left twin/`tfc`/`tap` up, and a replay reproduced an identical `playout_hash`; see CHANGELOG 2026-07-25 "…Stage 5 (`exercise_tier` capstone) PASSED"). **`exr` gained NAME-targeting 2026-08-04** (`docs/WORK_ORDER_corp_enterprise_bundle.md` §4, CHANGELOG) — the fourth and last tier to get it: `exercise.twin` is now optional + accepts a scenario/twin-config name (or a `--target` override), resolved via the shared `taps.resolve_target_deploy` (also fixing a latent cascade-orphan: a name-authored run's `plan_id` is now concrete-keyed so a `te destroy` cascade finds it). This closes the last minted-deploy-id weld, enabling the **co-located `corp-enterprise` bundle** (`twin-configs/corp-enterprise/`) — every tier's spec in one folder, all name-targeted: the twin config + `roles.yaml` + `tfc-default`/`tfc-busy` + four `tap-*` plans + the AD kill-chain `exr` spec (moved in from `deploy-dev45/`, re-pointed at the dedicated `attacker` node) + a `README.md` with the full deploy/teardown sequence (WO-2 code/content/docs DONE; **gated bundle `dev45` live-verify PARTIAL PASS 2026-08-05** — tap + tfc PROVEN live, clean-cascade teardown re-verified at 30-VM scale, te deploys soundly; **exr deferred** on a dev45 upstream-egress outage, external — CHANGELOG 2026-08-05). **The twin-model arc's `dev45` live-verify (Stages 1–5 of `docs/RUNBOOK_twin_model_live_verify.md`) is PASSED 2026-07-25** against a real 32-host `corp-enterprise` twin — Lab, Twin (+ twin-config + `role:` field), `tap_points` (incl. the first real `boundary` scope + a live re-shard), Traffic (+ persona drivers, both Linux and Windows), and Exercise (the AD kill chain) all confirmed end-to-end, not just unit-tested; seven code fixes were surfaced and landed in Stages 1–4, the deepest a capture-plane data-loss bug in the gretap de-scope diff (LES-118); Stage 5 drove no code fix but surfaced that **twin-configs have no incremental add** (editing a deployed config to add a node forces a whole new deploy — GAP-040, LES-126). Only **Stage 6** (`sme-flat`, the second twin-config) remains open. See CHANGELOG 2026-07-25 and `docs/design/02-twin-model.md` for the full picture. Live-verified end-to-end on **two clusters**: `pxtwin1` (PVE 8.4) and `dev45` (PVE 9.2 greenfield 2026-06-09). Re-running `axrange te deploy` is resume-safe; `axrange te destroy` is resume-safe too (idempotent, finishes a partial teardown on re-run).

### Item 1's build-by-build progress narrative (`OP-OVL`/`OP-ACT` archetype overlays)

This was the body of item 1 ("Per-scenario overlays ... and activity ...") — the chronological build/live-verify log for that thread, between its opening sentence ("Design + implementation are delegated to Claude Code ... Each archetype gets its own set.") and its still-open tail ("**Still open on Identity:** ..."), both of which remain in `CLAUDE.md` verbatim.

**Activity progress (CHANGELOG 2026-06-12):** the **OP-ACT inline-steps engine is implemented + live-verified on `dev45`** (`axrange/config_exec` render+backend; planner `_emit_activity`; `docs/SLICE_ACTIVITY.md`) — `inline.steps` over QGA, one-shot, local/external actors; `soc-detection-lab` carries the first inline attack chain; the live run on dev45 captured the activity's ICMP + TCP-SYN traffic at the sensor (done-criterion #2). **The AD attack chain (OP-ACT content) landed + LIVE-VERIFIED on dev45 2026-06-24 (CHANGELOG):** the corpus kill chain (recon → LLMNR poison → SMB/LDAP enum → AS-REP roast → Kerberoast → DCSync) authored as inline-steps on `ad-domain-lab` + `purple-team-range` (LAN attacker vs the corp.lab domain, `python3-impacket` apt kit); a focused verify (`ad-attack-verify`: router egress + a responding `windows-server-2022` DC + attacker + `tcpdump-sensor`) ran the chain against a LIVE DC — **secretsdump DCSync SUCCEEDED (krbtgt via DRSUAPI)** and the sensor captured 543 attacker↔DC packets (Kerberos 88, LDAP 389/636, SMB 445, GC 3268, 131 DRSUAPI/RPC). **Router-NAT reboot-persistence fixed (CHANGELOG 2026-06-24):** `configure-router` now installs a reboot-persistent `axr-router-nat.service` (nft-or-iptables, no apt) — a router clone's first-boot reboot previously wiped `ip_forward`/NAT and silently broke LAN egress; live-verified on dev45 (egress survives a gw reboot). **Router provisioning-ordering fixed (CHANGELOG 2026-06-24):** `provision-app`/`run-script` on a router-served host now gate on `configure-router` (pass-3 conditional edge from threaded `served_hosts`) — apt-over-NAT no longer races ahead of the router's NAT. **The AD DEFENSE overlay (blue team) landed + LIVE-VERIFIED on dev45 2026-06-24 (CHANGELOG):** an in-scenario `suricata-sensor` carrying an AD detection ruleset (new `Sensor.rules` → `suricata_ad.rules`; the modern replacement for the retired standing Suricata network-sensor) wired into `ad-domain-lab` + `purple-team-range`; the `ad-defense-verify` run caught the full AD kill chain — recon/LLMNR/SMB/LDAP/Kerberos + the **DCSync DRSUAPI bind (Priority 1)** — closing the **purple-team loop** (attack runs *and is measured*). **Host-side AD detection landed + LIVE-VERIFIED on dev45 2026-06-24 (CHANGELOG):** `install-defense-agent` gains a Windows branch that enables the DC's AD audit subcategories (4768/4769 Kerberos, 4662 DCSync via a domain-root SACL) post-promotion (gated on domain-ready); the `host-defense-verify` run captured the attack in the DC Security log (4768 ×3, 4769 ×12, 4662 ×15 incl. alice's replication access). Both blue planes (network IDS + host audit log) now cover AD. A **Windows `write_file` PVE-9.2 wide-char fix** (base64-in-command, matching the Linux path) landed with it. **Overlay progress (CHANGELOG 2026-06-15):** the **OP-OVL data overlay (`seed-dataset`) is implemented + live-verified on `dev45`** (`docs/SLICE_OVERLAY.md`) — synthetic deterministic datasets over QGA; `purple-team-range` carries the first crown-jewel PII overlay; the dev45 run materialized the dataset in-guest with a SHA256 byte-matching the local render (determinism proven). The **Defense** overlay is implemented (both ops): **`install-defense-agent`** (host EDR over QGA) is **live-verified on `dev45`** (CHANGELOG 2026-06-15), and **`deploy-network-sensor`** (CHANGELOG 2026-06-16) is **live-verified on `dev45`** as **Option C — an appliance-adjacent network sensor Suricata** (a standing Suricata box next to the capture appliance, fed a mirror of the capture appliance's capture iface; reuses `tap_from: external-mirror`, leaves the scenario/capture plane untouched). `soc-detection-lab` carries a Wazuh host-agent + a Suricata network sensor observing dmz+corp; the dev45 run proved attack traffic → capture appliance → network sensor → Suricata alerts. **The Defense overlay increment is complete + live-verified (both ops).** Overlay schema mechanics are settled (overlays configure existing nodes / standing infra; config-class emission). **Identity increment — W2a + W2b + host-join COMPLETE + live-verified on dev45** (scoped 2026-06-16 → Windows AD, three slices, `docs/SLICE_WINDOWS_AD.md`): W0 Windows imagebuild (**live-verified, template 101/102, 4/4 probes**); W1 substrate (**live-verified, 13/13 ops**); W2 AD identity — **W2a DC promotion (`role: adds` → `corp.axr.lab` forest) + W2b `create-domain-user` (CHANGELOG 2026-06-17) + `join-host-to-domain` (explicit `node.joins:`, live-verified 2026-06-24 — deploy `w2-join-verify-4bd64c05`, 26/26 ops, `Get-ADComputer ws01` joined + member `PartOfDomain=True`; CHANGELOG 2026-06-24)** all live-verified. **Local users (`create-domain-user` local branch — `New-LocalUser` on Windows / `useradd` on Linux, keyed off an explicit `host:` on no-`domain:` identities, after `host-ready[$host]`) implemented + live-verified on dev45 2026-06-24 — BOTH OS paths (`localuser-verify` Linux + `winlocaluser-verify` Windows); CHANGELOG 2026-06-24.** Archetype content complete — `ad-domain-lab`/`purple-team-range` carry the DC + domain users + win11 join clients + local-user identities (§6.6). **The Identity increment's full user surface — domain users + host joins + local users (both OS paths) — is done.** **Vulnerable-AD weakening + SPN/OU modeling landed + LIVE-VERIFIED on dev45 2026-06-24 (CHANGELOG):** `IdentityEntry` gains `spn` (Kerberoastable service account, forced RC4), `asrep_roastable` (no-preauth), `ou` (idempotent New-ADOrganizationalUnit + placement), `password` (weak per-account); the `vulnad-verify` run made the domain exploitable — `GetUserSPNs` returns the SPN account (was empty), `GetNPUsers` returns a real `$krb5asrep$23$` hash, and the RC4 Kerberoast IDS rule `9000006` finally fires. **Dangerous-ACL + delegation weakening landed + LIVE-VERIFIED on dev45 2026-06-25 (CHANGELOG):** `IdentityEntry` gains `acl: [{right, target}]` (GenericAll/GenericWrite/WriteDacl/WriteOwner/ForceChangePassword/AddMember takeover edges), `delegation: unconstrained`, and `delegation_to:` (constrained) — the BloodHound attack-path primitives; the `acl-deleg-verify` run confirmed `intern --GenericAll--> helpdesk (DA)`, `svc-unc` unconstrained delegation, and `svc-con` constrained delegation on the DC. The archetypes now model the canonical AD escalation paths.

### Items 2 and 3 (both fully resolved — no forward-looking content)

2. ~~`run-script` (`OP-APP-030`) live-verify~~ — **DONE.** Implemented + unit-tested (CHANGELOG 2026-06-03), **live-verified on `dev45` 2026-08-31** (7 hosts, fail-loud `systemctl is-active` check; closed as a side effect of the CorpAPCN fidelity loop — CHANGELOG 2026-08-31).

3. **`--fresh` deploy correctness fix — RESOLVED + LIVE-VERIFIED on `dev45` 2026-07-07 (CHANGELOG 2026-07-07).** Root-caused to `op_id` alone as the `ops` table PRIMARY KEY, which aliased two deploys of the same plan (and, previously undocumented, let a `destroy` after `--fresh` report DESTROYED having swept nothing — a live-resource leak). Fixed by a composite `(deploy_id, op_id)` PRIMARY KEY + `SCHEMA_VERSION=2` guard (`StateStoreSchemaError` on an old DB). **Operator action:** re-initialize any `state.db` predating 2026-07-07. The live-verify run also surfaced and fixed two further bugs: a too-broad `REF-120` validator skip (validator/references.py) and an unnormalized-disk-size adapter bug (`adapter_proxmox/rest.py`) — see CHANGELOG 2026-07-07 "Live-verify follow-up."

---

## 2026-09-19 — CLAUDE.md Current State archive

On 2026-09-19 `CLAUDE.md` was trimmed from ~57 KB to a lean (~10 KB) orientation doc — it loads
into every session and every background agent, and its quoted counters (pytest/op/rule totals)
drifted on nearly every commit. The complete pre-trim file is reproduced below **verbatim**, so
the long "Current State" history, the per-slice live-verify narratives, the full "How to Read"
spec list, the detailed "Immediate Next Actions" threads, and the longer Conventions/Operational
notes all survive. The only mechanical change: every heading is demoted two levels (`#` → `###`,
`##` → `####`, …) so the snapshot nests under this section instead of breaking this file's
outline. Counters and "current" claims inside are as of 2026-09-19 and will go stale; for what is
true today read `CLAUDE.md` and `docs/design/README.md`.

### cyber-range

<!--
  Auto-loaded entry point for Claude Code at every session start.
  This is the orientation. Detailed design lives under docs/.
-->

#### What This Project Is

An on-premises **cyber range platform** for designing, deploying, and exercising synthetic sandbox environments end-to-end. The platform lets an operator:

1. **Design** layered network/host/activity topologies on a free-hand canvas.
2. **Deploy** those designs to a virtualization backend (Proxmox first; VMware and cloud later via pluggable adapters).
3. **Capture** mirrored network traffic from inside the deployed range to a central sensor.
4. **Simulate attacks** against the deployed range and **replay** them after defensive controls are added, to validate that the controls actually work.
5. **Use AI** to plan scenarios, drive red-team and blue-team agents, and analyze captured telemetry.

**Primary purpose:** validate cyber defense solutions against repeatable attack scenarios in realistic, disposable environments.
**Secondary uses:** training, detection-engineering experimentation, scenario library curation.

#### Current State

**Iteration:** 0 — slice MVP **fully working end-to-end on the `pxtwin1` lab cluster** as of 2026-05-29. All five `docs/ADAPTER.md` §6 done-criteria pass with zero manual intervention: `axrange deploy` → 25/25 ops complete → ping h-a↔h-b flows → pcaps grow at the sensor (resume-safe; re-running is a no-op against a `completed` deploy; `--fresh` opts out of resume — **caveat RESOLVED (CHANGELOG 2026-07-07):** the `ops`/`op_attempts` tables now key on a composite `(deploy_id, op_id)` PRIMARY KEY (`SCHEMA_VERSION=2`), so two deploys of one plan no longer alias each other's op rows and `--fresh` correctly starts a clean deploy with no leftover-`completed` skips. **Migration note:** a `state.db` from before this change is schema v1 and fails loud (`StateStoreSchemaError`) under the new guard — re-initialize it (delete/move the file) on any cluster that ran deploys before 2026-07-07. **Live-verified end-to-end on `dev45` 2026-07-07:** deploy → `--fresh` (new deploy_id, independent op rows, genuinely re-ran) → destroy (resources actually removed, not a false-DESTROYED leak) → idempotent re-destroy clean. Two further fixes landed during that live-verify — see CHANGELOG 2026-07-07 "Live-verify follow-up").

- Architecture is worked through at the conceptual level across multiple extended Claude chat sessions (see `docs/CHANGELOG.md` entries dated 2026-05-21 and 2026-05-26).
- **Scenario IR, image manifest, planner, validator, op catalog, and Proxmox adapter are all ratified.** Schemas locked for v1 — see `docs/IR_SCHEMA.md`, `docs/IMAGE_MANIFEST.md`, `docs/PLANNER.md`, `docs/VALIDATOR.md`, `docs/OP_CATALOG.md`, `docs/ADAPTER.md`.
- **Capture appliance (`tcpdump-sensor`)** — installer, systemd units, helper scripts, and a smoke test are committed under `o2/tcpdump-sensor/`. See `docs/tcpdump-sensor/README.md`. Real-cluster fixes from the 2026-05-29 bring-up: `tcpdump -W 999999999` (was `-W 0`; tcpdump 4.99+ rejects 0); systemd sandboxing re-tightened to a focused allowlist (`@system-service` minus `@privileged @resources @debug @mount`, plus explicit re-allows for the six syscalls tcpdump needs: `@chown bpf setuid setgid setgroups capset`); `MemoryDenyWriteExecute=true` restored.
- **`axrange/` Python package — fully assembled CLI** (Python 3.11+). Submodules:
  - `ir/`, `manifest/`, `ops/`, `validator/`, `cli/` — IR parser, manifest loader, op registry, validator (89 rules, 4 passes, 4 entry points), CLI.
  - `planner/` — DAG construction (passes 1-5: emit / dataflow / ordering / placement / validate), SQLite state store (`deploys` / `leases` / `resource_locks` / `ops` / `op_attempts`), sequential executor with N-retry and halt-subtree. **Resume-safe**: re-running `axrange deploy` against the same scenario picks up the prior deploy's state, skips already-`completed` ops (no adapter call), retries `failed`/`halted` ones. `--fresh` opts out. **Cross-process correctness locks landed 2026-06-03** (`locks.py` + the two new tables; CHANGELOG): a per-scenario **lease** (single-writer gate, §6.2) and DB-backed **resource locks** on the correctness categories (`ovs:<node>`/`vm:<vmid>`/`gretap:<sensor>`, §8.4) acquired per-op and heartbeat-renewed, so concurrent deploys serialize across processes. Executor stays internally **sequential**. Still not built (`docs/PLANNER.md` §8–§9): per-process throughput semaphores, the parallel intra-deploy executor, and escalate walk-back — fair game when a thread needs them (Immediate Next Actions §6). **Plan-diff/re-planning (§9) gained a reduced subset 2026-08-11** — `axrange te deploy --update` (add/remove whole nodes against a pinned `plan_id`; in-place field-level mutation is still refused, never redone — `docs/WORK_ORDER_twin_incremental_update.md`; **gated `dev45` live-verify PASSED 2026-08-14**, all four §6 proofs on a router-bearing twin, CHANGELOG 2026-08-14).
  - `adapter_proxmox/` — `ProxmoxAdapter` (one interface) routing to `rest` (Proxmox API) and `ssh` (`tc` / `ip link` / `ovs-vsctl` plus a VyOS `vbash` channel via `vyos-ssh-set`) backends. Model C check-then-act on every infra op; deterministic naming in `naming.py`. **Networking is SDN-native** (ratified + implemented 2026-06-03; slice-MVP `/nodes/<node>/network` bypass retired): OP-NET ops define `/cluster/sdn` objects with a cluster-wide apply (`PUT /cluster/sdn`), zone type **auto-derived** per OP-NET-010 — **VLAN** zone = one OVS underlay bridge per zone (`underlay_bridge_for_plan`) + per-network VLAN-tagged vnets; **Simple** zone = one PVE-materialized Linux vnet bridge per network. `create-ovs-bridge` runs *before* `create-sdn-zone`. **Capture is Option 1 — per-tap `tc` on the Linux vnet bridge → keyed gretap netdev → flow-based sensor** (ratified + implemented 2026-06-04, CHANGELOG; live-verified on `pxtwin1` gates 1–3 PASS, gate 4 mechanism-proven, gate 5 planner-verified; also live on `dev45` PVE 9.2 since the 2026-06-09 greenfield bring-up). The OVS-bridge-mandatory invariant is **retired** (capture no longer depends on bridge type). **Answered 2026-08-14 (CHANGELOG "W2 live-verify"): an SDN apply does NOT detach running taps.** Open since 2026-05-29 because a full `te destroy` never exercised it (everything is going away anyway); the W2 incremental removal is the first operation that applies SDN to a SURVIVING twin. With a tap bound to the segment, the post-removal capture count was identical to the pre-removal baseline (64 frames per 30 pings, both) and live traffic was visible decapsulated at the sink. The 2026-05-29 SSH re-assert can therefore retire when someone wants to remove it -- it is now belt-and-braces, not load-bearing. The **router slice** (`docs/SLICE_ROUTER.md`) added two ops: **OP-NET-050 `create-subnet`** (REST, Phase 1 — SDN subnet + SNAT egress; Simple-zone only per PVE — VLAN zones reject `exitnodes`; live-verified `dev45` 2026-06-09) and **OP-HOST-090 `configure-router`** (Phase 2 — router-node egress NAT + inter-segment routing). The planner's host-only gate is lifted for `kind: router` nodes (multi-homed across LAN segments + adapter-injected WAN NIC on `vmbr0`). Two router-OS branches, both live-verified on `dev45` 2026-06-10: **Debian/nftables** via REST/QGA (39/39 ops), and **VyOS** via SSH `vbash` (`vyos-ssh-set`; orchestrator discovers WAN IP from net0 MAC via `arp-scan` — VyOS has no QGA; template `hw-id` stripped pre-clone for deterministic NIC naming; 36/36 ops). Details: CHANGELOG 2026-06-03, 2026-06-04, 2026-06-09 (dev45 + Phase 1), 2026-06-10 (Phase 2 + VyOS); `docs/ARCHITECTURE.md` §3; `docs/ADAPTER.md` §3/§8; `docs/SLICE_ROUTER.md`.
  - `imagebuild/` — `axrange image build <image>` (`docs/IMAGE_BUILD.md`). Reproduces a Proxmox template from an upstream Linux cloud image + a `qemu-guest-agent` bake; two-layer conformance (per-`provides:` capability probes via the guest agent + a one-host **untapped** deploy smoke-test through the real planner+adapter), then writes the new `template_vmid` into the binding. Reuses `adapter_proxmox/`'s REST/SSH/config; broader `BUILD_*` credential scope (`qm` over SSH for VM-create/import/template; REST for `nextid` + guest-agent exec). First target `debian-12-cloud` (reproduces template 610). **Implemented + 34 unit tests; live-verified on `pxtwin1` (PVE 8.4, 2026-06-01)** — built template 620 from the upstream image: 4/4 capability probes + a 13/13-op smoke-test (clone→boot→guest-agent→teardown of the rebuilt template). Bring-up fixes: `qm importdisk`→`qm disk import` (PVE 8.4), configurable bake IP (`BUILD_IPCONFIG`).
  - `api/` — the **control-plane API** (`docs/design/11-api-ui.md`): a `cli/`-class FastAPI consumer over the engine (validate/plan/topology/node-detail/deploys/targets + async lease-guarded deploy + **`axrange destroy`** teardown), served by `axrange serve`. Implemented + tested; **headless or SPA-co-serving** (Phase-4). Boundary: clean DTOs only (`dtos.py`), never raw IR/Proxmox shapes.
  - CLI: **unified verb grid (2026-07-16, `docs/WORK_ORDER_cli_lifecycle.md`, CHANGELOG)** — `axrange {te,o2,tap,tfc} {validate,plan,deploy,status,info,destroy}` (spec positional on `validate`/`plan`/`deploy`; `status`/`info`/`destroy` default to the latest deploy of that tier; `tfc` — the Traffic tier, `docs/WORK_ORDER_traffic_tier.md` — added 2026-07-24), plus `list-deploys [--tier {te,o2,tap,tfc}] | serve | image build` outside the grid. **`te inventory [ID] [--json]` added 2026-07-23** (`docs/WORK_ORDER_tier_split.md`, CHANGELOG "Tier split KEYSTONE") — a deployed twin's published binding contract (`hosts[]` roster incl. each host's full `nets[]` attachment topology, and — since 2026-09-05 — each host's **declared** `listen[]` set where it declares one, `App.listen`; the separate `tap_points[]` catalog was dropped 2026-07-28, see the "twin substrate becomes tap-agnostic" blockquote below), a standalone verb since it's always deploy-scoped (unlike `info`, which also previews an undeployed `scenario.yaml`); API peer `GET /api/deploys/{id}/inventory`. **`te scorecard [ID]` added 2026-09-05** (`docs/WORK_ORDER_fidelity_scorecard.md`, CHANGELOG "Fidelity scorecard BUILT") — the second standalone deploy-scoped verb, on `te inventory`'s precedent: it certifies a deployed twin against `docs/design/13-golden-twin-fidelity.md`'s eleven rows and writes `<id>.scorecard.<deploy_id>.json` beside the twin config (`[--json] [--reference] [--out] [--no-write] [--include-deferred] [--no-probe] [--attest-visual] [--fail-on-verdict]`); API peer `GET /api/deploys/{id}/scorecard`. `deploy [--fresh]` lives at `te deploy [--fresh]`. One-cycle deprecation shims cover the old bare `deploy`/`validate`/`plan`/`status`/`destroy` and the old `o2 up`/`down`/`tap apply`/`tap destroy` spellings (print a notice, then dispatch — removed next release). New `info` verb: resolved config + runtime access facts (IPs, o2 observability endpoints) over persisted op rows, `--json`-capable. **Access facts & endpoint resolution BUILT + dev45 live-verified 2026-07-25** (`docs/WORK_ORDER_access_facts.md`, CHANGELOG; full detail in the "Access facts & endpoint resolution" blockquote below; the live-verify proved the `te info` access sheet, `o2 info` VM-102 endpoint resolution, `XPress-SW` external-sensor resolve, and a real password-only SSH login into a fresh Linux node) — `te info` shows each node's ip/user/effective-password/access-hint (`--no-secrets` masks), `o2 info` resolves the standing services VM's live IP into all five endpoint URLs (`O2_SERVICES_VMID`/`_NAME`, adapter.conf) instead of an operator hand-copying `O2_*_URL`, and a lab-wide `DEFAULT_PASSWORD` fallback (adapter.conf, built-in default unchanged from before) now also turns on Linux SSH password login. Adapter credentials/config in `adapter.conf.example` at the repo root (now also carries `DEFAULT_PASSWORD`, `O2_SERVICES_VMID`/`_NAME`/`O2_<SVC>_PORT`, and the optional `O2_*` observability-endpoint-URL override keys); `adapter.conf` and `state.db` are gitignored. **Dev45 smoke run 2026-07-16 (CHANGELOG "CLI bare-default deploy-targeting safety fix")**: `te deploy → te info → te status → te destroy` verified end-to-end — the new `info` card was correct on-cluster (over QGA) — and the same run caught + drove the fix of a HIGH-severity bare-default targeting bug (a bare `te destroy` could report DESTROYED against a stale deploy while a newer live one stayed up; fixed by ordering "latest deploy" on `updated_at` not `started_at`, no-matching a `destroyed` row on the deploy-creation resume path, and preferring the newest non-terminal row on the destroy bare-default). No schema-version bump.
  - `sensors/` — the **sensor-type registry** (`tcpdump-/suricata-/zeek-sensor`, open catalog), consumed by the validator (REF-210) and the sensor deploy lifecycle. **Sensors design Phases A–C complete** (`docs/design/06-taps-sensors.md`): typed `sensors:` IR + the capture-appliance→`sensor` rename (A); in-scenario sensor VMs + auto transport vnet + gretap re-point + `verify-capture` over QGA (B, live-verified dev45); captures-driven multi-sensor fan-out (C1, live-verified) + `export.ship: loki` (C2) + network sensor reconcile (C3). **suricata-sensor is cluster-proven via a pre-baked image** (`default_image` clones a template with suricata baked from bookworm-backports; live-verified dev45). **Follow-ons:** a baked `zeek-sensor` image (same pattern), `export.ship: loki` live-verify (needs Loki up), VLAN-zone sensor egress. **Phase B/C's in-scenario-VM synthesis is now `tier == "o2"`-gated (2026-07-13, `docs/WORK_ORDER_sensor_agnostic_te.md`, review fix F1)** — it fires only for `axrange.o2control.control.stand_up_surface`'s standing-surface deploy. An ordinary `te`-tier deploy that declares `sensors:` synthesizes **no** in-scenario sensor VM at all any more (a te-emitted sensor would receive zero mirrored traffic and get no heartbeat to catch it, now that taps and mirrors are entirely the A2 tap playout's job) — the validator warns (`REF-250`) rather than erroring, so the shipped `target-environments/` archetypes that still author `sensors:` keep validating. See the "Sensor-agnostic `te_` substrate" blockquote below for the full picture.
  - **3145 pytest tests** passing.
- The **v1 design surface was closed 2026-06-01** and refined in the 2026-06-03 session — SDN-native networking, per-tap sensor routing, cross-process resource locks — **all three now implemented + live-verified** (capture re-mechanization on `tc` 2026-06-04; per-tap sensor routing 2026-06-03; cross-process resource locks 2026-06-03). The image build pipeline (Q6) is resolved (`docs/IMAGE_BUILD.md`) and is **implemented + live-verified on `pxtwin1` (2026-06-01) and `dev45` (PVE 9.2 greenfield bring-up 2026-06-09, four implementation fixes recorded)**. The **first `config`-class execution slice is implemented (`axrange/config_exec/`; `docs/SLICE_CONFIG.md`)** — `provision-app` (`pkg`/`pkg@version`, Ansible) live-verified on `pxtwin1` 2026-06-02; `run-script` (`script`, direct exec) landed 2026-06-03 (unit-tested; **live-verified on `dev45` 2026-08-31** — 7 hosts, fail-loud `systemctl is-active` check confirming the delivered script actually started, not merely executed; CHANGELOG 2026-08-31) — delivered in-guest over the QGA channel. The **router slice** (`docs/SLICE_ROUTER.md`) is **implemented + live-verified on `dev45`**: Phase 1 SDN subnet + SNAT egress 2026-06-09 (Simple-zone only per PVE), Phase 2 router node 2026-06-10 (Debian/nftables; egress NAT + inter-segment routing), VyOS fold-in 2026-06-10 (config delivery over SSH `vbash` via `vyos-ssh-set`).
- **`qemu-guest-agent` in Debian templates the slice clones from:** QGA must be preinstalled + auto-start enabled. As of the Q6 resolution (2026-06-01) this is owned by the image build pipeline (`docs/IMAGE_BUILD.md` §3 bakes QGA preinstall + auto-start), so it is no longer a manual operator step for pipeline-built templates. The manual runbook remains the documented **fallback** for hand-built templates and is exactly what the pipeline automates: untemplate → toggle LV r/w → apt install QGA → `cloud-init clean` → reset machine-id + ssh host keys → poweroff → restore LV r/o → retemplate. Template 610 (`dtdc-debian-template`) on the lab cluster was rebuilt in place on 2026-05-29 via this procedure; the imagebuild slice (`docs/IMAGE_BUILD.md` §8) reproduces 610 from the upstream cloud image instead.
- **Implementation status (as of 2026-06-23):** the v1 stack — IR parser, validator (104 rules, 4 passes), op registry, planner (passes 1–5 + SQLite state store + cross-process lease + `resource_locks` + sequential executor), Proxmox adapter (REST + SSH backends, SDN-native infra + Option 1 capture), image build pipeline, config-class execution slice (`provision-app` + `run-script`), the router slice (Phase 1 + Phase 2 + VyOS fold-in), the **dynamic-tap slice** (originally declarative `tap:` at host/leg/segment + the `verify-capture` capture heartbeat — the `tap:` IR flags were retired 2026-07-28, see the "twin substrate becomes tap-agnostic" blockquote below; the heartbeat survives, now composed entirely by the A2 tap playout), the **control-plane API** (`axrange/api/` + `axrange serve`), and **`axrange destroy`** (reverse-of-create teardown, state-driven, name-guarded, idempotent — engine + CLI verb + `DELETE /api/deploys/{id}`; CHANGELOG 2026-06-23) — is **implemented, committed, and live-verified end-to-end on two clusters: `pxtwin1` (PVE 8.4) and `dev45` (PVE 9.2 greenfield 2026-06-09)** *(dynamic-tap slice: implemented + unit-verified, dev45 live-verify pending; API service + `destroy`: implemented + unit-verified, and `destroy` **live-verified on dev45 2026-06-23** — full deploy→destroy, 8 resources removed, blast-radius guard left unrelated deploys/appliances/bridges untouched, idempotent re-run clean; CHANGELOG 2026-06-23)*. All five `docs/ADAPTER.md` §6 done-criteria green. The **sensors refactor Phase A** has landed (`wip/scenario-sensors`): the capture-appliance→`sensor` rename, the typed `sensors:` IR block + `Sensor` model + `axrange/sensors/` registry, and REF-210 — behavior unchanged. **Phase B COMPLETE (B1–B4, live-verified on dev45 2026-06-23):** B1 — the **`OP-MIR-050 deploy-sensor`** op + per-type render (`tcpdump`/`suricata`/`zeek` + the in-guest flow-based gretap decap) + `ConfigBackend` handler; B2 — **emission**: each declared `sensors:` entry expands to an **in-scenario VM** (synthetic node → host lifecycle + `deploy-sensor`), the planner **auto-provisions an isolated transport vnet + host-gateway** (`create-subnet`, egress-capable so the sensor can apt-install its tool) and assigns each sensor a static transport IP, and **`attach-gre-tunnel` re-points** its gretap `remote` from the config IP to the deployed sensor's assigned IP (no `sensors:` block ⇒ config-IP path unchanged); B3 — **`verify-capture` reads the in-scenario sensor VM over QGA**; B4 — **live-verified end-to-end on dev45 (PVE 9.2): a `tcpdump-sensor` scenario deployed, the sensor VM brought up its `gre-cap` decap + tcpdump, `verify-capture` confirmed a sentinel traversed tap → host-gretap → sensor (Result: COMPLETED), and `axrange destroy` tore it all down (Result: DESTROYED).** Three fixes landed during live-verify: (1) **the PVE-9.2 `agent/exec` "Wide character" regression** — `GuestAgent.write_file` now delivers content as **base64 embedded in the command** (no `input-data`), sidestepping `encode_base64` at `Agent.pm:135` that 500'd every in-guest write on PVE 9.2; (2) **`verify-capture` inject ordering** — the sentinel is now fired in the **background** so it overlaps the read window (a real-time-capture race that the QGA-500 had masked on every prior run); (3) **subnet-aware `axrange destroy`** — delete a vnet's subnets before the vnet (the transport vnet carries one). **Phase C COMPLETE:** captures-driven **multi-sensor fan-out** (C1, live-verified on dev45 — two tcpdump-sensors on one net, both verify-captures pass; fixed the chained-`mirred` multi-mirror on the wire), **`export.ship: loki`** (C2, unit-tested; Loki live-verify deferred), `deploy-network-sensor` reconciled as superseded by `suricata-sensor` (C3). **`suricata-sensor` is now cluster-proven via a PRE-BAKED image** (2026-06-23): `axrange image build suricata-sensor` bakes suricata (from bookworm-backports, on the build host's vmbr0 egress where backports IS reachable) into a template; a suricata-sensor clones it (registry `default_image` → `suricata-sensor@v1`) and `deploy-sensor` starts the present daemon — **live-verified on dev45 (template 100; suricata runs on `gre-cap`, 0 drops; verify-capture passes; destroy clean).** Two start-check bugs fixed during live-verify: suricata's comm is `Suricata-Main` so `pgrep -x suricata` never matched (→ `pgrep -f 'suricata.*axrange'`), and `pkill` leaves a stale pidfile that aborts a re-run (→ `rm -f` it first). The runtime apt path stays **fail-loud** (a non-installable/non-starting tool fails the deploy with the reason, not a silent dead daemon). `tcpdump-sensor` is cluster-proven (tcpdump is in Debian main). Follow-ons: a baked **`zeek-sensor`** image (same pattern); `export.ship: loki` live-verify (needs Loki up); VLAN-zone sensor egress (SNAT rejected on VLAN zones). **3145 pytest tests; 40 op types (34 action/wait + 6 barriers) across 8 categories; 114 catalog rules.** The **fabric slice** (2026-06-29, `wip/fabric-slice`, `docs/design/04-networking-capture.md`) adds VyOS dynamic routing + overlay (OSPF / IS-IS / iBGP / eBGP / BGP-EVPN / VXLAN / redistribution / BFD) on `kind: router` nodes via `OP-HOST-100 configure-routing` + `OP-BAR-060 fabric-ready` + the top-level `fabric:` block + per-router `routing:` (FAB-010..080) — implemented + unit/plan-verified, **live deploy verify pending** (needs a VyOS template on a cluster). See `docs/CHANGELOG.md` for the full trail (most recent: 2026-06-29 fabric slice).
- The **AI scenario research** (`scenario_ai_research/`, ratified 2026-06-11; `docs/design/12-ai-scenario-research.md`) is a top-level WIP-branch component. **Stage 1** is an open-source local ingestion pipeline (Whisper / Ollama / `yt-dlp`) that builds a structured knowledge corpus from cyber-range OSS videos; **Stage 2** is Claude-Code-driven scenario design that consumes that corpus. Seven Stage-2 archetype Scenario IRs exist at `target-environments/` (consolidated + renamed from `scenario_ai_research/stage2/scenarios/` — CHANGELOG 2026-07-08) — `soc-detection-lab`, `ad-domain-lab`, `ctf-range`, `ot-ics-range`, `dfir-hunt-lab`, `vuln-management-lab`, `purple-team-range` — all VLAN-zoned, multi-segment, with `kind: router` for inter-segment routing + WAN NAT (the first five drove the router slice Phases 1 + 2). All seven validate and plan through the standard `axrange validate|plan|deploy` pipeline; the router-slice-driving five are also deploy-verified on `dev45`. **Per-scenario overlays (`OP-OVL` identity / data / defense) and activity (`OP-ACT`) for these archetypes are delegated to Claude Code** (CHANGELOG 2026-06-11): design + implementation per-archetype, using the archetypes as the input set. Coupling rule: `scenario_ai_research/...` (and the `target-environments/` IRs) may import axRange's validator/planner/op catalog; axRange must not import anything from `scenario_ai_research/`.

#### Architecture at a Glance

Five planes, each independently swappable:

```
┌───────────────────────────────────────────────────────────────────────┐
│ Topology canvas (free-hand UX, layered)                               │
│ Layers: Infrastructure / Apps & Tools / Activity                      │
│ Overlays (per-scenario, optional): Identity / Data / Defense          │
└───────────────────────────┬───────────────────────────────────────────┘
                            │ save → Scenario IR (platform-agnostic YAML)
                            ▼
┌───────────────────────────────────────────────────────────────────────┐
│ Validator → Planner (DAG) → Adapter                                   │
│ Adapter: Proxmox (v1), VMware/cloud later                             │
└───────────────────────────┬───────────────────────────────────────────┘
                            │ deploy
                            ▼
┌───────────────────────────────────────────────────────────────────────┐
│ Runtime: scenario zones                                               │
│ Each zone = SDN Zone + bridge + VMs/CTs + per-tap tc mirrors          │
│ Mirrored frames exit via GRE/ERSPAN over a dedicated vnet-transport   │
└───────────────────────────┬───────────────────────────────────────────┘
                            │ mirror
                            ▼
┌───────────────────────────────────────────────────────────────────────┐
│ Capture sensor (one node, multi-zone)                                 │
│ tcpdump on capture NIC → rotating pcaps → disk watchdog               │
│ Storage = time + tunnel source; no scenario correlation in v1         │
└───────────────────────────┬───────────────────────────────────────────┘
                            │ post-hoc analysis
                            ▼
┌───────────────────────────────────────────────────────────────────────┐
│ AI core (five roles)                                                  │
│ Planner · Red-team · Blue-team · Telemetry analyst · Traffic gen      │
└───────────────────────────────────────────────────────────────────────┘
```

Detailed design: see `docs/ARCHITECTURE.md`.

#### Tech Stack (Locked In So Far)

| Layer | Choice | Reason |
|---|---|---|
| Virtualization (v1) | Proxmox VE | Mixes VMs and LXC in one cluster; free; scriptable API; OVS-capable |
| Scenario networks | Proxmox SDN, Simple/VLAN zones | Cluster-wide programmable networks; pairs with OVS for mirroring |
| Mirror plane | per-tap `tc` mirror → keyed gretap → sensor | Mirrors on the SDN-materialized Linux vnet bridge where the tap lives (Option 1); GRE crosses hosts (ERSPAN-equivalent) |
| Transport vnet | Dedicated isolated vnet (e.g. `vnet-transport`) | Sensor invisible to scenario traffic; no broadcast-domain leakage |
| Capture appliance (default) | `tcpdump` on the decapsulated GRE iface, rotating pcaps | Simplest thing that works; replaceable without changing anything else |
| Capture retention | systemd-timer disk watchdog, two thresholds | Decouples retention from capture; oldest-first prune to a target % |
| IaC for guests | cloud-init + Ansible (QGA-mediated in v1) | First-boot config. v1 delivers the playbook *into* the guest over the qemu-guest-agent exec channel — isolated scenario SDN nets defeat both push-SSH and the originally-intended pull-mode (CHANGELOG 2026-06-02, `docs/PLANNER.md` §4.3) |
| Scenario IR | YAML + JSON schema, versioned in git | Human-editable, diffable, git-friendly |
| Workflow | Claude + Claude Code (this repo's `docs/WORKFLOW.md`) | Always-on baseline + optional modes (Chat-bridge / Pipeline); drift control |

##### Direction log

The dated "direction" notes that used to sit inline here (surface-layer/O2, the
twin-centric re-frame, tap eligibility, golden-twin fidelity, the Console canvas build
arc, and so on) are archived verbatim, newest first, in
[`docs/DIRECTION_LOG.md`](docs/DIRECTION_LOG.md) — read that file for the full
reasoning trail. `CLAUDE.md` keeps one line per entry, newest first:

- 2026-09-03 → 2026-09-16 — Golden-twin fidelity direction (spec 13/14/15, scorecard, Console canvas build arc S1–S7)
- 2026-09-02 — Protocol-faithful traffic: a `drive: command` persona driver
- 2026-08-25 — aximprint↔axRange integration, Track C slices 2+3 ("deploy as twin" + Console UI)
- 2026-08-25 — aximprint↔axRange integration, Track C slice 1 (belief-store/provenance READ surface)
- 2026-08-13/14 — Max-capture-rate characterization + capture/traffic-generation fixes
- 2026-08-11 — Workflow-template v7 generic scripts adopted
- 2026-08-11 — Incremental twin update: `axrange te deploy --update` (W2)
- 2026-07-25 — Access facts & endpoint resolution
- 2026-08-04 — Role and persona become `tfc`-owned (+ W1 build)
- 2026-07-28 — Persona volume lever + high-volume catalog wave
- 2026-07-28 — The twin substrate becomes tap-agnostic
- 2026-07-23 → 2026-07-29 — Twin-centric re-frame (the five-tier twin model)
- 2026-07-17 — Standing-O2-sensor capture supervision + retention fix
- 2026-07-11 → 2026-07-15 — Sensor-agnostic `te_` substrate
- 2026-07-08 → 2026-07-15 — Surface-layer direction (O2) + the O2 build arc
- 2026-06-23 — Capture-plane direction (sensors unify into one typed `sensor`)

#### How to Read These Docs

**For the system as it is today, start at [`docs/design/README.md`](docs/design/README.md)** — the
current design-of-record entry point (**15** present-tense files covering the twin model, engine, O2
surface, taps/sensors, traffic/exercise tiers, content/overlays, images, the API/CLI/UI, and — added
2026-09-03/04 — golden-twin fidelity (13), operator assertions (14) and the Console canvas (15)). The
ordered list below is the historical/contract reading path — `CLAUDE.md`, the `SLICE_*`/
`WORK_ORDER_*` trail, `docs/CHANGELOG.md`, and the version-locked formal specs — kept for the
reasoning trail and the contracts implementations must satisfy, not duplicated by `docs/design/`.
(A second, parallel design-brief file set that used to sit alongside `docs/design/` was retired
2026-07-27 and folded into it — see `docs/design/README.md`'s mapping table for where each
former brief's content now lives.)

In order, on first read:

1. **`CLAUDE.md`** (this file) — orientation.
2. **`docs/WORKFLOW.md`** — Claude + Claude Code workflow protocol. Read once; refer back when producing chat-session output.
3. **`docs/CHANGELOG.md`** — design decision log. Reverse chronological; each entry has an `**Authority:**` marker.
4. **`docs/ARCHITECTURE.md`** — full architecture across all five planes; the deep-dive for any implementation work.
5. **`docs/IR_SCHEMA.md`** — formal spec for the Scenario IR (v1, ratified 2026-05-21). The contract validator, planner, canvas, and AI scenario planner all consume.
6. **`docs/IMAGE_MANIFEST.md`** — formal spec for the two-tier image catalog (manifest + per-platform binding). The contract image builds must satisfy.
7. **`docs/PLANNER.md`** — formal spec for the planner (v1, ratified 2026-05-21). DAG construction, idempotency model, failure semantics, runtime, state store, concurrency, re-planning. The contract the adapter implements against.
8. **`docs/VALIDATOR.md`** — formal spec for the validator (v1, ratified 2026-05-26). 114-rule catalog (96 IR-side across 10 categories + 7 cluster rules + 11 catalog-side persona rules; REF-200 per-tap sensor routing 2026-06-03, REF-210 typed sensors 2026-06-23, FAB-010..080 fabric slice 2026-06-29, ACT-060/070 schedule executor + REF-220/230/240 adversary overlay 2026-07-11, REF-250 sensor-agnostic te 2026-07-13, PSN-010..090 persona library 2026-07-24, PSN-100/110 persona transport + peer_scope 2026-07-28); four-pass execution (+ PSN's own catalog-side-only checks, not part of the four passes); shared-library structure consumed by `axrange validate` and `axrange deploy`. The contract IRs must satisfy before deploy.
9. **`docs/OP_CATALOG.md`** — formal spec for the planner's op catalog (v1, ratified 2026-05-27; extended 2026-06-09 with `OP-NET-050 create-subnet`, 2026-06-10 with `OP-HOST-090 configure-router`, 2026-06-19 with `OP-MIR-040 verify-capture`, 2026-06-23 with `OP-MIR-050 deploy-sensor`, 2026-06-29 with `OP-HOST-100 configure-routing` + `OP-BAR-060 fabric-ready`, and 2026-07-13 with `OP-MIR-005 record-tap-intent` — itself removed 2026-07-28, `docs/WORK_ORDER_tap_eligibility.md`). The closed set of 40 op types (34 action/wait + 6 barriers) across 8 categories, with full `inputs`/`outputs`/`after`/`cleanup_strategy`/`idempotency_class`/`resource_categories`/`redo_on` signatures. Companion to PLANNER.md: PLANNER says *how ops behave*, OP_CATALOG says *which ops exist and what they're wired to*. The contract the adapter implements against, alongside PLANNER.md.
   - **`docs/design/04-networking-capture.md`** — the **fabric slice**: dynamic routing + overlay (OSPF / IS-IS / iBGP / eBGP / BGP-EVPN / VXLAN / redistribution / BFD) on `kind: router` (VyOS) nodes, ported from the sibling **dtDataCenter** lab. Adds the top-level `fabric:` block + per-router `routing:` stanza (`docs/IR_SCHEMA.md` §3.4). Implemented + unit/plan-verified on `wip/fabric-slice`; live deploy verify pending (needs a VyOS template on a cluster).
10. **`docs/ADAPTER.md`** — formal spec for the Proxmox adapter (v1, Q5 resolved 2026-05-28). REST+SSH transport split, one-interface/two-backend design, op→capability→backend mapping, auth model, Model-C idempotency, the authorized first implementation slice (§6), and the slice-MVP SDN-bypass note (§8). The contract the planner-execution + adapter slice is built against.
11. **`docs/IMAGE_BUILD.md`** — formal spec for the image build pipeline (v1, Q6 resolved 2026-06-01). The `axrange image build` CLI verb (no CI), Linux-only, building on upstream cloud images; two-layer conformance (capability probes + smoke-test); binding-side build recipe; lifecycle integration; the authorized first slice (§8). Companion to `docs/IMAGE_MANIFEST.md` — the manifest is the contract a build satisfies, this is how builds happen.
12. **Slice-scope docs** (`docs/SLICE_CONFIG.md`, `docs/SLICE_ROUTER.md`, `docs/SLICE_ACTIVITY.md`) — per-slice mini-designs. **SLICE_CONFIG** covers the config-class execution slice (`OP-APP` — `provision-app` / `run-script`, implemented + live-verified). **SLICE_ROUTER** covers the router/firewall slice (Phase 1 SDN-SNAT egress + Phase 2 router node + VyOS fold-in, implemented + live-verified on `dev45`). **SLICE_ACTIVITY** covers the activity layer — relevant to the per-archetype activity work delegated to Claude Code (Immediate Next Actions §1).
13. **`docs/OPEN_QUESTIONS.md`** — index of design topics and their status. Q2 / Q3 / Q4 / Q5 / Q6 / Q7 / Q13 are resolved (links to formal specs in each entry); Q1 / Q8 / Q9 / Q10 / Q11 / Q12 are open and user-directed.
14. **`docs/tcpdump-sensor/README.md`** — capture-appliance-specific spec.
15. **`docs/PRODUCT_GAPS.md`** — living register of **gaps in the implemented product discovered during real use** (brittle/missing/under-finished things that block or surprise a real operator). Distinct from OPEN_QUESTIONS (unlocked *design*) and CHANGELOG (what *was* built). Add entries here as gaps are hit so we don't lose track of what's still missing from the final product.

#### Immediate Next Actions for Claude Code

Eight implementation slices, the control-plane API, and `axrange destroy` have landed — full detail (the CLI lifecycle unification, the Traffic/Exercise tiers joining the grid, NAME-targeting, the twin-model arc's `dev45` live-verify Stages 1–5) is archived verbatim in [`docs/DIRECTION_LOG.md`](docs/DIRECTION_LOG.md). **40 op types (34 action/wait + 6 barriers); 114 catalog rules; 3145 pytest tests; `axrange {te,o2,tap,tfc} {validate,plan,deploy,status,info,destroy}` (+ `list-deploys [--tier]` | `serve` | `image build` outside the grid) all wired**. Only **Stage 6** (`sme-flat`, the second twin-config) remains open. Live-verified end-to-end on **two clusters**: `pxtwin1` (PVE 8.4) and `dev45` (PVE 9.2 greenfield 2026-06-09). Re-running `axrange te deploy` is resume-safe; `axrange te destroy` is resume-safe too (idempotent, finishes a partial teardown on re-run).

**Active threads for Claude Code** (no gate; the user routes work between chat and Claude Code on a per-thread basis — see "Collaboration & Design Authority" below):

1. **Per-scenario overlays (`OP-OVL` identity / data / defense) and activity (`OP-ACT`)** for the seven `target-environments/` archetypes (`soc-detection-lab`, `ad-domain-lab`, `ctf-range`, `ot-ics-range`, `dfir-hunt-lab`, `vuln-management-lab`, `purple-team-range`). Design + implementation are delegated to Claude Code (CHANGELOG 2026-06-11). Each archetype gets its own set. The detailed build-by-build progress record for this item (per-op landings, live-verify results, dated CHANGELOG citations) is archived verbatim in [`docs/DIRECTION_LOG.md`](docs/DIRECTION_LOG.md) — see its "Immediate Next Actions" section. **Still open on Identity:** richer `privileges:` mapping (the last small AD item). **Still open elsewhere:** the behavior library, domain-actor activities, and `schedule:` windows on the activity side; and a first-class teardown/`destroy` path (**now built** — `axrange destroy` + `DELETE /api/deploys/{id}`, state-driven reverse-of-create incl. removing the VLAN OVS underlay from `/etc/network/interfaces`, name-guarded, idempotent; CHANGELOG 2026-06-23. **Live-verified on dev45 2026-06-23.** The network sensor remains a documented one-time provisioning, like the capture appliance — out of scope for per-deploy destroy). Overlay schema mechanics — IR layer vs. transformation, whether overlays can add nodes (e.g., a DC for AD identity, a SIEM for Defense), when in planning they emit, how they compose with `layers.apps` — get decided inline by Claude Code while building the first archetype's overlays; that first archetype sets the precedent for the others, so pick deliberately.
2. ~~`run-script` (`OP-APP-030`) live-verify~~ — **DONE** (2026-08-31). Full record archived in [`docs/DIRECTION_LOG.md`](docs/DIRECTION_LOG.md).
3. **`--fresh` deploy correctness fix — RESOLVED** (2026-07-07). Full record archived in [`docs/DIRECTION_LOG.md`](docs/DIRECTION_LOG.md).
4. **Provision-app ordering vs. router-ready** — apt-on-host should wait for `configure-router` to complete before pulling packages over a router-NAT'd egress; small emission refinement noted in CHANGELOG 2026-06-10 Phase 2.
5. **Per-rule firewall policy — PROMOTED 2026-09-03** (golden-twin fidelity direction, see the blockquote above): the one reproduction gap injection alone cannot fill, so this item moves from "fair game" to an active thread; a dedicated work order is scoped but not yet written. Router slice ships permissive default forward + LAN→WAN masquerade. Per-rule policy (OT conduit allowlists, DMZ rules) needs an IR extension; can land as Defense-overlay work (item 1) or as a router-node-level `firewall:` / `policy:` IR block — Claude Code's call. Either way the compile path is dual: REST/QGA for Debian/nftables, SSH/vbash for VyOS.
6. **Standing refinement bucket within `axrange/` and `o2/tcpdump-sensor/`:**
   - Planner: behavior library registry (REF-140 / REF-150 / LIF-030 / LIF-040 / ACT-020 / ACT-030 stubs); defense tool registry (DEF-010 heuristic); validator UX polish (`--strict`, `--disable-rule`, caching — `docs/VALIDATOR.md` §9); the previously-deferred planner machinery — parallel intra-deploy executor + throughput semaphores (`docs/PLANNER.md` §8), escalate-walk-back (§4.4) — is fair game when an active thread needs it. Plan-diff / re-planning (§9) gained a reduced add/remove-only subset 2026-08-11 (`axrange te deploy --update`, `docs/WORK_ORDER_twin_incremental_update.md`); §9.2's per-field redo-rules table remains unbuilt and is still fair game.
   - Adapter: per-op REST endpoint corrections; SSH connection pooling instead of per-op `subprocess.run(["ssh", …])`; `qm` CLI vs. REST trade-offs. GRE plumbing supports **2+ tapped bridges** and **per-tap sensor routing**: port name + GRE key are per-(bridge, sensor) (`gre_port_for_bridge_sensor` / `gre_key_for_bridge_sensor`) — CHANGELOG 2026-06-01 + 2026-06-03. Each target sensor's capture iface must be a flow-based gretap (`type gretap external`, `o2/tcpdump-sensor/README.md`) to decap any key into one merged stream.
   - Imagebuild: Ubuntu target (cloud images are structurally identical to Debian's, unverified until built); multi-version lifecycle bump (append a manifest version + demote prior `status:` to `deprecated`) vs. in-place rebuild; `config` / `role` / `container` provisioning kinds (currently a loud error); `--check-upstream` newer-point-release reporting (`docs/IMAGE_BUILD.md` §7); Windows image building if it becomes useful.

#### Conventions to Follow

> **Naming + doc-genre conventions live in `docs/CONVENTIONS.md`** — canonical product names
> (`axRange` display / `axrange` code / `axr-product` repo / `axellio-` org namespace; the `axr`
> resource prefix is **retired** in favor of the tier tags), the `te`/`tap`/`o2`/`img` resource
> tier tags, and the five doc genres (spec / `docs/design/` design-of-record / `SLICE_*` /
> `WORK_ORDER_*` / living register). The code-structure conventions below are the companion.

- **One thing per top-level directory.** The current top-level layout — four component dirs (engine → surface → content → network sensor) plus supporting dirs and root config:
  - `axrange/` — the **standalone engine**: the Python package monorepo (plan → deploy → destroy). Submodules host the implementation-cleared planes: `ir/` (IR parser + schema), `manifest/` (image catalog loader), `ops/` (op registry), `validator/` (rule catalog + 4-pass engine), `planner/` (passes 1-5 + SQLite state store + sequential executor + cross-process lease/resource-locks), `adapter_proxmox/` (one interface + `rest`/`ssh` backends + Model C naming), `imagebuild/` (the `axrange image build` pipeline — `docs/IMAGE_BUILD.md`), `config_exec/` (the QGA-mediated Ansible config-execution backend for `config`-class ops — authorized 2026-06-02, `docs/SLICE_CONFIG.md`), `sensors/` (sensor-type registry — engine vocabulary), `api/` (control-plane API), `cli/`. The engine stays standalone; it never imports from `o2/`, `scenario_ai_research/`, or `target-environments/`. Canvas (Q1) and AI core (Q8) layouts are open.
  - `o2/` — the **Observability-&-Orchestration surface layer** (`docs/design/05-o2-surface.md`), the surfaces *around* the engine: the Grafana/Loki/Prometheus telemetry stack + the `axellio-axrange-control-panel` Grafana plugin (`o2/grafana/plugins/`), plus **`o2/tcpdump-sensor/`** — the capture appliance (the former top-level capture directory), a standalone systemd-managed component (shell scripts, systemd units, installer). The `tcpdump-sensor` payload is **not Python**, so it can be deployed on a host with no Python environment. **`o2/console/` joined it 2026-09-08** (`docs/design/15-console-canvas.md`, `docs/WORK_ORDER_console_canvas.md` §13) — the **Console canvas**, a clean-start Vite/React/TypeScript SPA that displays a cast twin's discovered structure and (from S5 on) edits it as assertion records; scaffold-only today. It is **not Python either** and is not installed by `pyproject.toml`: build it with `npm ci && npm run build` and co-serve the `dist/` with `axrange serve --spa-dir`. It never imports from the Grafana plugin and the plugin never imports from it.
  - `target-environments/` — the **deployable Scenario IR library** (the seven Stage-2 archetypes + the `fabric-verify` / `optrahealth-twin` verification/customer environments). Content, not code: versioned YAML IRs the engine consumes (`--scenarios-dir`). Renamed from `scenarios/` 2026-07-08 (CHANGELOG); the O2 direction reframes these as the *target environments* stood up and attacked. The version-locked "Scenario IR" schema term is unchanged (deferred).
  - `scenario_ai_research/` — the **AI scenario research** (ratified 2026-06-11, `docs/design/12-ai-scenario-research.md`): `research/` (Stage-1 OSS local ingestion: discover → fetch → transcribe → summarize → corpus emit) + `stage2/` (Stage-2 design notes: `CORPUS_DIGEST.md` + `RESEARCH_HANDOFF.md`). Its Stage-2 IR outputs live in `target-environments/`. One-directional coupling: `scenario_ai_research/...` (and the `target-environments/` IRs) may import axRange's validator/planner/op catalog; axRange must not import anything from `scenario_ai_research/`. Lives on `wip/ai-scenario-sidecar`; master-merge is a separate user call.
  - Supporting dirs (not components): `docs/` (design specs + `CHANGELOG.md` + `LESSONS.md`), `tests/` (the pytest suite), `scripts/` (workflow + helper tooling). Root config: `pyproject.toml`, `adapter.conf`(.example).

  Don't mix concerns across submodules: `planner/` doesn't know about Proxmox (it talks to `adapter_proxmox/` through the `Adapter` protocol); `adapter_proxmox/` doesn't know about IR or scenarios (it operates on `OpInstance` objects); the canvas wouldn't import the planner. The submodule structure inside `axrange/` is itself the boundary enforcement. `imagebuild/` is an orchestration consumer — it reuses `adapter_proxmox/`'s REST/SSH/config and runs a real `planner`+adapter deploy for its smoke-test (the same way `cli/` wires validate→plan→deploy); it is allowed to depend on those, like the CLI does. `config_exec/` is the `config`-class counterpart to `adapter_proxmox/`: the planner/executor routes `config`-class ops to it by `idempotency_class`, and it reuses `adapter_proxmox/`'s qemu-guest-agent exec channel to deliver Ansible into the guest (the Ansible *render* stays platform-agnostic; only the *delivery channel* is Proxmox-specific).

  Exception that proves the rule: `op_configure_cloud_init` in `adapter_proxmox/rest.py` calls into `adapter_proxmox/ssh.py` for the snippet-file SCP, because PVE has no REST endpoint for uploading snippets. Same reason `op_apply_sdn_config` shells out via SSH to re-assert VM taps after `PUT /nodes/<node>/network`. These cross-backend hops are op-internal — they don't break the planner-side `Adapter` protocol.
- **No leaking platform details up the stack.** The sensor doesn't know what scenarios are. The adapter doesn't know what AI is. Keep boundaries clean.
- **Config files over flags.** Anything an operator tunes lives in a config file (e.g. `capture.conf`), not in service unit flag soup. Easier to diff, document, version.
- **Health/metrics are first-class.** Every long-running component exposes a `--healthcheck` mode (exit 0/non-0) and structured log output. No silent failures.
- **No exotic kernels or third-party DKMS drivers in v1.** Stock Debian/Ubuntu only. PF_RING / DPDK / AF_XDP are future-work doors, not v1 commitments.

#### Operational Notes

A few operational facts worth knowing up front. These describe how things currently work, not what's forbidden.

- **Cloud-init delivery uses `cicustom`.** The slice's user-data snippet (hostname, sudo'd user from the binding's `cloud_init.user`, ssh authorized_keys, `runcmd` to start qga) is rendered by `_render_userdata` in `adapter_proxmox/rest.py` and SCP'd to `/var/lib/vz/snippets/`. Setting only `ciuser` / `sshkeys` directly on the VM config (without `cicustom`) won't get the runcmd, and the runcmd is what closes the QGA bootstrap loop for templates that don't preinstall it. *(Exception: VyOS routers — their binding sets `cloud_init.supported=false` because the generic ISO ships neither cloud-init nor QGA; config delivery goes over SSH `vbash` via `vyos-ssh-set`. See `docs/SLICE_ROUTER.md`.)*
- **Manual template-rebuild runbook is the documented fallback** for hand-built templates (not pipeline-built). `docs/IMAGE_BUILD.md` documents the untemplate → toggle LV r/w → apt install QGA → cloud-init clean → reset machine-id + ssh host keys → poweroff → restore LV r/o → retemplate procedure. The imagebuild slice (`axrange/imagebuild/`) automates the same flow from upstream cloud images.
- **Capture is merged per sensor.** Each sensor decapsulates all GRE-keyed streams into one `tcpdump`'d pcap rotation organized by time and tunnel source. Per-scenario attribution by GRE key (`OP-MIR-030 register-mirror-source`, reserved in the catalog) is not currently implemented; if a scenario needs a separate capture, route its taps to a different sensor (per-tap sensor routing, CHANGELOG 2026-06-03).
- **SDN-native networking + Option 1 capture** (per-tap `tc` on the Linux vnet bridge → keyed gretap → flow-based sensor) is the architecture. The earlier slice-MVP `/nodes/<node>/network` bridge bypass and the per-VM OVS port-mirror have both been retired. See `docs/ARCHITECTURE.md` §3 and the `adapter_proxmox/` bullet in Current State above for the full picture.

#### Git Policy

This project is tracked in a local git repo with a remote (`origin`). **Outbound push is user-authorized (2026-06-09); inbound sync stays manual.**

**Allowed:** `git status`, `git log`, `git diff`, `git show`, `git add`, `git commit`, `git branch`, `git checkout`, `git switch`, `git stash`, `git restore`, `git merge`, `git rebase`, **`git push`** (on the user's explicit request — e.g. pushing a `wip/*` branch).

**Forbidden:** `git fetch`, `git pull`, `git clone` (from remote), `git remote add`, `git remote set-url`. Any *inbound* sync or remote-reconfiguration command.

If asked to **pull/fetch/clone** from the remote, respond: *"Inbound remote sync is your manual responsibility. Run pull/fetch/clone yourself from the terminal."* Push is fine when the user asks.

**Commit style:** `type: subject`, where `type` names either the *kind* of change or the *area* of the codebase. Both axes are valid; pick whichever describes the commit best, and bias toward the area prefix when a commit is entirely scoped to one area.

Kind prefixes:

- `feat` — new functionality
- `fix` — bug fix
- `docs` — implementation-driven doc corrections
- `design` — deliberate design changes (confirm first only when the risk trigger in Design Document Maintenance applies)
- `refactor` — code restructure, no behavior change
- `test` — test-only changes
- `chore` — tooling, build, dependency bumps

Area prefixes (use when the entire commit is scoped to one area):

- `o2` — the observability & orchestration surface layer (`o2/`), including the capture appliance (`o2/tcpdump-sensor/`)
- `axrange` — the Python package (`axrange/`, covering `ir/`, `manifest/`, `ops/`, `validator/`, `cli/`, and future `planner/`, `adapter_proxmox/` submodules)
- `canvas`, `ai` — reserved for when those planes come online

#### Design Document Maintenance

The documents in `CLAUDE.md` and `docs/` are a living design spec, not a frozen contract. Update them as implementation reveals new information.

**Edit docs freely when:**
- Errors or inaccuracies found during implementation.
- Default parameters change based on real-world tuning (e.g. ring buffer sizes for the sensor).
- Small components added that the design didn't anticipate.
- Code comments or READMEs improve accuracy.

**Confirm with the user first only when a change is hard to reverse or ratified:**
- It **reverses a ratified decision** in the CHANGELOG (add a `**Supersedes:**` line and confirm).
- It **changes a version-locked contract schema** (`IR_SCHEMA` / `PLANNER` / `OP_CATALOG` / `VALIDATOR` / `ADAPTER` / `IMAGE_MANIFEST`) — bump the schema version and confirm.
- It **shifts the scope or direction** of the current or future iteration.

Otherwise: **advance the design, make the change, and record it.** Design authority is not gated by *surface* — Claude Code is a first-class origin for design decisions, not just implementation corrections (see "Collaboration & Design Authority"). The safety net is the *record* (Authority marker + `Supersedes:` on reversal) and the user's diff review, not a pre-flight permission step.

**When editing CHANGELOG entries:** use the Authority marker (`user` / `implementation (Claude Code)` / `design (chat session)` / `retrofit`). Use `design (chat session)` **only** when the decision actually came from an invoked chat-bridge session; a design decision Claude Code makes directly is `implementation (Claude Code)`.

**Commit prefixes:**
- `docs: <description>` — implementation-driven corrections.
- `design: <description>` — deliberate design changes (confirm first only when the risk trigger above applies).

**When a change might be hard to reverse,** confirm. Otherwise proceed and record — re-deriving a lost decision is the drift we're avoiding, and an over-eager gate is what slows the work.

#### Session Handoff

`docs/SESSION_STATE.md` is the handoff between sessions — a **committed** file, so it travels with the repo to the next session on any machine or clone. (Solo mode's checkpoint/restore machinery was retired 2026-08-04; see the CHANGELOG entry. `SESSION_STATE.md` is no longer gitignored.)

- **At session start,** read it. It says what the last session did, what's half-finished, the next concrete step, and the gotchas. Restate your understanding of it before continuing.
- **At session wrap,** update it and commit it with the work it describes. Git history says *what* changed; this says *why* and *what's next* — the part a diff can't tell you. The `doc-writer` pipeline stage owns this at the end of a `/dev` run.
- **Genuinely mid-flight work** goes on an ordinary branch (this project already works this way — `wip/restructure`, `wip/fabric-slice`); name that branch under "Work in progress" so the next session knows where to look.
- A stale handoff is worse than none, because it will be believed. If it no longer matches reality, fix it or empty it.

#### Collaboration & Design Authority

Design and implementation both happen here in Claude Code. Chat-bridge is one **optional** surface, not the home of design. The working agreement (`docs/WORKFLOW.md`):

- **Any surface can make design decisions; Claude Code advances design by default and records it.** `implementation (Claude Code)` is a first-class origin for *design* decisions, not just code corrections. Routing a question to a Claude **chat** session is the user's call, per-thread (CHANGELOG 2026-06-11) — reach for it for greenfield or cross-cutting design, not as a required step.
- **The record is the safety net, not a pre-flight gate.** Every decision carries a CHANGELOG Authority marker (`user` / `implementation (Claude Code)` / `design (chat session)` / `retrofit`) and a `**Supersedes:**` line when it reverses an earlier one. Pause-and-confirm only on the risk triggers in "Design Document Maintenance" (reversal of a ratified decision, a version-locked schema change, or a scope shift).
- **Don't silently overwrite a *ratified* decision from any surface** — reconcile it explicitly via a `Supersedes:` entry. (This replaces the old "defer to the last chat session" rule; origin no longer privileges one surface.)
- **When chat-bridge mode is invoked:** the user tags the completion commit `chat-YYYYMMDD` and pastes a `NEXT_SESSION_PROMPT.md` to open the next Claude Code session. Both are chat-mode mechanics — they don't apply to ordinary work or to pipeline runs. To see what changed since the last chat round: `git log $(git tag -l 'chat-*' | sort -V | tail -1)..HEAD --oneline -- CLAUDE.md docs/`.
- **The background subagent pipeline** (`/dev`, `.claude/agents/`, `docs/LESSONS.md`) is the third working mode — see `docs/WORKFLOW.md` "Pipeline mode."

#### Starting Work

There is no fixed pre-flight questionnaire (the old four capture-appliance-bring-up questions are retired — the sensor is live-verified on two clusters). Pick up any active thread (see "Immediate Next Actions for Claude Code") or a task the user routes to you. Two things worth confirming in context, not as a gate:

- **Before a live deploy:** the target cluster/host and any environment specifics not already in `adapter.conf` (default target is `dev45`; `pxtwin1` is the other live cluster).
- **For a background pipeline run:** use `/dev <task>` — see `docs/WORKFLOW.md` "Pipeline mode" for the code → test → review → docs → (gated) live-verify flow and how it checks back with you only when a decision is needed.
