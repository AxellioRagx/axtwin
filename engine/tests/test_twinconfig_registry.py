"""Unit tests for `axrange.twinconfig.registry` — catalog discovery over
`twin-configs/<id>/<id>.yaml` (docs/WORK_ORDER_twin_configs.md §2)."""
from __future__ import annotations

import pytest

from axrange.twinconfig.errors import TwinConfigNotFoundError
from axrange.twinconfig.registry import (
    default_catalog_dir,
    list_twin_configs,
    load_twin_config,
    load_twin_config_raw,
    resolve_twin_config_path,
    twin_config_source_dir,
)

# The five STARTER twin-configs (`docs/WORK_ORDER_twin_configs.md`), plus
# `svc-verify` -- the purpose-built THROWAWAY twin
# `docs/WORK_ORDER_protocol_faithful_services.md` §9.2 calls for (a bind9
# resolver, an nginx TLS web tier, a chrony host, a real AD DC + joined
# member, and a `drive: command` probe client), added 2026-09-05 so the gated
# `dev45` live-verify of row 5's server half has something to run against
# that is NOT the standing `corpapcn-twin`. It is deliberately in the shipped
# catalog rather than a scratch directory so `--config svc-verify` discovers
# it with no `--twin-configs-dir` gymnastics; it is NOT an archetype, and it
# is NOT in `test_twinconfig_catalog.py`'s `plan_id` pin set (a throwaway has
# no standing deploy to orphan).
# ... and `svc-engines` -- the second purpose-built THROWAWAY twin, added
# 2026-09-21 for the protocol-ENGINE round
# (`docs/WORK_ORDER_protocol_engines.md` §5 E8: a resolver, a web tier, a
# Samba file server, a DC + joined member, the any-IP sink and a Linux client
# carrying four persona instances). Same posture as `svc-verify`: shipped in
# the catalog so `--config svc-engines` discovers it, NOT an archetype, and NOT
# in `test_twinconfig_catalog.py`'s `plan_id` pin set -- a throwaway has no
# standing deploy to orphan.
_EXPECTED_IDS = {
    "corp-enterprise", "datacenter", "dmz-perimeter", "ics-ot", "sme-flat",
    "svc-verify", "svc-engines",
    # The three aximprint casts (was `twin-configs-cast/`) and the first
    # hand-authored `twins/` citizen -- one catalog since the twin
    # consolidation (`docs/WORK_ORDER_twin_consolidation.md` Phase 2).
    "corpapcn-golden", "corpapcn-twin", "pit-395a61f04e-1780405200", "corp-twin",
    "corp-twin-devlab",
}


def test_default_catalog_dir_exists_and_has_the_expected_configs():
    root = default_catalog_dir()
    assert root.is_dir()
    entries = list_twin_configs(root)
    assert {e.id for e in entries} == _EXPECTED_IDS


def test_list_twin_configs_sorted_by_id():
    entries = list_twin_configs()
    ids = [e.id for e in entries]
    assert ids == sorted(ids)


def test_subfolder_layout_config_found_and_tap_plan_skipped(tmp_path):
    """Per-config subfolder layout `twin-configs/<id>/<id>.yaml` is discovered
    recursively, and a co-located `tap-*.yaml` tap-plan in the same subfolder
    is NOT mistaken for a twin-config (dropped by the `twin_config:` filter)."""
    cfg_dir = tmp_path / "corp"
    cfg_dir.mkdir()
    (cfg_dir / "corp.yaml").write_text(
        "twin_config:\n  id: corp\n  description: sub twin\n  params: {}\n"
        "  template:\n    scenario_id: corp\n    version: 1\n    nodes: []\n"
    )
    (cfg_dir / "tap-default.yaml").write_text(
        "tap-plan:\n  target: corp\n  bindings:\n"
        "    - tap: h1\n      sensors: [s1]\n      mode: exclusive\n"
    )
    entries = list_twin_configs(tmp_path)
    assert [e.id for e in entries] == ["corp"]  # the tap-plan is not listed
    raw, path = load_twin_config_raw("corp", catalog_dir=tmp_path)
    assert raw["twin_config"]["id"] == "corp"
    assert path == cfg_dir / "corp.yaml"  # resolved the subfolder file


def test_list_twin_configs_missing_dir_returns_empty(tmp_path):
    assert list_twin_configs(tmp_path / "does-not-exist") == []


