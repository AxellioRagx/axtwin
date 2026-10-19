# AXAPI_CONTRACT — the `axapi` wire surface `xtract` uses

> **Genre:** formal spec (see `docs/CONVENTIONS.md` §3) — the version-locked contract `xtract`
> implementations must satisfy. Changing it after it lands is either (a) a correction to our
> transcription of the vendor's own contract — edit freely, record the fix — or (b) a re-pin to a
> new `axapi` release, which goes through the risk gate in `CLAUDE.md` "Design Document
> Maintenance": bump **Contract-doc version** below and confirm with the user first.
>
> **Contract-doc version:** 2 (2026-08-05 — partly measured, not purely transcribed. A live probe
> against CorpAPCN found six places the vendor PDFs are simply wrong, not just internally
> inconsistent — see `docs/VENDOR_API_DEFECTS.md` for the full evidence. Sections below marked
> **📏 MEASURED 2026-08-05** describe *observed* appliance behavior; the documented (and now known
> wrong) shape is kept alongside, not deleted, so a future reader can see exactly what the vendor
> claims vs. what the appliance does. Sections without that marker are still pure transcription,
> unverified against a live system, per the "Not tested" list at the bottom of
> `docs/VENDOR_API_DEFECTS.md`.)
> **Vendor API version pinned:** `axapi` 5.1.0
> **Authority for this transcription:** CHANGELOG 2026-08-04 — `xtract` design ratified;
> `docs/WORK_ORDER_axapi_contract.md` (WO-0). **Authority for the 2026-08-05 corrections:** CHANGELOG
> 2026-08-05 — "Q1–Q4 resolved by live probe; six vendor-contract corrections applied";
> `docs/WORK_ORDER_live_probe.md` (DONE); `docs/VENDOR_API_DEFECTS.md`.

## 0. Scope and how to read this

This document transcribes only the **read-only subset** of `axapi` that `xtract` calls — not the
whole platform surface (~50 endpoints across users, groups, services, encryption, plugins,
analytics, netflow, SigMF, VNC, and more). Transcribing the whole API is explicitly **not a goal**
(`docs/WORK_ORDER_axapi_contract.md` §1). §8 names what's out of scope; §7 itemizes, without
abbreviation, the mutating endpoints `xtract` must never call.

**Sources**, in priority order:

1. **PRIMARY** — `Axellio Xpress Platform REST API.pdf` ("REST API doc"), `axapi` 5.1.0, 84 pages.
   Wins on any disagreement with the secondary source.
2. **SECONDARY** — `PacketXpress Interface Control 5.1.0.pdf` ("ICG"), `axapi` 5.1.0, 7 pages.
   Narrower and partly superseded, but it is the **only** source for the `stream_ids` grammar and
   the vendor's worked example script. Cited only where it adds information the REST API doc lacks,
   or where the two disagree (§9).
3. **TERTIARY, and the one that overrides the other two — a live measurement.** `docs/VENDOR_API_DEFECTS.md`
   (2026-08-05) records six places where the appliance's actual behavior disagrees with **both**
   PDFs, not just where the PDFs disagree with each other. Where a 📏 MEASURED marker (below) appears,
   the live measurement is authoritative over anything either PDF says, including where they agree
   with each other (see §9.4's 2026-08-05 update for why "the PDFs agree" is not the same claim as
   "the PDFs are correct").

**Markers used throughout:**

- 🟡 **OPEN — Qn** — an unresolved wire-contract fact neither PDF states, not yet measured live. Do
  not infer an answer here; see `docs/OPEN_QUESTIONS.md` for the question. **As of 2026-08-05, no
  field in this document still carries this marker** — Q1–Q4 are all resolved (§10) — but the marker
  definition is kept for any future question this contract doc opens.
- 🛈 **VENDOR-DOC DEFECT** — a literal transcription of an internal inconsistency, likely copy-paste
  artifact, or unstated response body in the vendor PDF(s). Recorded, not silently corrected. As of
  2026-08-05 some of these are also confirmed **actually wrong**, not just internally inconsistent —
  those say so explicitly and point at the 📏 MEASURED section that confirmed it.
- ⇄ **DIVERGENCE** — the two PDFs genuinely disagree or one states something the other omits; see
  §9 for the full list. The REST API doc's version is what's authoritative below.
- 📏 **MEASURED 2026-08-05** — this section (or a specific row within it) describes **observed**
  appliance behavior from the live probe, not a transcription. Where it disagrees with the documented
  shape, both are shown — the documented one labeled "documented", the real one labeled "actual" —
  with a pointer to the relevant `docs/VENDOR_API_DEFECTS.md` section for the raw evidence. A section
  *without* this marker is still pure transcription, unverified against a live system — check
  `docs/VENDOR_API_DEFECTS.md`'s "Not tested" list before trusting it as fact.

