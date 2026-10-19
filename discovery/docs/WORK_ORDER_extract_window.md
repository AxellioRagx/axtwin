# Work Order — WO-5: `extract` — one window to one file

> **Genre:** work order (see `docs/CONVENTIONS.md` §3) — an execution ticket for the `/dev` Pipeline. Concrete files, concrete gates, time-bounded. Mark it **DONE** in place when its run lands; don't delete it.
>
> **Status:** ✅ DONE (CHANGELOG 2026-08-04 — "`xtract` iteration-1 build complete")
> **Authority for the decisions behind it:** CHANGELOG 2026-08-04 — `xtract` design ratified; iteration-1 scope and work orders

## 1. Goal

`xtract/extract.py` performs exactly one extraction: given a window, a stream spec, an optional
filter and an output directory, it issues `GET /axapi/pcap`, streams the body to
`<out>/<startTs>.pcap`, and returns a typed outcome — written, empty, or failed. It never loops.
After this lands, the batch runner is a loop around a function that is already correct.

**Not a goal:** the batch loop, resume, retries across windows, or the manifest. Those are WO-6.
Single-window retry of transient transport errors belongs to `client.py` (WO-2), not here.

## 2. Build — phases, in order

### Phase 1 — The request

- **Files:** `xtract/extract.py` (new), `tests/test_extract.py` (new)
- **Behavior:**
  - `extract_window(client, window, streams, filter, out_dir, ctid) -> Outcome`.
  - Issues `GET /axapi/pcap` with `params=` carrying `ctid`, `start_time`, `end_time`,
    `stream_ids` (**the caller's verbatim spec string**) and `filter` when non-empty.
  - Omits `filter` and `stream_ids` entirely when not supplied rather than sending empty strings —
    the API documents a `stream_ids` default of `"0-11"`, and an empty string is not obviously the
    same thing as absent.
  - Captures the `X-Task-GUID` response header into the outcome, so a run is traceable to appliance
    tasks after the fact.
  - `ctid` is supplied by the caller and is unique per window, so `GET /axapi/task/<ctid>` resolves.
- **Tests:** parameter set exactly as specified; a filter with spaces/parens/quotes survives
  encoding; absent filter/streams omit the keys; `X-Task-GUID` captured; verbatim spec passthrough
  (`"2-4"` is sent as `"2-4"`, never `"2,3,4"`).

### Phase 2 — Streaming to disk, atomically

- **Files:** `xtract/extract.py`, `tests/test_extract.py`
- **Behavior:**
  - The body streams in chunks to **`<out>/<startTs>.pcap.part`**, then `os.rename()` to
    `<out>/<startTs>.pcap` on success. A killed or failed run must never leave a truncated file at
    the final name — the filename is the resume key, so a partial file at that name would be
    silently treated as a completed window forever.
  - `<startTs>` is the **exact string sent as `start_time`**. Not reformatted, not zero-padded, not
    normalized. Filename and wire value are the same text.
  - The response body is never materialized in memory.
  - `.part` files are cleaned up on failure. A pre-existing `.part` from a prior crash is
    overwritten, not resumed — HTTP range resumption against an extract endpoint is not a documented
    capability and must not be assumed.
  - `fsync` the file before rename so the rename is not ordered ahead of the data.
- **Tests:** a completed extract lands at the final name and the `.part` is gone; a mid-stream
  exception leaves **no** file at the final name; the written bytes equal the served bytes; a
  pre-existing `.part` doesn't corrupt the result; filename equals the `start_time` string exactly.

### Phase 3 — Outcomes

- **Files:** `xtract/extract.py`, `tests/test_extract.py`
- **Behavior:**
  - Typed outcome, not a bool: `Written(path, bytes, task_guid)`, `Empty(reason)` for **418**, and
    `Failed(status, detail)` for everything else.
  - **418 produces no file and is not an error.** On a sparse capture most windows may legitimately
    return 418; a design that treats it as failure aborts the common case.
  - `401` propagates to the caller — the client has already tried refresh and re-login (WO-2), so
    reaching here means the run genuinely cannot continue.
  - Zero-byte 200 responses are treated as `Empty`, not `Written`. A zero-byte pcap is not a valid
    capture file and must not satisfy resume on a later run.
- **Tests:** each status → its outcome type; 418 writes nothing and leaves no `.part`; a zero-byte
  200 is `Empty`; `Failed` carries enough detail to diagnose without re-running.

### Phase 4 — Interrupt handling

- **Files:** `xtract/extract.py`, `tests/test_extract.py`
- **Behavior:** on `KeyboardInterrupt`/SIGINT during a transfer, issue
  `DELETE /axapi/task/<ctid>` to abort the extract server-side, clean up the `.part`, and re-raise.
  Orphaning a running extract on a capture appliance because a laptop lid closed is a real cost, not
  a theoretical one.
- **Tests:** interrupt mid-stream triggers the abort call and the `.part` cleanup, and the exception
  still propagates.

## 3. Boundaries — explicitly OUT of scope

- No looping over windows, no resume, no manifest, no progress reporting across windows — WO-6.
- No `archive=` / `servers=` parameters. Deferred by decision.
- No async transport (`POST /axapi/extract` → poll → `/output`). Keep the seam clean enough that
  adding it later is an envelope change, but do not build it.
- No inspection of the returned pcap beyond its byte count. We do not parse capture contents.
- Anything that would trip the risk gate — surface it as `DECISION NEEDED:` instead of doing it.

## 4. Validation

1. **Unit:** `pytest tests/test_extract.py` green, HTTP mocked, `tmp_path` for output. No sockets.
2. **Hermetic tier:** `scripts/verify_tier.sh` clean
3. **Live (gated):** one ~1-second extract against CorpAPCN produces a file whose name is the
   `start_time` string, which opens cleanly in a pcap reader, with a `X-Task-GUID` recorded.
4. **Docs:** CHANGELOG entry (Authority-marked), `doc-stats --check` clean, `LES-NNN` for anything
   durable

## 5. Depends on / sequence

- **Depends on:** WO-2 (`client`), WO-4 (`windows`).
- **Unblocks:** WO-6.
