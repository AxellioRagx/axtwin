"""REF-* rule tests."""
from __future__ import annotations

import textwrap
from pathlib import Path

from axrange.validator.engine import validate_full


def _ids(result) -> set[str]:
    return {f.rule_id for f in result.findings}


def _base() -> str:
    return textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24 }]
            nodes:
              - { id: h-corp, kind: host, net: corp, image: ubuntu-server-22@v5, requires: [linux] }
          apps:
            - id: a-app
              install: [{ pkg: nginx }]
          activity: []
        bindings: []
        overlays: {}
    """).strip()


def test_ref_010_dangling_app_in_binding(manifests):
    src = _base() + "\nbindings: [{ app: undefined-app, host: h-corp }]\n"
    # Note: previous bindings: [] is already in the base; appending here adds a
    # second bindings: key. Replace instead.
    src = _base().replace("bindings: []", "bindings: [{ app: undefined-app, host: h-corp }]")
    result = validate_full(text=src, manifests=manifests)
    assert "REF-010" in _ids(result)


def test_ref_020_dangling_host_in_binding(manifests):
    src = _base().replace(
        "bindings: []",
        "bindings: [{ app: a-app, host: nowhere }]",
    )
    result = validate_full(text=src, manifests=manifests)
    assert "REF-020" in _ids(result)


def test_ref_110_node_references_undefined_network(manifests):
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24 }]
            nodes:
              - { id: h-corp, kind: host, net: nowhere, image: ubuntu-server-22@v5, requires: [linux] }
          apps: []
          activity: []
        bindings: []
    """).strip()
    result = validate_full(text=src, manifests=manifests)
    assert "REF-110" in _ids(result)


def test_ref_120_missing_image(manifests):
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24 }]
            nodes:
              - { id: h-corp, kind: host, net: corp, image: nonexistent@v1, requires: [linux] }
          apps: []
          activity: []
        bindings: []
    """).strip()
    result = validate_full(text=src, manifests=manifests)
    assert "REF-120" in _ids(result)


def test_ref_120_skipped_in_structural_mode(empty_manifests):
    # validate_structural (Pass 1+2 only, e.g. the canvas on every keystroke)
    # runs with no manifests loaded at all AND sets
    # skip_unknown_image_lifecycle=True on its context — REF-120 must not
    # false-FAIL every image: ref just because the registry is empty in that
    # mode.
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24 }]
            nodes:
              - { id: h-corp, kind: host, net: corp, image: ubuntu-server-22@v5, requires: [linux] }
          apps: []
          activity: []
        bindings: []
    """).strip()
    from axrange.validator.engine import validate_structural

    result = validate_structural(text=src)
    assert "REF-120" not in _ids(result)


def test_ref_120_errors_when_validate_full_has_empty_registry(empty_manifests):
    # Regression guard: validate_full (the axrange validate/deploy path)
    # handed an EMPTY manifest registry (e.g. a wrong --images path) is a
    # genuine failure to load images — REF-120 must fail loudly here, not
    # skip. Skipping would let `axrange validate` PASS over a completely
    # unloaded image set and defer the failure to a cryptic deploy-time
    # error (clone-vm: template_vmid missing).
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24 }]
            nodes:
              - { id: h-corp, kind: host, net: corp, image: ubuntu-server-22@v5, requires: [linux] }
          apps: []
          activity: []
        bindings: []
    """).strip()
    result = validate_full(text=src, manifests=empty_manifests)
    assert "REF-120" in _ids(result)


def test_ref_130_unpinned_image_warns(manifests):
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24 }]
            nodes:
              - { id: h-corp, kind: host, net: corp, image: ubuntu-server-22, requires: [linux] }
          apps: []
          activity: []
        bindings: []
    """).strip()
    result = validate_full(text=src, manifests=manifests)
    ids = _ids(result)
    assert "REF-130" in ids
    # Without reproducible: true, REF-130 stays a warning.
    matches = [f for f in result.findings if f.rule_id == "REF-130"]
    assert matches[0].severity.value == "warning"


def test_ref_130_unpinned_image_under_reproducible_is_error(manifests):
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1, reproducible: true, seed: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24 }]
            nodes:
              - { id: h-corp, kind: host, net: corp, image: ubuntu-server-22, requires: [linux] }
          apps: []
          activity: []
        bindings: []
    """).strip()
    result = validate_full(text=src, manifests=manifests)
    matches = [f for f in result.findings if f.rule_id == "REF-130"]
    assert matches and matches[0].severity.value == "error"


def test_ref_180_duplicate_alias(manifests):
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24 }]
            nodes:
              - { id: a, kind: host, net: corp, image: ubuntu-server-22@v5, requires: [linux], aliases: [shared] }
              - { id: b, kind: host, net: corp, image: ubuntu-server-22@v5, requires: [linux], aliases: [shared] }
          apps: []
          activity: []
        bindings: []
    """).strip()
    result = validate_full(text=src, manifests=manifests)
    assert "REF-180" in _ids(result)


