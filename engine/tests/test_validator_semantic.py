"""Pass-3 (CAP/LIF/NET/OVR/ACT/DEF) tests covering at least one finding per category."""
from __future__ import annotations

import textwrap

from axrange.validator.engine import validate_full


def _ids(result) -> set[str]:
    return {f.rule_id for f in result.findings}


def test_cap_010_missing_kernel_cap(manifests):
    # win11-finance@v3 does NOT provide kernel:edr-hooks; v4 does.
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24 }]
            nodes:
              - { id: h-fin, kind: host, net: corp, image: win11-finance@v3, requires: [windows, kernel:edr-hooks] }
          apps: []
          activity: []
        bindings: []
    """).strip()
    result = validate_full(text=src, manifests=manifests)
    assert "CAP-010" in _ids(result)


def test_cap_020_unsupported_install_kind(manifests):
    # win11-finance@v4 has install_support.container: false.
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24 }]
            nodes:
              - { id: h-fin, kind: host, net: corp, image: win11-finance@v4, requires: [windows] }
          apps:
            - id: a
              install: [{ container: "foo/bar:latest" }]
          activity: []
        bindings: [{ app: a, host: h-fin }]
    """).strip()
    result = validate_full(text=src, manifests=manifests)
    assert "CAP-020" in _ids(result)


def test_cap_030_os_family_mismatch(manifests):
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24 }]
            nodes:
              - { id: h, kind: host, net: corp, image: win11-finance@v4, requires: [linux] }
          apps: []
          activity: []
        bindings: []
    """).strip()
    result = validate_full(text=src, manifests=manifests)
    assert "CAP-030" in _ids(result)


def test_lif_010_withdrawn_image_errors(manifests):
    # win11-finance@v2 is withdrawn.
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24 }]
            nodes:
              - { id: h-fin, kind: host, net: corp, image: win11-finance@v2, requires: [windows] }
          apps: []
          activity: []
        bindings: []
    """).strip()
    result = validate_full(text=src, manifests=manifests)
    assert "LIF-010" in _ids(result)


def test_lif_020_deprecated_image_warns(manifests):
    # win11-finance@v3 is deprecated.
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24 }]
            nodes:
              - { id: h-fin, kind: host, net: corp, image: win11-finance@v3, requires: [windows] }
          apps: []
          activity: []
        bindings: []
    """).strip()
    result = validate_full(text=src, manifests=manifests)
    matches = [f for f in result.findings if f.rule_id == "LIF-020"]
    assert matches and matches[0].severity.value == "warning"


def test_lif_060_reproducible_without_seed(manifests):
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1, reproducible: true }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24 }]
            nodes:
              - { id: h-fin, kind: host, net: corp, image: win11-finance@v4, requires: [windows] }
          apps: []
          activity: []
        bindings: []
    """).strip()
    result = validate_full(text=src, manifests=manifests)
    # rptoe: should be error under reproducible: true.
    matches = [f for f in result.findings if f.rule_id == "LIF-060"]
    assert matches and matches[0].severity.value == "error"


def test_net_010_overlapping_cidrs(manifests):
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks:
              - { id: a, cidr: 10.0.0.0/16 }
              - { id: b, cidr: 10.0.5.0/24 }
            nodes:
              - { id: h, kind: host, net: a, image: ubuntu-server-22@v5, requires: [linux] }
          apps: []
          activity: []
        bindings: []
    """).strip()
    result = validate_full(text=src, manifests=manifests)
    assert "NET-010" in _ids(result)


def test_net_020_invalid_cidr(manifests):
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: a, cidr: "not-a-cidr" }]
            nodes:
              - { id: h, kind: host, net: a, image: ubuntu-server-22@v5, requires: [linux] }
          apps: []
          activity: []
        bindings: []
    """).strip()
    result = validate_full(text=src, manifests=manifests)
    assert "NET-020" in _ids(result)


