# Work Order — WO-0: transcribe the `axapi` contract we actually use

> **Genre:** work order (see `docs/CONVENTIONS.md` §3) — an execution ticket for the `/dev` Pipeline. Concrete files, concrete gates, time-bounded. Mark it **DONE** in place when its run lands; don't delete it.
>
> **Status:** ✅ DONE (CHANGELOG 2026-08-04 — "`xtract` iteration-1 build complete")
> **Authority for the decisions behind it:** CHANGELOG 2026-08-04 — `xtract` design ratified; iteration-1 scope and work orders

## 1. Goal

`docs/AXAPI_CONTRACT.md` exists and is the single place any `xtract` module looks up a request
shape, a parameter name, or a status code. It is **version-locked to `axapi` 5.1.0** and covers only
the read-only subset `xtract` uses — not the whole 84-page platform surface. After this lands, no
work order needs to re-open a PDF, and a wrong assumption about the wire has exactly one place to be
corrected.

**Not a goal:** transcribing the whole API. Users, groups, services, encryption, plugins, analytics,
netflow, SigMF and the VNC endpoints are out. Naming them in a short "deliberately not implemented"
list *is* in scope — that list is a safety feature, not documentation debt.

## 2. Build — phases, in order

### Phase 1 — Contract doc

- **Files:** `docs/AXAPI_CONTRACT.md` (new)
- **Behavior:** For each endpoint below, record URI, method, required headers, every parameter with
  its documented type, the full status-code set with our interpretation, and the response shape:
  - `POST /axapi/login` (**201** on success — not 200), `POST /axapi/refresh`, `POST /axapi/logout`
  - `GET /axapi/apikeys`, `DELETE /axapi/apikeys/<key_id>`
  - `GET /axapi/fs/streams`, `GET /axapi/fs/streams?stream=<s>`, `GET /axapi/fs/files?stream=<s>`
  - `GET /axapi/pcap` — `ctid`, `start_time`, `end_time`, `filter`, `stream_ids`, `archive`,
    `servers`; response header `X-Task-GUID`; `stream_ids` **defaults to `"0-11"`**
  - `GET /axapi/task`, `GET /axapi/task/<guid|ctid>`, `DELETE /axapi/task/<guid|ctid>`
  - `GET /axapi/system/information` (field `time` = appliance clock)
  - `GET /axapi/minute|hour|day/<start_time>` — datapoints `[time, bytes, streamId, packets,
    packetRate, bitRate]`, recorded as a cross-check source for Q3
- **Behavior (contract facts that must be stated explicitly):**
  - Every time field is typed **`string`**, on both `GET /axapi/pcap` and the
    `POST /axapi/extract` payload (REST API doc p.53–54).
  - `stream_ids` grammar `n[,n][,n-m][,n..m]`, n in `0–4294967296` — **`..` is legal as well as
    `-`** (ICG only; the REST doc omits it).
  - `filter` accepts BPF, a community ID, *or* the ICG tuple form
    `src_ip,dst_ip,src_port,dst_port,proto`. Therefore: never validate it.
  - `GET /axapi/pcap` 418 = extraction produced no data. Not a failure.
  - A multi-stream extract returns **one merged, time-ordered pcap**.
  - Where the two PDFs disagree, the 84-page REST API doc wins; note each divergence found.
- **Behavior:** A "deliberately not implemented" section listing the mutating endpoints `xtract`
  must never call: `POST /axapi/clear`, `DELETE /axapi/fs/file`, `DELETE /axapi/fs/streams`,
  `DELETE /axapi/extract/<guid>`, `POST /axapi/service*`, user/group/role mutation,
  `POST /axapi/encryption/*`, `POST /axapi/apikeys` (create).
- **Behavior:** Mark Q1–Q4 inline at the fields they affect, each linking
  `docs/OPEN_QUESTIONS.md`. A reader must not be able to mistake an unverified assumption for a
  documented fact.
- **Tests:** none (documentation).

### Phase 2 — Lessons

- **Files:** `docs/LESSONS.md`
- **Behavior:** Append the lessons this contract already earned. At minimum:
  - **Never f-string query parameters into an `axapi` URL** — use `params=`. A BPF filter contains
    spaces, parens and quotes; the vendor's own example script gets this wrong and will corrupt any
    non-empty filter. Scope: `xtract`, `client.py`. Consumers: code-writer, code-reviewer.
  - **`POST /axapi/login` returns 201, not 200** — asserting 200 rejects every successful login.
    Scope: `xtract`, `client.py`.
  - **418 from `/axapi/pcap` means "no data", not an error** — treating it as a failure aborts
    batch runs over sparse captures, which is the normal case. Scope: `xtract`, `extract.py`.
  - **`stream_ids` is passed through verbatim** — never re-emit `2-4` as `2,3,4`.
    Scope: `xtract`, `catalog.py`, `windows.py`.
- **Tests:** `python3 scripts/doc-stats.py --check` clean.

## 3. Boundaries — explicitly OUT of scope

- No code. No `pyproject.toml`. This work order produces documentation only.
- Do **not** resolve Q1–Q4 by inference from the PDFs. They are marked open because the documents
  genuinely don't answer them; a plausible guess recorded as fact is the failure this doc exists to
  prevent.
- Anything that would trip the risk gate — surface it as `DECISION NEEDED:` instead of doing it.

## 4. Validation

1. **Unit:** n/a
2. **Hermetic tier:** `scripts/verify_tier.sh` clean
3. **Live:** none — this work order touches no environment
4. **Docs:** CHANGELOG entry (Authority-marked), `doc-stats --check` clean, `LES-NNN` entries
   appended per Phase 2. Spot-check: every endpoint in the `CLAUDE.md` surface table appears in
   `AXAPI_CONTRACT.md`, and vice versa.

## 5. Depends on / sequence

- **Depends on:** nothing.
- **Unblocks:** every other work order. WO-1, WO-2 and WO-4 can start in parallel with this one, but
  none may be marked DONE until the contract they were written against is on paper.
