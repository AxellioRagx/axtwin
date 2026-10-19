"""STR-* rule tests: structural pass."""
from __future__ import annotations

import textwrap

from axrange.validator.engine import validate_structural


def _ids(result) -> set[str]:
    return {f.rule_id for f in result.findings}


def test_str_010_yaml_parses() -> None:
    bad = "scenario_schema_version: 1\n  : not valid yaml\n"
    result = validate_structural(text=bad)
    assert "STR-010" in _ids(result) or "STR-040" in _ids(result)


def test_str_020_missing_schema_version() -> None:
    src = "scenario: { id: m, version: 1 }\nlayers: { infra: { networks: [], nodes: [] }, apps: [] }\nbindings: []\n"
    result = validate_structural(text=src)
    assert "STR-020" in _ids(result)


def test_str_020_unknown_schema_version() -> None:
    src = "scenario_schema_version: 99\nscenario: { id: m, version: 1 }\nlayers: { infra: { networks: [], nodes: [] }, apps: [] }\nbindings: []\n"
    result = validate_structural(text=src)
    assert "STR-020" in _ids(result)


def test_str_020_recognizes_v3() -> None:
    """Recognized set widened `{1, 2}` -> `{1, 2, 3}` (Authority: user,
    2026-07-28, `docs/WORK_ORDER_tap_eligibility.md` §4.3) -- `3` marks an
    IR as "authored post-tap-removal"; declaring it must not itself be a
    STR-020 error (code-review MUST-FIX: the check still hard-coded
    `(1, 2)` after the schema/docs were widened to `{1, 2, 3}`)."""
    src = "scenario_schema_version: 3\nscenario: { id: m, version: 1 }\nlayers: { infra: { networks: [], nodes: [] }, apps: [] }\nbindings: []\n"
    result = validate_structural(text=src)
    assert "STR-020" not in _ids(result)


def test_str_040_missing_scenario_block() -> None:
    src = "scenario_schema_version: 1\nlayers: { infra: { networks: [], nodes: [] }, apps: [] }\nbindings: []\n"
    result = validate_structural(text=src)
    assert "STR-040" in _ids(result)


def test_str_070_node_with_both_net_and_nets() -> None:
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks:
              - { id: corp, cidr: 10.0.0.0/24 }
            nodes:
              - id: h1
                kind: host
                net: corp
                nets: [{ net: corp, ip: 10.0.0.5 }]
                image: ubuntu-server-22@v5
                requires: [linux]
          apps: []
          activity: []
        bindings: []
    """).strip()
    result = validate_structural(text=src)
    assert "STR-070" in _ids(result)


def test_str_070_node_with_neither_net_nor_nets() -> None:
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: []
            nodes:
              - { id: h1, kind: host, image: ubuntu-server-22@v5, requires: [linux] }
          apps: []
          activity: []
        bindings: []
    """).strip()
    result = validate_structural(text=src)
    assert "STR-070" in _ids(result)


def test_str_080_invalid_kind() -> None:
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24 }]
            nodes:
              - { id: h1, kind: laptop, net: corp, image: ubuntu-server-22@v5, requires: [linux] }
          apps: []
          activity: []
        bindings: []
    """).strip()
    result = validate_structural(text=src)
    assert "STR-080" in _ids(result)


def test_str_090_duplicate_node_id() -> None:
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24 }]
            nodes:
              - { id: h1, kind: host, net: corp, image: ubuntu-server-22@v5, requires: [linux] }
              - { id: h1, kind: host, net: corp, image: ubuntu-server-22@v5, requires: [linux] }
          apps: []
          activity: []
        bindings: []
    """).strip()
    result = validate_structural(text=src)
    # Either INC-010 from parser (duplicate id detection) or STR-090 fires —
    # both indicate the same condition; we accept either.
    ids = _ids(result)
    assert "STR-090" in ids or "INC-010" in ids


