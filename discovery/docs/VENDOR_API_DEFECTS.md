# axapi 5.1.0 — documentation vs. observed behavior

Defect report for Axellio. Every item below was **measured against a live system**, not inferred
from reading. Where the documentation and the appliance disagree, the appliance's behavior is
recorded as "actual".

**Tested against:** CorpAPCN, PacketXpress / Axellio Xpress Platform 5.1.0, over HTTPS on
2026-08-05.
**Documents checked:** `Axellio Xpress Platform REST API.pdf` (v5.1.0, 84 pp — page refs below) and
`PacketXpress Interface Control 5.1.0.pdf` (7 pp).
**Client:** Python 3.12 / `requests` 2.32.

Severity is from an API-consumer's standpoint: **S** = security, **A** = silently corrupts data,
**B** = breaks a conforming client, **C** = schema/type mismatch, **D** = documentation-only.

---

## S1. `POST /axapi/logout` does not revoke access to `GET /axapi/pcap`

**Severity: security.** Logout reports success and does revoke the token for other endpoints, but
the revoked token continues to serve packet captures.

**Measured**, one session, same token throughout:

| Request | Before logout | After logout (which returned `200`) |
|---|---|---|
| `GET /axapi/fs/streams` | `200` | **`401`** — correctly rejected |
| `GET /axapi/pcap` | `200` | **`200`** — still served packet data |

The post-logout `/axapi/pcap` request returned real capture bytes.

**Impact.** A token that has been explicitly surrendered — or leaked, then "revoked" by logging out —
retains access to the single most sensitive operation the API offers: extracting raw network
traffic. Logging out is the action an operator takes *because* they believe a session should stop
working. Here it stops working for directory listings and keeps working for packet data, which is
the opposite of the useful order.

**Natural expiry behaves the same way — measured separately.** Revocation and expiry could have
been different code paths; they are not. Session tokens expire at **exactly 15 minutes**
(>14 min valid, ≤15 min expired), and an expired token is still accepted by `/axapi/pcap`:

| Token state | `GET /axapi/fs/streams` | `GET /axapi/pcap` |
|---|---|---|
| valid | `200` | `200` |
| revoked via logout | **`401`** | **`200`** — 511 bytes of packet data served |
| naturally expired (15 min, untouched) | **`401`** | **`200`** — 511 bytes of packet data served |

Method note, because the first attempt got it wrong: polling a single token to find its expiry
returned "still valid after 110 minutes". That was an artifact — the expiry is a **sliding window**,
so each probe refreshed the token and the measurement destroyed what it was measuring. The figure
above comes from a *cohort*: N tokens acquired together, each tested exactly once at a different
age, so no token is ever refreshed by its own measurement.

**So neither surrendering a token nor waiting it out stops packet extraction.** Every other probed
endpoint honors the 15-minute lifetime. `/axapi/pcap` honors nothing: any token ever issued appears
to be a permanent capture-extraction credential.

**Request.** Have `/axapi/pcap` validate the session token against the same store as the rest of the
API, and treat a revoked or expired token as `401` there too.

---

## D4. The session token is not a JWT, though the documentation says so throughout

**Severity D — documentation only, but it has practical consequences.** The docs describe
"JWT token-based authentication" (p.1, and the `Authorization: Bearer <token>` sections
throughout), and state that "tokens contain user information for login status and permissions."

**Actual:** the token returned by `POST /axapi/login` is an **80-character opaque string with no
`.` separators** — not a three-part JWT, not decodable, carrying no client-readable claims.

**Impact.** A client cannot read `exp` to know when to refresh proactively, cannot inspect
permissions without a round trip, and cannot validate the token offline — all things the
documentation's "JWT" framing would lead an integrator to design around. Measuring the lifetime
therefore required a cohort experiment against the live appliance (see S1) rather than reading a
claim: it is **15 minutes, sliding**, which no document states.

**Request.** Either issue real JWTs as documented, or describe the token as an opaque session
handle and document its lifetime explicitly.

---

## S2. `GET /axapi/pcap` returns **HTTP 200 with an error body** instead of a pcap, under concurrency

**Severity: security/data-integrity — this silently corrupts output.** The appliance serves exactly
one extract at a time. Every *additional* concurrent request is answered with status **`200 OK`** and
a body consisting of the string `Error starting listen thread, shutting down thread` repeated to
fill the response. One observed instance was **57 MB** of that text, delivered as a success.

