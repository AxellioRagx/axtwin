# Work Order — Tap scopes: `port` / `segment` / `perimeter` / `boundary` + derived direction

> **STATUS: DONE (code / tests / docs).** Authority: user (2026-07-27, design ratified in-session);
> build Authority: implementation (Claude Code). **Gated `dev45` live-verify PENDING** — requires a
> freshly deployed twin (LES-096: `perimeter: wan` cannot resolve on a twin deployed before this
> change; a `--fresh` redeploy is required, not a resume).
> M1–M7 shipped as designed below; M8 (docs) is this pass. **Three review rounds** were run
> (code-writer → tester → code-reviewer, iterated three times) before all findings closed — see
> CHANGELOG 2026-07-27 "Tap scopes BUILT" for what the reviews surfaced (the direction-aware
> `verify-capture` fix and the twin-discriminated `vmbr0` gretap naming were both review catches,
> not part of the original build pass). **M5's version-lock gate is RESOLVED (Authority: user,
> 2026-07-27): additive key within an existing input, NO version bump** — `docs/OP_CATALOG.md` now
> documents `tapped_nics.direction` and `record-tap-intent`'s always-included WAN entry, with no
> version change (see M5's gate below and the CHANGELOG entry's "Version-lock ruling (RESOLVED)").
> **Spec:** `docs/DESIGN_BRIEF_tap_scopes.md` (design of record) — §3 direction, §4 the scopes,
> §5 the WAN edge, §6 syntax, §7 the code-change map.
> Route: code-writer → tester → code-reviewer → doc-writer → **gated** live-verify (dev45).
> Evolves the existing tap-point model (`axrange/planner/tappoints.py`, BUILT 2026-07-24) and the
> A2 tap playout (`axrange/o2control/taps.py`) — **do not rebuild either.**
> Supersedes the scope set in `docs/DESIGN_BRIEF_twin_model.md` §4.1 (CHANGELOG 2026-07-27).

## 1. Goal

Re-base the tap-point scope vocabulary on the SPAN model the mechanism already implements, and
give it a **capture direction derived from the scope** so a multi-port tap stops delivering every
frame to the sensor 2..N times.

Four scopes, each answering a distinct question:

| scope | target | ports | direction |
|---|---|---|---|
| `port` | `{host, net}` | 1 — that host's leg on that net | **tx & rx** |
| `segment` | net id | every port on the net, **routers included** | **rx** |
| `perimeter` | net id | every **router** port attached to the net | **tx & rx** |
| `boundary` | `[net-a, net-b]` | the joining router's port on each net | **rx** each side |

Direction is **never authored** — it follows from the scope. Also closes the WAN-egress half of
`docs/PRODUCT_GAPS.md` GAP-029 (`perimeter: wan`).

## 2. Build — six phases, in order

### M1 — scope vocabulary (`axrange/planner/tappoints.py`)

- `TAP_SCOPES` → `("port", "segment", "perimeter", "boundary")`.
- `leg` → **`port`**: pure rename, same `{host, net}` target shape and same resolution.
- Old **`host`** scope (every leg of one host) is **dropped**. It becomes N `port` bindings or a
  `select_tap_points` query — do not add a replacement scope.
- New **`perimeter`**: `target` is a net id; resolves to every **`kind: router`** port attached to
  that net. Requires `HostInfo.kind` (M2).
- New `direction_for_scope(scope) -> "rx" | "both"` — `"rx"` for `segment`/`boundary`, `"both"` for
  `port`/`perimeter`. **Keep `resolve_tap_point`'s signature unchanged** (`list[TapInterfaceRef]`);
  direction is a property of the *binding*, not the port, so the caller composes the two.
- `derive_default_tap_points`: emit `port` points (renamed from `leg`, id stays `host:net:nic`) and
  `perimeter` points (id `<net>-perimeter`, plus `wan-perimeter` once M2 lands). Drop `host`-point
  derivation. `segment`/`boundary` derivation is unchanged — **and `segment` keeps router legs**
  (brief §3.1: under rx-only, excluding them is a coverage hole, not a de-dup).
