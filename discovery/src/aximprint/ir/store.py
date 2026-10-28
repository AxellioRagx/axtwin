"""The IR store — where PITs are persisted (`docs/WORK_ORDER_network_ir.md` D2, build step 3).

**A separate DuckDB database, not a view over the belief store and not one file per PIT.** D2's
reasoning, restated because the shape looks arbitrary otherwise:

  * *Not a view.* A versioned read surface over an unversioned, freely-changing store would inherit
    every change the store is allowed to make — and the belief store is allowed to make any.
  * *Not one file per PIT.* Drift comparison wants many PITs queryable together; N files means N
    attaches for every question.
  * The concrete incompatibility that settles it: `BeliefStore.__init__`'s
    `_check_schema_matches_this_build` **fails loud on any schema mismatch**, which is right for an
    internal projection nothing reads across builds, and exactly wrong for a versioned artifact
    whose entire purpose is that an older one stays readable.

**Never a second source of truth.** The append-only evidence log is the source of truth, the belief
store its rebuildable projection, and a PIT a frozen, re-derivable view of that projection at one
instant. Nothing here updates a row -- with one deliberate exception, `rename_pit`, which changes
`ir_pit.name` and nothing else: the name is a label a person gave the snapshot, not a fact about
the network, and every row stays keyed on the unchanging `pit_id`.
"""
from __future__ import annotations

from typing import Optional

from aximprint.ir.schema import IR_TABLES, SCHEMA_VERSION, _SCHEMA, can_read


class IRStoreError(Exception):
    """The IR store cannot be used as asked. Always loud, never a degraded read."""


