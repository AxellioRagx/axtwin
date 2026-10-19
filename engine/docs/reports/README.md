# docs/reports/

Self-contained HTML deliverables from **real clusters** — measured runs and environment snapshots. Each one is
a snapshot of what was observed on a given day, not a living document: they are
dated, and they are never edited to match later reality. If a later run
contradicts an earlier one, that is the interesting part — add the new report
and let the pair stand.

Open them in a browser; each is a single file with no external assets (the
charts are inline SVG, so they render offline and survive being emailed).

## What is here

| Report | Date | Cluster | What it measures |
|---|---|---|---|
| `2026-08-13-capture-rate-corp-enterprise.html` | 2026-08-13 | dev45 | First capture-plane rate characterisation: six load rungs from a live `corp-enterprise` twin through per-tap `tc` → keyed gretap → the external `XPress-SW-A` packetXpress sink, with per-stage drop accounting. Includes the sink-multiqueue fix applied mid-run. |
| `2026-08-14-capture-rate-post-fixes.html` | 2026-08-14 | dev45 | The same ramp re-measured after the three capture-plane fixes landed, on identical 120 s windows. |
| `2026-08-14-corp-enterprise-topology.html` | 2026-08-14 | dev45 | Topology diagram of the standing `corp-enterprise` twin + the O2 surface — four VLANs behind the `gw` router, the capture plane converging on `XPress-SW-A`, and the O2 nodes. Hand-SVG; regenerate with `…-topology.gen.py`. |
| `2026-08-14-corp-enterprise-config-topology.html` | 2026-08-14 | — (from YAML) | The same twin as a **blueprint** — drawn from `twin-configs/corp-enterprise/corp-enterprise.yaml` alone (default `N_WORKSTATIONS=25`, images/apps, and the identity + data overlays). No tap/O2 plane: a twin config authors no capture/traffic/attack intent. The config-vs-deployed pair. |

## Reading them together

The 2026-08-13 report is where the numbers were *discovered*, so it carries the
investigation: what was tried, what the bottleneck turned out to be at each
stage, and the two fixes applied while the run was in progress (sink NIC
multiqueue, and the gretap qdisc — then still applied by hand).

The 2026-08-14 report is the clean re-measurement with everything in place and
the qdisc maintained by the adapter itself. Its numbers supersede the earlier
ones **as a description of the current system**; they do not invalidate the
earlier run, which correctly describes the system as it was.

Two methodology points carried forward into both, learned the hard way:

- **Measure over long windows.** A rung passed two 25 s windows and a 90 s
  window, then lost 5,965 packets over 120 s. Marginal rates look clean when
  sampled briefly, so every rung in the 2026-08-14 report uses the same 120 s
  window.
- **Counters read over separate SSH round-trips cannot share one interval.**
  Stamping a whole sample once and dividing every stage by that `dt` inflated
  the mirror stage ~4.5× when one read stalled — it reported 588k pps against
  a gretap that had carried 138k. The harness now timestamps each host read
  separately.

## Reproducing

The harness and the exact tier specs behind both reports live in
`twin-configs/corp-enterprise-maxrate/` (`measure.py`, `genplot.py`, the twin
config's expanded IR, the tap plans, the traffic profiles, and the two
gitignored lab facts that otherwise would not travel). See that folder's
README for the deploy sequence.