**Measured** 2026-08-07, N concurrent `GET /axapi/pcap` requests for distinct non-overlapping
windows on the same stream, each response checked against the pcap magic numbers:

| Concurrency | Valid pcaps returned | Error-body responses (status 200) |
|---|---|---|
| 1 | 1 | 0 |
| 2 | 1 | **1** |
| 4 | 1 | **3** |
| 8 | 1 | **7** |

**Impact, and why it is worse than an outage.** No HTTP-level client can detect this: the status is
200, the body streams cleanly, `Content-Length` is consistent with what arrives. A conforming client
writes the error text to disk under a `.pcap` name and reports success. The corruption is discovered
only when something downstream tries to parse the file — in our case Zeek, with
`problem with trace file (unknown file format)`, three windows and several minutes later.

It also silently falsifies throughput measurement. Our own first concurrency benchmark counted these
bodies as payload and appeared to show near-linear scaling to ~50 MB/s; the real figure is ~13.9 MB/s
with no parallelism available at all. **Anyone benchmarking this API without validating the response
bytes will measure the speed of the error message.**

**Request.** Return a real failure status for a request that cannot be served — `429 Too Many
Requests` or `503 Service Unavailable`, with `Retry-After` — rather than `200` with an error body.
If single-flight extraction is a deliberate design limit, document it and state the expected client
behavior. As an interim measure, a distinguishing `Content-Type` on the error path (e.g.
`text/plain` vs `application/octet-stream`) would at least let a client detect it without inspecting
the payload.

**Client-side mitigation now in place**, since the API offers no signal: `xtract` validates the
leading 4 bytes of every 200 response against the known capture-container magic numbers and treats a
mismatch as a failed extraction rather than a written pcap.

---

## A1. `end_time` is inclusive, and a zero fractional part silently covers the whole second

**Severity A — silent data duplication.** This is the most serious item: a conforming client gets
*more* data than it asked for and no error, so the defect is invisible without packet-level
inspection.

**Documented** (p.58): `end_time=<time>` — "Optional sub-second epoch time for end of extract". No
statement of inclusivity; no statement that fractional precision changes the semantics.

**Actual**, four measurements over the same stream, all requesting a window starting at
`1785934195`:

| `end_time` sent | Last packet returned | Effective span |
|---|---|---|
| `1785934225` | `1785934225.986821` | 30.98 s |
| `1785934225.000000000` | `1785934225.986821` | 30.98 s |
| `1785934225.500000000` | `1785934225.487038` | bounded at `.5` |
| `1785934224.999999999` | `1785934224.993575` | 29.99 s |

So:

1. `end_time` is **inclusive**, not exclusive.
2. An `end_time` with a **zero** fractional part is treated as covering that entire second —
   `…225` and `…225.000000000` both returned packets up to `…225.98`.
3. A **non-zero** fractional part *is* honored precisely.

**Impact.** Any client tiling a time range into consecutive windows — the obvious use of this API —
gets a full second of duplicated packets at every window boundary. Requesting `[T, T+60)` then
`[T+60, T+120)` returns the packets in second `T+60` **twice**. Nothing in the response indicates
this.

**Request.** Document the inclusivity explicitly, and either honor a zero fraction as an exact
bound or document that it is deliberately widened to the second. A worked example of two adjacent
non-overlapping windows would remove all ambiguity.

---

## B1. A disk-usage summary line is returned as if it were a file record

**Severity B — crashes a conforming client.** `GET /axapi/fs/files?stream=<s>` returns, alongside
1161 genuine file records, one entry that is a **`df`-style summary line field-shifted into the
file schema**:

```json
{"date": "TB of 20.958", "epoch": "utilized", "group": "using", "is_directory": false,
 "name": "TB", "permissions": "0000", "size": "20.538", "time_end": "98.00%",
 "time_start": "available,", "type": "file", "user": "is"}
```

Read across the fields in column order, this reassembles to:

> `20.538 TB of 20.958 TB available, 98.00% utilized`

The same artifact appears in `GET /axapi/fs/streams` as a stream directory named **`TB`**.

