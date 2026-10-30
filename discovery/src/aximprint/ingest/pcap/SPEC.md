# pcap ingest plugin

## What it is

A discovery source that takes a pcap (or a directory of pcaps), runs the
pinned Zeek over it into a temporary log directory, and reads that directory
with the existing `zeek` plugin. It serves the **discover** step of the loop
for the case where the operator has a capture file and no sensor: one
command, `aximprint ingest --plugin pcap --source pcap:/caps/site.pcap
--vantage …`, yields the same imprint the appliance path yields.

It is the deliberate exception to "aximprint is not a sensor": the plugin
runs a packet parser, but nothing from `ingest` downward sees a packet — the
parser is Zeek in a container and its output is sensor metadata on disk. The
plugin does not parse packets itself, and will not: the role learner needs a
sensor-reported acceptance state for every flow, and Windows roles need
`kerberos`, `ntlm`, `smb_mapping` and `dce_rpc` records, which only Zeek
produces with structure.

## Seam

`IngestPlugin`, registered as `pcap` in `ingest/__init__.py` beside the three
shipped plugins.

| Member | Value |
|---|---|
| `name` | `pcap` |
| `source_kind` | `path` |
| `source_help` | `pcap:<file or directory>[?image=<ref>&scripts=local\|bare&checksums=skip]` |
| `events(source, ts_from, ts_to)` | **raises** on any `ts_from`/`ts_to` — a pcap has no partition index; the operator slices with `editcap` or reads the `parquet:` archive a later `features` run writes |
| `probe(source)` | `{"supported": True, "files": n, "bytes": …, "runner": "podman"\|"docker"\|"zeek"\|None}`; `reachable: False` with the reason when no runner is available |
| `probe_vantage(source)` | `None` — nothing guesses; `--vantage` is required |
| `unparsed_stems` / `unrecognized_stems` | inherited from the `zeek` reader after the run |

Flow: resolve the locator → pick the runner → run `zeek -C -r <file> <script
set>` per file into one temporary directory (several files become a window
tree the `zeek` reader already understands) → delegate to the `zeek`
plugin's `events()` on that directory → remove the directory when the
iterator closes.

The runner (`ZeekRunner` with its podman implementation and the `FakeZeekRunner`
used by tests) moves from `acquire/harvest/zeek.py` to a module below both
`acquire` and `ingest` that imports nothing from `aximprint` — the
`vantage.py` precedent — so that `acquire` and this plugin share one copy of
the runner and of the stem tables, and the acquire layering test keeps
passing unchanged. The plugin takes its runner by constructor injection.

Plugin-specific options ride in the locator query string because
`config_schema` is closed (`source` plus the shared run options) and tests
assert that.

## Config

| Locator key | Default | Meaning |
|---|---|---|
| `image` | the pinned default image | Zeek image by tag or digest; `:latest` is refused by name |
| `scripts` | `local` | `local` (the full default log set, including `known_*`, `capture_loss`) or `bare` |
| `checksums` | `skip` | `-C`; `verify` drops the flag |

Runner choice is a probe, not a config: a native `zeek` binary on `PATH`
first, then podman, then docker; none → the plugin refuses with the reason.
No credentials. Temporary output lives under the run's own scratch root,
never in the checkout.

## Acceptance

- **Automated:** `tests/test_pcap_ingest.py` with the `FakeZeekRunner`
  (podman is absent in CI and the conftest forbids network): the plugin is
  listed with the closed schema (`test_ingest_plugins.py` tuple gains
  `pcap`); a one-file locator yields the same events as the `zeek` plugin on
  the fake runner's output directory; a directory locator yields a window
  tree; `ts_from`/`ts_to` raise; `probe_vantage` is `None`; the `:latest`
  image is refused; the temporary directory is gone after the iterator
  closes; `test_acquire_layering.py` and `test_acquire_zeek_stem_sync.py`
  pass with the hoisted module.
- **Live:** a real pcap through `aximprint ingest --plugin pcap`, then
  `bake` and `cast`, produces a twin whose Windows roles (domain controller,
  file server) match the appliance path's cast of the same capture.

## Notes

- Sign-off items: the hoist of the runner module; the "not a sensor"
  exception stated above.
- Suricata `-r` could be a second extractor later through the existing
  eve.json reader; it is not first because the Suricata reader has never
  been checked against a real capture and derives acceptance from counters.
- Python packet libraries were considered and rejected: none yields a
  sensor-reported connection state or the Windows protocol logs, and the
  project has no precedent for any of them.
