"""IR parser tests: round-trip, includes, duplicate-ID detection."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from axrange.ir.parser import parse_ir


def test_parses_minimal(tmp_path: Path) -> None:
    src = tmp_path / "scenario.yaml"
    src.write_text(textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: []
            nodes: []
          apps: []
          activity: []
        bindings: []
    """).strip())
    res = parse_ir(path=src)
    assert res.ir.scenario.id == "m"
    assert res.ir.scenario.version == 1
    assert res.parse_findings == []


def test_parses_inline_text() -> None:
    res = parse_ir(text="scenario_schema_version: 1\nscenario: {id: m, version: 1}\n")
    assert res.ir.scenario_schema_version == 1


def test_include_splices_lists(tmp_path: Path) -> None:
    frag = tmp_path / "frag.yaml"
    frag.write_text(textwrap.dedent("""
        layers:
          infra:
            networks:
              - { id: corp, cidr: 10.0.0.0/24 }
            nodes:
              - { id: h-corp, kind: host, net: corp, image: ubuntu-server-22, requires: [linux] }
    """).strip())
    main = tmp_path / "scenario.yaml"
    main.write_text(textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1, includes: ["./frag.yaml"] }
        layers:
          infra:
            networks: []
            nodes: []
          apps: []
          activity: []
        bindings: []
    """).strip())
    res = parse_ir(path=main)
    assert res.parse_findings == []
    assert len(res.ir.layers.infra.networks) == 1
    assert len(res.ir.layers.infra.nodes) == 1
    assert res.ir.layers.infra.nodes[0].id == "h-corp"


def test_include_duplicate_id_is_inc_010(tmp_path: Path) -> None:
    frag = tmp_path / "frag.yaml"
    frag.write_text(textwrap.dedent("""
        layers:
          infra:
            networks: []
            nodes:
              - { id: h-corp, kind: host, net: corp }
    """).strip())
    main = tmp_path / "scenario.yaml"
    main.write_text(textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1, includes: ["./frag.yaml"] }
        layers:
          infra:
            networks:
              - { id: corp, cidr: 10.0.0.0/24 }
            nodes:
              - { id: h-corp, kind: host, net: corp }
          apps: []
          activity: []
        bindings: []
    """).strip())
    res = parse_ir(path=main)
    rule_ids = {p.rule_id for p in res.parse_findings}
    assert "INC-010" in rule_ids


def test_include_missing_file_is_inc_020(tmp_path: Path) -> None:
    main = tmp_path / "scenario.yaml"
    main.write_text(textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1, includes: ["./does-not-exist.yaml"] }
        layers: { infra: { networks: [], nodes: [] }, apps: [], activity: [] }
        bindings: []
    """).strip())
    res = parse_ir(path=main)
    rule_ids = {p.rule_id for p in res.parse_findings}
    assert "INC-020" in rule_ids


def test_include_schema_version_mismatch_is_inc_040(tmp_path: Path) -> None:
    frag = tmp_path / "frag.yaml"
    frag.write_text(textwrap.dedent("""
        scenario_schema_version: 2
        layers:
          infra: { networks: [], nodes: [] }
    """).strip())
    main = tmp_path / "scenario.yaml"
    main.write_text(textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1, includes: ["./frag.yaml"] }
        layers: { infra: { networks: [], nodes: [] }, apps: [], activity: [] }
        bindings: []
    """).strip())
    res = parse_ir(path=main)
    rule_ids = {p.rule_id for p in res.parse_findings}
    assert "INC-040" in rule_ids