This looks like whitespace-column parsing of `ls`/`df` output without dropping the summary footer.

**Impact.** `time_start` is documented `integer`; here it is the string `"available,"`. Any client
that parses the documented type — `int(rec["time_start"])` — raises on this record. It is also
indistinguishable from a real entry by `type` (`"file"`) or `is_directory` (`false`). Clients must
defensively discard any record whose timestamps aren't numeric, and any stream named `TB`.

**Request.** Suppress the summary line from both endpoints. If the utilization figures are useful,
expose them as a separate object or a dedicated endpoint rather than an entry in a file list.

---

## B2. `fs/streams` and `fs/files` return bare JSON arrays, not the documented wrapper objects

**Severity B.**

| Endpoint | Documented | Actual |
|---|---|---|
| `GET /axapi/fs/streams` (p.72) | `{"stream": [ {...}, {...} ]}` | bare top-level `[ {...}, ... ]` |
| `GET /axapi/fs/files?stream=<s>` (p.74) | `{"files": [ {...}, {...} ]}` | bare top-level `[ {...}, ... ]` |

**Impact.** A client written to the documented schema fails immediately — indexing a `list` with a
string key. This is what our first live call did.

**Request.** Either restore the documented wrapper or correct the documentation. The wrapper is
preferable: it leaves room for pagination and metadata without another breaking change.

*Not tested:* the single-stream form `GET /axapi/fs/streams?stream=<s>` (p.73), which the docs show
returning an **object** under the same `stream` key. Worth confirming it is consistent with whatever
is chosen here — the documented list/single pair share a key name and differ only by JSON type,
which is itself awkward for typed clients.

---

## B3. `POST /axapi/login` returns **200**, not the documented **201**

**Severity B.**

**Documented** (p.4): the status-code table reads `201 - - OK`. The same page's response section is
headed `Success (200):`, so **the document contradicts itself on the same page.** The 7-page
Interface Control Guide also says 201.

**Actual:** `200 OK`, with the documented `{"token": ...}` body.

**Impact.** A client asserting the documented 201 rejects every successful login. (The vendor's own
example script sidesteps this by never checking the status code.)

**Request.** Correct the table, and reconcile it with the `Success (200):` heading above it.

*Not tested:* `POST /axapi/refresh`, documented as 201 on p.5 with **no response body specified at
all**; and `POST /axapi/logout`, documented 200.

---

## C1. Field types across `fs/streams` and `fs/files` do not match the documentation

**Severity C.** Documented types are from pp.72–76. Actual values are verbatim from the appliance.

| Field | Documented | Actual | Example |
|---|---|---|---|
| `time_start` | `integer` | **string** | `"1781795842"` |
| `time_end` | `integer` | **string** | `"1781799442"` |
| `epoch` | `integer` | **string** | `"1777907484"` |
| `seqnum` | `integer` | **string** | `"1083"` |
| `permissions` | `integer` | **string** | `"444"` (files) / `"0444"` (streams) |
| `size` | `integer` | integer ✓ | `671923900` |
| `is_directory` | *undocumented* | boolean | `true` |

Two further notes:

- **`permissions` is formatted inconsistently between the two endpoints** — `"0444"` (4 digits,
  leading zero) from `fs/streams`, `"444"` (3 digits) from `fs/files`.
- **`is_directory` is returned but appears nowhere in the documentation.**

---

## C2. `time_start`/`time_end` units are undocumented (they are unix epoch **seconds**)

**Severity C.** The documentation gives the type (`integer`) but never the unit or epoch. This is
the single most important undocumented fact in the filesystem API: a client that guesses
milliseconds or nanoseconds is wrong by three or six orders of magnitude.

**Actual:** unix epoch **seconds**. `"time_start": "1781795842"` = 2026-06-18 15:17:24 UTC, which
matches the same record's own `date` field (`"Jun 18 15:17"`) — that internal cross-check is what
confirmed it.

Note this also means the filesystem API's second-resolution timestamps and `/axapi/pcap`'s
sub-second `start_time`/`end_time` are in **different resolutions**, which the docs never mention,
though the two are obviously meant to be used together.

**Request.** State the unit and epoch in the field description.

---

## C3. Stream directory names are `streamNN`, but `stream_ids` takes bare integers

