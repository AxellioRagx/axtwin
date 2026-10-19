# Work Order — H-2: `zeek` — a pinned, containerized Zeek runner

> **Genre:** work order (see `docs/CONVENTIONS.md` §3) — an execution ticket for the `/dev` Pipeline. Concrete files, concrete gates, time-bounded. Mark it **DONE** in place when its run lands; don't delete it.
>
> **Status:** ✅ **DONE.** Built and landed in commit `62013a0` (CHANGELOG 2026-08-06 "harvest
> foundations: `timespec`, containerized Zeek runner, disk governor"). §3 Phase 3 below is
> **superseded in place** — see the annotation there and CHANGELOG 2026-08-06 "Zeek pinned to 8.0.9
> with `local` loaded".
> **Authority for the decisions behind it:** `docs/PLAN_metadata_harvester.md` §5, §6

## 1. Goal

`harvest` can turn one pcap into one directory of Zeek logs, reproducibly, recording everything the
archive manifest will later need to declare: the exact Zeek version, which log types were produced,
how long it took, and what went wrong if anything did. After this lands, Zeek is a dependency the
pipeline *drives*, not one it assumes.

**Not a goal:** parsing Zeek logs, converting to Parquet (H-5), scheduling or concurrency (H-4), or
writing any archive.

## 2. Context that shapes this

- **Zeek is not installed on this host and is not in the configured repos.** `podman` is installed.
  There is no root access assumed.
- **The archive manifest must record the Zeek version**, and Zeek's log set shifts between releases.
  Pinning is therefore a requirement, not a preference.
- **`aximprint` parses exactly 16 log types** — `conn`, `dhcp`, `dns`, `kerberos`, `known_hosts`,
  `ssl`, `dce_rpc`, `ldap_search`, `smb_mapping`, `smb_files`, `ntp`, `known_services`, `notice`,
  `weird`, `capture_loss`, `stats` — and explicitly skips `telemetry`, `loaded_scripts`,
  `packet_filter`, `files`. Anything else it reports as unrecognized.
- This host is SELinux-enforcing (Rocky), so bind mounts need a `:Z`/`:z` label or the container
  cannot read the pcap.

## 3. Build — phases, in order

### Phase 1 — The runner interface and a fake

- **Files:** `xtract/harvest/zeek.py` (new), `tests/test_zeek_runner.py` (new)
- **Behavior:**
  - `ZeekRunner` protocol: `run(pcap: Path, out_dir: Path) -> ZeekResult`.
  - `ZeekResult` (frozen): `version: str`, `logs: dict[str, Path]` (log stem → file),
    `duration_s: float`, `returncode: int`, `stderr_tail: str`, `bytes_in: int`.
  - `FakeZeekRunner` writing a small, valid Zeek TSV `conn.log` (full header: `#separator`,
    `#set_separator`, `#empty_field`, `#unset_field`, `#path`, `#fields`, `#types`) plus a
    configurable set of other stems.
  - **The fake is production code, not a test helper**, and lives in `xtract/harvest/zeek.py`:
    every later work order (H-4 the pipeline, H-5 the converter, H-7 resume) has to be testable on
    a host where Zeek does not exist, which is this host today.
- **Tests:** the fake produces a file the reference parser accepts (compare its header shape against
  `/home/rocky/aximprint/metadata/zeek/conn.log`); `ZeekResult` carries every field.

### Phase 2 — The podman runner

- **Files:** `xtract/harvest/zeek.py`, `tests/test_zeek_runner.py`
- **Behavior:**
  - `PodmanZeekRunner(image: str, ...)` — image **pinned by tag or digest**, no `:latest` default.
    A `:latest` image silently changes the log schema under a growing archive; refuse it explicitly
    with a message saying why.
  - Invocation shape:
    `podman run --rm -v <pcap>:/in.pcap:ro,Z -v <out_dir>:/out:Z -w /out <image> zeek -r /in.pcap [scripts...]`
  - `--userns=keep-id` (or equivalent) so output files are owned by the invoking user, not root.
    Verify this rather than assuming — root-owned logs would break the reaper in H-3.
  - Capture the Zeek version by running `zeek --version` **in the same pinned image**, once, and
    cache it on the runner. It goes in the manifest; deriving it from the image tag is not the same
    thing and would be wrong for a digest-pinned image.
  - Enumerate produced logs by listing `out_dir` after the run — never by assuming a set.
  - Timeout per pcap, configurable; on timeout, kill the container and return a failed `ZeekResult`
    rather than raising, so the pipeline can record and continue.
  - Non-zero exit is a `ZeekResult` with `returncode` and `stderr_tail`, not an exception. Zeek
    exits non-zero on some malformed captures and the pipeline must record-and-continue, not abort a
    multi-day run.
- **Tests:** the constructed argv is asserted exactly (mock `subprocess.run`), including `:Z` labels,
  `--rm`, and the pinned image; `:latest` is refused; a non-zero exit yields a failed result rather
  than raising; timeout yields a failed result.

### Phase 3 — Log-set policy

> **⚠️ SUPERSEDED 2026-08-06 — see CHANGELOG "Zeek pinned to 8.0.9 with `local` loaded".** The
> instruction below ("defaulting to Zeek's default script set") was built as written, then measured
> against the pinned image and found wrong: Zeek's *bare* default is itself a lossy trim relative to
> `aximprint`'s 16 parsed log types (5 of 16 produced, vs. 10 of 16 with `local` loaded) — and the
> five gained include the only sources of `SensorHealthEvent`. The reasoning that motivated this
> phase ("don't silently trim the evidence tier") turned out to argue *for* loading `local`, not
> against it. Kept below, not rewritten, per this project's supersede-in-place discipline — the
> actual shipped behavior is `DEFAULT_SCRIPT_POLICY` = `local` loaded, not "Zeek's default script
> set".

- **Files:** `xtract/harvest/zeek.py`, `tests/test_zeek_runner.py`
- **Behavior:**
  - A named, explicit policy for which Zeek scripts to load, defaulting to Zeek's default script
    set. **Do not silently trim to `aximprint`'s 16 types** — the cold tier is the evidence tier,
    and discarding a log at generation time makes it unrecoverable, which defeats the point of
    having a raw tier at all (`docs/PLAN_metadata_harvester.md` §3).
  - Instead, `ZeekResult.logs` reports what was produced, and the caller decides what to carry into
    the hot tier. Record, in the result, which produced stems fall outside `aximprint`'s parsed set,
    so a Zeek upgrade that starts emitting a new log type is visible rather than silent.
- **Tests:** the unrecognized-stem report fires for a stem outside the 16 + 4; an all-recognized run
  reports none.

### Phase 4 — Availability probe

- **Files:** `xtract/harvest/zeek.py`, `tests/test_zeek_runner.py`
- **Behavior:** `probe()` returning a structured "can this runner actually run?" answer — podman
  present, image pullable/present, `zeek --version` succeeding — so `harvest` can fail at startup
  with a clear message instead of on the first pcap after an hour of extraction.
- **Tests:** probe reports each failure mode distinctly (mocked).

## 4. Boundaries — explicitly OUT of scope

- **Do not `podman pull` as part of the automated build or tests.** Pulling an image is a gated,
  network-touching action; the orchestrator will do it once, deliberately. Every test must pass on a
  host with no Zeek and no image, using `FakeZeekRunner` and mocked `subprocess`.
- No log parsing, no Parquet, no archive, no concurrency, no disk accounting.
- No native-install runner. If one is ever wanted it implements the same protocol; the point of
  Phase 1 is that the choice stays reversible.
- Anything that would trip the risk gate — surface it as `DECISION NEEDED:` instead of doing it.

## 5. Validation

1. **Unit:** `pytest tests/test_zeek_runner.py` green, `subprocess` mocked. **No test may invoke
   podman or open a socket** (`tests/conftest.py` enforces the socket half).
2. **Hermetic tier:** `bash scripts/verify_tier.sh` clean (394 tests currently pass).
3. **Live (gated, NOT part of this work order):** pulling the pinned image and running one real pcap
   through it. The orchestrator runs that separately, with the user's go-ahead.
4. **Docs:** CHANGELOG entry (Authority-marked), `doc-stats --check` clean, `LES-NNN` for anything
   durable, `GAP-NNN` for anything left unproven (the real-podman path will be unproven when this
   lands — say so).

## 6. Depends on / sequence

- **Depends on:** nothing in-tree.
- **Unblocks:** H-4 (pipeline), H-5 (converter — it needs real Zeek output shapes to build against).