class IRStore:
    """PIT persistence. Open, write, read — no mutation path exists."""

    def __init__(self, path: str):
        import duckdb
        self.path = path
        self.con = duckdb.connect(path)
        self.con.execute(_SCHEMA)
        self._add_missing_columns()
        self._check_versions_readable()

    def _add_missing_columns(self) -> None:
        """Bring a store written by an earlier MINOR up to this build's column list.

        A MINOR is additive by definition (`schema.can_read`), so the only migration a store
        ever needs is `ADD COLUMN` for each column the DDL now ends with and the file does not
        have. `1.5` added `ir_pit.name`, backfilled with the id so every PIT has a name.
        """
        have = {r[1] for r in self.con.execute("PRAGMA table_info('ir_pit')").fetchall()}
        if "name" not in have:
            self.con.execute("ALTER TABLE ir_pit ADD COLUMN name VARCHAR")
        self.con.execute("UPDATE ir_pit SET name = pit_id WHERE name IS NULL")

    def _check_versions_readable(self) -> None:
        """Refuse a store holding a PIT this build cannot read (§11 fail-closed load).

        Checked at OPEN, before any read, so an unreadable artifact cannot be half-consumed. The
        check is per distinct version rather than per row: a store may legitimately hold several
        MINORs of one MAJOR, all readable.

        Note what this deliberately does NOT do, in contrast with `BeliefStore`'s guard: it does
        not refuse on any difference. A `1.0` artifact read by a `1.7` build is fine and is the
        normal case after an additive change — refusing it would defeat the reason the artifact is
        versioned at all.
        """
        try:
            versions = [v for (v,) in self.con.execute(
                "SELECT DISTINCT schema_version FROM ir_pit").fetchall() if v]
        except Exception:                      # freshly created, no rows
            return
        unreadable = sorted(v for v in versions if not can_read(v, SCHEMA_VERSION))
        if unreadable:
            raise IRStoreError(
                f"{self.path}: holds PIT(s) at schema version(s) {unreadable} which this build "
                f"(schema {SCHEMA_VERSION}) cannot read. A differing MAJOR may have removed a "
                f"field this build depends on (DESIGN_BRIEF §11: fail-closed load). Read it with "
                f"a build of that MAJOR, or re-materialize from the evidence log.")

    def pit_ids(self) -> list[str]:
        return [p for (p,) in self.con.execute(
            "SELECT pit_id FROM ir_pit ORDER BY as_of, pit_id").fetchall()]

    def pits(self) -> list[dict]:
        """`{pit_id, name, as_of, source, network, vantage, windows, entities, segments}` per
        PIT, oldest first -- what `aximprint pit list` prints and the API publishes."""
        cols = ("pit_id", "name", "as_of", "observed_from", "observed_to", "source", "network",
                "vantage", "windows", "entities", "segments", "merge_axis")
        rows = self.con.execute(
            f"SELECT {', '.join(cols)} FROM ir_pit ORDER BY as_of, pit_id").fetchall()
        return [dict(zip(cols, r)) for r in rows]

    def resolve_pit(self, ref: str) -> str:
        """The `pit_id` a caller means by `ref`: an id exactly, else the one PIT with that name.
        Ids win over names so a name that happens to equal another PIT's id cannot hijack it;
        no match raises `KeyError` naming the ref."""
        if self.con.execute("SELECT 1 FROM ir_pit WHERE pit_id = ?", [ref]).fetchone():
            return ref
        hits = [p for (p,) in self.con.execute(
            "SELECT pit_id FROM ir_pit WHERE name = ? ORDER BY pit_id", [ref]).fetchall()]
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise KeyError(f"{len(hits)} PITs are named {ref!r} ({hits}); use the id")
        raise KeyError(f"no PIT with id or name {ref!r} in {self.path}")

    def rename_pit(self, ref: str, name: str) -> dict:
        """Give one PIT a new name. The id never changes; the name must be non-empty, one
        line, and not already another PIT's name or id. Returns the PIT's row from `pits()`."""
        name = str(name).strip()
        if not name or "\n" in name:
            raise IRStoreError("a PIT name is a non-empty single line")
        pit_id = self.resolve_pit(ref)
        taken = self.con.execute(
            "SELECT pit_id FROM ir_pit WHERE (name = ? OR pit_id = ?) AND pit_id <> ?",
            [name, name, pit_id]).fetchone()
        if taken:
            raise IRStoreError(f"{name!r} is already the name or id of PIT {taken[0]!r}")
        self.con.execute("UPDATE ir_pit SET name = ? WHERE pit_id = ?", [name, pit_id])
        return next(p for p in self.pits() if p["pit_id"] == pit_id)

    def write_pit(self, pit: dict, *, replace: bool = False) -> None:
        """Persist one materialized PIT.

        **Refuses to overwrite an existing `pit_id` unless `replace=True`** — the recommendation
        `docs/SPEC_network_ir.md` put on record, taken. A PIT is something another artifact may
        already have compared against, and silently replacing it would change the meaning of a
        drift report already written without changing anything a reader could see. `replace=True`
        is the deliberate act of saying that is fine.
        """
        head = pit["ir_pit"][0]
        pit_id = head["pit_id"]
        # `1.5`: every PIT has a name; the id is the default one.
        if not head.get("name"):
            head["name"] = pit_id
        if pit_id in self.pit_ids():
            if not replace:
                raise IRStoreError(
                    f"{self.path}: PIT {pit_id!r} already exists. Something may already have "
                    f"compared against it; pass replace=True to overwrite deliberately.")
            for table in IR_TABLES:
                self.con.execute(f"DELETE FROM {table} WHERE pit_id = ?", [pit_id])
        taken = self.con.execute(
            "SELECT pit_id FROM ir_pit WHERE (name = ? OR pit_id = ?) AND pit_id <> ?",
            [head["name"], head["name"], pit_id]).fetchone()
        if taken:
            raise IRStoreError(
                f"{self.path}: {head['name']!r} is already the name or id of PIT {taken[0]!r}")
        for table, columns in IR_TABLES.items():
            rows = pit.get(table) or []
            if not rows:
                continue
            placeholders = ", ".join("?" * len(columns))
            self.con.executemany(
                f"INSERT INTO {table} VALUES ({placeholders})",
                [[r.get(c) for c in columns] for r in rows])

    def read_pit(self, pit_id: str) -> dict:
        """Every table's rows for one PIT, as dicts, deterministically ordered.

        Ordered explicitly rather than relying on insertion order: a DuckDB result consumed as-is
        is one of the four ways `LES-002` names for a determinism break, and a comparator
        subtracting two PITs would read any instability as drift.
        """
        out: dict[str, list[dict]] = {}
        for table, columns in IR_TABLES.items():
            cols = ", ".join(columns)
            rows = self.con.execute(
                f"SELECT {cols} FROM {table} WHERE pit_id = ? ORDER BY {cols}",
                [pit_id]).fetchall()
            out[table] = [dict(zip(columns, r)) for r in rows]
        return out

    def summary(self, pit_id: Optional[str] = None) -> list[dict]:
        """One row per PIT: what it is and how much it saw. What `status` would print."""
        where, params = ("WHERE pit_id = ?", [pit_id]) if pit_id else ("", [])
        # `code_fingerprint` is here because `compare._caveats` needs it: entity keys derive from
        # the resolver's normalization, so a build change is the only signal that two PITs may have
        # stopped joining for a reason that is not drift (`LES-113`).
        # `vantage`/`network`/`vantages`/`merge_axis` are here for the same reason
        # `code_fingerprint` is: the comparator and the merger decide whether two PITs may be put
        # side by side from these columns alone (`compare._check_commensurable`,
        # `merge._check_mergeable`), and a summary that omitted them would force both to re-open
        # every PIT to answer a question about its identity.
        cols = ("pit_id", "name", "source", "as_of", "observed_from", "observed_to", "windows",
                "schema_version", "entities", "segments", "capture_loss_pct_max",
                "code_fingerprint", "vantage", "network", "vantages", "merge_axis")
        rows = self.con.execute(
            f"SELECT {', '.join(cols)} FROM ir_pit {where} ORDER BY as_of, pit_id",
            params).fetchall()
        return [dict(zip(cols, r)) for r in rows]

    def close(self) -> None:
        self.con.close()
