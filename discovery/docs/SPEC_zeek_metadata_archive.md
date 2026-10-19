# SPEC — Zeek metadata archive (an optional internal read-cache)

**STATUS:** INCUBATING, like every other shape here (`LES-001`). No version field; see
"Versioning" at the end for why, and what would change that.
**Authority:** design + implementation (Claude Code), 2026-08-05, from the pcap/Zeek separation
decision recorded in CHANGELOG.

> **DEMOTED 2026-08-10 (user ruling; CHANGELOG same date, DESIGN_BRIEF §0.1).** This was written as
> an **interchange contract** with a converter maintained outside the repo, and its whole defensive
> posture follows from that. The producer is merging in, and there is **no archive seam** — so this
> is now the format note for an **optional, regenerable internal cache**, not a contract with anyone.
> Two consequences to read the rest of this document through:
>
> - **The cold tier is now a first-class input, not only a rebuild source.** As of CHANGELOG
>   2026-08-10 ("multi-window Zeek log-directory read"), aximprint reads the per-window Zeek log
>   tree `harvest` writes, directly. The "aximprint reads only the hot tier" claim below is
>   superseded.
> - **Everything here is still implemented and still gated** — the reader, the six checks, the
>   transport-equivalence step in `verify_pipeline_gates.sh`. Nothing is removed. Its measured value
>   is selectivity (9.9x on a 10 % time slice) and storage (5.8x smaller), never throughput (full
>   scan measured 1.02x — no speedup, `LES-019`). Build it when a slow re-read actually hurts.
>
> Validating your own output as strictly as a stranger's is now belt-and-braces rather than
> necessity. That is not a reason to relax it — see "Fail loud, fail closed" — but it *is* the
> reason `Q15` (versioning this contract) dissolved rather than being answered.

## What this is

pcap extraction and Zeek run **once** and are expensive; aximprint re-reads the metadata they
produce many times as extractors, resolvers, and features are iterated on. This document describes
the columnar cache that can sit between those two halves.

Two tiers, mirroring DESIGN_BRIEF §7's own split one layer earlier:

| Tier | What | Regenerable |
|---|---|---|
| **cold** | Zeek's native output, gzipped, immutable — **readable directly** | no — only by re-running Zeek over pcaps |
| **hot** | this archive: Parquet + manifest | **yes**, from cold, in minutes |

The cold tier is the source of truth and a working input on its own. The hot tier is a derived index
over it — rebuildable whenever this spec improves, without ever touching a pcap again.

## Non-goals

- Not a live/streaming transport. `Q2` settled that live tail is a plugin detail; this archive is
  the batch-replay path and is what determinism is verified on.
- Not a belief store, and not a feature archive. This is normalized *sensor metadata*, upstream of
  both.
- Not a place to enrich. The converter transcodes; it does not add fields Zeek did not emit.
  Enriching what flows downstream is an event-model change (brief §3).

## Layout

```
<archive-root>/
  manifest.json
  parquet/
    log=conn/date=2026-08-01/part-000.parquet
    log=conn/date=2026-08-02/part-000.parquet
    log=dns/date=2026-08-01/part-000.parquet
    ...
```

Hive-style `log=` / `date=` partitioning, read by DuckDB with `hive_partitioning=true`.

**Partition on the row's own `ts`, never on the source pcap's filename.** Zeek keys a `conn.log` row
on connection *start* but writes it at *close*, so rows routinely belong to an earlier period than
the file containing them. Partitioning by filename would scatter a window's rows across partitions
and silently defeat pruning.

**Day granularity, not hour.** At the target scale (~100x the reference capture: ~296 days, ~150 M
`conn` rows) day partitions give ~296 files per log type at roughly 10 MB each. Hour partitions
would give ~7,100 files per log type, most of them too small to be worth a row group. Sub-day
pruning comes from row-group min/max statistics on `ts`, which is what makes the sort below
mandatory rather than cosmetic.

## Schema rules

### `ts` is DOUBLE. Never TIMESTAMP.

Zeek's `ts` is epoch seconds as a double. DuckDB's `TIMESTAMP` is microsecond-quantized, so a
`DOUBLE -> TIMESTAMP -> DOUBLE` round trip is lossy and can move an event across a window boundary —
changing which window's features it lands in. `LES-013`: a derived order never repairs a float.

