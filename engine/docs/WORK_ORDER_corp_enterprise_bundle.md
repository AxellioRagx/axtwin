# Work Order — A complete, co-located `corp-enterprise` deployment bundle in the new schema

> **STATUS: DONE (code / content / docs). Gated `dev45` live-verify PARTIAL PASS 2026-08-05** — tap + tfc PROVEN live, clean-cascade teardown PROVEN at 30-VM scale, te deploys soundly (377/383); **exr DEFERRED** on a dev45 upstream-egress outage (external, not a bundle issue), see CHANGELOG 2026-08-05. Authority: **user (2026-08-04)** — "create a new set of yaml files for
> corp-enterprise deployment from scratch using the new schemas … all the configs for all the tiers
> … saved together in the same folder … in one place." **Route:** Pipeline (`/dev`) — code-writer
> (authors content + the one exr code change) → tester → code-reviewer → doc-writer → **gated**
> live-verify (deploy the real bundle on `dev45`). **Blocked by**
> `docs/WORK_ORDER_w1_live_verify.md` (the user's explicit sequencing: verify W1 live *first*).

## 1. Goal

One folder — `twin-configs/corp-enterprise/` — that holds **every tier's spec for the corp-enterprise
twin**, authored from scratch in the post-2026-08-04 schema, so an operator can stand up the whole
five-tier world from a single place and a short, documented command sequence. Today the folder holds
te/tap/tfc but the **exercise** spec lives orphaned at `deploy-dev45/exr-ad-killchain.yaml` and is
welded to a dead deploy id — the bundle is neither complete nor portable.

## 2. What "the new schema" means (this is the point of "from scratch")

The existing `twin-configs/corp-enterprise/*.yaml` predate parts of W1 and were mechanically migrated,
not re-authored. Re-author them cleanly against the current contracts:

- **Twin config** (`corp-enterprise.yaml`): `nodes[].name` (id stem) + optional `nodes[].role`
  (semantic baseline) — never the old single `role:`-as-stem. No `persona:`, no `purpose:`, no
  `identity_role:`, no `policy:`, no top-level `identity.groups/ous` (all removed; `extra="forbid"`
  rejects them now, so a leftover fails loudly). This is also a real exercise of the closed
  vocabulary on content, which is worth doing deliberately.
- **Traffic profiles** (`tfc-*.yaml`): `target:` a twin-config **name**, not a deploy id;
  `roles:`/`roles_from:` where a shared map helps; personas validated against the live registry.
- **Tap plans** (`tap-*.yaml`): already `selector: {scope, target}` and name-targeted — keep, but
  re-confirm they resolve against the re-authored twin's topology.
- **Exercise spec** (`exr-*.yaml`): brought INTO the folder, re-addressed to corp-enterprise's
  topology, and **name-targetable** (see §4).

Keep the persona/role *intent* the current files express — the deployment must be behavior-equivalent
to what the archetype means. "From scratch" is about the schema and the co-location, not about
inventing a different corp-enterprise.

## 3. Deliverables (all under `twin-configs/corp-enterprise/`)

| File | Tier | Notes |
|---|---|---|
| `corp-enterprise.yaml` | `te` | re-authored twin config (name/role split, forbid-clean) |
| `roles.yaml` | shared | the tfc role map, `roles_from:`-included |
| `tfc-default.yaml`, `tfc-busy.yaml` | `tfc` | name-targeted traffic profiles |
| `tap-default.yaml` (+ the existing `tap-reshard`/`tap-xpress*`) | `tap` | re-confirmed against the new twin |
| `exr-ad-killchain.yaml` | `exr` | **moved in** from `deploy-dev45/`, name-targetable |
| `README.md` | — | the index + the deploy/destroy command sequence (§5) |

The **only file outside the folder** that this work order removes is the orphaned
`deploy-dev45/exr-ad-killchain.yaml` — moved, not copied (leaving a stale duplicate would re-create
exactly the "which one is real" confusion this order exists to end). Verify nothing references its old
path first (`grep -rn exr-ad-killchain`).

## 4. The one code change: exercise-tier name-targeting (Authority: user — flag at review)

`exr` is the last tier still requiring a **literal deploy-id** `twin:` (`exercise.py` does
`store.get_deploy(spec.twin)`, and `twin:` is required). That is the exact LES-072 staleness trap tap
plans fixed 2026-07-25 and `tfc` fixed under W1 (2026-08-04) — and it is what makes a co-located,
"just run them" exr spec impossible: the id it names dies with every redeploy.

**Give `exr` the same parity, reusing the same resolver** (LES-037 — do not write a third copy):
- `exercise.twin` accepts a deploy id **or** a scenario/twin-config name, resolved via
  `taps.resolve_target_deploy` (wrapped to raise `ExercisePlanError`, exactly as
  `traffic.resolve_traffic_target` wraps it).
- `--target` override on the `exr` deploy/run verb + API body, same precedence (override > spec >
  error).
- Keep `twin:` as the spec key (it is already the exr spelling; no alias churn needed). It becomes
  OPTIONAL only if a `--target` is supplied — match whatever `tfc` settled on so the two tiers behave
  identically.

This is a behavior extension, not a version-locked schema change (no IR/OP_CATALOG/VALIDATOR touch,
no op count change). It is **in scope because the bundle is not portable without it** — but it is a
code contract change, so it must be called out explicitly at review, not slipped in. If the reviewer
or user would rather ship the exr spec with a documented `twin:` placeholder and defer the code, that
is a legitimate smaller scope — but then say so loudly in the README, because a placeholder that
looks runnable is its own trap.

## 5. The README (the thing that makes it "one place")

`twin-configs/corp-enterprise/README.md` — short, operator-facing:
- one line per file: which tier, what it does;
- the **deploy sequence** as copy-pasteable commands, in order, with the `--images deploy-dev45` /
  `--db deploy-dev45/state.db` flags that a dev45 operator actually needs (GAP-024/GAP-031), and the
  standing-O2 precondition the tap plan needs (`o2-suricata`/`o2-tcpdump` must exist — `o2 status`);
- the **teardown** command (`te destroy` cascades tap/tfc/exr, LES-171) and what it does and does not
  touch;
- the one manual step, if §4 lands as a placeholder rather than the code change.

## 6. Validation

**Unit/suite:** every shipped file parses and resolves; the twin config expands and validates
(`--images deploy-dev45`); its `plan_id` is recorded (it MAY differ from the current
`corp-enterprise-86423faa` if the re-authoring changes IR content — if so, that is expected and the
existing plan-id regression pin must be updated deliberately, with the change noted, not silently).
The exr name-resolution gets the same test shape `tfc`'s did. Full suite green.

**Gated `dev45` live-verify (the real thing):** stand up the actual bundle end to end on the clean
cluster (§2 of the live-verify order describes its state) — `te deploy` → `tap deploy` (bind to the
standing O2 sensors) → `tfc deploy` → `exr run` (the AD kill chain, name-targeted) — then tear it all
down with one `te destroy` and confirm the cascade cleaned tap/tfc/exr and left every protected row
(O2 surface, external sinks, templates) untouched. This is a bigger, longer run than the W1 verify;
it is the acceptance test for the bundle as a deliverable.

> **Do the W1 live-verify FIRST** (`docs/WORK_ORDER_w1_live_verify.md`). If W1's mechanism is broken
> on-cluster, this bundle run would just rediscover it at 32-host scale. Gate accordingly.

## 7. Out of scope

- Changing what the corp-enterprise archetype *is* (topology, identity, the kill chain). Re-author
  the schema, preserve the meaning.
- The other four twin-configs' bundles. If this one lands well, the same co-location pattern is worth
  repeating — but that is follow-on work, not this order.
- Promoting `exr` (or anything) to a `_VALID_TIERS`-level change beyond the name-resolution parity in
  §4.
