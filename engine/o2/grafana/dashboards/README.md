Provisioned Grafana dashboards for `o2/compose/`. Folder "axRange", `allowUiUpdates: true`.

## Kept — live telemetry

- `capture-health.json` (`axr-capture-health`) — Prometheus: `capture_up`, pcap age, disk %, drops.
- `suricata-alerts.json` (`axr-suricata-alerts`) — Loki: alerts over time, severity, top signatures.
- `attack-coverage.json` (`axr-attack-coverage`) — Loki: technique attempts vs. Suricata alerts.

Linked from the axRange Console's Telemetry area (`docs/design/16-unified-console.md` §2).

## Removed 2026-10-01

> Deprecated 2026-09-29 (`docs/design/16-unified-console.md`; `docs/CHANGELOG.md` 2026-09-29),
> deleted 2026-10-01 — Authority: user (approved the deletion list,
> `docs/reference/ui-consolidation/05-deletion-candidates.md`). No longer in this directory.

- `deploy-op-status.json` (`axr-deploy-op-status`) — superseded by the Console's Range area (deploy
  list) and Twin area (per-op status).
- `pcap-browser.json` (`axr-pcap-browser`) — was already a stub (`GET /api/pcaps` returns
  `implemented=false`); pcap download stays out of scope (`docs/design/16-unified-console.md` §10).
  No replacement yet.
- `control-console.json` (`axr-control-console`) — hosted the deprecated
  `axellio-axrange-control-panel` plugin (also removed); superseded by the Console app itself.
- `topology-eval/` — see `docs/reference/ui-consolidation/05-deletion-candidates.md` for its
  history; superseded by the Console's layout engine.
