"""harvest — the pcap -> Zeek -> metadata-archive pipeline.

Distinct from the `xtract` primitives it sits on: `xtract` is a read-only
extraction client for one appliance; `harvest` is a long-running pipeline that
drives it, runs Zeek over the results, and materializes a metadata archive for
`aximprint` (see `docs/PLAN_metadata_harvester.md`).

The ownership boundary that shapes this package: it guarantees the *shape* of
what it produces — layout, typing, provenance, disorder accounting — and never
assigns *meaning* to a sensor field. Normalization into `aximprint`'s event
model happens in `aximprint`, not here.
"""