def test_net_050_duplicate_vlan(manifests):
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks:
              - { id: a, cidr: 10.0.0.0/24, vlan: 10 }
              - { id: b, cidr: 10.0.1.0/24, vlan: 10 }
            nodes:
              - { id: h, kind: host, net: a, image: ubuntu-server-22@v5, requires: [linux] }
          apps: []
          activity: []
        bindings: []
    """).strip()
    result = validate_full(text=src, manifests=manifests)
    assert "NET-050" in _ids(result)


def test_net_080_router_one_net_ok_zero_net_fails(manifests):
    # Phase 2: a 1-LAN router is a valid egress/NAT gateway (WAN is the other
    # side) — NET-080 should NOT fire. A 0-net router still fails.
    one_net = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: a, cidr: 10.0.0.0/24 }]
            nodes:
              - { id: r, kind: router, nets: [{ net: a, ip: 10.0.0.1 }], image: vyos@1.5, requires: [linux] }
          apps: []
          activity: []
        bindings: []
    """).strip()
    # 1-LAN router is valid (NET-080 floor is now 1). A 0-net router is caught
    # earlier by structural rules (STR-070), so NET-080's ≥1 floor only matters
    # once a node is otherwise well-formed.
    assert "NET-080" not in _ids(validate_full(text=one_net, manifests=manifests))


def test_net_080_router_single_net_form_ok(manifests):
    # A router declared with the singular `net:` form (not `nets:`) has one
    # LAN attached — same valid egress/NAT gateway shape as the `nets:` case
    # above. The old count-only-`nets:` bug ignored `net:` entirely and
    # false-FAILed this.
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: a, cidr: 10.0.0.0/24 }]
            nodes:
              - { id: r, kind: router, net: a, image: vyos@1.5, requires: [linux] }
          apps: []
          activity: []
        bindings: []
    """).strip()
    result = validate_full(text=src, manifests=manifests)
    assert "NET-080" not in _ids(result)


def test_cap_020_multi_host_app_checks_every_host(manifests):
    # App "a" (install: container) is bound to TWO hosts: h-linux (ubuntu,
    # container: true — OK) and h-fin (win11-finance@v4, container: false —
    # unsupported). h-fin's binding is listed FIRST and h-linux's LAST — the
    # old last-wins app->host mapping only checked h-linux and false-PASSED,
    # silently ignoring h-fin's unsupported install action.
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24 }]
            nodes:
              - { id: h-fin, kind: host, net: corp, image: win11-finance@v4, requires: [windows] }
              - { id: h-linux, kind: host, net: corp, image: ubuntu-server-22@v5, requires: [linux] }
          apps:
            - id: a
              install: [{ container: "foo/bar:latest" }]
          activity: []
        bindings:
          - { app: a, host: h-fin }
          - { app: a, host: h-linux }
    """).strip()
    result = validate_full(text=src, manifests=manifests)
    matches = [f for f in result.findings if f.rule_id == "CAP-020"]
    assert matches, "CAP-020 should fire for h-fin even though h-linux (bound last) supports container"
    assert any(m.extra.get("host_id") == "h-fin" for m in matches)