def test_include_splices_fabric_regions_and_scalars(tmp_path: Path) -> None:
    """Code-review finding 1: `fabric:` must splice across includes like the
    other mergeable sections — `regions` list-concats, and the scalar
    sub-keys (`fabric_asn`, `tenant`) carry over from a fragment without
    tripping the generic top-level-key-override guard (which doesn't know
    about `fabric` sub-structure).
    """
    frag = tmp_path / "frag.yaml"
    frag.write_text(textwrap.dedent("""
        fabric:
          fabric_asn: 65000
          tenant: { name: optra, l3vni: 10999, table: 1000 }
          regions:
            - { id: dc-east, igp: isis, isis_area: "49.0001" }
    """).strip())
    main = tmp_path / "scenario.yaml"
    main.write_text(textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1, includes: ["./frag.yaml"] }
        layers:
          infra: { networks: [], nodes: [] }
          apps: []
          activity: []
        bindings: []
        fabric:
          regions:
            - { id: wan, igp: none }
    """).strip())
    res = parse_ir(path=main)
    assert res.parse_findings == []
    assert res.ir.fabric is not None
    assert res.ir.fabric.fabric_asn == 65000
    assert res.ir.fabric.tenant is not None
    assert res.ir.fabric.tenant.name == "optra"
    assert res.ir.fabric.tenant.l3vni == 10999
    region_ids = {r.id for r in res.ir.fabric.regions}
    assert region_ids == {"wan", "dc-east"}


def test_include_fabric_scalar_override_is_inc_010(tmp_path: Path) -> None:
    """A fragment may only SET fabric_asn/tenant, not override an already-set
    value — mirrors the strict-no-override semantics of other scalar
    sections.
    """
    frag = tmp_path / "frag.yaml"
    frag.write_text(textwrap.dedent("""
        fabric:
          fabric_asn: 65001
    """).strip())
    main = tmp_path / "scenario.yaml"
    main.write_text(textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1, includes: ["./frag.yaml"] }
        layers: { infra: { networks: [], nodes: [] }, apps: [], activity: [] }
        bindings: []
        fabric:
          fabric_asn: 65000
    """).strip())
    res = parse_ir(path=main)
    rule_ids = {p.rule_id for p in res.parse_findings}
    assert "INC-010" in rule_ids
    assert res.ir.fabric.fabric_asn == 65000


def test_free_form_id_key_does_not_trigger_inc_010(tmp_path: Path) -> None:
    """Code-review finding 2: a dict key literally named `id` inside a
    free-form value-map (apps[].env, activity[].inline.steps[]) is not an IR
    entity identifier and must not trip strict-no-override, even when the
    same literal value also happens to appear as a real entity id.
    """
    main = tmp_path / "scenario.yaml"
    main.write_text(textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24 }]
            nodes: [{ id: h-corp, kind: host, net: corp }]
          apps:
            - id: web-1
              host: h-corp
              env: { id: corp }
          activity:
            - id: act-1
              inline:
                steps:
                  - { id: corp, actor: h-corp, action: run, args: {} }
        bindings: []
    """).strip())
    res = parse_ir(path=main)
    assert res.parse_findings == []


def test_include_duplicate_id_attributed_to_fragment_file(tmp_path: Path) -> None:
    """Code-review finding 3: a duplicate id introduced by a fragment must be
    attributed to that fragment's file, not always to the main file.
    """
    frag = tmp_path / "frag.yaml"
    frag.write_text(textwrap.dedent("""
        layers:
          infra:
            networks: []
            nodes:
              - { id: h-corp, kind: host, net: corp }
    """).strip())
    main = tmp_path / "scenario.yaml"
    main.write_text(textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1, includes: ["./frag.yaml"] }
        layers:
          infra:
            networks:
              - { id: corp, cidr: 10.0.0.0/24 }
            nodes:
              - { id: h-corp, kind: host, net: corp }
          apps: []
          activity: []
        bindings: []
    """).strip())
    res = parse_ir(path=main)
    dupes = [p for p in res.parse_findings if p.rule_id == "INC-010" and p.extra.get("id") == "h-corp"]
    assert len(dupes) == 1
    assert dupes[0].file == str(frag.resolve())
    assert dupes[0].extra["other_file"] == str(main.resolve())