**Severity C.** `GET /axapi/fs/streams` returns names `stream00` … `stream11`. `GET /axapi/pcap`'s
`stream_ids` parameter takes bare integers and ranges (`"0-11"`). The documentation never states
the relationship between the two.

**Apparent mapping:** `streamNN` ↔ `N`. `stream_ids=0` returned packets falling inside a window
derived from `stream00`'s file listing, which is consistent with that mapping but does not by itself
exclude others — we did not extract from each stream individually to prove the correspondence.

**Request.** Document the mapping, and consider returning a numeric `stream_id` field on each
directory record so clients need not parse names.

---

## C4. Directory records carry a nonsensical `time_start`

**Severity C.** In `GET /axapi/fs/streams`, directory entries return `"time_start": "4000000h"` —
a string with an `h` suffix, in a field documented as `integer`, on a record where a capture
timestamp has no meaning.

**Request.** Omit the field on directory records rather than emitting a placeholder.

---

## D1. `start_time`'s description is a copy-paste of `end_time`'s

**Severity D — documentation only.** Both PDFs describe the `start_time` query parameter of
`GET /axapi/pcap` as *"Optional sub-second epoch time for **end** of extract"* — identical to the
text for `end_time`. The field's meaning isn't genuinely in doubt, but neither document actually
says what it does.

---

## D2. `stream_ids` grammar appears only in the 7-page guide, not the 84-page reference

**Severity D.** The REST API PDF (p.58) gives only the default value `"0-11"` and never states the
accepted syntax. The full grammar — `n[,n][,n-m][,n..m]`, `n` in `0`–`4294967296`, with **both `-`
and `..`** valid as range separators — appears only in a source comment in the Interface Control
Guide's example script.

Similarly, the `filter` parameter is described as "Berkeley Packet Filter or community ID" in the
REST doc, while the Interface Control Guide's example also documents a tuple form
(`source_ip,dest_ip,source_port,dest_port,protocol`). **Neither document lists all three accepted
forms.**

---

## D3. `GET /axapi/guidextract/...` is documented as `DELETE` but described as a getter

**Severity D.** Pages 54–55 document "Get Extract by start time, end time, and username" and "Get
Extract by GUID" with **`Method: DELETE`**. Either the verb or the description is wrong. We did not
call these endpoints, precisely because the safe reading of an ambiguous `DELETE` is "don't."

---

## Confirmed working as documented

Recorded so the report isn't read as wholesale criticism — these behaved exactly as specified:

- `GET /axapi/pcap` returns a valid classic pcap (nanosecond-resolution, linktype 1) streamed as
  the response body.
- The `X-Task-GUID` response header is present and populated.
- `ctid` is accepted.
- Sub-second `start_time` with 9-digit precision is accepted and honored.
- JWT bearer auth over TLS works as described.
- Extraction throughput was good: ~2.9 MB / 7,058 packets for a 60 s window in ~3.2 s.

## Not tested

`/axapi/task/*`, `/axapi/extract` (the async path),
`/axapi/minute|hour|day`, `/axapi/system/*`, `/axapi/apikeys`, the `archive=`/`servers=` parameters
of `/axapi/pcap`, 418 (no-data) behavior, and the single-stream `fs/streams?stream=<s>` form. Their
documentation may contain further discrepancies.

`POST /axapi/logout` **was** exercised (see S1). `POST /axapi/refresh` was exercised indirectly: a
401 on `GET /axapi/fs/streams` with a revoked token triggered the client's refresh-then-re-login
recovery, which returned a new working token and a usable response — so the recovery path is
functional. Natural token **expiry** was measured too — 15 minutes, sliding — and is likewise not
enforced on `/axapi/pcap` (S1).

## One environment note, not a defect

The appliance's TLS certificate is self-signed with **no Subject Alternative Name** (`CN=<appliance hostname, redacted>`
only). Python has ignored `CN` for hostname verification since 3.7 (PEP 476 / RFC 6125), so
certificate verification **cannot** succeed against this appliance under any trust anchor — pinning
the certificate as a CA doesn't help. Clients are forced to disable verification entirely, which is
presumably why the vendor's own example script hardcodes `verify=False`. Adding a SAN (IP and/or
DNS name) would let customers pin the certificate and verify it properly. This may be a per-deployment
configuration rather than a product default.