def test_str_110_unknown_capability() -> None:
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24 }]
            nodes:
              - { id: h1, kind: host, net: corp, image: ubuntu-server-22@v5, requires: [linux, host:moon-base] }
          apps: []
          activity: []
        bindings: []
    """).strip()
    result = validate_structural(text=src)
    assert "STR-110" in _ids(result)


def test_str_120_no_os_family() -> None:
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24 }]
            nodes:
              - { id: h1, kind: host, net: corp, image: ubuntu-server-22@v5, requires: [host:tpm] }
          apps: []
          activity: []
        bindings: []
    """).strip()
    result = validate_structural(text=src)
    assert "STR-120" in _ids(result)


def test_str_130_unknown_install_action() -> None:
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24 }]
            nodes:
              - { id: h1, kind: host, net: corp, image: ubuntu-server-22@v5, requires: [linux] }
          apps:
            - id: a
              install:
                - { snap: nginx }
          activity: []
        bindings: []
    """).strip()
    result = validate_structural(text=src)
    assert "STR-130" in _ids(result)


def test_str_130_double_action_key() -> None:
    # {pkg: nginx, script: setup.sh} — two of the five closed action keys in
    # one install action. install_action_kind() resolves "pkg" first, and
    # "script" is itself a recognized action key, so a naive extras-diff
    # would false-pass this. STR-130 must flag it.
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24 }]
            nodes:
              - { id: h1, kind: host, net: corp, image: ubuntu-server-22@v5, requires: [linux] }
          apps:
            - id: a
              install:
                - { pkg: nginx, script: setup.sh }
          activity: []
        bindings: []
    """).strip()
    result = validate_structural(text=src)
    assert "STR-130" in _ids(result)


def test_str_160_reproducible_not_bool() -> None:
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1, reproducible: "yes" }
        layers: { infra: { networks: [], nodes: [] }, apps: [], activity: [] }
        bindings: []
    """).strip()
    result = validate_structural(text=src)
    assert "STR-160" in _ids(result)


def test_str_180_unknown_top_level_key_is_info() -> None:
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers: { infra: { networks: [], nodes: [] }, apps: [], activity: [] }
        bindings: []
        future_feature: { foo: bar }
    """).strip()
    result = validate_structural(text=src)
    ids = _ids(result)
    assert "STR-180" in ids
    # Info severity does NOT cause FAIL.
    assert result.disposition.value == "PASS"


# ---------------------------------------------------------------------------
# STR-200 (`docs/WORK_ORDER_tap_eligibility.md`, Authority: user, 2026-07-28):
# `tap:` is REMOVED from IR_SCHEMA entirely -- a leftover `tap:` anywhere
# (node / leg / network) must be rejected loudly, not silently absorbed by
# Pydantic's `extra="allow"` (LES-111). Supersedes the retired STR-190
# (which only checked a PRESENT tap:'s boolean-ness); STR-200 rejects mere
# PRESENCE, regardless of value -- covered explicitly below.
# ---------------------------------------------------------------------------
def _findings_by_id(result, rule_id: str):
    return [f for f in result.findings if f.rule_id == rule_id]


def test_str_200_flags_leftover_tap_on_node() -> None:
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24 }]
            nodes:
              - { id: h1, kind: host, net: corp, image: ubuntu-server-22@v5, requires: [linux], tap: true }
          apps: []
          activity: []
        bindings: []
    """).strip()
    result = validate_structural(text=src)
    hits = _findings_by_id(result, "STR-200")
    assert len(hits) == 1
    assert hits[0].location == "layers.infra.nodes[0].tap"


def test_str_200_flags_leftover_tap_on_leg() -> None:
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24 }, { id: dmz, cidr: 10.0.1.0/24 }]
            nodes:
              - id: r1
                kind: router
                nets: [{ net: corp, ip: 10.0.0.1 }, { net: dmz, ip: 10.0.1.1, tap: true }]
                image: ubuntu-server-22@v5
                requires: [linux]
          apps: []
          activity: []
        bindings: []
    """).strip()
    result = validate_structural(text=src)
    hits = _findings_by_id(result, "STR-200")
    assert len(hits) == 1
    assert hits[0].location == "layers.infra.nodes[0].nets[1].tap"