def test_load_twin_config_raw_by_filename():
    raw, path = load_twin_config_raw("sme-flat")
    assert (path.parent.name, path.name) == ("sme-flat", "te.yaml")
    assert raw["twin_config"]["id"] == "sme-flat"


def test_the_legacy_root_no_longer_declares_any_twin():
    """Every twin moved to `twins/<id>/`; what the legacy root still holds is
    the maxrate duplicates held for Phase 4 -- no `twin_config:` among them,
    so nothing in it can shadow or be shadowed."""
    from axrange.twinconfig.registry import legacy_catalog_dir

    assert list_twin_configs(legacy_catalog_dir()) == []


def test_load_twin_config_not_found_raises(tmp_path):
    with pytest.raises(TwinConfigNotFoundError):
        load_twin_config_raw("nope", catalog_dir=tmp_path)


def test_load_twin_config_scan_by_declared_id_fallback(tmp_path):
    # A file whose NAME doesn't match its declared id -- exercises the
    # scan-by-declared-id fallback path.
    (tmp_path / "renamed.yaml").write_text(
        "twin_config:\n  id: my-config\n  segments: []\n  nodes: []\n"
    )
    raw, path = load_twin_config_raw("my-config", catalog_dir=tmp_path)
    assert path.name == "renamed.yaml"


def test_load_twin_config_every_shipped_config_parses():
    for entry in list_twin_configs():
        tc = load_twin_config(entry.id)
        assert tc.id == entry.id
        assert tc.segments
        assert tc.nodes


# ---------------------------------------------------------------------------
# Multi-dir search path (`docs/WORK_ORDER_console_selector_and_stale_
# deploys.md` follow-up, Authority: user, 2026-08-27) -- `catalog_dir` now
# accepts a colon-separated search path, mirroring `AXRANGE_PERSONAS_DIR`.
# The concrete complaint this fixes: a single swappable dir made the shipped
# catalog disappear the moment an operator pointed at a cast/working dir.
# ---------------------------------------------------------------------------
from axrange.twinconfig.registry import search_dirs  # noqa: E402


def test_search_dirs_single_value_round_trips_unchanged(tmp_path):
    """No `:` in the value -> exactly one dir, same as every pre-existing
    single-dir caller already passes -- proves the change is additive."""
    assert search_dirs(str(tmp_path)) == [tmp_path]
    assert search_dirs(tmp_path) == [tmp_path]


def test_search_dirs_none_or_empty_falls_back_to_the_default():
    """The default search path is the legacy `twin-configs/` root THEN the
    `twins/` root (`docs/WORK_ORDER_corp_twin.md` S0) -- later wins an id
    collision, so a migrated twin shadows its old catalog copy."""
    from axrange.twinconfig.manifest import default_twins_dir
    from axrange.twinconfig.registry import legacy_catalog_dir

    assert default_catalog_dir() == default_twins_dir()
    assert search_dirs(None) == [legacy_catalog_dir(), default_twins_dir()]
    assert search_dirs("") == [legacy_catalog_dir(), default_twins_dir()]