Every request in this document requires an `Authorization: Bearer <token>` header (JWT or API key)
unless stated otherwise — this is a whole-API rule stated once (REST API doc p.1) and not always
repeated in each endpoint's own `Headers:` block; where the per-endpoint text omits it, this
document still requires it, because 401 is listed as a possible status on nearly every one.

---

## 1. Authentication

### 1.1 `POST /axapi/login` — get a token — 📏 MEASURED 2026-08-05

| | |
|---|---|
| **Headers** | `Content-Type: application/json` |
| **Request body** | `{"username": string, "password": string}` (both required) |
| **Success response** | `{"token": string}` — confirmed correct against the live appliance |
| **Error response** | `{"message": string}` |
| **Documented status** | `201` — both PDFs' status-code tables say this (see 🛈 below). |
| **Actual status (measured 2026-08-05)** | **200.** CorpAPCN (PacketXpress 5.1.0) returns `200 OK` with the documented `{"token": ...}` body. Both vendor PDFs are wrong, not just internally inconsistent — see `docs/VENDOR_API_DEFECTS.md` §B3. `400`/`401` were not exercised live but are kept as documented. |

🛈 **VENDOR-DOC DEFECT** (present identically in both PDFs, and now confirmed **actually wrong**, not
just self-contradictory): the prose response block is headed "Success (**200**):" but the Status
Codes list immediately below it says "**201** - - OK". The 200 heading turned out to be the one that
matches the real appliance. `xtract`'s `client.py` now accepts **both** `200` and `201` for login and
refresh (`expected_status=(200, 201)`) rather than asserting either alone — see `LES-034` (amended in
place 2026-08-05).

### 1.2 `POST /axapi/refresh` — refresh a token

| | |
|---|---|
| **Headers** | `Content-Type: application/json` |
| **Request body** | None |
| **Success response** | 🛈 **VENDOR-DOC DEFECT — not documented.** Neither PDF prints a response body for this endpoint (the REST API doc goes straight from "Success (200):" to the Status Codes list with no JSON in between). By symmetry with login, `{"token": string}` is *plausible* — but that is an inference, not a transcribed fact, and this document does not assert it as one. |
| **Status codes** | **201** — OK. `401` — username or token does not match. |

Refresh, like login, is documented as returning 201 in its status-code table (not 200).

### 1.3 `POST /axapi/logout`

| | |
|---|---|
| **Headers** | `Content-Type: application/json` |
| **Request body** | `{"token": string}` — the token to be logged out |
| **Success response** | `{"token": string}` per the REST API doc's JSON block |
| **Error response** | `{"message": string}` |
| **Status codes** | **200** — OK (not 201 — logout is the one auth endpoint that really does return 200 in the status table). `400` — bad request. `401` — missing/invalid token provided. |

🛈 **VENDOR-DOC DEFECT** (present in both PDFs): the ICG explicitly glosses the 200-status response
as "The newly generated token" — copy-pasted boilerplate from the login section. A logout endpoint
returning a *newly generated* token makes no semantic sense; this is almost certainly leftover text
from login, not a real behavior. `xtract` does not depend on the logout response body for anything
(per `CLAUDE.md`, logout is fire-and-forget in a `finally`), so this defect has no practical effect
on `xtract`, but it is recorded here rather than silently smoothed over.