def test_str_200_flags_leftover_tap_on_network() -> None:
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24, tap: true }]
            nodes:
              - { id: h1, kind: host, net: corp, image: ubuntu-server-22@v5, requires: [linux] }
          apps: []
          activity: []
        bindings: []
    """).strip()
    result = validate_structural(text=src)
    hits = _findings_by_id(result, "STR-200")
    assert len(hits) == 1
    assert hits[0].location == "layers.infra.networks[0].tap"


def test_str_200_fires_regardless_of_value_not_just_non_bool() -> None:
    """Unlike the retired STR-190 (only a non-bool `tap:` value flagged),
    STR-200 rejects the field's mere PRESENCE -- a `tap: false` (a
    perfectly well-typed bool, the value an old default-omitted field would
    have carried) is flagged exactly like `tap: true`."""
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24, tap: false }]
            nodes:
              - { id: h1, kind: host, net: corp, image: ubuntu-server-22@v5, requires: [linux] }
          apps: []
          activity: []
        bindings: []
    """).strip()
    result = validate_structural(text=src)
    assert "STR-200" in _ids(result)


def test_str_200_absent_when_no_tap_field_anywhere() -> None:
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24 }]
            nodes:
              - id: r1
                kind: router
                nets: [{ net: corp, ip: 10.0.0.1 }]
                image: ubuntu-server-22@v5
                requires: [linux]
          apps: []
          activity: []
        bindings: []
    """).strip()
    result = validate_structural(text=src)
    assert "STR-200" not in _ids(result)


def test_str_200_still_fires_on_v3_ir_with_leftover_tap() -> None:
    """Declaring `scenario_schema_version: 3` (the shipped docs' own
    instruction for a post-removal twin) does not exempt an IR from
    STR-200 -- the rejection fires on a leftover `tap:` regardless of
    declared version, exactly as it already does under `1`/`2` above."""
    src = textwrap.dedent("""
        scenario_schema_version: 3
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24 }]
            nodes:
              - { id: h1, kind: host, net: corp, image: ubuntu-server-22@v5, requires: [linux], tap: true }
          apps: []
          activity: []
        bindings: []
    """).strip()
    result = validate_structural(text=src)
    assert "STR-020" not in _ids(result)
    hits = _findings_by_id(result, "STR-200")
    assert len(hits) == 1
    assert hits[0].location == "layers.infra.nodes[0].tap"


def test_str_190_no_longer_registered() -> None:
    """STR-190 is retired (superseded by STR-200) -- the id must never fire,
    and per docs/VALIDATOR.md §3.3 a retired id is never reused for
    something else either (that's a catalog-level invariant, checked in
    test_catalog.py; this just pins the behavioral half)."""
    from axrange.validator.catalog import RULES

    assert "STR-190" not in RULES


# ---------------------------------------------------------------------------
# D6 (`docs/WORK_ORDER_tap_eligibility.md`, Authority: user, 2026-07-28): the
# `tap_host`/`tap_net` -> `sensor` binding kind is REMOVED from `Binding`
# (IR_SCHEMA) and from `_binding_kind`'s recognized-kind set -- no dedicated
# rule needed, STR-100's existing closed binding-kind set catches a leftover
# one as an unrecognized kind. REF-200 (which used to validate this kind's
# references) retired alongside it -- confirmed separately in
# test_validator_ref.py.
# ---------------------------------------------------------------------------
def test_str_100_rejects_leftover_tap_host_sensor_binding() -> None:
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24 }]
            nodes:
              - { id: h1, kind: host, net: corp, image: ubuntu-server-22@v5, requires: [linux] }
          apps: []
          activity: []
        bindings: [{ tap_host: h1, sensor: s1 }]
    """).strip()
    result = validate_structural(text=src)
    hits = _findings_by_id(result, "STR-100")
    assert len(hits) == 1
    assert hits[0].location == "bindings[0]"


def test_str_100_rejects_leftover_tap_net_binding() -> None:
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24 }]
            nodes:
              - { id: h1, kind: host, net: corp, image: ubuntu-server-22@v5, requires: [linux] }
          apps: []
          activity: []
        bindings: [{ tap_net: corp, sensor: s1 }]
    """).strip()
    result = validate_structural(text=src)
    assert "STR-100" in _ids(result)