def test_search_dirs_colon_separated_string_splits_in_order(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    assert search_dirs(f"{a}:{b}") == [a, b]


def test_search_dirs_accepts_an_explicit_list(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    assert search_dirs([str(a), str(b)]) == [a, b]


def _write_config(path, config_id, description="a config"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"twin_config:\n  id: {config_id}\n  description: {description}\n"
        "  segments: []\n  nodes: []\n"
    )


def test_list_twin_configs_multi_dir_concatenates_both_catalogs(tmp_path):
    """The concrete fix: pointing at BOTH a 'shipped' and a 'cast' dir lists
    entries from both -- neither replaces the other."""
    shipped = tmp_path / "shipped"
    cast = tmp_path / "cast"
    _write_config(shipped / "corp-enterprise" / "corp-enterprise.yaml", "corp-enterprise")
    _write_config(cast / "corpapcn-twin" / "corpapcn-twin.yaml", "corpapcn-twin")

    entries = list_twin_configs(f"{shipped}:{cast}")
    assert {e.id for e in entries} == {"corp-enterprise", "corpapcn-twin"}


def test_load_twin_config_raw_multi_dir_later_directory_wins_a_collision(tmp_path):
    """An id present in BOTH directories resolves to the LATER one in the
    search path -- the override direction (a local/cast config shadows a
    same-named shipped one), matching `AXRANGE_PERSONAS_DIR`."""
    shipped = tmp_path / "shipped"
    cast = tmp_path / "cast"
    _write_config(shipped / "corp-enterprise" / "corp-enterprise.yaml", "corp-enterprise", "shipped version")
    _write_config(cast / "corp-enterprise" / "corp-enterprise.yaml", "corp-enterprise", "cast override")

    raw, path = load_twin_config_raw("corp-enterprise", catalog_dir=f"{shipped}:{cast}")
    assert raw["twin_config"]["description"] == "cast override"
    assert path == cast / "corp-enterprise" / "corp-enterprise.yaml"

    # Reversed order in the search path -> the (now-first) shipped entry wins.
    raw2, path2 = load_twin_config_raw("corp-enterprise", catalog_dir=f"{cast}:{shipped}")
    assert raw2["twin_config"]["description"] == "shipped version"
    assert path2 == shipped / "corp-enterprise" / "corp-enterprise.yaml"


def test_load_twin_config_raw_multi_dir_finds_an_id_unique_to_one_dir(tmp_path):
    """No collision -- an id that exists in only ONE of the search path's
    directories still resolves, regardless of position."""
    shipped = tmp_path / "shipped"
    cast = tmp_path / "cast"
    _write_config(shipped / "corp-enterprise" / "corp-enterprise.yaml", "corp-enterprise")
    _write_config(cast / "corpapcn-twin" / "corpapcn-twin.yaml", "corpapcn-twin")

    raw, _path = load_twin_config_raw("corpapcn-twin", catalog_dir=f"{shipped}:{cast}")
    assert raw["twin_config"]["id"] == "corpapcn-twin"


def test_load_twin_config_raw_multi_dir_not_found_names_every_dir_searched(tmp_path):
    import re

    shipped = tmp_path / "shipped"
    cast = tmp_path / "cast"
    shipped.mkdir()
    cast.mkdir()
    with pytest.raises(
        TwinConfigNotFoundError, match=re.escape(f"{shipped}:{cast}"),
    ):
        load_twin_config_raw("nope", catalog_dir=f"{shipped}:{cast}")


def test_resolve_twin_config_path_is_a_stat_not_a_parse(tmp_path, monkeypatch):
    """GAP-082 review round 2 (SF1): `twin_config_source_dir` parsed the whole
    file only to learn where it is -- ~210 ms on the real pit bundle, on
    every plan. The fast path (subfolder, then flat) names the file WITHOUT
    reading it."""
    from axrange.twinconfig import registry

    _write_config(tmp_path / "a" / "a.yaml", "a")
    _write_config(tmp_path / "flat.yaml", "flat")

    def _no_parse(*_a, **_kw):
        raise AssertionError("the fast path parsed a file")

    monkeypatch.setattr(registry, "load_yaml_raw", _no_parse)
    assert resolve_twin_config_path("a", catalog_dir=tmp_path) == tmp_path / "a" / "a.yaml"
    assert resolve_twin_config_path("flat", catalog_dir=tmp_path) == tmp_path / "flat.yaml"
    assert twin_config_source_dir("a", catalog_dir=tmp_path) == (tmp_path / "a").resolve()


def test_resolve_twin_config_path_names_the_file_the_loader_reads(tmp_path):
    """ONE resolution rule for the loader, the expander behind it and the
    source-directory lookup: a later directory wins a collision, a renamed
    file is found by its declared id, and a miss is the loader's miss."""
    shipped, cast = tmp_path / "shipped", tmp_path / "cast"
    _write_config(shipped / "x" / "x.yaml", "x", "shipped")
    _write_config(cast / "x" / "x.yaml", "x", "cast")
    _write_config(cast / "renamed.yaml", "y")
    search = f"{shipped}:{cast}"
    for config_id, expected in (("x", cast / "x" / "x.yaml"), ("y", cast / "renamed.yaml")):
        _raw, loaded = load_twin_config_raw(config_id, catalog_dir=search)
        assert resolve_twin_config_path(config_id, catalog_dir=search) == loaded == expected
    with pytest.raises(TwinConfigNotFoundError):
        resolve_twin_config_path("z", catalog_dir=search)
