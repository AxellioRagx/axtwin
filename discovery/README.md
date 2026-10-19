# aximprint

Learn a network's **imprint** — hosts, topology, services, endpoint behavior — from sensor-generated
metadata (`zeek`, `axquery` and `suricata` today; packetXpress later), persist it as a temporal
belief store, and **cast** a snapshot of it into deployable digital-twin configuration
(axrange `twin_config` + `tfc` traffic profile + `tap` plan).

aximprint is **not a sensor**. It is an independent module that can run within a sensor host:
sensors ingest pcap streams and emit metadata; a per-sensor **ingest plugin** normalizes that
metadata into aximprint's event model.

```
sensor metadata → [ingest plugin] → windowed features → learners → belief store
                                             snapshot as-of T → aximprint synth → twin YAML
```

## Status

Incubating. Schemas (event model, features, plugin contract, store shapes) are deliberately
**unversioned** until ratified — see `docs/CONVENTIONS.md` §1.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,acquire]"          # both extras — see the note below the block
pytest                                   # smoke test on synthetic zeek logs
aximprint ingest --plugin zeek --source /path/to/zeek/logs --store ./imprint.duckdb
aximprint status --store ./imprint.duckdb
aximprint synth  --store ./imprint.duckdb --out twin_draft.yaml

# feature files for model training — same ingest + windowing, no belief store
aximprint features --source /path/to/zeek/logs --out ./features   # --out is shorthand for --sink jsonl=./features

# either verb can add any other registered sink; --sink is repeatable
aximprint ingest --source /path/to/zeek/logs --store ./imprint.duckdb --sink evidence=./evidence.jsonl

# encode a second capture against the first's columns
aximprint features --source /path/to/more/logs --out ./features2 \
                   --vocabulary ./features/manifest.json
```

**`[acquire]` is optional at runtime and required to run the suite.** The discovery pipeline
genuinely installs and runs with no acquisition stack present — that is the point of keeping
`requests` out of the base dependencies (`pyproject.toml`, `docs/WORK_ORDER_merge_xtract_acquire.md`
D4) — but `pytest` collects `tests/acquire/` regardless, and without `requests` importable those
modules fail at import, not at a skip. Half the suite disappears behind eight collection errors.
Install both extras for any development environment; drop back to a bare `pip install -e .` only to
prove the no-acquisition-stack install still works, which is a deliberate check, not the default.

`features` writes the five per-window artifacts of the design brief §5.4 —
`nodes.jsonl` / `edges.jsonl` / `windows.jsonl` / `identity.jsonl` / `manifest.json`. A feature
archive is capture-derived and therefore sensitive: the sink drops a `.gitignore` into its own
output directory so it cannot be committed.

**The manifest is enough to encode from.** `manifest.json` carries a `categorical` block giving each
vocabulary-bearing field its index map — position in the list *is* the tensor column — along with
where that index came from (`observed` from this run, `pinned` from a supplied map, `closed` from
the code), what the weights mean, how much of the run's mass the map covered, and what fell outside
it. A consumer reads the manifest, builds its index map, then streams `nodes.jsonl`; no prior pass
over the row files is needed. `--vocabulary` points at a prior `manifest.json` (or a bare vocabulary
file) so a second capture encodes to the same columns; keys outside the pinned map are counted as
out-of-vocabulary, never appended or renumbered.

## Layout

- `src/aximprint/events/` — normalized event model (Flow/Identity/Service/Alert/SensorHealth)
- `src/aximprint/ingest/` — plugin base + registry; three sensors — `ingest/zeek/` (streaming logs
  or a Parquet archive), `ingest/axquery/` (an appliance query API), `ingest/suricata/`
  (`eve.json`) — plus `ingest/reorder.py`, the sensor-neutral time machinery below all three
- `src/aximprint/windowing/` — entity resolver, sketch bank, window manager
- `src/aximprint/sinks/` — the window-sink contract + registry; `store`/`evidence` sinks live here
  and register on import — `jsonl` (below) registers only once `features` is also imported
- `src/aximprint/provenance.py` — `build_stamp()`, the run's build fingerprint, shared by every sink
- `src/aximprint/features/` — the `jsonl` sink (windows → files) and `vocabulary.py` (the manifest's
  categorical index map, observed or pinned)
- `src/aximprint/learners/` — deterministic extractors (ML annotators later)
- `src/aximprint/store/` — append-only evidence log + DuckDB belief-store projection
- `src/aximprint/synth/` — snapshot → draft twin YAML (deterministic, provenance-tagged)
- `docs/` — design brief, conventions, workflow, changelog, open questions, lessons
- `scripts/` — verification tier + the Claude workflow scripts (solo / chat-bridge)
- `.claude/` — subagent pipeline: `/dev` orchestrator, five agents, harness config

## Verification

```bash
pytest                          # unit tier — synthetic Zeek fixtures
./scripts/verify_tier.sh        # cluster-free tier: + determinism invariants + CLI round-trip
                                #   (incl. a --vocabulary-pinned re-run)
python scripts/verify_determinism.py    # the invariants alone (brief §5.4 and §9)
```

The unit suite cannot catch a determinism break — a single run always agrees with itself — so
`verify_determinism.py` runs the whole pipeline twice and compares, byte for byte, the canonical
window features, the synthesized YAML, and the emitted feature archive (three checks; the archive
is where the sink path's own ordering can break, and wall-clock is confined to one named manifest
key so the check compares the differing key *set* rather than a skip list).

## Working with Claude Code

`docs/WORKFLOW.md` is the working agreement — three optional modes (Solo handoff, background
subagent Pipeline via `/dev <task>`, and Chat-bridge), imported from the axrange project family.
`CLAUDE.md` is the orientation anchor; `docs/LESSONS.md` holds the `LES-NNN` rules that get
auto-injected before edits to the files they scope.

See `docs/DESIGN_BRIEF_aximprint.md` for the design of record.