The `date` partition column is derived from `ts` **for pruning only**. It is never the source of
truth for windowing; `windowing/` reads `ts` and nothing else.

### Unset is null, not `-`

Zeek's ASCII writer marks unset with `-` (or whatever `#unset_field` declares). The archive uses
**real Parquet nulls**.

This is a correctness upgrade, not just a tidiness one. The unset-vs-zero distinction (`LES-007`) is
what keeps an unset counter from publishing as an observed `0`, and today it rides on string-matching
a per-file marker set. Parquet nulls carry it natively.

The reader tolerates both: `_s`/`_num` test `v is None` explicitly and `_f`/`_i` catch `TypeError`
from `float(None)`, so a converter that emits `-` as a literal string still parses correctly. Nulls
are required anyway, because only nulls survive a typed column.

### Columns

Per log type, the archive must carry every field aximprint's parser reads. A **superset is fine** —
extra columns are ignored, and carrying Zeek's full record is encouraged so the archive outlives the
current parser table. A **missing column is fatal** for the required set below.

| log | required | read if present |
|---|---|---|
| `conn` | `ts`, `id.orig_h`, `id.resp_h`, `id.orig_p`, `id.resp_p`, `proto`, `conn_state` | `service`, `duration`, `orig_bytes`, `resp_bytes`, `orig_pkts`, `resp_pkts`, `local_orig`, `local_resp` |
| `dhcp` | `ts`, `mac` | `assigned_addr`, `requested_addr`, `host_name`, `lease_time` |
| `dns` | `ts`, `query` | `answers`, `TTLs` |
| `kerberos` | `ts`, `client`, `id.orig_h` | — |
| `known_hosts` | `ts`, `host` | — |
| `ntlm` | `ts`, `id.resp_h` | `server_dns_computer_name`, `server_nb_computer_name` |
| `radius` | `ts`, `framed_addr` | `mac`, `username`, `tunnel_client`, `result` |
| `rdp` | `ts`, `id.resp_h` | `id.resp_p`, `cookie`, `result`, `security_protocol`, `keyboard_layout`, `client_build`, `client_name`, `client_dig_product_id`, `desktop_width`, `desktop_height`, `requested_color_depth`, `cert_type`, `cert_count`, `cert_permanent`, `encryption_level`, `encryption_method` |
| `rfb` | `ts`, `id.resp_h` | `id.resp_p`, `client_major_version`, `client_minor_version`, `server_major_version`, `server_minor_version`, `authentication_method`, `auth`, `share_flag`, `desktop_name`, `width`, `height` |
| `redis` | `ts`, `id.resp_h` | `id.resp_p`, `cmd.name`, `cmd.key`, `cmd.value`, `success`, `reply.value` |
| `websocket` | `ts`, `id.resp_h` | `id.resp_p`, `host`, `uri`, `user_agent`, `subprotocol`, `client_protocols`, `server_extensions`, `client_extensions` |
| `syslog` | `ts`, `id.resp_h` | `id.resp_p`, `proto`, `facility`, `severity`, `message` |
| `mqtt_connect` | `ts`, `id.resp_h` | `id.resp_p`, `proto_name`, `proto_version`, `client_id`, `connect_status`, `will_topic`, `will_payload` |
| `known_certs` | `ts`, `host` | `port_num`, `subject`, `issuer_subject`, `serial` |
| `known_services` | `ts`, `host` | `port_num`, `port_proto`, `service` |
| `ssl` | `ts`, `id.resp_h` | `id.resp_p`, `server_name`, `version`, `ja3`, `ja3s`, `subject`, `issuer` |
| `dce_rpc` | `ts`, `id.resp_h` | `id.resp_p`, `endpoint`, `operation`, `named_pipe` |
| `ldap_search` | `ts`, `id.resp_h` | `id.resp_p`, `scope`, `result`, `result_count`, `base_object`, `filter`, `attributes`, `diagnostic_message` |
| `ldap` | `ts`, `id.resp_h` | `id.resp_p`, `message_id`, `version`, `opcode`, `result`, `diagnostic_message`, `object`, `argument` |
| `smb_mapping` | `ts`, `id.resp_h` | `id.resp_p`, `share_type`, `service`, `path`, `native_file_system` |
| `smb_files` | `ts`, `id.resp_h` | `id.resp_p`, `action`, `path`, `name` |
| `software` | `ts`, `host` | `host_p`, `software_type`, `name`, `version.major`, `version.minor`, `version.minor2`, `version.minor3`, `version.addl`, `unparsed_version` |
| `ntp` | `ts`, `id.resp_h` | `id.resp_p`, `version`, `mode`, `stratum`, `poll` |
| `http` | `ts`, `id.resp_h` | `id.resp_p`, `trans_depth`, `method`, `host`, `uri`, `referrer`, `version`, `user_agent`, `origin`, `request_body_len`, `response_body_len`, `status_code`, `status_msg`, `info_code`, `info_msg`, `tags`, `username`, `password`, `proxied`, `orig_fuids`, `orig_filenames`, `orig_mime_types`, `resp_fuids`, `resp_filenames`, `resp_mime_types` |
| `ssh` | `ts`, `id.resp_h` | `id.resp_p`, `version`, `auth_success`, `auth_attempts`, `direction`, `client`, `server`, `cipher_alg`, `mac_alg`, `compression_alg`, `kex_alg`, `host_key_alg`, `host_key`, `remote_location.country_code`, `remote_location.region`, `remote_location.city`, `remote_location.latitude`, `remote_location.longitude` |
| `quic` | `ts`, `id.resp_h` | `id.resp_p`, `version`, `client_initial_dcid`, `client_scid`, `server_scid`, `server_name`, `client_protocol`, `history` |
| `snmp` | `ts`, `id.resp_h` | `id.resp_p`, `version`, `community`, `get_requests`, `get_bulk_requests`, `get_responses`, `set_requests`, `display_string`, `up_since`, `duration` |
| `tunnel` | `ts`, `id.resp_h` | `id.resp_p`, `tunnel_type`, `action` |
| `smtp` | `ts`, `id.resp_h` | `id.resp_p`, `trans_depth`, `helo`, `mailfrom`, `rcptto`, `date`, `from`, `to`, `cc`, `reply_to`, `msg_id`, `in_reply_to`, `subject`, `x_originating_ip`, `first_received`, `second_received`, `last_reply`, `path`, `user_agent`, `tls`, `fuids`, `is_webmail` |
| `sip` | `ts`, `id.resp_h` | `id.resp_p`, `trans_depth`, `method`, `uri`, `date`, `request_from`, `request_to`, `response_from`, `response_to`, `reply_to`, `call_id`, `seq`, `subject`, `request_path`, `response_path`, `user_agent`, `status_code`, `status_msg`, `warning`, `request_body_len`, `response_body_len`, `content_type` |
| `x509` | `ts` | `fingerprint`, `certificate.version`, `certificate.serial`, `certificate.subject`, `certificate.issuer`, `certificate.not_valid_before`, `certificate.not_valid_after`, `certificate.key_alg`, `certificate.sig_alg`, `certificate.key_type`, `certificate.key_length`, `certificate.exponent`, `certificate.curve`, `san.dns`, `san.uri`, `san.email`, `san.ip`, `basic_constraints.ca`, `basic_constraints.path_len`, `host_cert`, `client_cert` |
| `ocsp` | `ts`, `thisUpdate`, `nextUpdate` | — |
| `notice` | `ts` | `id.orig_h`, `id.resp_h`, `src`, `dst`, `note`, `msg`, `name` |
| `weird` | `ts` | `id.orig_h`, `id.resp_h`, `src`, `dst`, `name`, `msg` |
| `capture_loss` | `ts`, `percent_lost` | `peer` |
| `stats` | `ts` | `peer`, `pkts_dropped`, `pkt_lag`, `pkts_proc`, `bytes_recv`, `mem`, `pkts_link`, `pkts_filtered`, `events_proc`, `events_queued`, `active_tcp_conns`, `active_udp_conns`, `active_icmp_conns`, `tcp_conns`, `udp_conns`, `icmp_conns`, `timers`, `active_timers`, `files`, `active_files`, `dns_requests`, `active_dns_requests`, `reassem_tcp_size`, `reassem_file_size`, `reassem_frag_size`, `reassem_unknown_size` |

