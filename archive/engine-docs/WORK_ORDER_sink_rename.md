# Work Order — retire "sink" terminology (appliance tokens + prose)

> **STATUS: DONE (Stages 1 + 2) — 2026-07-09.** Implemented + code-reviewed + suite-verified
> (583 pytest tests, unchanged baseline); acceptance grep clean — see the 2026-07-09 entry in
> `docs/CHANGELOG.md` for the exact carve-out allowlist. **Still open / gated:**
> the §4 live-migration on `dev45` — this work order stays on file (not archived/deleted) as the
> spec for that pending pass, per the Living-register-adjacent "mark DONE, don't delete" rule
> (`docs/CONVENTIONS.md` §3). This file itself is a permanent carve-out from the acceptance grep
> it defines, since it names the retired term by design.

> **For a subagent Pipeline session (`/dev`), not the design session.** Authority: user
> (2026-07-09). Route: Pipeline mode (`docs/WORKFLOW.md`) — code-writer → tester → code-reviewer →
> doc-writer → **gated** live-verifier. **Two stages** (§2, §3); run Stage 1 first (it has the live
> hazard), then Stage 2 (docs). Template: `docs/WORK_ORDER_resource_tags.md`.

## 1. Goal & context

The `sink`→`sensor` **code** rename already happened (`SINK_*`→`SENSOR_*`). This finishes the job:
the **appliance-internal tokens** (`o2/tcpdump-sensor/`) and the **pervasive prose**. After both
stages, zero "sink" outside CHANGELOG history (and the unrelated `wip/ai-scenario-sidecar` branch).
The appliance's internal verb is already **`capture`** (`capture-start`, `capture.service`); in the
O2 taxonomy it's the **tcpdump-sensor**.

## 2. Stage 1 — appliance tokens + code defaults (has the live-migration hazard)

**Token map** (rename the string *and* `git mv` the files whose names change):

| Token | → | Kind |
|---|---|---|
| `/etc/capture-sink/` (config dir) | `/etc/capture/` | path in scripts/units/docs |
| `sink.conf` / `sink.conf.example` | `capture.conf` / `capture.conf.example` | **`git mv`** the example |
| `CAPTURE_SINK_CONF` (env override) | `CAPTURE_CONF` | scripts + smoke test |
| `gre-sink` (capture iface) | **`gre-cap`** | **unify** with the in-scenario sensor's `gre-cap` |
| `sink-health-exporter.sh` | `capture-health-exporter.sh` | **`git mv`** |
| `axrange-sink-health-exporter.{service,timer}` | `axrange-capture-health-exporter.{service,timer}` | **`git mv`** |
| `capture-sink-health.json` (dashboard) | `capture-health.json` | **`git mv`** + provisioning ref |

**Files (grep-confirmed):** `o2/tcpdump-sensor/**` (`install.sh`, `uninstall.sh`, `bin/capture-*`,
`systemd/*`, `sink.conf.example`, `test/smoke.sh`, `test/README.md`, `udev/*`), `o2/exporters/`,
`o2/systemd/`, `o2/grafana/dashboards/` + `provisioning/datasources`, `o2/observability.conf.example`,
`o2/prometheus/prometheus.yml`, `o2/alloy/config.alloy`, `o2/compose/`, `o2/loki/`; **code defaults**:
`axrange/adapter_proxmox/config.py` (`sensor_capture_iface` default `"gre-sink"`→`"gre-cap"`),
plus any `gre-sink` string in `ir/schema.py` / `sensors/registry.py` / `config_exec/`; the appliance
docs `docs/tcpdump-sensor/README.md` + `o2/tcpdump-sensor/README.md`.

**`gre-sink`→`gre-cap` is coordinated:** the adapter's `sensor_capture_iface` default AND the
appliance's iface-creation both move; they must agree.

## 3. Stage 2 — prose sweep (context-sensitive; NOT a blind replace)

Like the sidecar sweep, drive by phrase, reading context:

| Prose | → |
|---|---|
| "the sink" / "capture sink" (the appliance) | "the **tcpdump-sensor**" / "the capture appliance" |
| "sink" as the receiver / "sink IP" / "sink transport IP" | "**sensor**" / "sensor (transport) IP" (code is already `SENSOR_*`) |
| "capture sink" as the *plane/role* | "**capture**" / "the capture plane" |

**Files:** all `docs/*.md` (heavy: `OP_CATALOG`, `ARCHITECTURE`, `DESIGN_BRIEF_{sensors,defense_overlays,dynamic_tap,production_topology,sdn_capture,o2,ui}`, `SLICE_*`, `PLANNER`, `ADAPTER`, `IR_SCHEMA`, `IMAGE_BUILD`, `OPEN_QUESTIONS`, `PRODUCT_GAPS`, `LESSONS`), `CLAUDE.md`, `target-environments/**` (scenario `tap_from`/comments), test fixtures.

**Version-locked specs:** the `sink` mentions in `OP_CATALOG`/`IR_SCHEMA`/`PLANNER`/`ADAPTER` are
**stale prose** (the code is already `sensor`) — aligning them is **not** a schema change, **no version
bump**. Keep the change prose-only; do not touch schema field names (already `sensor`).

## 4. Live-migration hazard (Stage 1, dev45)

Renaming `/etc/capture-sink/`→`/etc/capture/`, `gre-sink`→`gre-cap`, and the systemd unit names
**strands existing capture-appliance installs** — same shape as LES-044. The gated live-verifier must
**reinstall the appliance from the renamed scripts** (uninstall old → install new), or migrate (move
the config dir, re-create the iface, reload units). **Record a `LES-NNN`.**

## 5. Validation

- **Stage 1:** `o2/tcpdump-sensor/test/smoke.sh` passes with the renamed tokens; update unit tests
  touching tokens (`test_adapter_proxmox`, `test_teardown`, `test_dynamic_tap`, fixtures); full suite
  green; **gated live-verify on dev45** — reinstall the appliance under the new tokens, deploy a tapped
  scenario → capture on **`gre-cap`** → verify-capture → destroy.
- **Stage 2:** `python scripts/doc-stats.py --check` clean; **grep gate — zero `sink`** outside
  `docs/CHANGELOG.md` and `wip/ai-scenario-sidecar`.

## 6. Out of scope / leave

- **CHANGELOG history** — not rewritten.
- The `wip/ai-scenario-sidecar` **branch name** — an unrelated "sidecar", not "sink".
- Each stage lands its own CHANGELOG entry (`implementation (Claude Code)`).