- Retain `_is_router_kind` — now load-bearing for `perimeter`, no longer used for exclusion.

### M2 — the WAN edge in the inventory (`axrange/planner/inventory.py`)

- `HostNet` gains `external: bool = False`. **A typed marker, not name-sniffing `__wan__` at each
  call site.**
- Stop discarding the uplink at `inventory.py:344-345`; record it with `external=True`. The data is
  already emitted by `pass1_emit.py:869-882` (`net_id: "__wan__"`, real `nic_index`, `vmbr0`).
- `HostInfo.kind` + `clone-vm`'s `node_kind` input are **already in the working tree** — finish and
  test them, don't re-derive. Keep the back-fill of `kind: "router"` from the presence of a
  `configure-router`/`configure-routing` op, so twins deployed before the field existed still
  identify their routers (LES-096).
- **Audit every consumer of `hosts[].nets[]`** and make each one that means "twin segments only"
  filter on `external` — known sites: `o2control/taps.py:642`, `planner/access.py:123`, the API
  DTOs, the traffic tier. This is where the change can leak; it is the main review focus.
- Thread `kind` + `external` onto `HostInventoryDTO`/`HostNetDTO` (`axrange/api/{dtos,service}.py`;
  `kind` is already in the tree).

### M3 — `wan` alias + WAN-port eligibility

- `wan` is the **author-facing alias** for the `__wan__` sentinel, resolved in the tap-plan parser
  (`axrange/o2control/taps.py`). `__wan__` stays the stored `net_id` — it is already the value in
  `state.db` and renaming it would mean migrating state. The sentinel must never appear in an
  operator-authored file.
- **Eligibility unblock (decision, Claude Code, 2026-07-27):** `OP-MIR-005 record-tap-intent`
  always marks a router's WAN port tappable. This is the **minimal, reversible** option (b) from
  brief §9(2) — the WAN port has no `NetRef` to carry a `tap:` flag, so `perimeter: wan` cannot
  resolve otherwise. The larger structural fix (move eligibility out of the twin IR entirely) is
  **`docs/WORK_ORDER_tap_eligibility.md`, deliberately NOT in this order.** Leave a comment at the
  special-case site pointing at that order so it is removed, not layered on.

### M4 — direction through binding resolution (`axrange/o2control/taps.py`)