"Read if present" is genuinely optional: a missing `orig_bytes` means every non-`S0`/`REJ` flow is
marked `counters_imputed`, which is the honest outcome rather than a failure. A missing **required**
column would instead produce structurally complete-looking events with empty endpoints, which is
exactly the silent corruption "fail loud, fail closed" exists to prevent.

`answers` and `TTLs` may be either a Parquet `LIST` or a comma-joined `VARCHAR`; the `dns` parser
already accepts both.

A converter is free to choose any DuckDB type for a column that reaches a parser only through
`_attrs()` (`ingest/zeek/plugin.py`) — as long as that type is **VARCHAR**. `_attrs()` stores
every attribute as a string, unconditionally, and never re-parses it numerically; a column typed
DOUBLE/BIGINT instead is a conforming archive that silently breaks transport equivalence, because
DuckDB hands back a native Python value whose `str()` does not reproduce Zeek's own on-the-wire
text — an `interval` field written as `"64.000000"` on the TSV transport becomes
`str(64.0) == "64.0"` once typed DOUBLE, and the two transports disagree on the same field's
value even though nothing was lost. (A `list`/`set`-typed `_attrs()` column, e.g. a Parquet
`LIST`, is safe by contrast: the reader joins it on `,` before anything sees it, matching Zeek's
own TSV convention for a vector/set field — see `answers`/`TTLs` above.)