# --- REF-200: per-tap sensor routing (2026-06-03) -- RETIRED (`docs/
# WORK_ORDER_tap_eligibility.md` D6, Authority: user, 2026-07-28): the
# `tap_host`/`tap_net` -> `sensor` binding kind it validated is REMOVED from
# `bindings:` grammar entirely, and `node.tap` (the OTHER half of what it
# checked) is REMOVED from the IR too (D2) -- both premises are gone, so the
# rule retires with them (id never reused, `axrange/validator/catalog.py`).
# `_tap_base()` below drops `tap: true`/the `h-a`-vs-`h-q` distinction that
# existed ONLY to exercise REF-200 -- the REF-210 tests that still use it
# don't need either.


def _tap_base() -> str:
    return textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24 }]
            nodes:
              - { id: h-a, kind: host, net: corp, image: ubuntu-server-22@v5, requires: [linux] }
              - { id: h-q, kind: host, net: corp, image: ubuntu-server-22@v5, requires: [linux] }
          apps: []
          activity: []
        sensors:
          - { id: s1, primary: true }
          - { id: s2 }
        bindings: []
        overlays: {}
    """).strip()


# --- REF-210: typed sensors (sensors design, 2026-06-23) -------------------
def test_ref_210_known_type_ok(manifests):
    src = _tap_base().replace(
        "- { id: s2 }", "- { id: s2, type: suricata-sensor }")
    result = validate_full(text=src, manifests=manifests)
    assert "REF-210" not in _ids(result)


def test_ref_210_absent_type_defaults_ok(manifests):
    # s1/s2 carry no `type:` → default tcpdump-sensor; no finding.
    result = validate_full(text=_tap_base(), manifests=manifests)
    assert "REF-210" not in _ids(result)


def test_ref_210_unknown_type_fails(manifests):
    src = _tap_base().replace(
        "- { id: s2 }", "- { id: s2, type: wireshark-sensor }")
    result = validate_full(text=src, manifests=manifests)
    assert "REF-210" in _ids(result)


def test_ref_210_captures_undefined_source_fails(manifests):
    src = _tap_base().replace(
        "- { id: s2 }", "- { id: s2, captures: [nope] }")
    result = validate_full(text=src, manifests=manifests)
    assert "REF-210" in _ids(result)


def test_ref_210_transport_must_be_transport_net(manifests):
    # `corp` exists but is not transport: true → REF-210.
    src = _tap_base().replace(
        "- { id: s2 }", "- { id: s2, transport: corp }")
    result = validate_full(text=src, manifests=manifests)
    assert "REF-210" in _ids(result)


# --- REF-260: Exercise-tier scope-shift (docs/WORK_ORDER_exercise_tier.md) -
def test_ref_260_absent_with_no_activity_or_adversary(manifests):
    """The clean base IR (empty `layers.activity: []`, `overlays: {}`) must
    not fire REF-260 -- it is not a blanket warning, only a legacy-content
    one."""
    result = validate_full(text=_base(), manifests=manifests)
    assert "REF-260" not in _ids(result)


def test_ref_260_fires_warning_on_declared_activity(manifests):
    src = _base().replace(
        "  activity: []\n",
        "  activity:\n"
        "    - id: act-recon\n"
        "      actor: red-operator\n"
        "      inline: { steps: [{ run: 'echo hi' }] }\n",
    ).replace("bindings: []", "bindings: [{ activity: act-recon, host: h-corp }]")
    result = validate_full(text=src, manifests=manifests)
    ids = _ids(result)
    assert "REF-260" in ids
    # Warning only -- must not fail validation on its own.
    assert result.exit_code == 0


def test_ref_260_fires_warning_on_declared_adversary_overlay(manifests):
    src = _base().replace(
        "overlays: {}",
        "overlays:\n"
        "  adversary:\n"
        "    - id: red-team\n"
        "      targets: [h-corp]\n"
        "      server: http://caldera.o2.lab:8888\n",
    )
    result = validate_full(text=src, manifests=manifests)
    ids = _ids(result)
    assert "REF-260" in ids
    assert result.exit_code == 0