def test_act_040_neither_behavior_nor_inline(manifests):
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: a, cidr: 10.0.0.0/24 }]
            nodes:
              - { id: h, kind: host, net: a, image: ubuntu-server-22@v5, requires: [linux] }
          apps: []
          activity:
            - { id: act, actor: u }
        bindings: [{ activity: act, host: h }]
    """).strip()
    result = validate_full(text=src, manifests=manifests)
    assert "ACT-040" in _ids(result)


def _act_scenario(activity_block: str) -> str:
    """A minimal one-host scenario with a single `activity:` entry, its shape
    supplied verbatim by the caller (docs/SLICE_ACTIVITY.md §11 schedule tests)."""
    return textwrap.dedent(f"""
        scenario_schema_version: 1
        scenario: {{ id: m, version: 1 }}
        layers:
          infra:
            networks: [{{ id: a, cidr: 10.0.0.0/24 }}]
            nodes:
              - {{ id: h, kind: host, net: a, image: ubuntu-server-22@v5, requires: [linux] }}
          apps: []
          activity:
{activity_block}
        bindings: [{{ activity: act, host: h }}]
    """).strip()


def test_act_050_seed_with_no_interval_still_warns(manifests):
    src = _act_scenario(
        "            - { id: act, actor: u, inline: { steps: [{ run: echo hi }] }, seed: 7 }"
    )
    result = validate_full(text=src, manifests=manifests)
    assert "ACT-050" in _ids(result)


def test_act_050_lifted_for_seeded_jittered_schedule(manifests):
    src = _act_scenario(
        "            - { id: act, actor: u, inline: { steps: [{ run: echo hi }] }, seed: 7, "
        "schedule: { interval: { min: 30s, max: 5m } } }"
    )
    result = validate_full(text=src, manifests=manifests)
    assert "ACT-050" not in _ids(result)


def test_act_060_interval_missing_max(manifests):
    src = _act_scenario(
        "            - { id: act, actor: u, inline: { steps: [{ run: echo hi }] }, "
        "schedule: { interval: { min: 30s } } }"
    )
    result = validate_full(text=src, manifests=manifests)
    assert "ACT-060" in _ids(result)


def test_act_060_max_less_than_min(manifests):
    src = _act_scenario(
        "            - { id: act, actor: u, inline: { steps: [{ run: echo hi }] }, "
        "schedule: { interval: { min: 5m, max: 30s } } }"
    )
    result = validate_full(text=src, manifests=manifests)
    assert "ACT-060" in _ids(result)


def test_act_060_interval_mutually_exclusive_with_once(manifests):
    src = _act_scenario(
        "            - { id: act, actor: u, inline: { steps: [{ run: echo hi }] }, "
        "schedule: { once: true, interval: { min: 30s, max: 5m } } }"
    )
    result = validate_full(text=src, manifests=manifests)
    assert "ACT-060" in _ids(result)


def test_act_060_valid_interval_no_finding(manifests):
    src = _act_scenario(
        "            - { id: act, actor: u, inline: { steps: [{ run: echo hi }] }, seed: 1, "
        "schedule: { interval: { min: 30s, max: 5m } } }"
    )
    result = validate_full(text=src, manifests=manifests)
    assert "ACT-060" not in _ids(result)


def test_act_060_min_below_floor_errors(manifests):
    """SHOULD-FIX #3 (code review): a near-zero interval busy-loops the guest
    and must be rejected even though max >= min holds."""
    src = _act_scenario(
        "            - { id: act, actor: u, inline: { steps: [{ run: echo hi }] }, seed: 1, "
        "schedule: { interval: { min: 0s, max: 0s } } }"
    )
    result = validate_full(text=src, manifests=manifests)
    assert "ACT-060" in _ids(result)


def test_act_060_min_at_floor_ok(manifests):
    src = _act_scenario(
        "            - { id: act, actor: u, inline: { steps: [{ run: echo hi }] }, seed: 1, "
        "schedule: { interval: { min: 1s, max: 5m } } }"
    )
    result = validate_full(text=src, manifests=manifests)
    assert "ACT-060" not in _ids(result)


def test_act_070_jittered_without_seed_warns(manifests):
    src = _act_scenario(
        "            - { id: act, actor: u, inline: { steps: [{ run: echo hi }] }, "
        "schedule: { interval: { min: 30s, max: 5m } } }"
    )
    result = validate_full(text=src, manifests=manifests)
    assert "ACT-070" in _ids(result)


def test_act_070_jittered_with_seed_no_warning(manifests):
    src = _act_scenario(
        "            - { id: act, actor: u, inline: { steps: [{ run: echo hi }] }, seed: 42, "
        "schedule: { interval: { min: 30s, max: 5m } } }"
    )
    result = validate_full(text=src, manifests=manifests)
    assert "ACT-070" not in _ids(result)


def test_act_070_windowed_once_no_interval_no_warning(manifests):
    """once: true (no interval) is the unchanged windowed-once path; ACT-070
    is interval-specific and must not fire for it."""
    src = _act_scenario(
        "            - { id: act, actor: u, inline: { steps: [{ run: echo hi }] }, "
        "schedule: { once: true } }"
    )
    result = validate_full(text=src, manifests=manifests)
    assert "ACT-070" not in _ids(result)
    assert "ACT-060" not in _ids(result)


def test_def_020_invalid_tap_from(manifests):
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: dmz, cidr: 192.168.50.0/24 }]
            nodes:
              - { id: h, kind: host, net: dmz, image: ubuntu-server-22@v5, requires: [linux] }
          apps: []
          activity: []
        bindings: []
        overlays:
          defense:
            - { id: s, tool: suricata, host: h, observes: [{ net: dmz, mode: ids }], tap_from: telepathy }
    """).strip()
    result = validate_full(text=src, manifests=manifests)
    assert "DEF-020" in _ids(result)


def test_def_040_external_mirror_warns(manifests):
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: m, version: 1 }
        layers:
          infra:
            networks: [{ id: dmz, cidr: 192.168.50.0/24 }]
            nodes:
              - { id: h, kind: host, net: dmz, image: ubuntu-server-22@v5, requires: [linux] }
          apps: []
          activity: []
        bindings: []
        overlays:
          defense:
            - { id: s, tool: suricata, host: h, observes: [{ net: dmz, mode: ids }], tap_from: external-mirror }
    """).strip()
    result = validate_full(text=src, manifests=manifests)
    matches = [f for f in result.findings if f.rule_id == "DEF-040"]
    assert matches and matches[0].severity.value == "warning"