**The reader must pass `hive_partitioning=false` on every `read_parquet` call.** This archive's
own layout ("Layout" above) partitions on `log=<stem>/date=<D>`, and DuckDB's DEFAULT
hive-partitioning inference treats those two path components as reserved, synthesized column
names — verified empirically: `read_parquet()` over a single literal partition file with the
default left on (no glob, no explicit flag) returns the partition's own calendar date for a
column named `date`, silently discarding whatever the file's own `date` column actually held,
with no error raised. `smtp.log` and `sip.log` each carry a `date` field (the message's
self-reported `Date:` header) that collides with this on the archive transport — found when a
first attempt to fix it omitted `date` from `_smtp`/`_sip`'s `_COLUMNS`/`_attrs()` entirely
(discussed and reversed, code review `S1`, `docs/WORK_ORDER_zeek_stem_coverage.md` step 3): that
was a workaround at the wrong layer, forfeiting a real field permanently instead of fixing the
transport. `ParquetArchiveSource._read` now passes `hive_partitioning=false` explicitly on its
`read_parquet` call — exact for what this reader actually needs (every partition column it cares
about, `log` and `date`, is already read from the DIRECTORY structure `ParquetArchiveSource`
walks, never from a row) and free (no columns are pruned differently), and it immunizes every
column name, present and future, rather than requiring every stem's `_COLUMNS` to individually
avoid two reserved names forever. The failure this avoids is worse than the numeric-typing one
two paragraphs up: that one changes a value's *representation*; this one silently substitutes an
unrelated value with no relationship to the field's own meaning, and nothing about the request
raises to say so — so a producer implementing this SPEC independently must set the flag too, not
merely avoid naming a column `date`/`log`.

### Provenance columns

Every row carries:

| column | type | meaning |
|---|---|---|
| `_pcap` | VARCHAR | source pcap basename |
| `_zeek_run` | VARCHAR | converter's id for the Zeek invocation that produced the row |
| `_row_no` | BIGINT | the row's ordinal in its Zeek log **in original file order** |
| `_displacement_seconds` | DOUBLE, nullable | see below |

Dictionary encoding makes `_pcap` and `_zeek_run` effectively free. Without them, pcap-boundary and
dedup analysis is impossible after the fact, and re-derivation loses its audit trail.

**`_row_no` is what makes the read order total.** Sorting on `ts` alone leaves equal timestamps to
resolve however the reader happened to produce them, and a tie broken differently across runs
renumbers a published column (`LES-009`, `Q14`).