- Parse the new scope values (`_selector_from_dict`'s validation against `TAP_SCOPES` picks the new
  set up automatically — verify, don't duplicate the list).
- Carry each binding's direction from `direction_for_scope` into op composition.
- **Union directions per `(port, sensor)`** before composing ops. A port can legitimately appear in
  two bindings aimed at the same sensor with different directions (`segment: X` plus
  `port: {h, X}`); the result must be **one** mirror carrying the union (`both` wins over `rx`).
  `mode: exclusive` does **not** cover this — it guards port→multiple-*sensors*. Without the union
  the second binding silently clobbers the first.
- The de-scope pass must diff at the same `(port, sensor, direction)` granularity — **LES-118** (the
  shared-gretap de-scope data-loss bug) is the precedent; re-read it before touching this path.

### M5 — per-gretap direction at the mirror (`axrange/adapter_proxmox/ssh.py`)

`install-ovs-mirror` currently installs **both** direction filters chaining **all** gretaps
(`ssh.py:383-384`). Make direction per-gretap:

- `tapped_nics[]` entries gain `direction: "rx" | "both"`.
- **ingress** filter: chains every gretap (all directions include rx).
- **egress** filter: chains only the `"both"` gretaps; **omit the filter entirely** when none.
- Unchanged: one `clsact` reset per NIC, chained `mirred ... pipe action mirred ...` per gretap,
  unique per-bridge port names (**LES-011**), and the empty-`tapped_nics` clear path
  (CHANGELOG 2026-07-26 — a narrowing re-apply must still clear the mirror, not raise).
- Apply the same change to the post-start/pre-stop hook render so a VM reboot restores the same
  directions.

> **GATE — RESOLVED 2026-07-27 (Authority: user): additive key within an existing input, NO version
> bump.** `tapped_nics` sits in `OP-MIR-020 install-ovs-mirror`'s `inputs` in `docs/OP_CATALOG.md`, a
> **version-locked contract**, so this was raised rather than decided silently (CLAUDE.md "Design
> Document Maintenance"). Both the code-writer and the code-reviewer independently ruled it additive,
> and the user ratified: the input name and type token (`tapped_nics: nic-index-list:list`) are
> unchanged, the per-entry shape is prose-documented rather than a typed field list, two prior keys
> (`sensor_id`, `gre_port_input`) were added the same way in 2026-06-19, and an absent `direction`
> defaults to `"both"` so every pre-existing persisted op behaves identically (LES-096). `OP_CATALOG`
> is documented, not bumped; op count stays 40 and rule count stays 114. See the CHANGELOG entry's
> "Version-lock ruling (RESOLVED)" block.

### M6 — CLI consistency (added 2026-07-27, Authority: user)

The CLI must speak the new vocabulary; these are the concrete surfaces:

- `axrange/cli/main.py:~1544` — the `te inventory` renderer's `for net in h.nets:` loop. The WAN
  uplink now appears in `hosts[].nets[]` (M2): render it with its `external` marker visible (e.g.
  `wan (external)`) rather than silently listing it as if it were a twin segment. Show `kind` on
  the host line where it aids reading (a router should be identifiable).
- `axrange/cli/main.py:~1546-1550` — the `tap_points:` block: render the new scopes, and show each
  point's **derived direction** (`rx` / `tx&rx`) so an operator can see what a point will capture
  without reading the brief.
- `axrange/cli/main.py:~2204` — `binding_label(b)` in the tap-plan renderer (`tap validate` /
  `tap plan` / `tap info` / `tap status`). Bindings must show their direction. **Extend
  `binding_label` in `axrange/o2control/taps.py`, do not re-derive the logic CLI-side** —
  **LES-037** exists precisely because that logic was duplicated once already.
- `axrange/cli/main.py:~2171-2186` — `tap validate`'s "well-formed selector/scope" path: its error
  messages must name the new scope set, and a plan using a retired scope (`leg`/`host`) must fail
  with an **actionable** message naming the replacement (`leg` → `port`; `host` → N `port`
  bindings or a selector), not a bare "unknown scope".
- `axrange/cli/main.py:~3111-3114` — `twin-config show`'s `tap_points` rendering (`scope=…`).
- `--json` output carries the real values for all of the above; direction is a field, not only a
  rendered string.

### M7 — shipped content migration (added 2026-07-27)

`twin-configs/datacenter/datacenter.yaml:140` is the **only** shipped use of a retired scope:

```yaml
- { id: fab-r2-legs, scope: host, target: fab-r2, labels: [fabric], when: FABRIC }
```

`fab-r2` is single-homed (`net: fab-link`, line ~121), so "every leg of fab-r2" is exactly one
port. Migrate mechanically, preserving `labels:` and the `when: FABRIC` gate:

```yaml
- { id: fab-r2-legs, scope: port, target: { host: fab-r2, net: fab-link }, labels: [fabric], when: FABRIC }
```

No other shipped file uses `host` or `leg` (audited 2026-07-27 across `twin-configs/` and
`target-environments/`). Also update `_apply_tap_points`' scope handling in
`axrange/twinconfig/expand.py` — a `TwinConfigTapPoint` shares this scope vocabulary and its
materialization must accept `port`/`perimeter` and reject the retired names with the same
actionable message as M6. `TapPointMaterializationError` behavior is otherwise unchanged.

### M8 — docs

- `docs/CHANGELOG.md` — a BUILT entry (Authority: implementation (Claude Code)) beneath the
  2026-07-27 design entry; do not edit the design entry.
- `docs/DESIGN_BRIEF_tap_scopes.md` — flip the status header to BUILT, record what shipped and what
  did not.
- `docs/DESIGN_BRIEF_o2_taps.md` §10, `docs/DESIGN_BRIEF_twin_model.md` §4.1 — update the existing
  supersedes pointers to say BUILT.
- `docs/OP_CATALOG.md` — `install-ovs-mirror` inputs, if M5's gate resolves that way.
- `docs/PRODUCT_GAPS.md` GAP-029 — WAN half 🟡 → ✅ Resolved (build); VXLAN half stays 🔴.
- `docs/LESSONS.md` — new LES-NNN for anything learned (check the file tail for the next number,
  **LES-050**: never trust a prior session's count).

## 3. Boundaries — explicitly OUT of scope

- **Filtering (BPF).** Deferred by the user 2026-07-27; likely a full BPF filter later. Do not add
  a `filter:` key, a `tc flower` compile path, or any validation for one.
- **The eligibility shift.** `docs/WORK_ORDER_tap_eligibility.md`. M3's WAN special-case is the
  only eligibility change permitted here.
- **`exclude:` on `segment`.** Deliberately dropped (brief §4.2) — under rx-only it is a silent
  coverage hole. Do not add it.
- **VXLAN-bridged segment pairs** (the other half of GAP-029) — `perimeter`/`boundary` both key off
  a joining host; a spine-mediated pair has none. Untouched.
- **Selector / `tap_point:` addressing, `mode: fanout|exclusive`, the tap-plan file shape.** All
  carry the new scopes unchanged (`docs/DESIGN_BRIEF_o2_taps.md` §10). No new addressing mode.
- **The three shipped `twin-configs/corp-enterprise/tap-*.yaml` plans need no edit** — they use only
  `segment` and `boundary`, unchanged in spelling. Their *semantics* change to rx-only; that is
  expected, not a regression.

## 4. Validation

- **Done: 1693 passed / 12 skipped / 0 failed, 1705 collected** (`.venv/bin/python -m pytest -q`),
  re-derived at doc-writer time rather than trusted from a prior report (LES-050); baseline was
  **1646 passed / 12 skipped**.
- Full suite green (`.venv/bin/python -m pytest -q`; baseline **1646 passed / 12 skipped**).
  `.venv/bin/python scripts/doc-stats.py --check` clean. **Use `.venv/bin/python`** — the system
  interpreter has no pytest and `doc-stats.py` silently skips its check.
- New unit coverage, at minimum: `direction_for_scope` per scope; `perimeter` resolution on a
  multi-router net; `perimeter: wan`; `segment` **includes** router legs; the `(port, sensor)`
  direction union; the M5 render with a mixed rx/both NIC and with an all-rx NIC (no egress filter
  emitted); `external` filtering in each audited consumer.
- Plan-level check against the `corp-enterprise` twin-config: all three shipped tap plans still
  parse, resolve, and produce the same *port sets* (only directions change).
- **Gated live-verify (dev45) — STOP and ask the user before any deploy/destroy.** Needs a real
  twin: bind a `segment`, a `perimeter`, and a `perimeter: wan` point; confirm at the sensor that a
  host↔host frame arrives **once** (not twice) under `segment`, and that the WAN uplink capture
  carries real egress traffic.

## 5. Depends on / sequence

- Depends on nothing unbuilt. M1→M2→M3→M4→M5→M6→M7→M8 in order; M2 blocks M1's `perimeter`
  (needs `kind`) and M3 (needs the recorded uplink); M6/M7 follow M1–M5 (they consume the new
  vocabulary). M8 is the doc-writer stage's, not the code-writer's.
- **Working tree, before starting:** `axrange/{planner/pass1_emit.py,planner/inventory.py,
  api/dtos.py,api/service.py}` carry an uncommitted `node_kind`/`HostInfo.kind` prototype — **keep
  and finish it** (M2). The router-leg exclusion that shipped alongside it in `tappoints.py` was
  **reverted 2026-07-27 as superseded** (brief §3.1); do not reinstate it. The reverted diff is at
  `scratchpad/superseded-router-leg-exclusion.diff` if needed for reference.
- Follow-on: `docs/WORK_ORDER_tap_eligibility.md` (user-gated); BPF filtering (deferred, no order
  yet).
