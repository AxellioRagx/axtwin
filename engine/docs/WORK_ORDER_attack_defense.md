# Work Order — A3: attack/defense — substrate agents + O2 servers

> **STATUS: DONE (build) — 2026-07-11.** Implemented + code-reviewed + suite-verified (794 pytest
> tests). Stage A: `overlays.adversary`/`AdversaryEntry` + `OP-OVL-070 install-agent` +
> `scenario.default_agents` (**default ON — Authority: user, 2026-07-11**) + REF-220/230/240. Stage
> B: Caldera + Wazuh-manager docker-compose under `o2/compose/` + `o2/caldera/Dockerfile`, kept
> human-gated (not wired into `axrange o2 up`). **Still open:** the §4 gated `dev45` live-verify; no
> playout automation (manual, by design). See CHANGELOG 2026-07-11 and
> `docs/DESIGN_BRIEF_o2_attack_defense.md` for the built-status record.
>
> **§4 live-verify PASSED — 2026-07-15** (dev45, dedicated O2-services VM 102;
> `docs/WORK_ORDER_o2_server_stack.md` §5, which resolved the O2-stack bring-up failures — GAP-016
> Caldera 500 / GAP-017 Wazuh filebeat crash-loop / GAP-018 rootlessport orphan — all Resolved
> (live-verified)). Deployed a verify TE with `overlays.adversary` (Sandcat → Caldera) +
> `overlays.defense` (Wazuh-agent → manager). **Sandcat enrolled in Caldera** (paw `ahkojg`, host
> `agent1`, group `red`); **the Wazuh agent enrolled in the manager** (ID `001`, Active). **The
> Prometheus exporter→Prometheus-server half of the three agent→server pairs was descoped** — no
> mechanism auto-adds a TE host to `o2/prometheus/prometheus.yml`'s static scrape targets; tracked as
> new `docs/PRODUCT_GAPS.md` GAP-022 (Open, Low — a follow-on, not a blocker). See CHANGELOG
> 2026-07-15 "O2 server-stack live bring-up + A3/A4 gated live-verify: PASSED".

> **For a subagent Pipeline session (`/dev`).** Authority: user (2026-07-10). **Spec:**
> `docs/DESIGN_BRIEF_o2_attack_defense.md` (+ `DESIGN_BRIEF_{adversary,defense}_overlays.md`).
> Route: code-writer → tester → code-reviewer → doc-writer → **gated** live-verifier. **Large —
> decompose into the two stages below.** Stage B depends on **A1** (servers stood up as `o2_`).

## 1. Goal

Realize the A3 model: the TE substrate bakes in **default agents**; O2 holds the matching **servers**;
red/blue **playout stays manual** (no automation here). Three agent→server pairs (§3 of the brief):
Prometheus exporter→Prometheus server (exists); **Sandcat**(`overlays.adversary`)→**Caldera server**;
**Wazuh agent**(`overlays.defense`)→**Wazuh server**.

## 2. Stage A — substrate default agents (`te_`, engine deploy)

- **`overlays.defense`** — retained as blue **host** agents. Extend the existing `install-defense-agent`
  (`config_exec`, already ships `wazuh-agent`) to be a **default** on TE nodes; keep the AD-audit
  Windows branch. (Near-real — `soc-detection-lab` already runs it.)
- **`overlays.adversary`** — NEW: emit a **Sandcat** (Caldera agent) install on the declared nodes
  (config-class over QGA, like `install-defense-agent`), pointed at the O2 Caldera server IP.
  Realizes `DESIGN_BRIEF_adversary_overlays.md`'s declaration (execution = manual at the server).
- **Prometheus exporter** — a plain **default** node-exporter install on TE nodes.
- **Default-agent-set mechanism** — a small registry of "baked-in by default" agents + per-node
  opt-out/scoping (brief §9). Server IPs resolve from the O2 surface (A1) or adapter config.

## 3. Stage B — O2 servers (`o2_`, stood up by A1)

- Add **Caldera server** + **Wazuh server** as standing O2 surface components (docker-compose under
  `o2/`, like Grafana/Loki/Prometheus; Prometheus server already exists). A1's `o2 up` stands them up;
  the substrate agents report to them. **Manual playout** — no engine/o2control automation of attacks.

## 4. Validation

- Unit: `overlays.adversary` emits the Sandcat install; defense/exporter defaults emit; opt-out works.
  Full suite green.
- **Gated live-verify (dev45):** deploy a TE with `overlays.{defense,adversary}` → Wazuh + Sandcat +
  exporter present on nodes → (A1) O2 Wazuh/Caldera/Prometheus servers up → agents check in to their
  servers (Wazuh agent enrolled; Sandcat beacon visible in Caldera; exporter scraped). Run one
  **manual** Caldera ability + one `activity` attack → both visible; measurement is A4.

## 5. Out of scope / deps

No playout automation (manual). AI-driven red/blue is future. Depends on A1 (Stage B servers) + the
config_exec QGA channel (Stage A). Docs: mark the overlay briefs realized; CHANGELOG; LES.
