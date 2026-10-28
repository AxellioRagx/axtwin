"""`1.5`: a PIT has a name beside its id (`ir_pit.name`), and `bake` is the verb that writes it.

The id is the identity -- the deterministic hash every row, cast sidecar and drift report keys
on -- and never moves. The name is what a person calls the snapshot: it defaults to the id, is
unique in a store, and is the ONE column the store lets change after the bake.
"""
import json
import subprocess
import sys

import pytest

from aximprint.ir.materialize import bake, materialize
from aximprint.ir.store import IRStore, IRStoreError
from aximprint.ir.schema import SCHEMA_VERSION

from tests.test_ir_materialize import _basic as _store  # the belief-store fixture every IR test uses


def _bake_into(tmp_path, name=None, **kw):
    store = _store(tmp_path)
    pit = bake(store, name=name, **kw)
    store.close()
    pit.pop("_stats", None)
    ir = IRStore(str(tmp_path / "ir.duckdb"))
    ir.write_pit(pit, replace=True)
    return ir, pit["ir_pit"][0]["pit_id"]


def test_the_name_defaults_to_the_id_and_the_id_is_unchanged(tmp_path):
    ir, pit_id = _bake_into(tmp_path)
    (row,) = ir.pits()
    assert row["pit_id"] == pit_id and row["name"] == pit_id
    assert pit_id.startswith("pit-"), "the id is still the deterministic hash"
    assert ir.summary(pit_id)[0]["name"] == pit_id
    assert ir.read_pit(pit_id)["ir_pit"][0]["schema_version"] == SCHEMA_VERSION == "1.5"
    ir.close()


def test_bake_is_materialize_under_its_operator_name(tmp_path):
    assert materialize is bake


def test_a_name_given_at_bake_time_is_carried_and_resolves(tmp_path):
    ir, pit_id = _bake_into(tmp_path, name="corp-apcn-10d")
    assert ir.pits()[0]["name"] == "corp-apcn-10d"
    assert ir.resolve_pit("corp-apcn-10d") == pit_id
    assert ir.resolve_pit(pit_id) == pit_id, "the id always resolves"
    with pytest.raises(KeyError, match="no PIT with id or name 'nope'"):
        ir.resolve_pit("nope")
    ir.close()


def test_rename_changes_the_name_only_and_refuses_a_taken_one(tmp_path):
    ir, pit_id = _bake_into(tmp_path)
    before = ir.read_pit(pit_id)
    row = ir.rename_pit(pit_id, "first look")
    assert (row["pit_id"], row["name"]) == (pit_id, "first look")
    after = ir.read_pit(pit_id)
    after["ir_pit"][0]["name"] = before["ir_pit"][0]["name"]
    assert after == before, "nothing but the name moved"
    assert ir.resolve_pit("first look") == pit_id
    # Renaming by the current name works too; the id itself is not a legal name for another.
    assert ir.rename_pit("first look", "second look")["name"] == "second look"
    with pytest.raises(IRStoreError, match="non-empty single line"):
        ir.rename_pit(pit_id, "  ")
    ir.close()


def test_a_name_cannot_collide_with_another_pits_name_or_id(tmp_path):
    ir, a = _bake_into(tmp_path, name="a")
    # A second PIT, by hand: a different id under the same store.
    doc = ir.read_pit(a)
    for rows in doc.values():
        for r in rows:
            r["pit_id"] = "pit-other-1"
    doc["ir_pit"][0]["name"] = "b"
    ir.write_pit(doc)
    with pytest.raises(IRStoreError, match="already the name or id"):
        ir.rename_pit("pit-other-1", "a")
    with pytest.raises(IRStoreError, match="already the name or id"):
        ir.rename_pit("pit-other-1", a)
    doc["ir_pit"][0]["name"] = "a"
    doc["ir_pit"][0]["pit_id"] = "pit-other-2"
    for rows in doc.values():
        for r in rows:
            r["pit_id"] = "pit-other-2"
    with pytest.raises(IRStoreError, match="already the name or id"):
        ir.write_pit(doc)
    ir.close()


def test_a_1_4_store_gains_the_column_on_open_backfilled_with_the_id(tmp_path):
    """The live `~/.axrange/discovery/ir.duckdb` was written at `1.4`. Opening it with this
    build must not refuse it (same MAJOR) and must leave every PIT with a name."""
    import duckdb

    from aximprint.ir.schema import _SCHEMA

    path = str(tmp_path / "old.duckdb")
    old_ddl = _SCHEMA.replace(",\n  -- `1.5`", ");\n  -- `1.5`")  # cut `name` off the DDL
    old_ddl = "\n".join(l for l in old_ddl.splitlines() if not l.strip().startswith("--"))
    old_ddl = old_ddl.replace("name VARCHAR);", "")
    con = duckdb.connect(path)
    con.execute(old_ddl)
    assert "name" not in {r[1] for r in con.execute("PRAGMA table_info('ir_pit')").fetchall()}
    con.execute("INSERT INTO ir_pit (pit_id, source, as_of, schema_version) "
                "VALUES ('pit-old-1', '/cap', 100.0, '1.4')")
    con.close()
    ir = IRStore(path)
    assert ir.pits()[0]["name"] == "pit-old-1"
    assert ir.resolve_pit("pit-old-1") == "pit-old-1"
    ir.close()


def _cli(*argv, cwd):
    return subprocess.run([sys.executable, "-m", "aximprint.cli", *argv], cwd=cwd,
                          capture_output=True, text=True)


def test_the_cli_bakes_names_lists_and_renames(tmp_path):
    _store(tmp_path).close()
    belief = str(tmp_path / "s.duckdb")
    ir_path = str(tmp_path / "ir.duckdb")
    r = _cli("bake", "--store", belief, "--out", ir_path, "--name", "day one", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    baked = json.loads(r.stdout)
    assert baked["name"] == "day one" and baked["pit_id"].startswith("pit-")
    # The old spelling still works and refuses the duplicate id without --replace.
    r = _cli("materialize", "--store", belief, "--out", ir_path, cwd=tmp_path)
    assert r.returncode != 0 and "already exists" in (r.stderr + r.stdout)
    r = _cli("pit", "list", "--store", ir_path, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert [p["name"] for p in json.loads(r.stdout)["pits"]] == ["day one"]
    r = _cli("pit", "rename", "--store", ir_path, "day one", "day two", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["name"] == "day two"
    r = _cli("pit", "rename", "--store", ir_path, "day one", "x", cwd=tmp_path)
    assert r.returncode == 2 and "no PIT with id or name 'day one'" in r.stderr
    # `cast --pit` takes the name.
    r = _cli("cast", "--store", ir_path, "--pit", "day two", "--out", str(tmp_path / "cast"),
             cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["pit_id"] == baked["pit_id"]
