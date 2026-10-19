"""SQLite-backed state store per docs/PLANNER.md §7 (minimal-but-real subset).

Schema (subset of §7.2):

  deploys         one row per `axrange deploy` invocation
  leases          one row per scenario being deployed (§6.2 single-writer gate)
  resource_locks  one row per held correctness-resource lock (§8.4)
  ops             one row per op instance in a deploy
  op_attempts     many per op (typically 1, up to max_attempts)

Atomicity (PLANNER.md §7.3):

  - Op completion is atomic with output persistence: a single transaction
    updates `ops.status='completed'`, `ops.completed_at`, and `ops.outputs`.
  - Plan persistence (writing every row of `ops` at deploy start) is one
    transaction.
  - Lease takeover and resource-lock acquisition are each a single
    check-then-write transaction (§7.3) — no partial-hold window.

The `leases` + `resource_locks` tables implement the cross-process
correctness layer ratified 2026-06-03 (CHANGELOG "cross-process resource
locking"; PLANNER §6.2/§7.2/§8.4):

  - The per-scenario **lease** is single-writer-per-scenario: a second
    concurrent deploy of the *same* scenario is refused while a valid lease
    exists; a crashed holder's lease is reclaimed after `expires_at`.
  - **Resource locks** generalize the same machinery to the "correctness"
    concurrency categories (`ovs:<node>`, `vm:<vmid>`, `gretap:<sensor>`) so
    they serialize across *different* concurrent deploy processes — the
    in-process semaphores of §8.1–§8.3 cannot, since each deploy is its own
    process. Throughput categories stay per-process and are not stored here.

The slice still omits re-plan / plan-diff machinery (`scenario_version` is
recorded for diffing later) and the parallel intra-deploy executor.

The store doubles as the `axrange status` surface (§7.4). Inspection via the
`sqlite3` CLI is intended; no application-level cache.
"""
from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Iterator


# Default lock/lease lifetime, matching the §6.2 5-minute expiry. A holder
# heartbeats every minute (see axrange.planner.locks.Heartbeat) to keep its
# rows alive; a crashed holder's rows fall out after this window.
DEFAULT_LEASE_TTL_S = 300.0

# Schema version, stamped via `PRAGMA user_version`. Bump this whenever the
# DDL changes shape (not just adds an `IF NOT EXISTS` table/index) so a state
# DB written by an older version fails loudly instead of silently limping
# along against a stale schema (`CREATE TABLE IF NOT EXISTS` is a no-op
# against an existing table — it will never repair one). v2: the `ops` table
# PRIMARY KEY became composite `(deploy_id, op_id)` (was `op_id` alone),
# fixing the `--fresh`/destroy deploy-aliasing bug (CHANGELOG 2026-07-07) —
# two deploys of the same plan (identical op_ids) now hold independent rows.
# v3: `deploys` gained a `tier` column (`docs/design/05-o2-surface.md`)
# recording the resource-ownership tier tag (`"te"`/`"o2"`) a deploy's
# resources are named under — `planner/teardown.py` reads it to pick the
# right tier's positive shape-guards + protect-guard semantics for a
# `destroy`/`axrange o2 down` call.
SCHEMA_VERSION = 3


class StateStoreSchemaError(RuntimeError):
    """Raised when an existing `state.db` predates the current schema.

    `state.db` is gitignored and carries no data-migration path (per the
    2026-07-07 review that introduced the composite `ops` PK) — the fix is to
    stop any running deploys and delete/move the file so a fresh one is
    created on next open.
    """


def default_process_info() -> dict[str, Any]:
    """host + pid stamped onto a lease / lock row for crash diagnosis (§7.2)."""
    return {"host": socket.gethostname(), "pid": os.getpid()}


# ---------------------------------------------------------------------------
# Status enums (mirror docs/PLANNER.md §7.2)
# ---------------------------------------------------------------------------


class DeployStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    # Teardown terminal (`axrange destroy`, docs/design/11-api-ui.md): every
    # axr-created resource for this deploy was removed in reverse-of-create order.
    DESTROYED = "destroyed"


class OpStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    HALTED = "halted-due-to-upstream"
    # Set by `axrange destroy` once the op's created resource is torn down.
    DESTROYED = "destroyed"