**What the reader actually does, and what that requires of a partition's own sort order.** The
reader does **not** issue `ORDER BY ts, _zeek_run, _row_no` — it has not since the redundant-sort
finding below, and less so now that partitions are merged rather than read one at a time
(`docs/WORK_ORDER_multi_window_log_read.md`). `ParquetArchiveSource._read()` opens every partition
file's own cursor **simultaneously** and merges them with `heapq.merge`, keyed on
`_archive_row_key = (ts, _zeek_run, _row_no)`. That key does **not** obligate the converter to write
rows in `(ts, _zeek_run, _row_no)` order — only `sorted_by: "ts"` is required, and only that is
checked at open (`load_manifest`). `heapq.merge` preserves each partition's own rows in that
partition's absolute order; the key only ever decides how *different* partitions interleave with
each other when their `ts` values collide, so a partition sorted purely on `ts` can never be
internally re-ordered by a richer key, no matter what else the key compares. `_zeek_run`/`_row_no`
earn their place narrowly: as the deterministic tie-break for a `ts` collision **across** files,
matching this section's own stated total order — not as a claim about how any one file was written.
This is exactly the tie-break the text path already uses one layer down (`reorder_by_key` keys its
heap on `(ts, seq)`, where `seq` is the row's position within its own file).

The one thing this DOES require of the merge: `ts`, `_zeek_run`, and `_row_no` must be typed the way
this section declares (`ts` DOUBLE, `_zeek_run` VARCHAR, `_row_no` any integral type) wherever
declared — a wrongly-typed `ts` in particular does not raise, it makes every row compare as `-inf`
and silently degrades the merge back into path-order concatenation, which is the exact failure this
merge exists to refuse (see "The disorder contract" below, and `load_manifest`'s own type check).

## The disorder contract — the part that is easy to get wrong

Rows **must be sorted by `ts`** within each partition file, because row-group pruning is what makes
time-range slicing work, and slicing is the primary access pattern at this scale.

But sorting destroys a signal. Zeek logs are *not* ts-sorted — worst displacement measured on the
reference capture is **294.8 s** — and aximprint's bounded reorder buffer exists precisely to repair
that and to **report what it could not repair** as
`SensorHealthEvent(kind="ingest_unordered")`, which reaches the window health record.

If the converter silently sorts, that event never fires, and every window health record reads clean
while the underlying sensor data was disordered. That is `LES-006`'s "no evidence" masquerading as
"evidence of none", and `LES-013`'s "sorting *derives* an order; it never repairs one you were
given."

**So the disorder must be measured before the sort and carried in the data.**

`_displacement_seconds` is defined as, for each row, in the **original file order** within its
`_zeek_run`:

```
running_max_ts  = MAX(ts) over all rows at or before this one, in file order
displacement    = running_max_ts - ts,  when positive
                  NULL,                 when the row was already in order
```

The reader re-emits `ingest_unordered` for rows whose recorded displacement exceeds its own reorder
horizon, and repairs nothing else — matching what the bounded buffer would have done on the text
path. On the reference capture nothing exceeds the 600 s horizon, so the column is entirely null and
costs nothing under Parquet encoding.

Note the definition is against the raw running max in file order, whereas the text path's buffer
compares against the last *emitted* ts. The two agree except in edge cases where the buffer's own
reordering shifts the comparison point. That divergence is bounded by the horizon, and the
equivalence test below is what keeps it honest.

**An archive that declares no disorder statistics is rejected, not read as clean.**

## `manifest.json`

Read first, written last, and only on a clean conversion — the same discipline as
`features/jsonl.py`'s manifest, for the same reason: a manifest present in a half-written archive is
worse than no archive at all.

```jsonc
{
  "capture_node": "<sensor id>",
  "producer":     { "tool": "...", "version": "...", "zeek_versions": ["7.0.1"] },
  "wallclock":    { "converted_at": "..." },     // the ONE non-reproducible key
  "time_bounds":  { "min_ts": 1754006400.0, "max_ts": 1754261999.9 },
  "pcaps":        { "count": 296, "spans": [ { "name": "...", "min_ts": .., "max_ts": .. } ] },
  "boundary":     { "policy": "time-aligned pcaps; split flows accepted",
                    "flows_within_1s_of_boundary": 0 },
  "dedup":        { "policy": "none | exact-row", "rows_removed": 0 },
  "logs": {
    "conn": {
      "rows": 150000000,
      "files": 296,
      "columns": { "ts": "DOUBLE", "id.orig_h": "VARCHAR", ... },
      "sorted_by": "ts",
      "disorder": { "measured": true, "max_displacement_seconds": 294.8,
                    "rows_displaced": 12841, "rows_beyond_600s": 0 }
    }
  }
}
```

`wallclock` is the single key holding a value that cannot reproduce across runs, isolated exactly as
`features/jsonl.py::WALLCLOCK_FIELD` isolates its own. Everything else in the manifest, and every
byte of every Parquet file, must be reproducible from the same cold tier.

**aximprint reads only what the manifest declares.** A Parquet file present on disk but absent from
`logs` is an error, not a bonus — the "an unrecognized shape is rejected, not passed through" rule
applied to an input we do not control. A declared log type whose files are missing is likewise an
error.

**Known gap: a present-but-unconverted stem is invisible to this manifest.** The reference converter
above (and `scripts/make_zeek_archive.py`, this repo's own) only ever writes a `logs` entry for a stem
it converts — a stem the reader declines to parse (`ingest/zeek/plugin.py`'s `_SKIP`) or has never
heard of at all has no partition and no manifest entry, so this archive cannot audit what it is
missing the way a filesystem-walking transport can. `ZeekSource.inventory_is_complete` is `False` for
this transport specifically because of this, which keeps `unparsed_log_types`/`unrecognized_log_types`
honestly `None` (unaudited) rather than a false `{}` (audited, clean) — see `docs/OPEN_QUESTIONS.md`
`Q30`, which tracks whether the converter should ever record a declined stem without converting its
content.

## What aximprint validates on open

1. `manifest.json` exists, parses, and declares `logs`.
2. Every declared log type is either in the parser table or named in the skip table; an unknown one
   is **reported** (matching how the text path reports an unrecognized log stem).
3. Every declared log type carries its **required** columns; a missing one names the log type and
   the column and aborts.
4. Every declared log type carries `disorder.measured == true`.
5. Row counts read reconcile against `logs.<type>.rows`.
6. A log type that yields **0 events from >0 rows** is reported loudly — the generic tripwire for a
   schema drift that the column check did not anticipate.

## Reference conversion (illustrative)

The converter is external; this is here so the semantics are unambiguous. It parses Zeek's
`#fields` / `#types` header to build the column spec, then lets DuckDB do the bulk read in C++ —
never a Python row loop.

```sql
-- 1. read one Zeek run's log, typed from its own header
CREATE OR REPLACE TABLE raw AS
SELECT *, '<pcap>' AS _pcap, '<run-id>' AS _zeek_run,
       row_number() OVER () AS _row_no
FROM read_csv('<run>/conn.log', delim='\t', header=false, comment='#',
              nullstr='-', columns={'ts':'DOUBLE', 'id.orig_h':'VARCHAR', ...});

-- 2. measure disorder in ORIGINAL file order, before any sort
CREATE OR REPLACE TABLE stamped AS
SELECT * EXCLUDE (_row_no, running_max),
       CASE WHEN running_max > ts THEN running_max - ts END AS _displacement_seconds
FROM (SELECT *, MAX(ts) OVER (PARTITION BY _zeek_run ORDER BY _row_no
                              ROWS UNBOUNDED PRECEDING) AS running_max
      FROM raw);

-- 3. partition on the row's own ts, sorted within each file
COPY (SELECT *, strftime(to_timestamp(ts), '%Y-%m-%d') AS date, 'conn' AS log
      FROM stamped ORDER BY ts)
TO '<archive-root>/parquet' (FORMAT PARQUET, PARTITION_BY (log, date), OVERWRITE_OR_IGNORE);
```

`to_timestamp` here derives a partition label only; the stored `ts` stays DOUBLE and untouched.

## Privacy

The archive is capture-derived and inherits the capture's sensitivity in full (`LES-003`,
`LES-011`). It is **more** identity-dense than the feature archive, since it carries raw Zeek
records: hostnames, principals, cert subjects, SMB paths, DNS queries.

- gitignored, exactly as `metadata/` is.
- Never quoted in docs, logs, commits, or test fixtures. Tests use synthetic data only.
- Shapes, counts, and distributions may be reported; values may not.

## Versioning

This spec carries **no version field**, per `LES-001` and CLAUDE.md §"Design authority": schemas here
are incubating, and ratifying one is the user's call, not a habit to slide into.

It is worth stating plainly that this is the first artifact in the project whose **producer lives
outside the repo** and can therefore drift independently of it — which is the precise condition
versioning exists to manage. The current answer is that read-time validation (the six checks above)
detects drift at the boundary and fails loud, which covers the near term. If the converter is ever
maintained by someone who does not read this file, that answer stops being sufficient and the
question should be re-opened deliberately.