`/axapi/validate` exists (`POST`, page 5 of the REST API doc) but is **not used by `xtract`** and is
not transcribed further here — its response body is also entangled in the same page-break/column
reordering artifact as §1.3 above (a stray `{"redirectPage": string}` block appears between the
Validate and Logout sections and most plausibly belongs to Validate given its "routing on initial
app load" description, but this document does not assert that placement as fact).

---

## 2. API key management

`xtract` uses **list** and **revoke** only. Key creation is out of scope by design (`CLAUDE.md`
"What NOT to Do" — minting long-lived credentials is a different risk class from reading capture
data) and is listed under §7 as a mutating endpoint `xtract` must never call.

### 2.1 `GET /axapi/apikeys` — list keys

| | |
|---|---|
| **Headers** | `Authorization: Bearer <token>` |
| **Query params** | `include_revoked` — optional, default `false` |
| **Success response** | `{"keys": [{"id": integer, "key_prefix": string, "user": string, "description": string, "created_at": integer, "expires_at": integer, "last_used_at": integer, "revoked": boolean}, ...], "count": integer}` |
| **Status codes** | `200` — success. `401` — authentication required. `500` — internal server error. |

`created_at` / `expires_at` / `last_used_at` are documented as Unix timestamps (seconds, per the
prose "Unix timestamp when key was created" etc.) — unlike every `axapi` *capture-data* time field,
these are typed `integer`, not `string`. Do not reuse `timeutil.py`'s string-nanosecond convention
for these fields; they are a distinct, ordinary-integer-Unix-seconds representation.

### 2.2 `DELETE /axapi/apikeys/<key_id>` — revoke a key

| | |
|---|---|
| **Headers** | `Authorization: Bearer <token>` |
| **Request body** | None (`key_id` is in the URI path) |
| **Success response** | `{"message": string, "key_id": integer}` |
| **Error response** | `{"error": string}` |
| **Status codes** | `200` — successfully revoked. `401` — authentication required. `403` — access denied (not owner). `404` — key not found. |

Note: no `500` is listed for revoke, unlike list/create/info — transcribed as documented, not
assumed to be an omission.

**Adjacent, not used by `xtract`:** `GET /axapi/apikeys/<key_id>` (single-key info; same fields as
§2.1's list entries plus `is_expired: boolean`; status codes `200/401/403/404`) is read-only and
harmless, but `xtract` has no need for it — `xtract auth list-keys` uses §2.1 only. Not transcribed
further.

---

## 3. Task management

Tasks are how `axapi` reports progress on an in-flight extraction (created implicitly by
`GET /axapi/pcap`, see §6) and how it's aborted.

### 3.1 `GET /axapi/task` — list running tasks

| | |
|---|---|
| **Request body** | None |
| **Success response** | `{"data": {<task_guid>: {"directory": string, "info": {"bytes_sent": int, "comment": string, "downloaded": bool, "end_time": string, "filter": string, "flowpreview_bytes": int, "flowpreview_packets": int, "flowpreview_percent": int, "guid": string, "output_length": int, "percent": int, "pid": int, "start_time": string, "status": string, "streamIds": string, "time_started": string, "type": string, "user": string, "ctid": string}}}}` |
| **Status codes** | `200` — OK. `401` — missing/invalid token provided. |

`<task_guid>` in the response is a literal placeholder for the real task GUID string — it is a map
keyed by GUID, not a literal field named `task_guid`. This shape repeats identically for §3.2 and
§3.3 below.

🛈 Note the field is `streamIds` (camelCase) here, but the *request* parameter on `GET /axapi/pcap`
is `stream_ids` (snake_case, §6). Same concept, two different names on request vs. response — do
not assume they round-trip under the same key.

### 3.2 `GET /axapi/task/<guid>` — task info by GUID or CTID

Same response shape as §3.1, scoped to one task. Per the REST API doc: "Returns task based on GUID
or CTID" — the same URI parameter accepts either identifier.

| | |
|---|---|
| **Status codes** | `200` — OK. `401` — missing/invalid token provided. |

### 3.3 `GET /axapi/task/latest` — most recently started task

Same response shape as §3.1. Not currently used by `xtract` but documented for completeness since
it shares the family.

### 3.4 `DELETE /axapi/task/<guid>` — abort task by GUID or CTID

| | |
|---|---|
| **Request body** | None |
| **Success response** | `{"guid": string}` |
| **Status codes** | `200` — OK. `401` — missing/invalid token provided. |

Used to abort an in-flight extract on SIGINT (`CLAUDE.md` architecture diagram).

---

## 4. System status

### 4.1 `GET /axapi/system/information`

| | |
|---|---|
| **Request body** | None |
| **Success response** | `{"name": string, "identifier": string, "time": number, "management_containers": boolean, "software": {"<packagename>": {"version": string, "build": string, "details": string}}}` |
| **Status codes** | `200` — OK. |

`time` is the appliance clock, used for skew checks (`CLAUDE.md` surface table). Note it is typed
`number` here, not `string` — unlike the capture-data time fields in §5 and §6, which are `string`.
Don't conflate the two conventions.

---

## 5. Filesystem

### 5.1 `GET /axapi/fs/streams` — list all stream directories — 📏 MEASURED 2026-08-05

| | |
|---|---|
| **Request body** | None |
| **Documented response** | `{"stream": [{"name": string, "permissions": integer, "user": string, "group": string, "size": integer, "date": string, "type": string}, ...]}` |
| **Actual response (measured 2026-08-05)** | a **bare top-level JSON array** — `[{...}, {...}, ...]` — no `{"stream": [...]}` wrapper at all. A client written to the documented shape fails immediately (indexing a `list` with a string key); this is what `xtract`'s first live call did. See `docs/VENDOR_API_DEFECTS.md` §B2. |
| **Actual field values (measured)** | `name` is `streamNN` (e.g. `stream08`), not a bare decimal — see §6.2/Q3 below. `permissions` is a **string** (`"0444"`, 4 digits with leading zero — note this differs from `fs/files`' 3-digit form, §5.3), not `integer`. `is_directory` (boolean) is present but undocumented. One entry is a disk-usage footer line masquerading as a stream directory named `"TB"` with `time_start: "4000000h"` — see the junk-filtering note under §5.3. |
| **Status codes** | `200` — OK (matches documented). `401` — missing/invalid token provided. `500` — internal server error. |

🛈 **VENDOR-DOC DEFECT, confirmed wrong (not just under-specified):** the documented response wrapper
key is **`stream`** (singular) holding a JSON array — the real appliance returns the bare array with
no wrapper. `catalog.py`'s `_list_payload` still tolerates the documented wrapper form defensively (in
case another firmware pairs the two), but treats the bare-array form, confirmed live, as the primary
case.

### 5.2 `GET /axapi/fs/streams?stream=<stream>` — single stream's info — ⚠️ NOT LIVE-TESTED

| | |
|---|---|
| **Query params** | `stream` — required |
| **Success response** | `{"stream": {"name": string, "permissions": integer, "user": string, "group": string, "size": integer, "date": string, "type": string, "entries": integer}}` — as documented; still pure transcription |
| **Status codes** | `200` — OK. `401` — missing/invalid token provided. `404` — stream not found. `500` — internal server error. |

⚠️ **Not exercised by the 2026-08-05 live probe** (`docs/VENDOR_API_DEFECTS.md` "Not tested"). Given
that §5.1 (the list-all sibling of this exact endpoint) turned out to return a bare array rather than
the documented `{"stream": [...]}` wrapper, treat this section's documented `{"stream": {...}}`
wrapper as **unconfirmed, not just untested** — the same defect class plausibly applies here too.
Worth confirming before relying on it.

🛈 Same wrapper key `stream`, this time holding a JSON **object**, not an array — §5.1 and §5.2
are only distinguishable by JSON type, not by key name. Client code must not branch on the presence
of the `stream` key to decide "is this a list or a single item"; it must check `isinstance(..., list)`.

🛈 The single-stream response additionally carries an **`entries`** field (integer — presumably a
count of files in the stream) that is **absent from the list-all response** (§5.1). This is easy to
miss reading the doc once; confirmed by re-reading the raw extracted text around the page break
between the two sections.

The REST API doc's text literally labels one part of this section "Error (200):" — a response
labeled as an error yet carrying a 200 status. No distinguishing JSON body is printed for it beyond
what's shown above; likely a rendering artifact where the vendor's "error" schema block didn't
survive extraction (or wasn't authored distinctly from the success schema). Recorded, not resolved.

### 5.3 `GET /axapi/fs/files?stream=<stream>` — list files in a stream — 📏 MEASURED 2026-08-05

| | |
|---|---|
| **Query params** | `stream` — required |
| **Documented response** | `{"files": [{"name": string, "permissions": integer, "user": string, "group": string, "size": integer, "date": string, "time_start": integer, "time_end": integer, "epoch": integer, "seqnum": integer, "type": string}, ...]}` |
| **Actual response (measured 2026-08-05)** | a **bare top-level JSON array**, same defect as §5.1 — no `{"files": [...]}` wrapper. `time_start`, `time_end`, `epoch`, `seqnum` are **strings**, not `integer` (`size` is genuinely `integer`, matching documented). `is_directory` (boolean) is present but undocumented. See `docs/VENDOR_API_DEFECTS.md` §B2, §C1. |
| **Status codes** | `200` — OK (matches documented). `401` — missing/invalid token provided. `404` — stream not found. `500` — internal server error. |

This is the endpoint `xtract streams` uses to assemble each stream's time range —
`min(time_start)`/`max(time_end)` over these records, per CHANGELOG 2026-08-04. It is also where gap
detection happens: consecutive files whose `[time_start, time_end]` don't abut, past a 2-second
threshold (`catalog.DEFAULT_GAP_THRESHOLD`, CHANGELOG 2026-08-05 "Gap-detection threshold defaults to
2s"), indicate a storage gap. The threshold exists because the second-resolution timestamps (below)
make an hourly boundary read as a spurious 1-second gap on their own.

🛈 **VENDOR-DOC DEFECT, confirmed** — a disk-usage summary line (`df`-style: "20.538 TB of 20.958 TB
available, 98.00% utilized") is returned as a **fake, field-shifted file record** alongside the
genuine ones — `{"time_start": "available,", "name": "TB", "epoch": "utilized", ...}`. A client that
parses the documented `integer` type (`int(rec["time_start"])`) crashes on it, and it's
indistinguishable from a real record by `type` or `is_directory` alone. `catalog.py` filters it out
structurally before parsing (`_is_footer_junk_file_entry`) — see `LES-049`. Real directory entries in
the sibling §5.1 endpoint separately carry a different junk value (`"4000000h"`), so the filter is not
a blanket "non-numeric timestamp" rule — see `LES-049` for why that would be wrong.

✅ **RESOLVED — Q1** (`docs/OPEN_QUESTIONS.md` Q1, was 🟡 OPEN): `time_start`/`time_end` are unix
epoch **seconds**, confirmed by cross-checking `"1781795842"` against the same record's own `date`
field (`"Jun 18 15:17"`). The unit assumption baked into `timeutil.FS_FIELD_NS_PER_UNIT` was already
correct; only the documented `integer` type was wrong (see above — it's actually `string`). `epoch`
and `seqnum` were not needed to disambiguate the unit, and whether `epoch` is the same clock as
`time_start` was not determined. **Note the resolution's most important undocumented fact:** this
endpoint's timestamps are second-resolution, while `/axapi/pcap`'s `start_time`/`end_time` (§6.1) are
sub-second — the two were never stated as different resolutions in either PDF, though they're
obviously meant to be used together. Unit conversion still happens in exactly one place
(`timeutil.py`, `FS_FIELD_NS_PER_UNIT`), now a settled fact rather than a placeholder.

**Adjacent, not used by `xtract`, and not live-tested:** `GET /axapi/fs/file?stream=<s>&file=<filename>`
(single-file info; same field set as above, wrapped under the key `files` again — same
list/single-object ambiguity pattern as §5.1/§5.2, and the same reason to distrust the documented
wrapper without confirming it) is read-only but not needed by either `xtract` command. Not
transcribed further. Its mutating sibling `DELETE /axapi/fs/file?...` is in §7.

---

## 6. PCAP extraction — the core endpoint

### 6.1 `GET /axapi/pcap` — 📏 MEASURED 2026-08-05 (end_time semantics)

| | |
|---|---|
| **Headers** | `authorization: "Bearer <token>"` |
| **Success response** | Raw pcap bytes (streamed) |
| **Response header** | `X-Task-GUID` — the unique task identifier for this extraction |
| **Status codes** | `200` — OK, file transfer. `401` — missing/invalid token provided. `418` — extraction did not provide any data (no output available). `500` — an error occurred while extracting. |

**Query parameters**, all optional except where noted:

| Param | Type (as documented) | Meaning |
|---|---|---|
| `ctid` | string | Client task ID, recorded in the task (§3) for correlation |
| `start_time` | string | Sub-second epoch time. 🛈 The REST API doc's own description literally reads "Optional sub-second epoch time for **end** of extract" for *both* `start_time` and `end_time` — almost certainly a copy-paste error (the same wording appears twice, verbatim, in the REST API doc, and again in the ICG). No PDF corrects this. Given the parameter name, `start_time` is treated as the start of the window and `end_time` as the end — that reading is not in doubt, only the doc's prose is defective. |
| `end_time` | string | Sub-second epoch time for end of extract |
| `filter` | string | BPF or community ID (REST API doc's wording; see ⇄ §9.2 for what the ICG adds) |
| `stream_ids` | string | Stream IDs to extract. **Default: `"0-11"`.** Grammar in §6.2. |
| `archive` | string | Address of archive system. **Deferred** — `xtract` does not wire this in iteration 1 (`CLAUDE.md` "What NOT to Do"). Documented here because it's part of the endpoint's contract, not because `xtract` sends it. |
| `servers` | string | Multi-extract: comma-separated list of cluster systems. **Deferred**, same as `archive`. |

Every time field on this endpoint is typed **`string`** — confirmed independently on the
`POST /axapi/extract` request payload (REST API doc p.53–54: `{"start_time":string,
"end_time":string, "filter":string, "stream_ids":string, "comment":string}`), which uses the
identical field set. `xtract` never constructs a numeric time value on the wire: parse → `int`
nanoseconds internally → format back to a string, preserving the caller's precision.

✅ **RESOLVED — Q2** (`docs/OPEN_QUESTIONS.md` Q2, was 🟡 OPEN): the appliance accepts both
integer-second and sub-second-decimal timestamp strings and parses sub-second precision exactly —
**but only when the fractional part is non-zero.** `end_time=1785934225.500000000` bounded the
extract precisely at `.487038`; `end_time=1785934225.000000000` (and the bare integer
`1785934225`) did **not** bound at all — see Q4 immediately below for what it did instead. Neither
PDF states this; it's a genuine undocumented behavior, not a copy-paste defect like the ones marked
🛈 elsewhere in this doc.

✅ **RESOLVED — Q4** (`docs/OPEN_QUESTIONS.md` Q4, was 🟡 OPEN) — **`end_time` is INCLUSIVE**, and a
**zero** fractional part widens that inclusivity to cover the entire second, not just the exact
instant. Four measurements over the same stream, all requesting a window starting at `1785934195`:

| `end_time` sent | Last packet returned | Effective span |
|---|---|---|
| `1785934225` | `1785934225.986821` | 30.98 s |
| `1785934225.000000000` | `1785934225.986821` | 30.98 s (same as bare integer) |
| `1785934225.500000000` | `1785934225.487038` | bounded precisely at `.5` |
| `1785934224.999999999` | `1785934224.993575` | 29.99 s |

**Impact, confirmed live:** a client tiling `[T, T+60)` then `[T+60, T+120)` with `end_time` sent
unadjusted gets the packets in second `T+60` **twice**. A 60s extract with the boundary unadjusted
returned 7,058 packets; the same range split into two adjacent 30s windows, corrected (`end_time`
sent as `end − 1ns`), returned 4,328 + 2,653 = 6,981 packets — the 77-packet difference is exactly
the previously-duplicated second. `windows.Q4_END_TIME_INCLUSIVE = True`; re-verified afterwards that
adjacent windows (`…195.002029→224.993575` then `…225.025223→254.998116`) share no packet. The
Q4-driven adjustment is applied only at request-construction time in `windows.py` — the manifest
records the window's *logical* end, never the wire-adjusted one (CHANGELOG 2026-08-05 "Manifest
records the logical window end"; `LES-050`). Full evidence: `docs/VENDOR_API_DEFECTS.md` §A1.

**Still not conclusively tested:** what an extract spanning a storage gap returns — 418, or a short
pcap. Filed as part of the untested-endpoints gap in `docs/PRODUCT_GAPS.md`.

**A multi-stream extract returns one merged, time-ordered pcap** — not one pcap per stream
(CHANGELOG 2026-08-04). `--streams` therefore never multiplies the window count, and `xtract` never
merges output itself.

**418 is not a failure.** It means the extraction ran and produced no data for the requested window
— the expected, normal outcome for a quiet window or stream, not an error condition. Treating it as
one aborts batch runs over sparse captures, which is the common case (`LES-035` below).

### 6.2 `stream_ids` grammar

🛈 **This grammar appears only in the ICG** (as a comment in the worked example script) — the REST
API doc's `/axapi/pcap` section states only the default value (`"0-11"`) and never spells out the
grammar itself. See ⇄ §9.1.

```
n[,n][,n-m][,n..m]
```

where `n` and `m` are integer values in the range `0`–`4294967296`. **Both `-` and `..` are legal**
range separators (the ICG's own worked example comment states this explicitly: `"The list may be
specified as numbers and ranges using "-" or ".." with comma separators between entries."`).
`stream_ids` is passed through **verbatim** by `xtract` — never re-emit a caller's `2-4` as `2,3,4`,
and never re-spell `..` as `-` or vice versa. A separate `expand()` function produces integers for
local cataloguing purposes only; it never round-trips back onto the wire.

✅ **RESOLVED — Q3** (`docs/OPEN_QUESTIONS.md` Q3, was 🟡 OPEN) — **NO**, `fs/streams`' `name` field
(§5.1/§5.2, actually typed `string` — see §5.1's correction) is **not** the decimal `stream_ids`
integer directly. Measured 2026-08-05: names are `streamNN` (`stream00`…`stream11`), 12 of them.
`stream_id_for_name` (`catalog.py`) parses the `NN` suffix — the apparent mapping is `streamNN` ↔
`N` (`stream_ids=0` returned packets consistent with `stream00`'s file listing), consistent with
observation but **not exhaustively proven per-stream**; each of the 12 was not individually extracted
to confirm the correspondence. A name matching neither `streamNN` nor a bare decimal (e.g. `"TB"`,
the disk-usage-footer junk entry — see §5.3) raises `StreamIdMappingError` rather than guessing.
`streamId` (camelCase, integer) in the `/axapi/minute|hour|day` datapoint records (§6.3 below) was
recorded as a possible cross-check but was not exercised in the probe. See
`docs/VENDOR_API_DEFECTS.md` §C3.

### 6.3 Datapoints — `GET /axapi/minute|hour|day/<start_time>`

Not part of the extraction path itself, but recorded here as a cross-check source for Q3, per the
work order.

| Endpoint | Window |
|---|---|
| `GET /axapi/minute/<start_time>` | data timestamped between `start_time` and 60s after |
| `GET /axapi/hour/<start_time>` | data timestamped between `start_time` and 3600s after |
| `GET /axapi/day/<start_time>` | data timestamped between `start_time` and 86400s after |

| | |
|---|---|
| **Success response** | `{"data": [[time, bytes, streamId, packets, packetRate, bitRate], ...]}` — a flat array of 6-tuples, one per datapoint, **not** an array of objects |
| **Status codes** | `200` — OK. `401` — missing/invalid token provided. `500` — error. |

🛈 The field is `streamId` (camelCase, integer) here — a third spelling of the stream-ID concept
alongside `stream_ids` (request param, snake_case string) and `streamIds` (task-info response,
camelCase string, §3.1). Three different names for closely related things; do not conflate them or
assume one can substitute for another on the wire.

---

## 7. Deliberately NOT implemented — mutating endpoints `xtract` must never call

This is a safety property, not documentation debt (`docs/WORK_ORDER_axapi_contract.md` §1). Every
endpoint below is destructive, stops or reconfigures a running service, or mutates
users/groups/credentials/encryption state on the appliance. `xtract`'s codebase must never issue any
of these requests. Listed without abbreviation:

**Datapoint database**
- `POST /axapi/clear` — clears the entire datapoint database.

**Filesystem (captured data)**
- `DELETE /axapi/fs/file?stream=<s>&file=<f>` — deletes a specific captured file.
- `DELETE /axapi/fs/streams?stream=<s>` — deletes an entire stream directory.

**Extract lifecycle**
- `DELETE /axapi/extract/<guid>` — deletes a stored extract.
- `DELETE /axapi/guidextract/<start_time>/<end_time>/<user>` — per the REST API doc this is
  described as "Gets specified extract info from database" but its documented **Method is DELETE**
  (page 54–55). 🛈 This description/method mismatch reads like a vendor doc error (a GET mislabeled
  DELETE), but `xtract` treats the documented HTTP verb as authoritative and never calls it, since
  the safe reading of an ambiguous DELETE is "don't call it."
- `DELETE /axapi/guidextract/<guid>` — same caveat as above.
- `POST /axapi/apikeys` — creates a new API key. `xtract` only lists and revokes (§2); minting a
  long-lived credential is a different risk class and stays a UI-only operation.

**Services**
- `POST /axapi/service/<name>/control` — starts or stops a named service.
- `PUT /axapi/services` — adds a new service to the database.
- `DELETE /axapi/services` — deletes a service by name.
- `POST /axapi/changeService` — edits an existing service.
- `POST /axapi/services/<filename>` — creates a new service file from a template.
- `DELETE /axapi/services/<filename>` — deletes a service file.

**Users, groups, roles**
- `POST /axapi/user` — adds a user.
- `PUT /axapi/user` — modifies a user's assigned roles.
- `POST /axapi/group` — adds a group.
- `PUT /axapi/group` — modifies a group's assigned roles.

**Encryption**
- `POST /axapi/encryption/status` — unlocks the system (changes encryption state).
- `POST /axapi/encryption/password` — changes the system encryption password.

**Configuration files**
- `POST /axapi/configuration_file/<filename>` — overwrites a configuration file (one of `px.ini`,
  `site.ini`, `pxhealth.ini`, `ntservice.ini`, `01FilterMacros.ntpl`, `02AxellioDefault.ntpl`).

**Cluster**
- `POST /axapi/cluster` — adds a system to the cluster.

**Plugin control**
- `PATCH /axapi/plugins/<plugin_name>/config` — changes which protocols the EncryptedSlicer plugin
  slices, per-stream or system-wide.

Absent code cannot be called by mistake — this list exists so a future change is a conscious,
reviewable decision to add one of these calls, not an accidental import.

---

## 8. Out of scope entirely — not transcribed, not planned

Whole functional areas the REST API doc covers that have nothing to do with `xtract`'s job (pulling
capture data off CorpAPCN). Named for completeness, per the work order, not transcribed in detail:

- **Users / groups / roles (read side)** — `GET /axapi/users`, `/axapi/roles`, `/axapi/groups`,
  `/axapi/group_roles`, `/axapi/user/role_check`.
- **Cluster (read side)** — `GET /axapi/cluster`.
- **Analytics** — `GET /axapi/analytics`, `/axapi/analytics/size`,
  `/axapi/analytics/reports/conversation_count`.
- **Network flows** — `GET /axapi/netflow`, `/axapi/netflowstream` (SSE).
- **SigMF** — `GET /axapi/sigmf`.
- **Documents** — `GET /axapi/documents`, `/axapi/documents/<filename>`.
- **Logs** — `GET /axapi/loglist`.
- **Extractors** (config-file-adjacent, unrelated to pcap extraction despite the name) —
  `GET/POST /axapi/extractors/<filename>`.
- **Configuration files (read side)** — `GET /axapi/configuration_file/<filename>`.
- **Encryption (read side)** — `GET /axapi/encryption/status`.
- **System license / utilization** — `GET /axapi/system/license`; `GET /axapi/system/utilization`
  (the vendor doc itself marks this **"NOT YET IMPLEMENTED"**, page 45).
- **Statistics** — the REST API doc's own section body reads **"Not yet defined"** (page 47).
- **Plugin control (read side)** — `GET /axapi/plugins`, `GET /axapi/plugins/<name>/config`,
  `GET /axapi/plugins/<name>/protocols`.
- **Extract lifecycle (async transport, deferred, not forbidden)** — `GET /axapi/extract` (list),
  `POST /axapi/extract` (create — same five fields as `GET /axapi/pcap`: `start_time`, `end_time`,
  `filter`, `stream_ids`, plus `comment`; returns `{"guid": string}`, status `201`), and
  `GET /axapi/extract/<guid>/output` (poll for results; status `200` file transfer, `418` no
  output, `425` task not complete). This triad is the async equivalent of `GET /axapi/pcap` and is
  explicitly **deferred, not forbidden** (`CLAUDE.md` Tech Stack: "the swap is an envelope change if
  long-window timeouts force it"). Distinct from §7 — nothing here is destructive, it's just not
  wired for iteration 1.

---

## 9. Divergences between the two PDFs

Where the two disagree, the REST API doc wins per `docs/WORK_ORDER_axapi_contract.md`. Found during
this transcription:

### 9.1 `stream_ids` grammar — ICG has it, REST API doc doesn't

The REST API doc's `/axapi/pcap` section states only the default (`"0-11"`) and the type
(`string`); it never spells out the grammar. The full grammar — `n[,n][,n-m][,n..m]`, `n`/`m` in
`0`–`4294967296`, with **both `-` and `..`** legal separators — appears only in the ICG's worked
example script comments. Not a contradiction (the REST doc says nothing to disagree with), but a
genuine coverage gap: `xtract` would be unable to validate `stream_ids` syntax correctly using the
REST API doc alone.

### 9.2 `filter` grammar — each PDF names a different second form

The REST API doc's `/axapi/pcap` section says `filter` accepts "**Berkeley Packet Filter or
community ID**" — it never mentions a tuple form. The ICG's worked example script comment says
`bpfFilter` may be "**a BPFFilter** (see man 7 pcap-filter) **or Tuple Filter**
(source_ip,dest_ip,source_port,dest_port,protocol)" — it never mentions community ID. Neither PDF,
read alone, documents all three accepted forms. `xtract` treats the union of both (BPF, community
ID, or the ICG's tuple form) as valid per `CLAUDE.md`'s explicit rule to never validate `filter` —
the appliance is the authority on what it accepts, and passing a value through unexamined is safe
regardless of which of the three forms the caller used.

### 9.3 `ctid` and `X-Task-GUID` — present in the REST API doc, entirely absent from the ICG

The REST API doc's `GET /axapi/pcap` query string includes `ctid=<string>` and documents an
`X-Task-GUID` response header. The ICG's `/axapi/pcap` section has neither — its query string is
`start_time`/`end_time`/`filter`/`stream_ids`/`archive`/`servers` only, and its response section
just says "Raw Data" with no header mentioned. This is a real capability gap between the two docs
(not a contradiction): the ICG predates task tracking. `xtract` uses `ctid` and `X-Task-GUID` per
the REST API doc — they're what makes an extract abortable via §3.

### 9.4 Everything else checked, found identical

`GET /axapi/pcap` status codes (200/401/418/500), `stream_ids` default (`"0-11"`), and the
login/refresh/logout success status codes (201/201/200) all match exactly between the two PDFs —
including the shared internal inconsistencies noted in §1.1 and §1.3, which are vendor-doc defects
present in *both* sources identically, not disagreements *between* them. **2026-08-05 update:** this
agreement between the PDFs turned out not to mean the number is *correct* — the live probe measured
login actually returning 200, not the 201 both PDFs agree on (§1.1). Two independent documents
agreeing with each other is not the same evidentiary weight as one live measurement of the real
appliance; treat §9's "found identical" findings as "the PDFs don't disagree with each other here,"
not as "and therefore this is confirmed correct."

---

## 10. Cross-reference — Q1–Q4

| # | Affects | Section here | Status |
|---|---|---|---|
| Q1 | Units of `time_start`/`time_end` | §5.3 | ✅ RESOLVED 2026-08-05 — seconds, delivered as strings |
| Q2 | Does `/axapi/pcap` accept both integer and sub-second timestamp strings? | §6.1 | ✅ RESOLVED 2026-08-05 — yes, but a zero fraction widens to the whole second |
| Q3 | Does `fs/streams`' `name` map to the numeric `stream_ids`? | §6.2 | ✅ RESOLVED 2026-08-05 — no; names are `streamNN`, apparent `streamNN`↔`N` mapping |
| Q4 | Is `end_time` inclusive or exclusive? | §6.1 | ✅ RESOLVED 2026-08-05 — inclusive, and a zero fraction widens to the whole second |

All four are resolved by the 2026-08-05 live probe (`docs/WORK_ORDER_live_probe.md`, now DONE); the
evidence for each is inline in the sections above and in full in `docs/VENDOR_API_DEFECTS.md`. See
`docs/OPEN_QUESTIONS.md` for the design-register view of the same resolutions, and CHANGELOG
2026-08-05 "Q1–Q4 resolved by live probe; six vendor-contract corrections applied" for the
Authority-marked record.
