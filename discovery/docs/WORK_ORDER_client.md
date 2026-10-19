# Work Order — WO-2: `client` — auth, session, and the only module that imports `requests`

> **Genre:** work order (see `docs/CONVENTIONS.md` §3) — an execution ticket for the `/dev` Pipeline. Concrete files, concrete gates, time-bounded. Mark it **DONE** in place when its run lands; don't delete it.
>
> **Status:** ✅ DONE (CHANGELOG 2026-08-04 — "`xtract` iteration-1 build complete")
> **Authority for the decisions behind it:** CHANGELOG 2026-08-04 — `xtract` design ratified; iteration-1 scope and work orders

## 1. Goal

`xtract/client.py` can authenticate to an `axapi` host by API key or by username/password, issue
read-only requests with correct parameter encoding, map `axapi` status codes to typed exceptions,
and guarantee that no credential can reach a log line, a traceback, or `argv`. It is the only module
in the package that imports `requests`. After this lands, every later work order talks to CorpAPCN
through one seam that is fully mockable.

**Not a goal:** any endpoint-specific logic. `client.py` knows about auth, transport, retries and
status mapping; it does not know what a stream or a window is. Endpoint wrappers live in
`catalog.py` and `extract.py`.

## 2. Build — phases, in order

### Phase 1 — The credential type

- **Files:** `xtract/auth.py` (new), `tests/test_auth.py` (new)
- **Behavior:**
  - A `Credential` type wrapping either an API key or a username/password pair. Its `__repr__`,
    `__str__` and `__format__` all return `Bearer ****`. The raw value is reachable only via one
    explicitly named accessor used at header-construction time — so a traceback, an f-string, a
    `logging` call or a `pprint` of a config object cannot leak it even by accident.
  - Resolution order, first hit wins: `--api-key-file <path>` → `XTRACT_API_KEY` env →
    `~/.config/xtract/config.toml` profile → `getpass` prompt (**only** when stdin is a TTY) →
    raise, with an error naming every location searched.
  - **No `--password` or `--api-key` flag exists.** A secret in `argv` is in `ps` output and shell
    history for every user on the box. Flags take *paths*, never values.
  - Credential files are refused if group- or world-readable, the same check `ssh` performs on a
    private key. The error states the required mode.
  - Config is profile-keyed (`[profile.<name>]` with `host`, `api_key_file` or `user`) plus an
    optional `default_profile`.
- **Tests:**
  - `repr()`/`str()`/`"{}".format()`/`f"{cred}"` all yield `Bearer ****`; a test asserts the raw
    secret string appears in no formatting output.
  - Precedence table: each source wins over the ones below it.
  - A 0644 key file is rejected; 0600 is accepted (use `tmp_path`, skip on non-POSIX).
  - No-TTY + no credential → typed error, no prompt, no hang.

### Phase 2 — Session and request plumbing

- **Files:** `xtract/client.py` (new), `tests/test_client.py` (new)
- **Behavior:**
  - `Client(host, credential, *, verify=True, ca_bundle=None, timeout=...)` holding a
    `requests.Session` with the bearer header applied once.
  - **All query parameters go through `params=`.** Never string-formatted into the URL. A test
    proves a filter containing spaces, parens and quotes survives round-trip encoding — this is the
    bug in the vendor's example script, and it must not be reintroduced.
  - TLS: verification on by default; `ca_bundle` sets the CA path; `verify=False` emits a loud
    warning stating that a bearer credential is being sent over an unverified channel.
  - Streaming reads (`stream=True`, chunked iteration) are supported for binary bodies. The client
    never materializes a response body in memory for the pcap path.
- **Tests:** filter-encoding round trip; bearer header present on every request; `ca_bundle`
  threaded through; `verify=False` warns exactly once.

### Phase 3 — Auth lifecycle

- **Files:** `xtract/client.py`, `tests/test_client.py`
- **Behavior:**
  - API-key mode: no login, no logout. The key is the bearer value.
  - JWT mode: `POST /axapi/login` → token. **Success is 201, not 200** — asserting 200 rejects every
    successful login. On `401` mid-run, attempt `POST /axapi/refresh` once, then one re-login, then
    fail with a typed error.
  - `POST /axapi/logout` runs in a `finally` / context-manager exit, including on exception and on
    SIGINT. A crashed run still gives its token back.
  - Tokens are **never written to disk**.
- **Tests:** 201 accepted, 200 from login treated as anomalous; refresh-then-retry on 401; logout
  fires on the exception path (assert on a mock, not on a happy-path run); no filesystem write
  occurs in JWT mode.

### Phase 4 — Status mapping and retries

- **Files:** `xtract/client.py`, `xtract/errors.py` (new), `tests/test_client.py`
- **Behavior:**
  - Typed exceptions per documented code: `401` unauthorized, `403` forbidden, `404` not found,
    `415` invalid payload, `418` **`NoDataError`**, `425` not complete, `500` server error.
    `418` is a distinct type precisely so callers can treat it as an outcome rather than a failure.
  - Parse the documented error envelope (`{code, message, errors:[{domain, reason, message,
    instance}]}`) into the exception message when present; degrade gracefully when it isn't.
  - Retry with exponential backoff **only** on connection errors, timeouts, and `500`. Never retry
    `4xx` — including `418`, which is a stable answer, not a transient one.
  - Retry budget and backoff are constructor parameters with conservative defaults.
- **Tests:** each status → its exception type; `418` is not retried; `500` is retried to the budget
  then raises; a malformed error body doesn't mask the status code.

### Phase 5 — API-key hygiene commands

- **Files:** `xtract/client.py`, `tests/test_client.py`
- **Behavior:** `GET /axapi/apikeys` (list; supports `include_revoked`) and
  `DELETE /axapi/apikeys/<key_id>` (revoke). Listing shows prefixes only — the API never returns
  full keys after creation, and neither do we.
- **Tests:** list parses the documented shape; revoke issues the right method and path.

## 3. Boundaries — explicitly OUT of scope

- **No `create-key`.** Minting long-lived credentials is a different risk class from reading capture
  data; creation happens in the UI. Revocation is in scope because it's the emergency path.
- **No mutating endpoint of any kind** — not `POST /axapi/clear`, not `DELETE /axapi/fs/*`, not
  service control, not user/group/encryption management. Do not add "just in case" wrappers.
- No stream, window, or pcap logic. That's WO-3 and WO-5.
- No token caching to disk, however tempting the startup saving.
- Anything that would trip the risk gate — surface it as `DECISION NEEDED:` instead of doing it.

## 4. Validation

1. **Unit:** `pytest tests/test_auth.py tests/test_client.py` green, with HTTP mocked at the
   `requests` boundary. **No test may open a socket.**
2. **Hermetic tier:** `scripts/verify_tier.sh` clean
3. **Live (gated):** deferred to `WORK_ORDER_live_probe.md` — do not run this against CorpAPCN as
   part of this work order.
4. **Docs:** CHANGELOG entry (Authority-marked), `docs/AXAPI_CONTRACT.md` cross-checked against what
   was implemented, `doc-stats --check` clean, `LES-NNN` for anything durable this surfaces

## 5. Depends on / sequence

- **Depends on:** WO-0 (the contract this implements).
- **Unblocks:** the live probe, WO-3 (`catalog`), WO-5 (`extract`).