class AttemptResult(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Row types
# ---------------------------------------------------------------------------


@dataclass
class DeployRow:
    deploy_id: str
    scenario_id: str
    scenario_version: int | None
    scenario_schema_version: int | None
    plan_id: str
    ir_hash: str
    status: DeployStatus
    placement_json: str
    started_at: float
    updated_at: float
    completed_at: float | None
    # Resource-ownership tier tag (`docs/CONVENTIONS.md` §2). `"te"` for every
    # normal scenario deploy (default); `"o2"` for the standing surface deploy
    # `axrange.o2control` stands up (`docs/design/05-o2-surface.md`).
    # `planner/teardown.py` reads this to pick the right tier's shape-guards.
    tier: str = "te"


@dataclass
class OpRow:
    op_id: str
    deploy_id: str
    op_name: str
    op_catalog_id: str
    kind: str
    scope_json: str
    inputs_resolved_json: str
    inputs_from_outputs_json: str
    outputs_json: str | None
    cleanup_strategy: str | None
    idempotency_class: str | None
    status: OpStatus
    started_at: float | None
    completed_at: float | None


@dataclass
class AttemptRow:
    attempt_id: int
    deploy_id: str
    op_id: str
    started_at: float
    ended_at: float | None
    result: AttemptResult | None
    error_message: str | None
    error_class: str | None


@dataclass
class LeaseRow:
    scenario_id: str
    deploy_id: str
    process_info_json: str
    acquired_at: float
    expires_at: float


@dataclass
class ResourceLockRow:
    resource_key: str
    deploy_id: str
    op_id: str
    process_info_json: str
    acquired_at: float
    expires_at: float


@dataclass
class LeaseResult:
    """Outcome of an `acquire_lease` attempt.

    `acquired` is False only when a *valid* (non-expired) lease is already
    held — `held_by_deploy_id` / `expires_at` then identify the incumbent so
    the CLI can print an actionable refusal.
    """

    acquired: bool
    held_by_deploy_id: str | None = None
    expires_at: float | None = None


# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------


_SCHEMA = """
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS deploys (
    deploy_id              TEXT PRIMARY KEY,
    scenario_id            TEXT NOT NULL,
    scenario_version       INTEGER,
    scenario_schema_version INTEGER,
    plan_id                TEXT NOT NULL,
    ir_hash                TEXT NOT NULL,
    status                 TEXT NOT NULL,
    placement_json         TEXT NOT NULL DEFAULT '{}',
    started_at             REAL NOT NULL,
    updated_at             REAL NOT NULL,
    completed_at           REAL,
    -- v3: resource-ownership tier tag (`docs/CONVENTIONS.md` §2) — `te` for
    -- every normal deploy, `o2` for the standing O2 surface deploy
    -- (`docs/design/05-o2-surface.md`). See SCHEMA_VERSION's docstring.
    tier                   TEXT NOT NULL DEFAULT 'te'
);

CREATE INDEX IF NOT EXISTS deploys_by_scenario ON deploys(scenario_id, started_at DESC);
-- Hot discovery-path index for `latest_deploy_for_tier` (`axrange.o2control`'s
-- "find the standing o2 surface" query) — the o2 surface is by design the
-- OLDEST persistent deploy row (survives every TE destroy) while `te` rows
-- accumulate monotonically, so a `started_at DESC LIMIT N` scan over ALL
-- deploys can blow past N before ever reaching it. Filter by tier first.
CREATE INDEX IF NOT EXISTS deploys_by_tier ON deploys(tier, started_at DESC);

CREATE TABLE IF NOT EXISTS leases (
    scenario_id            TEXT PRIMARY KEY,
    deploy_id              TEXT NOT NULL,
    process_info_json      TEXT NOT NULL DEFAULT '{}',
    acquired_at            REAL NOT NULL,
    expires_at             REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS resource_locks (
    resource_key           TEXT PRIMARY KEY,
    deploy_id              TEXT NOT NULL,
    op_id                  TEXT NOT NULL,
    process_info_json      TEXT NOT NULL DEFAULT '{}',
    acquired_at            REAL NOT NULL,
    expires_at             REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS resource_locks_by_deploy ON resource_locks(deploy_id);

-- `op_id` is deterministic from (plan_id, op-type, scope) — see dag.py's
-- `_op_id` — so two deploys of the SAME plan (a `--fresh` redeploy, or a
-- destroy that outlives the original deploy row) produce IDENTICAL op_id
-- strings. The PK is composite `(deploy_id, op_id)` so those two deploys
-- hold INDEPENDENT rows instead of aliasing onto one (CHANGELOG 2026-07-07;
-- was the root cause of `--fresh` silently skipping every op and a
-- subsequent `axrange destroy` sweeping zero resources).
CREATE TABLE IF NOT EXISTS ops (
    op_id                  TEXT NOT NULL,
    deploy_id              TEXT NOT NULL REFERENCES deploys(deploy_id),
    op_name                TEXT NOT NULL,
    op_catalog_id          TEXT NOT NULL,
    kind                   TEXT NOT NULL,
    scope_json             TEXT NOT NULL,
    inputs_resolved_json   TEXT NOT NULL DEFAULT '{}',
    inputs_from_outputs_json TEXT NOT NULL DEFAULT '{}',
    outputs_json           TEXT,
    cleanup_strategy       TEXT,
    idempotency_class      TEXT,
    status                 TEXT NOT NULL,
    started_at             REAL,
    completed_at           REAL,
    PRIMARY KEY (deploy_id, op_id)
);

CREATE INDEX IF NOT EXISTS ops_by_deploy ON ops(deploy_id, op_name);
-- Non-unique: op_id alone is no longer a key, but callers that only have an
-- op_id (rare — everything deploy-scoped should prefer ops_by_deploy) still
-- benefit from an index rather than a full scan.
CREATE INDEX IF NOT EXISTS ops_by_op_id ON ops(op_id);

-- deploy_id is carried alongside op_id (rather than relying on op_id alone)
-- for the same reason as `ops` above — two deploys of one plan must not
-- commingle attempt history under one op_id.
CREATE TABLE IF NOT EXISTS op_attempts (
    attempt_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    deploy_id              TEXT NOT NULL,
    op_id                  TEXT NOT NULL,
    started_at             REAL NOT NULL,
    ended_at               REAL,
    result                 TEXT,
    error_message          TEXT,
    error_class            TEXT,
    FOREIGN KEY (deploy_id, op_id) REFERENCES ops(deploy_id, op_id)
);

CREATE INDEX IF NOT EXISTS attempts_by_op ON op_attempts(deploy_id, op_id, started_at);

PRAGMA user_version=__SCHEMA_VERSION__;
COMMIT;
""".replace("__SCHEMA_VERSION__", str(SCHEMA_VERSION))


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


#: Every table/index `_SCHEMA` declares -- a writable open whose
#: `user_version` already matches AND that has all of these skips the DDL
#: script entirely (it is a write transaction; re-running it on every open
#: bumped the change counter and competed with deploys for the write lock).
_SCHEMA_OBJECTS = frozenset(
    re.findall(r"CREATE (?:TABLE|INDEX) IF NOT EXISTS (\w+)", _SCHEMA)
)

#: Context-local "open read-only" switch (`readonly_opens()`): the API's read
#: routes set it so every `StateStore(...)` they -- or the engine code they
#: call -- construct is a read-only connection, without threading a flag
#: through every read helper.
_READONLY: ContextVar[bool] = ContextVar("axrange_state_readonly", default=False)


@contextmanager
def readonly_opens() -> Iterator[None]:
    """Within the block, `StateStore(path)` opens READ-ONLY: `mode=ro`, no
    DDL, no `user_version` stamp, no `journal_mode` change, `query_only` --
    and a MISSING file is never created (an empty in-memory store stands in,
    so a read of a store that does not exist yet answers "nothing recorded";
    a 0-byte or schema-less file is treated the same way).

    NEVER `immutable=1` (LES-359): it tells SQLite the file cannot change,
    so it takes no locks and ignores the WAL -- a writer committing and
    closing (checkpointing) under a scanning reader then yields corrupt
    reads and wrong row counts. "No -wal right now" proves nothing about
    the next millisecond. `mode=ro` takes proper read locks; the price is
    that SQLite may leave `-wal`/`-shm` files beside a WAL-mode db, which
    is accepted -- they carry no data a reader wrote."""
    token = _READONLY.set(True)
    try:
        yield
    finally:
        _READONLY.reset(token)


class StateStore:
    """One installation -> one StateStore -> one SQLite file.

    Thread-safety: SQLite's own connection-per-thread model. The slice's
    sequential executor uses one connection.

    `readonly=True` (or inside `readonly_opens()`): see `readonly_opens`.
    Any write through a read-only store fails loud (`attempt to write a
    readonly database`), never silently.
    """

    def __init__(self, db_path: str | Path, *, readonly: bool | None = None) -> None:
        self.db_path = Path(db_path)
        self.readonly = _READONLY.get() if readonly is None else readonly
        if self.readonly:
            self._open_readonly()
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        # WAL lets a heartbeat connection (a second StateStore on the same
        # file, see locks.Heartbeat) renew leases/locks while the executor's
        # connection writes op state. busy_timeout absorbs the brief writer
        # contention between the two — these writes are sub-millisecond.
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._check_schema_version()
        if self._schema_complete():
            # Already at SCHEMA_VERSION with every table/index: nothing to
            # create or stamp -- do not open a write transaction for it.
            return
        # `_SCHEMA` wraps the table/index DDL AND the `PRAGMA user_version`
        # stamp in one explicit `BEGIN IMMEDIATE ... COMMIT` (2026-07-07 fix:
        # two processes racing to create a fresh shared state.db could
        # otherwise have process B observe A's committed `ops` table with
        # `user_version` still 0 -- a false `StateStoreSchemaError` against a
        # legitimately-fresh DB. `executescript()` auto-commits any pending
        # transaction *before* running, even with `isolation_level=None`, so
        # the BEGIN/COMMIT must be literal SQL text inside the script itself
        # for the whole thing to land as one atomic unit a concurrent opener
        # either sees entirely or not at all.)
        self._conn.executescript(_SCHEMA)

    def _schema_complete(self) -> bool:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            return False
        have = {
            r[0] for r in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"
            )
        }
        return _SCHEMA_OBJECTS <= have

    def _open_readonly(self) -> None:
        # Never create a missing store on a read: an empty in-memory store
        # answers "nothing recorded" instead.
        if not self.db_path.is_file() or self.db_path.stat().st_size == 0:
            self._open_empty()
            return
        # `mode=ro` + `query_only` + `busy_timeout`, and NOTHING else -- see
        # `readonly_opens` for why never `immutable` (LES-359).
        uri = self.db_path.resolve().as_uri() + "?mode=ro"
        self._conn = sqlite3.connect(uri, uri=True, isolation_level=None)
        self._conn.execute("PRAGMA query_only=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        has_ops = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ops'"
        ).fetchone()
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if has_ops is None and version == 0:
            # A schema-less file (never initialised): the missing-file case.
            self._conn.close()
            self._open_empty()
            return
        self._check_schema_version()

    def _open_empty(self) -> None:
        self._conn = sqlite3.connect(":memory:", isolation_level=None)
        self._conn.executescript(_SCHEMA)
        self._conn.execute("PRAGMA query_only=ON")

    def close(self) -> None:
        self._conn.close()

    def _check_schema_version(self) -> None:
        """Refuse to open a `state.db` written by an older, incompatible
        schema. `CREATE TABLE IF NOT EXISTS` is a no-op against an existing
        table, so simply re-running `_SCHEMA` would never repair a stale one
        — a version mismatch has to fail loudly instead.
        """
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            return
        if version == 0:
            has_ops_table = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ops'"
            ).fetchone()
            if has_ops_table is None:
                # Brand-new file — nothing to migrate.
                return
            raise StateStoreSchemaError(
                f"{self.db_path}: this state DB predates schema v{SCHEMA_VERSION} "
                "(composite `ops` PRIMARY KEY (deploy_id, op_id) — CHANGELOG "
                "2026-07-07) — re-init required. Stop any running deploys and "
                "delete/move this file; state.db is gitignored and carries no "
                "data-migration path."
            )
        raise StateStoreSchemaError(
            f"{self.db_path}: state DB schema version {version} != expected "
            f"{SCHEMA_VERSION} — re-init required (delete/move this file)."
        )

    # --- transaction helper -------------------------------------------------

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        cur = self._conn
        cur.execute("BEGIN IMMEDIATE")
        try:
            yield cur
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise

    # --- deploys ------------------------------------------------------------

    def create_deploy(
        self,
        *,
        deploy_id: str,
        scenario_id: str,
        scenario_version: int | None,
        scenario_schema_version: int | None,
        plan_id: str,
        ir_hash: str,
        placement: dict[str, str],
        tier: str = "te",
    ) -> DeployRow:
        # Fail at the point of the mistake (docs/WORK_ORDER_tier_split.md
        # review MUST-FIX #13): a bad `tier=` used to only surface later,
        # deep inside a naming producer or `planner/teardown.py` mid-sweep.
        # Lazy/function-local import (not module-level) -- `adapter_proxmox`
        # transitively imports `axrange.planner.executor`/`dag`, which are
        # THEMSELVES mid-initialization when `state.py` is first loaded
        # (`axrange/planner/__init__.py` loads `executor` before `state`,
        # and `executor.py` imports `state.py` directly) -- a module-level
        # import here would risk a real circular-import failure; deferring
        # it to call time (long after every module has finished loading)
        # sidesteps that entirely, same pattern this file's callers already
        # use for cross-package engine imports.
        from axrange.adapter_proxmox.naming import check_tier

        check_tier(tier)
        now = time.time()
        row = DeployRow(
            deploy_id=deploy_id,
            scenario_id=scenario_id,
            scenario_version=scenario_version,
            scenario_schema_version=scenario_schema_version,
            plan_id=plan_id,
            ir_hash=ir_hash,
            status=DeployStatus.RUNNING,
            placement_json=json.dumps(placement, sort_keys=True),
            started_at=now,
            updated_at=now,
            completed_at=None,
            tier=tier,
        )
        with self._tx() as cur:
            cur.execute(
                """
                INSERT INTO deploys (
                    deploy_id, scenario_id, scenario_version,
                    scenario_schema_version, plan_id, ir_hash, status,
                    placement_json, started_at, updated_at, completed_at, tier
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.deploy_id,
                    row.scenario_id,
                    row.scenario_version,
                    row.scenario_schema_version,
                    row.plan_id,
                    row.ir_hash,
                    row.status.value,
                    row.placement_json,
                    row.started_at,
                    row.updated_at,
                    row.completed_at,
                    row.tier,
                ),
            )
        return row

    def update_deploy_status(self, deploy_id: str, status: DeployStatus) -> None:
        now = time.time()
        completed = now if status != DeployStatus.RUNNING else None
        with self._tx() as cur:
            cur.execute(
                """
                UPDATE deploys
                   SET status = ?, updated_at = ?, completed_at = ?
                 WHERE deploy_id = ?
                """,
                (status.value, now, completed, deploy_id),
            )

    def update_deploy_generation(
        self, deploy_id: str, *, ir_hash: str, placement: dict[str, str],
    ) -> None:
        """Record a NEW `ir_hash`/`placement` for an EXISTING deploy row,
        WITHOUT touching `plan_id` (`docs/WORK_ORDER_twin_incremental_
        update.md` §3.4: "update the deploy row's ir_hash ... leave plan_id
        pinned. The deploys table needs no new column."). The only caller is
        an `--update`'s success tail: `plan_id` becomes the twin's STABLE
        IDENTITY across updates, `ir_hash` its CURRENT GENERATION — after
        this call `deploys.plan_id` no longer equals `derive_plan_id(
        scenario_id, ir_hash)`, by design (see `dag.derive_plan_id`'s
        docstring; `deploys_matching(scenario_id=...)` is how a caller finds
        this row again once plan_id has drifted).

        `placement` REPLACES the prior value wholesale (not merged) with the
        new plan's placement — it naturally excludes a removed host (the
        edited IR never places one) and includes an added one. Callers doing
        a scoped removal sweep (`planner.teardown.destroy_ops_scoped`) must
        read the OLD placement (still on the row) BEFORE calling this, since
        a removed host has no entry in the new placement being written here.
        """
        now = time.time()
        with self._tx() as cur:
            cur.execute(
                """
                UPDATE deploys
                   SET ir_hash = ?, placement_json = ?, updated_at = ?
                 WHERE deploy_id = ?
                """,
                (ir_hash, json.dumps(placement, sort_keys=True), now, deploy_id),
            )

    def get_deploy(self, deploy_id: str) -> DeployRow | None:
        cur = self._conn.execute(
            "SELECT * FROM deploys WHERE deploy_id = ?", (deploy_id,)
        )
        r = cur.fetchone()
        return _row_to_deploy(r) if r else None

    def latest_deploys(self, limit: int = 20) -> list[DeployRow]:
        # Ordered by `updated_at`, NOT `started_at` (code review finding,
        # docs/WORK_ORDER_cli_lifecycle.md): a resumed/revived deploy keeps
        # its ORIGINAL `started_at` (only `updated_at` moves), so ordering
        # by `started_at` ranks it BEHIND an unrelated deploy that merely
        # started more recently -- a bare "latest deploy" default (CLI or
        # `axrange/api/`) would then silently act on the wrong row. Live
        # repro (dev45): bare `te destroy` printed DESTROYED against a
        # stale deploy while the just-resumed one stayed live.
        # `COALESCE(updated_at, started_at)` is defensive insurance against
        # a NULL `updated_at` on an old row (the column is NOT NULL in the
        # current schema, so every row written by this code always has one).
        cur = self._conn.execute(
            "SELECT * FROM deploys ORDER BY COALESCE(updated_at, started_at) DESC LIMIT ?",
            (limit,),
        )
        return [_row_to_deploy(r) for r in cur.fetchall()]

    def latest_deploy_for_tier(
        self, tier: str, *, exclude_destroyed: bool = False,
    ) -> DeployRow | None:
        """Most recent deploy of a given resource-ownership tier (`"te"`/`"o2"`,
        `docs/CONVENTIONS.md` §2), or None.

        Purpose-built for `axrange.o2control`'s "find the standing O2
        surface" discovery: the surface deploy is BY DESIGN the oldest
        persistent row (it survives every `te` deploy+destroy), while `te`
        rows accumulate monotonically on a busy cluster. Scanning
        `latest_deploys(limit=N)` and filtering client-side can blow past N
        newer `te` rows before ever reaching the `o2` one — this queries the
        `tier` column directly (backed by the `deploys_by_tier` index) so
        discovery is correct regardless of how many other-tier deploys exist.

        Ordered by `updated_at` (see `latest_deploys`'s docstring for why
        `started_at` alone is unsafe for a resumed/revived row).

        `exclude_destroyed=True` skips `destroyed` rows entirely and returns
        the newest NON-terminal one instead -- used by a DESTROY verb's
        bare-default (`axrange o2 destroy`, code review finding,
        docs/WORK_ORDER_cli_lifecycle.md): `mark_destroyed` bumps
        `updated_at`, so a just-destroyed deploy is the top of the plain
        `updated_at` ordering -- without this, a bare destroy right after
        destroying an OLDER deploy would resolve to that tombstone (a safe
        but misleading no-op "DESTROYED" while a genuinely live, newer
        deploy of the same tier goes untouched). `o2 status`/`info`'s
        bare-default deliberately leaves this `False` — they WANT the
        tombstone on top, to inspect what you just destroyed.
        """
        clauses = ["tier = ?"]
        params: list[Any] = [tier]
        if exclude_destroyed:
            clauses.append("status != ?")
            params.append(DeployStatus.DESTROYED.value)
        where = " AND ".join(clauses)
        cur = self._conn.execute(
            f"SELECT * FROM deploys WHERE {where} "
            "ORDER BY COALESCE(updated_at, started_at) DESC LIMIT 1",
            tuple(params),
        )
        r = cur.fetchone()
        return _row_to_deploy(r) if r else None

    def deploys_matching(
        self,
        *,
        scenario_id: str | None = None,
        tier: str | None = None,
        plan_id_prefix: str | None = None,
        exclude_plan_id_prefix: str | None = None,
        exclude_destroyed: bool = False,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[DeployRow]:
        """General-purpose deploy listing: scenario/tier filter and/or a
        `plan_id` prefix filter (inclusive or exclusive), most-recent-first.
        `limit=None` returns every match (LES-053: correct discovery of a
        rare/singleton row needs an unbounded, filter-scoped query, not a
        capped recent-N scan). Filtering (incl. `status`/`exclude_destroyed`)
        happens IN SQL, before `LIMIT` is applied — a Python-side post-filter
        would silently under-return once destroyed rows (which keep getting
        newer `updated_at` stamps as they're torn down) crowd the most-recent
        page (`docs/WORK_ORDER_console_selector_and_stale_deploys.md`).

        Generalizes `latest_deploys`/`latest_deploy_for_tier` for a caller
        (`axrange.cli.main`'s `te`/`tap`-tier discovery,
        `axrange.o2control.taps`'s tap-plan discovery) that must distinguish
        a `tier="te"` deploy that is STRUCTURALLY a tap-plan playout (its
        `plan_id` always starts with `tap-`, `docs/design/06-taps-sensors.md`)
        from a genuine target-environment deploy that merely shares the
        same `tier` column value — `_VALID_TIERS` (`adapter_proxmox/
        naming.py`) has no separate `"tap"` deploy-level tier, so this is a
        `plan_id`-shape filter layered on top of the `tier` column, not a
        new tier value / schema change.

        `scenario_id` (`docs/WORK_ORDER_twin_incremental_update.md` §4.1) is
        backed by the `deploys_by_scenario` index — the query an `--update`
        deploy needs to resolve "the standing deploy for this scenario" once
        `plan_id` has been pinned away from a fresh `derive_plan_id(
        scenario_id, ir_hash)` result by a prior update (§3.4): `plan_id`
        alone can no longer find it, but `scenario_id` still can, since an
        update never changes it. Do NOT scan `latest_deploys` for this —
        that is an unbounded, unfiltered read that degrades as the DB grows
        and can silently pick a wrong/unrelated row before ever reaching a
        rare match (LES-053's same "capped scan" trap, applied here).

        `exclude_destroyed=True` -- see `latest_deploy_for_tier`'s docstring
        (the SAME "prefer newest non-terminal" need, generalized here for
        `te`/`tap`-tier discovery: `axrange te destroy`/`axrange tap
        destroy`'s bare-default).
        """
        clauses: list[str] = []
        params: list[Any] = []
        if scenario_id is not None:
            clauses.append("scenario_id = ?")
            params.append(scenario_id)
        if tier is not None:
            clauses.append("tier = ?")
            params.append(tier)
        if plan_id_prefix is not None:
            clauses.append("plan_id LIKE ? ESCAPE '\\'")
            params.append(_like_prefix(plan_id_prefix))
        if exclude_plan_id_prefix is not None:
            clauses.append("plan_id NOT LIKE ? ESCAPE '\\'")
            params.append(_like_prefix(exclude_plan_id_prefix))
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if exclude_destroyed:
            clauses.append("status != ?")
            params.append(DeployStatus.DESTROYED.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        # Ordered by `updated_at`, not `started_at` -- see `latest_deploys`'s
        # docstring (a resumed/revived row keeps its ORIGINAL `started_at`).
        sql = f"SELECT * FROM deploys {where} ORDER BY COALESCE(updated_at, started_at) DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        cur = self._conn.execute(sql, tuple(params))
        return [_row_to_deploy(r) for r in cur.fetchall()]

    def find_deploy_for_plan(
        self, plan_id: str, *, exclude_destroyed: bool = False,
    ) -> DeployRow | None:
        """Most recent deploy for `plan_id`, if any (ordered by
        `updated_at`, not `started_at` -- see `latest_deploys`'s docstring).
        Plan_id is deterministic from the IR, so re-running `axrange deploy`
        against an unchanged scenario finds the prior deploy here — that's
        the resume substrate.

        `exclude_destroyed=True` (the DEPLOY-CREATION resume decision --
        `axrange te deploy`/`o2 deploy`/`tap deploy` and the API's
        `create_deploy`, all non-`--fresh`) treats a `destroyed` top match
        as NO match: `destroyed` is terminal (its resources are already
        gone), so it must never be silently revived in place (status
        destroyed→running, ops reset, same old `deploy_id`/`started_at`) --
        code review finding, docs/WORK_ORDER_cli_lifecycle.md. The caller's
        existing `existing is None -> mint a fresh deploy_id` branch (the
        SAME branch `--fresh` already uses) then fires automatically, so no
        second "was it destroyed" check is needed at the call site.

        This does NOT search past a destroyed top match for an OLDER
        non-destroyed row sharing the same `plan_id` (e.g. one predating an
        earlier `--fresh` re-deploy) -- a destroyed top match means "mint a
        fresh deploy_id", exactly as `--fresh` would, not "reach back
        further into history."

        Callers that look up a deploy for TEARDOWN purposes (`destroy_taps`'s
        target-based lookup, `cascade_destroy_for_te`) deliberately leave
        this `False` (the default) -- finding an already-destroyed deploy
        there is the expected idempotent-no-op path, not a revival.
        """
        cur = self._conn.execute(
            "SELECT * FROM deploys WHERE plan_id = ? "
            "ORDER BY COALESCE(updated_at, started_at) DESC LIMIT 1",
            (plan_id,),
        )
        r = cur.fetchone()
        if not r:
            return None
        row = _row_to_deploy(r)
        if exclude_destroyed and row.status == DeployStatus.DESTROYED:
            return None
        return row

    def reset_incomplete_ops(self, deploy_id: str) -> int:
        """Reset every non-`completed`, non-`destroyed` op back to `pending`
        for a clean re-run.

        Resume semantics (`docs/PLANNER.md` §5.4): `completed` ops stay
        untouched (their outputs feed downstream consumers); `failed`,
        `halted-due-to-upstream`, and any stuck `running` rows (process died
        mid-attempt) go back to `pending` so the executor walks them again.

        `destroyed` rows are ALSO left alone (code review M2/S1, `docs/
        WORK_ORDER_twin_incremental_update.md`) — they are the durable
        record of an `--update`'s removal sweep (`planner.teardown.
        destroy_ops_scoped`) or an ordinary `axrange destroy`; resurrecting
        one back to `pending` here would make it eligible to run again on
        the very next resume, and if THAT run's executor pass then failed
        before completing it, the row would be `pending` forever with no op
        in the current plan able to ever complete it (`te status`/`GET /api/
        deploys/{id}` would report a phantom pending op indefinitely). A
        `destroyed` op that legitimately needs to exist again (its host was
        removed then later re-added under the SAME pinned `plan_id`, so it
        gets the SAME deterministic op_id) does not need this reset to run:
        the executor's resume short-circuit only skips ids already recorded
        `completed` (`Executor._prime_completed_outputs`), so a `destroyed`
        row still in the CURRENT plan is executed normally regardless of
        whether it passed through this reset first.
        Returns the number of rows reset.
        """
        with self._tx() as cur:
            cur.execute(
                """
                UPDATE ops
                   SET status = ?, started_at = NULL, completed_at = NULL
                 WHERE deploy_id = ? AND status NOT IN (?, ?)
                """,
                (
                    OpStatus.PENDING.value, deploy_id,
                    OpStatus.COMPLETED.value, OpStatus.DESTROYED.value,
                ),
            )
            return cur.execute(
                "SELECT changes()"
            ).fetchone()[0]

    def force_reset_ops(self, deploy_id: str, op_ids: Iterable[str]) -> int:
        """Reset SPECIFIC op_ids back to `pending`, regardless of their
        current status (including `completed`) -- unlike `reset_incomplete_
        ops` above, which deliberately leaves `completed` rows alone.

        Needed by callers that must FORCE a re-assertion of an already-
        completed op because the op's adapter side effect is "regenerate
        the whole resource from whatever input I'm given this call" (e.g.
        `install-ovs-mirror`'s hook script) and the CALLER's desired content
        has changed since the op last ran under a STABLE op_id -- content-
        addressing the op_id (a fresh id per content) gives dedup, not
        re-assertion; forcing a known-stable op_id back to pending is what
        makes a narrow/revert actually reach the cluster again
        (`axrange.o2control.taps`, `docs/design/06-taps-sensors.md`).

        A no-op op_id (not present for this deploy) is silently skipped.
        Returns the number of rows reset.
        """
        ids = list(op_ids)
        if not ids:
            return 0
        with self._tx() as cur:
            placeholders = ",".join("?" for _ in ids)
            cur.execute(
                f"""
                UPDATE ops
                   SET status = ?, started_at = NULL, completed_at = NULL
                 WHERE deploy_id = ? AND op_id IN ({placeholders})
                """,
                (OpStatus.PENDING.value, deploy_id, *ids),
            )
            return cur.execute(
                "SELECT changes()"
            ).fetchone()[0]

    def mark_destroyed(self, deploy_id: str) -> None:
        """Transition a deploy and all its ops to `destroyed` in one tx.

        The terminal state `axrange destroy` writes once teardown
        (docs/design/11-api-ui.md) has removed every axr-created resource.
        Ops go `destroyed` too so the status surface shows the deploy is gone,
        not merely `completed`. Idempotent: re-running over an already-destroyed
        deploy is a harmless no-op.
        """
        now = time.time()
        with self._tx() as cur:
            cur.execute(
                "UPDATE ops SET status = ? WHERE deploy_id = ?",
                (OpStatus.DESTROYED.value, deploy_id),
            )
            cur.execute(
                """
                UPDATE deploys
                   SET status = ?, updated_at = ?, completed_at = ?
                 WHERE deploy_id = ?
                """,
                (DeployStatus.DESTROYED.value, now, now, deploy_id),
            )

    # --- ops ---------------------------------------------------------------

    def insert_ops(self, deploy_id: str, ops: list[OpRow]) -> None:
        """Insert op rows for a deploy. Idempotent (`INSERT OR IGNORE`) so
        a resume of an existing deploy can re-call this safely — existing
        rows keep their status and outputs."""
        with self._tx() as cur:
            for op in ops:
                cur.execute(
                    """
                    INSERT OR IGNORE INTO ops (
                        op_id, deploy_id, op_name, op_catalog_id, kind,
                        scope_json, inputs_resolved_json,
                        inputs_from_outputs_json, outputs_json,
                        cleanup_strategy, idempotency_class, status,
                        started_at, completed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        op.op_id,
                        deploy_id,
                        op.op_name,
                        op.op_catalog_id,
                        op.kind,
                        op.scope_json,
                        op.inputs_resolved_json,
                        op.inputs_from_outputs_json,
                        op.outputs_json,
                        op.cleanup_strategy,
                        op.idempotency_class,
                        op.status.value,
                        op.started_at,
                        op.completed_at,
                    ),
                )

    def set_op_running(
        self,
        deploy_id: str,
        op_id: str,
        *,
        inputs_resolved_json: str | None = None,
        inputs_from_outputs_json: str | None = None,
    ) -> None:
        """Mark an op RUNNING, stamping `started_at` once (`COALESCE` keeps
        the first start time across retries).

        Optionally refreshes `inputs_resolved_json`/`inputs_from_outputs_json`
        in the SAME transaction as the status flip, so a re-applied deploy's
        op row reflects the CURRENT plan the instant an attempt starts, not
        just the first-ever apply (GAP-044, LES-157). Both kwargs must be
        given together or neither -- passing only one raises `ValueError`
        (S2, code review): a lone kwarg would otherwise silently wipe the
        OTHER column to an empty `"{}"`, manufacturing a confident "no
        wiring" answer out of an omission (LES-096's shape). Callers passing
        neither (e.g. a bare status check) leave inputs untouched -- the
        original one-arg call shape. Note (S4, code review): both columns
        hold the op's PLAN-level inputs (`OpInstance.inputs_resolved`/
        `.inputs_from_outputs`), not the dataflow-merged EFFECTIVE inputs an
        adapter actually receives (`Executor._resolve_inputs`'s producer-
        output substitution) -- "what was attempted" here means "with this
        plan wiring," not "with these literal resolved values."
        """
        have_resolved = inputs_resolved_json is not None
        have_from_outputs = inputs_from_outputs_json is not None
        if have_resolved != have_from_outputs:
            raise ValueError(
                "set_op_running: inputs_resolved_json and "
                "inputs_from_outputs_json must be supplied together or not "
                "at all -- a lone kwarg would silently wipe the other column"
            )
        with self._tx() as cur:
            if have_resolved:
                cur.execute(
                    """
                    UPDATE ops
                       SET status = ?,
                           started_at = COALESCE(started_at, ?),
                           inputs_resolved_json = ?,
                           inputs_from_outputs_json = ?
                     WHERE deploy_id = ? AND op_id = ?
                    """,
                    (
                        OpStatus.RUNNING.value,
                        time.time(),
                        inputs_resolved_json,
                        inputs_from_outputs_json,
                        deploy_id,
                        op_id,
                    ),
                )
            else:
                cur.execute(
                    "UPDATE ops SET status = ?, started_at = COALESCE(started_at, ?) "
                    "WHERE deploy_id = ? AND op_id = ?",
                    (OpStatus.RUNNING.value, time.time(), deploy_id, op_id),
                )

    def complete_op(self, deploy_id: str, op_id: str, outputs: dict[str, Any]) -> None:
        """Atomic per PLANNER.md §7.3: status, completed_at, outputs in one tx."""
        with self._tx() as cur:
            cur.execute(
                """
                UPDATE ops
                   SET status = ?, completed_at = ?, outputs_json = ?
                 WHERE deploy_id = ? AND op_id = ?
                """,
                (
                    OpStatus.COMPLETED.value,
                    time.time(),
                    json.dumps(outputs, sort_keys=True, default=str),
                    deploy_id,
                    op_id,
                ),
            )

    def merge_op_outputs(
        self, deploy_id: str, op_id: str, patch: dict[str, Any],
    ) -> bool:
        """Merge `patch` into ONE op row's recorded outputs, leaving its
        status, timestamps and inputs untouched. Returns False when the row
        does not exist; raises `ValueError` when its recorded outputs are
        not a JSON object (never coerced -- see the body).

        For a LIVE read-back that supersedes what the op itself reported --
        the one consumer is `axrange te egress` (`o2control.containment`,
        docs/WORK_ORDER_corp_twin.md §11 step 2), which re-reads a router's
        egress phase from the guest and records it on that router's
        `configure-router` row, so `te info` and the tap/tfc containment gate
        (both `planner.egress.egress_posture_from_ops` over these rows) see
        the phase the router is IN, not the one it reported at deploy time.
        Outputs are not part of `plandiff`'s Mutated fingerprint (inputs
        are), so this cannot turn an `--update` into a refusal."""
        with self._tx() as cur:
            row = cur.execute(
                "SELECT outputs_json FROM ops WHERE deploy_id = ? AND op_id = ?",
                (deploy_id, op_id),
            ).fetchone()
            if row is None:
                return False
            # A corrupt record is REFUSED, not coerced to `{}`: merging onto an
            # empty dict would silently drop every other output the op reported
            # (the router's `configured`, its interface map) and write the loss
            # back as if it were the truth.
            try:
                current = json.loads(row[0]) if row[0] else {}
            except (ValueError, TypeError) as e:
                raise ValueError(
                    f"deploy {deploy_id}: op {op_id!r} has corrupt outputs_json "
                    f"({e}); refusing to merge over it"
                ) from e
            if not isinstance(current, dict):
                raise ValueError(
                    f"deploy {deploy_id}: op {op_id!r} outputs_json is a "
                    f"{type(current).__name__}, not an object; refusing to merge over it"
                )
            current.update(patch)
            cur.execute(
                "UPDATE ops SET outputs_json = ? WHERE deploy_id = ? AND op_id = ?",
                (json.dumps(current, sort_keys=True, default=str), deploy_id, op_id),
            )
            return True

    def fail_op(self, deploy_id: str, op_id: str) -> None:
        with self._tx() as cur:
            cur.execute(
                "UPDATE ops SET status = ?, completed_at = ? WHERE deploy_id = ? AND op_id = ?",
                (OpStatus.FAILED.value, time.time(), deploy_id, op_id),
            )

    def halt_op(self, deploy_id: str, op_id: str) -> None:
        with self._tx() as cur:
            cur.execute(
                "UPDATE ops SET status = ?, completed_at = ? WHERE deploy_id = ? AND op_id = ?",
                (OpStatus.HALTED.value, time.time(), deploy_id, op_id),
            )

    def mark_op_destroyed(self, deploy_id: str, op_id: str) -> None:
        """Transition ONE op to `destroyed` without touching the rest of the
        deploy (unlike `mark_destroyed`, which transitions every op + the
        deploy itself at full-deploy-teardown time).

        Needed by a playout that removes/stops ONE persisted resource
        out-of-band -- not by re-running the op, so `complete_op`/`fail_op`/
        `halt_op` don't fit -- without tearing down its whole deploy. First
        consumer: the Traffic tier's narrowing re-apply (`docs/
        WORK_ORDER_traffic_tier.md`) -- a dropped persona instance is
        stopped directly via its driver, so its `drive-persona` op row is
        marked `destroyed` here so status/info reads stop reporting it as
        live (mirrors what `mark_destroyed` already does for a full
        teardown, at op granularity).
        """
        with self._tx() as cur:
            cur.execute(
                "UPDATE ops SET status = ?, completed_at = ? WHERE deploy_id = ? AND op_id = ?",
                (OpStatus.DESTROYED.value, time.time(), deploy_id, op_id),
            )

    def mark_ops_destroyed(self, deploy_id: str, op_ids: Iterable[str]) -> int:
        """Batch form of `mark_op_destroyed`, one transaction for many rows
        (mirrors `force_reset_ops`'s IN-clause batching). Needed by a SCOPED
        teardown sweep (`planner.teardown.destroy_ops_scoped`,
        `docs/WORK_ORDER_twin_incremental_update.md` §3.2/§4.2): an
        `--update`'s removed set can touch many ops across several removed
        hosts in one clean sweep, and each should transition atomically
        alongside the others, not one-row-per-transaction. A no-op op_id
        (not present for this deploy) is silently skipped. Returns the
        number of rows changed."""
        ids = list(op_ids)
        if not ids:
            return 0
        with self._tx() as cur:
            placeholders = ",".join("?" for _ in ids)
            cur.execute(
                f"""
                UPDATE ops
                   SET status = ?, completed_at = ?
                 WHERE deploy_id = ? AND op_id IN ({placeholders})
                """,
                (OpStatus.DESTROYED.value, time.time(), deploy_id, *ids),
            )
            return cur.execute(
                "SELECT changes()"
            ).fetchone()[0]

    def get_op(self, deploy_id: str, op_id: str) -> OpRow | None:
        cur = self._conn.execute(
            "SELECT * FROM ops WHERE deploy_id = ? AND op_id = ?", (deploy_id, op_id)
        )
        r = cur.fetchone()
        return _row_to_op(r) if r else None

    def ops_for_deploy(self, deploy_id: str) -> list[OpRow]:
        cur = self._conn.execute(
            "SELECT * FROM ops WHERE deploy_id = ? ORDER BY op_id",
            (deploy_id,),
        )
        return [_row_to_op(r) for r in cur.fetchall()]

    # --- attempts ----------------------------------------------------------

    def start_attempt(self, deploy_id: str, op_id: str) -> int:
        with self._tx() as cur:
            cur.execute(
                "INSERT INTO op_attempts (deploy_id, op_id, started_at) VALUES (?, ?, ?)",
                (deploy_id, op_id, time.time()),
            )
            row = cur.execute("SELECT last_insert_rowid()").fetchone()
            return int(row[0])

    def end_attempt(
        self,
        attempt_id: int,
        result: AttemptResult,
        *,
        error_message: str | None = None,
        error_class: str | None = None,
    ) -> None:
        with self._tx() as cur:
            cur.execute(
                """
                UPDATE op_attempts
                   SET ended_at = ?, result = ?, error_message = ?, error_class = ?
                 WHERE attempt_id = ?
                """,
                (time.time(), result.value, error_message, error_class, attempt_id),
            )

    def attempts_for_op(self, deploy_id: str, op_id: str) -> list[AttemptRow]:
        cur = self._conn.execute(
            "SELECT * FROM op_attempts WHERE deploy_id = ? AND op_id = ? ORDER BY attempt_id",
            (deploy_id, op_id),
        )
        return [_row_to_attempt(r) for r in cur.fetchall()]

    # --- leases (PLANNER.md §6.2) ------------------------------------------

    def acquire_lease(
        self,
        scenario_id: str,
        deploy_id: str,
        *,
        process_info: dict[str, Any] | None = None,
        ttl_s: float = DEFAULT_LEASE_TTL_S,
        now: float | None = None,
    ) -> LeaseResult:
        """Take the single-writer lease for `scenario_id` (§6.2).

        Atomic check-then-write (§7.3): a *valid* (non-expired) lease — held
        by anyone, including a still-running process that happens to share
        this `deploy_id` — refuses the acquisition. An absent or expired lease
        is taken over (the crash-recovery path). The caller is a fresh process,
        so "valid lease exists" always means another live deploy.
        """
        now = time.time() if now is None else now
        new_expiry = now + ttl_s
        with self._tx() as cur:
            r = cur.execute(
                "SELECT deploy_id, expires_at FROM leases WHERE scenario_id = ?",
                (scenario_id,),
            ).fetchone()
            if r is not None and r[1] > now:
                return LeaseResult(
                    acquired=False, held_by_deploy_id=r[0], expires_at=r[1]
                )
            cur.execute(
                """
                INSERT INTO leases (
                    scenario_id, deploy_id, process_info_json,
                    acquired_at, expires_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(scenario_id) DO UPDATE SET
                    deploy_id         = excluded.deploy_id,
                    process_info_json = excluded.process_info_json,
                    acquired_at       = excluded.acquired_at,
                    expires_at        = excluded.expires_at
                """,
                (
                    scenario_id,
                    deploy_id,
                    json.dumps(process_info or {}, sort_keys=True),
                    now,
                    new_expiry,
                ),
            )
        return LeaseResult(acquired=True, held_by_deploy_id=deploy_id, expires_at=new_expiry)

    def renew_lease(
        self,
        scenario_id: str,
        deploy_id: str,
        *,
        ttl_s: float = DEFAULT_LEASE_TTL_S,
        now: float | None = None,
    ) -> bool:
        """Heartbeat: extend `expires_at` iff this deploy still holds the lease.
        Returns False if the lease was lost (expired and reclaimed elsewhere)."""
        now = time.time() if now is None else now
        with self._tx() as cur:
            cur.execute(
                "UPDATE leases SET expires_at = ? WHERE scenario_id = ? AND deploy_id = ?",
                (now + ttl_s, scenario_id, deploy_id),
            )
            return cur.execute("SELECT changes()").fetchone()[0] > 0

    def release_lease(self, scenario_id: str, deploy_id: str) -> None:
        with self._tx() as cur:
            cur.execute(
                "DELETE FROM leases WHERE scenario_id = ? AND deploy_id = ?",
                (scenario_id, deploy_id),
            )

    def get_lease(self, scenario_id: str) -> LeaseRow | None:
        r = self._conn.execute(
            "SELECT * FROM leases WHERE scenario_id = ?", (scenario_id,)
        ).fetchone()
        return _row_to_lease(r) if r else None

    # --- resource locks (PLANNER.md §8.4) ----------------------------------

    def acquire_resource_locks(
        self,
        keys: list[str],
        deploy_id: str,
        op_id: str,
        *,
        process_info: dict[str, Any] | None = None,
        ttl_s: float = DEFAULT_LEASE_TTL_S,
        now: float | None = None,
    ) -> bool:
        """Acquire **all** of `keys` for `op_id`, or none (§8.4).

        One transaction checks every key is free (absent, expired, or already
        ours) and only then writes them all. All-or-nothing means a holder
        never sits on a partial set while blocking on the rest — so no two
        processes can deadlock (PLANNER §8.3). Callers that contend retry the
        whole set (see locks.acquire_blocking).
        """
        if not keys:
            return True
        now = time.time() if now is None else now
        new_expiry = now + ttl_s
        with self._tx() as cur:
            for key in keys:
                r = cur.execute(
                    "SELECT deploy_id, expires_at FROM resource_locks WHERE resource_key = ?",
                    (key,),
                ).fetchone()
                if r is not None and r[1] > now and r[0] != deploy_id:
                    return False  # held by another live deploy; nothing written yet
            pinfo = json.dumps(process_info or {}, sort_keys=True)
            for key in keys:
                cur.execute(
                    """
                    INSERT INTO resource_locks (
                        resource_key, deploy_id, op_id, process_info_json,
                        acquired_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(resource_key) DO UPDATE SET
                        deploy_id         = excluded.deploy_id,
                        op_id             = excluded.op_id,
                        process_info_json = excluded.process_info_json,
                        acquired_at       = excluded.acquired_at,
                        expires_at        = excluded.expires_at
                    """,
                    (key, deploy_id, op_id, pinfo, now, new_expiry),
                )
        return True

    def renew_resource_locks(
        self,
        deploy_id: str,
        *,
        ttl_s: float = DEFAULT_LEASE_TTL_S,
        now: float | None = None,
    ) -> int:
        """Heartbeat every lock held by `deploy_id`. Returns the row count."""
        now = time.time() if now is None else now
        with self._tx() as cur:
            cur.execute(
                "UPDATE resource_locks SET expires_at = ? WHERE deploy_id = ?",
                (now + ttl_s, deploy_id),
            )
            return cur.execute("SELECT changes()").fetchone()[0]

    def release_resource_locks_for_op(self, op_id: str) -> int:
        """Release every lock an op holds (called on op completion/failure)."""
        with self._tx() as cur:
            cur.execute("DELETE FROM resource_locks WHERE op_id = ?", (op_id,))
            return cur.execute("SELECT changes()").fetchone()[0]

    def release_resource_locks_for_deploy(self, deploy_id: str) -> int:
        """Release everything a deploy holds — the end-of-deploy safety net."""
        with self._tx() as cur:
            cur.execute("DELETE FROM resource_locks WHERE deploy_id = ?", (deploy_id,))
            return cur.execute("SELECT changes()").fetchone()[0]

    def held_resource_locks(self, deploy_id: str | None = None) -> list[ResourceLockRow]:
        if deploy_id is None:
            cur = self._conn.execute("SELECT * FROM resource_locks ORDER BY resource_key")
        else:
            cur = self._conn.execute(
                "SELECT * FROM resource_locks WHERE deploy_id = ? ORDER BY resource_key",
                (deploy_id,),
            )
        return [_row_to_resource_lock(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Row mapping helpers
# ---------------------------------------------------------------------------


def _like_prefix(prefix: str) -> str:
    """Escape `%`/`_`/the escape char itself for a `LIKE ... ESCAPE '\\'`
    prefix match, then append the wildcard. `deploys_matching`'s
    `plan_id_prefix`/`exclude_plan_id_prefix` params are naming-convention
    literals (e.g. `"tap-"`), not user-authored patterns, but escaping is
    cheap insurance against a plan_id that happens to contain a literal `%`
    or `_` (a scenario id an author chose)."""
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "%"


def _row_to_deploy(r: tuple[Any, ...]) -> DeployRow:
    return DeployRow(
        deploy_id=r[0],
        scenario_id=r[1],
        scenario_version=r[2],
        scenario_schema_version=r[3],
        plan_id=r[4],
        ir_hash=r[5],
        status=DeployStatus(r[6]),
        placement_json=r[7],
        started_at=r[8],
        updated_at=r[9],
        completed_at=r[10],
        tier=r[11],
    )


def _row_to_op(r: tuple[Any, ...]) -> OpRow:
    return OpRow(
        op_id=r[0],
        deploy_id=r[1],
        op_name=r[2],
        op_catalog_id=r[3],
        kind=r[4],
        scope_json=r[5],
        inputs_resolved_json=r[6],
        inputs_from_outputs_json=r[7],
        outputs_json=r[8],
        cleanup_strategy=r[9],
        idempotency_class=r[10],
        status=OpStatus(r[11]),
        started_at=r[12],
        completed_at=r[13],
    )


def _row_to_attempt(r: tuple[Any, ...]) -> AttemptRow:
    return AttemptRow(
        attempt_id=int(r[0]),
        deploy_id=r[1],
        op_id=r[2],
        started_at=r[3],
        ended_at=r[4],
        result=AttemptResult(r[5]) if r[5] else None,
        error_message=r[6],
        error_class=r[7],
    )


def _row_to_lease(r: tuple[Any, ...]) -> LeaseRow:
    return LeaseRow(
        scenario_id=r[0],
        deploy_id=r[1],
        process_info_json=r[2],
        acquired_at=r[3],
        expires_at=r[4],
    )


def _row_to_resource_lock(r: tuple[Any, ...]) -> ResourceLockRow:
    return ResourceLockRow(
        resource_key=r[0],
        deploy_id=r[1],
        op_id=r[2],
        process_info_json=r[3],
        acquired_at=r[4],
        expires_at=r[5],
    )


# ---------------------------------------------------------------------------
# Conversion helpers between Plan and OpRow
# ---------------------------------------------------------------------------


def op_to_row(op: Any, deploy_id: str) -> OpRow:
    """Turn an OpInstance + deploy_id into the row we'll insert."""
    from axrange.planner.dag import OpInstance  # local to avoid import cycle

    if not isinstance(op, OpInstance):
        raise TypeError(f"op_to_row expects OpInstance, got {type(op).__name__}")
    return OpRow(
        op_id=op.op_id,
        deploy_id=deploy_id,
        op_name=op.op_name,
        op_catalog_id=op.op_catalog_id,
        kind=op.kind.value,
        scope_json=json.dumps(op.scope, sort_keys=True),
        inputs_resolved_json=json.dumps(
            op.inputs_resolved, sort_keys=True, default=str
        ),
        inputs_from_outputs_json=json.dumps(
            {k: list(v) for k, v in op.inputs_from_outputs.items()},
            sort_keys=True,
        ),
        outputs_json=None,
        cleanup_strategy=(
            op.cleanup_strategy.value if op.cleanup_strategy else None
        ),
        idempotency_class=(
            op.idempotency_class.value if op.idempotency_class else None
        ),
        status=OpStatus.PENDING,
        started_at=None,
        completed_at=None,
    )


def barrier_to_row(barrier: Any, deploy_id: str) -> OpRow:
    """Turn a BarrierInstance into a row. Barriers persist for the status view
    even though they carry no execution."""
    from axrange.planner.dag import BarrierInstance  # local to avoid cycle

    if not isinstance(barrier, BarrierInstance):
        raise TypeError(
            f"barrier_to_row expects BarrierInstance, got {type(barrier).__name__}"
        )
    return OpRow(
        op_id=barrier.op_id,
        deploy_id=deploy_id,
        op_name=barrier.barrier_name,
        op_catalog_id=barrier.barrier_catalog_id,
        kind="barrier",
        scope_json=json.dumps(barrier.scope, sort_keys=True),
        inputs_resolved_json="{}",
        inputs_from_outputs_json="{}",
        outputs_json=None,
        cleanup_strategy=None,
        idempotency_class=None,
        status=OpStatus.PENDING,
        started_at=None,
        completed_at=None,
    )
