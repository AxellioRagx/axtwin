"""The validator rule catalog: all 53 IR rules + 7 cluster rules per
docs/VALIDATOR.md §5.

This file is the source-of-truth surface. The check functions in
axrange.validator.checks.* implement these by `rule_id`. Adding a rule is a
two-edit change: add it here, register a check in the right `checks/` module.

Rule IDs are stable across versions. Never renumber. Never reuse a retired ID
(spec, §3.3). Severities and `reproducible_promotes_to_error` flags reflect
the ratified surface — changes require a CHANGELOG entry.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from axrange.validator.finding import RuleCategory, Severity


@dataclass(frozen=True)
class RuleExamples:
    fails: str = ""
    passes: str = ""


@dataclass(frozen=True)
class Rule:
    id: str
    category: RuleCategory
    severity: Severity
    pass_: int  # 1, 2, 3, or 4
    title: str
    description: str
    rationale: str
    input: tuple[str, ...] = ()
    message_template: str = ""
    fixable: bool = False
    examples: RuleExamples = field(default_factory=RuleExamples)
    related: tuple[str, ...] = ()
    reproducible_promotes_to_error: bool = False


def _R(
    id: str,
    category: RuleCategory,
    severity: Severity,
    pass_: int,
    title: str,
    *,
    description: str = "",
    rationale: str = "",
    input: tuple[str, ...] = (),
    message_template: str = "",
    fixable: bool = False,
    fails: str = "",
    passes: str = "",
    related: tuple[str, ...] = (),
    reproducible_promotes_to_error: bool = False,
) -> Rule:
    return Rule(
        id=id,
        category=category,
        severity=severity,
        pass_=pass_,
        title=title,
        description=description.strip(),
        rationale=rationale.strip(),
        input=input,
        message_template=message_template,
        fixable=fixable,
        examples=RuleExamples(fails=fails.strip(), passes=passes.strip()),
        related=related,
        reproducible_promotes_to_error=reproducible_promotes_to_error,
    )


# ---------------------------------------------------------------------------
# STR — Structural (Pass 1, halt on error)
# ---------------------------------------------------------------------------

STR = RuleCategory.STRUCTURAL
ERR, WARN, INFO = Severity.ERROR, Severity.WARNING, Severity.INFO

_RULES_LIST: list[Rule] = [
    _R("STR-010", STR, ERR, 1, "YAML parses",
       message_template="Failed to parse YAML: {reason}"),
    _R("STR-020", STR, ERR, 1, "scenario_schema_version present and recognized",
       message_template="scenario_schema_version is missing or unrecognized: {value}"),
    _R("STR-030", STR, ERR, 1, "scenario.version present and is a non-negative int",
       message_template="scenario.version must be a non-negative int; got {value}"),
    _R("STR-040", STR, ERR, 1, "scenario: header present with required fields",
       message_template="scenario header missing required field(s): {missing}"),
    _R("STR-050", STR, ERR, 1, "layers: present with infra and apps sub-keys",
       message_template="layers section missing required sub-key(s): {missing}"),
    _R("STR-060", STR, ERR, 1, "bindings: present (may be empty list)",
       message_template="bindings section is missing (must be a list, may be empty)"),
    _R("STR-070", STR, ERR, 1, "Every node has exactly one of net: or nets:",
       message_template="Node '{node_id}' must declare exactly one of `net:` or `nets:`; got {state}"),
    _R("STR-080", STR, ERR, 1, "Every node has a kind: in {host, router, switch}",
       message_template="Node '{node_id}' has invalid kind: '{kind}'; must be one of host, router, switch"),
    _R("STR-090", STR, ERR, 1, "Every node ID is unique within its layer",
       message_template="Duplicate node ID '{node_id}' (occurs {count} times in layers.infra.nodes)"),
    _R("STR-095", STR, ERR, 1, "Every network ID is unique within the scenario",
       message_template="Duplicate network ID '{net_id}' (occurs {count} times)"),
    _R("STR-100", STR, ERR, 1, "Every binding has a kind: in the closed set",
       message_template="bindings[{index}] does not match any known binding kind (keys: {keys})"),
    _R("STR-110", STR, ERR, 1, "Every requires: keyword is in the closed 16-keyword set",
       message_template="Node '{node_id}' requires unknown capability keyword(s): {unknown}"),
    _R("STR-120", STR, ERR, 1, "Every node has exactly one OS-family keyword",
       message_template="Node '{node_id}' must declare exactly one OS-family in requires: (windows|linux|bsd); found {found}"),
    _R("STR-130", STR, ERR, 1, "Every install: action kind is in the closed 6-kind set",
       message_template="App '{app_id}' install action at index {index} is not in the closed action set (keys: {keys})"),
    _R("STR-140", STR, ERR, 1, "Every overlays: sub-section is identity|data|defense",
       message_template="overlays: contains unknown sub-section '{section}'; allowed: identity, data, defense"),
    _R("STR-150", STR, ERR, 1, "scenario.seed: is a string or int if present",
       message_template="scenario.seed must be a string or int; got {type}"),
    _R("STR-160", STR, ERR, 1, "scenario.reproducible: is a bool if present",
       message_template="scenario.reproducible must be a bool; got {type}"),
    _R("STR-170", STR, INFO, 1, "aliases used during reference resolution",
       message_template="Reference resolved via alias '{alias}' → '{canonical}' on '{location}'"),
    _R("STR-180", STR, INFO, 1, "Unknown top-level key observed",
       message_template="Unknown top-level key '{key}' — preserved for forward compatibility"),
    # STR-210 / STR-220: the device tree (`docs/design/topology-layers.md`
    # §10, Authority: user, 2026-11-07; `scenario_schema_version` 3 -> 4,
    # additive). STR 20 -> 22.
    _R("STR-210", STR, ERR, 1,
       "Every links[] entry is well-formed (a/b {device, port}, mode, state, native, vlans)",
       description=(
           "IR_SCHEMA §3.3: `a` and `b` are `{device, port}` with non-blank strings; "
           "`mode` is trunk | access; `state` (if present) is up | down | blocked; "
           "`native` only with `mode: trunk`; `vlans` a non-empty list, exactly one "
           "entry under `access`."
       ),
       message_template="links[{index}] is malformed: {detail}",
       related=("REF-270", "REF-280", "NET-120", "NET-130")),
    _R("STR-220", STR, ERR, 1,
       "Provenance fields are well-formed (source in the closed set; confidence 0..1 on a cast's verdict only)",
       description=(
           "IR_SCHEMA §3.6: `source` is one of discovered, asserted, authored, "
           "unconfirmed; `confidence` is a float in 0.0..1.0 and present only with "
           "`discovered` or `unconfirmed`."
       ),
       message_template="{entity}: {detail}"),
    # STR-190 "Every tap: flag is a boolean (node / leg / network)" RETIRED
    # 2026-07-28 (`docs/WORK_ORDER_tap_eligibility.md`, Authority: user) --
    # `tap:` is removed from IR_SCHEMA entirely, so there is no longer a
    # field whose boolean-ness this rule could check. Superseded by
    # STR-200 (rejects a leftover `tap:`'s mere PRESENCE, any value). The
    # id is marked deprecated and never reused (docs/VALIDATOR.md §3.3).
    _R("STR-200", STR, ERR, 1,
       "tap: is removed -- no field, at any location, is ever valid",
       description=(
           "The twin substrate is tap-agnostic (docs/WORK_ORDER_tap_eligibility.md, "
           "Authority: user, 2026-07-28): capture eligibility, scope, and target "
           "are authored entirely in a standalone tap plan, resolved against the "
           "twin's published topology -- never against a flag in the Scenario "
           "IR. A `tap:` field anywhere (`nodes[].tap`, `nodes[].nets[].tap`, "
           "`networks[].tap`) is rejected loudly rather than silently absorbed "
           "by Pydantic's `extra=\"allow\"` and vanishing an author's capture "
           "intent with no signal (LES-111)."
       ),
       message_template="tap: is removed; author taps in the tap plan (scope+target selector)."),

    # -------------------------------------------------------------------
    # INC — Include consistency (Pass 1, halt on error)
    # -------------------------------------------------------------------
    _R("INC-010", RuleCategory.INCLUDE_CONSISTENCY, ERR, 1,
       "ID defined in both an included fragment and main file (or two fragments)",
       message_template="strict-no-override violated: {message}"),
    _R("INC-020", RuleCategory.INCLUDE_CONSISTENCY, ERR, 1,
       "Included file path resolves and parses",
       message_template="{message}"),
    _R("INC-030", RuleCategory.INCLUDE_CONSISTENCY, ERR, 1,
       "No circular includes",
       message_template="Circular include detected for '{path}'"),
    _R("INC-040", RuleCategory.INCLUDE_CONSISTENCY, ERR, 1,
       "Included fragment's scenario_schema_version matches main",
       message_template="Fragment '{path}' has scenario_schema_version {frag} != main's {main}"),
    _R("INC-050", RuleCategory.INCLUDE_CONSISTENCY, INFO, 1,
       "Unused include — fragment defines nothing the main scenario references",
       message_template="Included fragment '{path}' defines content the main scenario does not reference"),

    # -------------------------------------------------------------------
    # REF — Reference Integrity (Pass 2, halt on error)
    # -------------------------------------------------------------------
    _R("REF-010", RuleCategory.REFERENCE_INTEGRITY, ERR, 2,
       "Binding app→host references a defined app",
       message_template="bindings[{index}]: app '{app_id}' is not defined in layers.apps"),
    _R("REF-020", RuleCategory.REFERENCE_INTEGRITY, ERR, 2,
       "Binding app→host references a defined host",
       message_template="bindings[{index}]: host '{host_id}' is not defined in layers.infra.nodes"),
    _R("REF-030", RuleCategory.REFERENCE_INTEGRITY, ERR, 2,
       "Binding activity→host references a defined activity",
       message_template="bindings[{index}]: activity '{activity_id}' is not defined in layers.activity"),
    _R("REF-040", RuleCategory.REFERENCE_INTEGRITY, ERR, 2,
       "Binding activity→host references a defined host",
       message_template="bindings[{index}]: host '{host_id}' is not defined in layers.infra.nodes"),
    _R("REF-050", RuleCategory.REFERENCE_INTEGRITY, ERR, 2,
       "Binding activity→targets references a defined activity",
       message_template="bindings[{index}]: activity '{activity_id}' is not defined in layers.activity"),
    _R("REF-060", RuleCategory.REFERENCE_INTEGRITY, ERR, 2,
       "Binding activity→targets references resolve",
       message_template="bindings[{index}]: target '{target_id}' is not a defined host, app, or actor"),
    _R("REF-070", RuleCategory.REFERENCE_INTEGRITY, ERR, 2,
       "Defense→host references a defined defense tool and host",
       message_template="overlays.defense[{index}]: host '{host_id}' is not defined"),
    _R("REF-080", RuleCategory.REFERENCE_INTEGRITY, ERR, 2,
       "Defense→network references a defined defense tool and network",
       message_template="overlays.defense[{index}].observes[{j}]: net '{net_id}' is not defined"),
    _R("REF-090", RuleCategory.REFERENCE_INTEGRITY, ERR, 2,
       "Identity overlay references resolve",
       message_template="overlays.identity[{index}]: id '{id}' is not a valid identity"),
    _R("REF-100", RuleCategory.REFERENCE_INTEGRITY, ERR, 2,
       "Data overlay host reference resolves",
       message_template="overlays.data[{index}]: host '{host_id}' is not defined"),
    _R("REF-110", RuleCategory.REFERENCE_INTEGRITY, ERR, 2,
       "Every net:/nets: ref on a node resolves to a defined network",
       message_template="Node '{node_id}' references undefined network '{net_id}'"),
    _R("REF-120", RuleCategory.REFERENCE_INTEGRITY, ERR, 2,
       "Every image: ref on a node resolves to a manifest entry",
       message_template="Node '{node_id}' image '{image_ref}' does not resolve to a manifest entry"),
    _R("REF-130", RuleCategory.REFERENCE_INTEGRITY, WARN, 2,
       "Image ref without version pinning",
       message_template="Node '{node_id}' image '{image_ref}' is unpinned; resolves to current version '{resolved}'",
       reproducible_promotes_to_error=True),
    _R("REF-140", RuleCategory.REFERENCE_INTEGRITY, ERR, 2,
       "Every behavior library ref resolves",
       message_template="Activity '{activity_id}' behavior '{behavior_ref}' does not resolve"),
    _R("REF-150", RuleCategory.REFERENCE_INTEGRITY, WARN, 2,
       "Behavior ref without version pinning",
       message_template="Activity '{activity_id}' behavior '{behavior_ref}' is unpinned",
       reproducible_promotes_to_error=True),
    _R("REF-160", RuleCategory.REFERENCE_INTEGRITY, ERR, 2,
       "Activity actor: resolves to an overlay identity or implicit local host user",
       message_template="Activity '{activity_id}' actor '{actor}' does not resolve to an identity or host user"),
    _R("REF-170", RuleCategory.REFERENCE_INTEGRITY, WARN, 2,
       "Activity references an actor missing from a present overlays.identity",
       message_template="Activity '{activity_id}' actor '{actor}' is missing from overlays.identity; will resolve to local host user"),
    _R("REF-180", RuleCategory.REFERENCE_INTEGRITY, ERR, 2,
       "Every alias is unique across the whole IR",
       message_template="Alias '{alias}' is declared by multiple entities ({where})"),
    _R("REF-190", RuleCategory.REFERENCE_INTEGRITY, ERR, 2,
       "Every binding's phase: references a defined phase (dormant in v1)",
       message_template="bindings[{index}] phase '{phase}' is not defined (phases are dormant in v1)"),
    # REF-200 "tap→sensor binding references a defined sensor and a tapped
    # host/network" RETIRED 2026-07-28 (`docs/WORK_ORDER_tap_eligibility.md`,
    # Authority: user) -- the `tap_host`/`tap_net`/`sensor` binding kind it
    # validated is removed from IR_SCHEMA (D6); a stray one now falls
    # through STR-100's closed binding-kind set instead (no dedicated rule
    # needed). The id is marked deprecated and never reused
    # (docs/VALIDATOR.md §3.3).
    _R("REF-210", RuleCategory.REFERENCE_INTEGRITY, ERR, 2,
       "sensor `type` is known and `captures`/`transport` reference defined sources",
       description=(
           "Sensors design (2026-06-23, docs/design/06-taps-sensors.md): a "
           "sensor's `type` must be a known sensor type (the axrange.sensors "
           "registry — tcpdump-sensor / suricata-sensor / zeek-sensor; absent ⇒ "
           "tcpdump-sensor, the former capture appliance). Its `captures` "
           "entries must each name a defined network or node, and `transport` "
           "must name a network with `transport: true`."
       ),
       message_template="sensors[{index}]: {detail}"),
    _R("REF-220", RuleCategory.REFERENCE_INTEGRITY, ERR, 2,
       "Adversary overlay entry has an id",
       description=(
           "A3 Stage A (2026-07-11, docs/design/08-exercise-measurement.md): "
           "every overlays.adversary[] entry needs an id, mirroring "
           "REF-090 for overlays.identity."
       ),
       message_template="overlays.adversary[{index}] missing id"),
    _R("REF-230", RuleCategory.REFERENCE_INTEGRITY, ERR, 2,
       "Adversary overlay targets: every target references a defined host",
       description=(
           "A3 Stage A: overlays.adversary[].targets is a fan-out (one entry "
           "can plant the Sandcat agent on several hosts) — every target is "
           "checked individually (LES-034), not just the first."
       ),
       message_template="overlays.adversary[{index}].targets[{j}] references unknown host '{host_id}'"),
    _R("REF-240", RuleCategory.REFERENCE_INTEGRITY, ERR, 2,
       "Adversary overlay targets: every target is a kind: host node",
       description=(
           "A3 Stage A code-review fix (2026-07-11): the Sandcat agent is "
           "planted over QGA, so a target must be a QGA-capable host node — "
           "a router (no QGA, e.g. VyOS) or switch would hang at deploy "
           "rather than fail at validate. Sibling to REF-230 (existence); "
           "this checks shape/kind, every fan-out binding (LES-034)."
       ),
       message_template="overlays.adversary[{index}].targets[{j}] '{host_id}' is kind={kind}, not host"),
    _R("REF-250", RuleCategory.REFERENCE_INTEGRITY, WARN, 2,
       "sensors: is declared, but a target-environment deploy does not stand it up",
       description=(
           "Sensor-agnostic te (2026-07-13, docs/WORK_ORDER_sensor_agnostic_te.md, "
           "review fix F1): a `te_` deploy no longer synthesizes an in-scenario VM "
           "for a declared `sensors:` entry, and emits no attach-gre-tunnel/"
           "install-ovs-mirror/verify-capture for it either (pass1_emit's "
           "Sensors-design Phase B synthesis + deploy-sensor emission are now "
           "gated on `tier == \"o2\"`, the standing-surface path "
           "`axrange.o2control.control.stand_up_surface` builds — never a "
           "user-authored scenario). A WARNING, not an error, so the shipped "
           "target-environments that still author `sensors:` (ad-domain-lab, "
           "purple-team-range, etc.) keep validating; capture on a deployed te "
           "is `axrange o2 up` (stand the sensor) + `axrange o2 tap apply` "
           "(bind the te's taps to it)."
       ),
       message_template=(
           "sensors: declares {count} entry/entries; a te deploy does not stand "
           "these up (sensor-agnostic te) — capture requires `axrange o2 up` + "
           "`axrange o2 tap apply` after this scenario is deployed"
       )),
    _R("REF-260", RuleCategory.REFERENCE_INTEGRITY, WARN, 2,
       "layers.activity:/overlays.adversary: is declared, but a te deploy does not run it",
       description=(
           "Exercise tier scope-shift (2026-07-24, docs/WORK_ORDER_exercise_tier.md, "
           "docs/design/02-twin-model.md): a `te_` deploy no longer emits the "
           "OP-ACT inline-steps trio (deploy-behavior-agent/start-activity/"
           "wait-actor-ready) for `layers.activity:`, nor `install-agent` for "
           "`overlays.adversary:` — both are re-homed to the standalone, replayable "
           "Exercise (`exr`) tier playout (`axrange.o2control.exercise`). A WARNING, "
           "not an error, mirroring REF-250's posture exactly, so the shipped "
           "target-environments that still author `activity:`/`overlays.adversary:` "
           "(ad-domain-lab, purple-team-range, soc-detection-lab) keep validating — "
           "that content is now exr-consumed, not te-emitted."
       ),
       message_template=(
           "{count} activity/adversary entry/entries declared "
           "({activity_count} activity, {adversary_count} overlays.adversary); a te "
           "deploy does not run these (Exercise-tier scope-shift) — run them via "
           "`axrange exr deploy` against the deployed twin"
       )),
    # REF-270 / REF-280: the device tree's link references (`docs/design/
    # topology-layers.md` §10, Authority: user, 2026-11-07). REF 25 -> 27.
    _R("REF-270", RuleCategory.REFERENCE_INTEGRITY, ERR, 2,
       "Every links[].a.device / .b.device resolves to a defined node",
       message_template="links[{index}].{side}.device '{device}' is not a defined node",
       related=("STR-210", "REF-280")),
    _R("REF-280", RuleCategory.REFERENCE_INTEGRITY, ERR, 2,
       "Every links[].vlans[] entry and links[].native resolves to a defined network",
       message_template="links[{index}].{field} names unknown network '{net_id}'",
       related=("STR-210", "REF-270", "REF-110")),

    # -------------------------------------------------------------------
    # CAP — Capability (Pass 3, accumulate)
    # -------------------------------------------------------------------
    _R("CAP-010", RuleCategory.CAPABILITY, ERR, 3,
       "Image provides ⊇ node requires (kernel + OS-family)",
       message_template="Node '{node_id}' requires capabilities {missing} that image '{image_ref}' does not provide. Image provides: {provided}",
       related=("CAP-020", "CAP-030", "STR-110", "STR-120")),
    _R("CAP-020", RuleCategory.CAPABILITY, ERR, 3,
       "Every install: action kind is supported by the host's image",
       message_template="App '{app_id}' uses install action '{kind}' but image '{image_ref}' on host '{host_id}' does not support it"),
    _R("CAP-030", RuleCategory.CAPABILITY, ERR, 3,
       "Image OS family matches the node's OS-family keyword",
       message_template="Node '{node_id}' declares OS family '{node_os}' but image '{image_ref}' is OS family '{image_os}'"),
    _R("CAP-040", RuleCategory.CAPABILITY, INFO, 3,
       "Node requires already provided by image's OS-family — redundant",
       message_template="Node '{node_id}' redundantly requires '{cap}' (image already provides it via OS-family declaration)"),
    _R("CAP-050", RuleCategory.CAPABILITY, WARN, 3,
       "Node requires host:* keyword that no binding's requires_host_caps declares",
       message_template="Node '{node_id}' requires '{cap}' but no manifest binding declares it (did you mean a kernel:* keyword?)"),
    _R("CAP-060", RuleCategory.CAPABILITY, ERR, 3,
       "Router/switch capability requirements consistent with kind",
       message_template="Node '{node_id}' of kind '{kind}' declares incompatible requires {caps}"),

    # -------------------------------------------------------------------
    # LIF — Lifecycle (Pass 3, accumulate)
    # -------------------------------------------------------------------
    _R("LIF-010", RuleCategory.LIFECYCLE, ERR, 3,
       "No node references a withdrawn image version",
       message_template="Node '{node_id}' references withdrawn image version '{image_ref}'. Replacement: '{replacement}'"),
    _R("LIF-020", RuleCategory.LIFECYCLE, WARN, 3,
       "Node references a deprecated image version",
       message_template="Node '{node_id}' uses deprecated image '{image_ref}'{replacement_hint}"),
    _R("LIF-030", RuleCategory.LIFECYCLE, WARN, 3,
       "Activity references a deprecated behavior version",
       message_template="Activity '{activity_id}' references deprecated behavior '{behavior_ref}'",
       reproducible_promotes_to_error=True),
    _R("LIF-040", RuleCategory.LIFECYCLE, ERR, 3,
       "Activity references a withdrawn behavior version",
       message_template="Activity '{activity_id}' references withdrawn behavior '{behavior_ref}'"),
    _R("LIF-050", RuleCategory.LIFECYCLE, WARN, 3,
       "Pinned-but-deprecated ref (status drifted)",
       message_template="Pinned ref '{ref}' on '{location}' is now deprecated since authoring"),
    _R("LIF-060", RuleCategory.LIFECYCLE, WARN, 3,
       "reproducible: true and missing top-level seed:",
       message_template="scenario.reproducible is true but scenario.seed is not set",
       reproducible_promotes_to_error=True),

    # -------------------------------------------------------------------
    # NET — Network Sanity (Pass 3, accumulate)
    # -------------------------------------------------------------------
    _R("NET-010", RuleCategory.NETWORK_SANITY, ERR, 3,
       "No two networks share overlapping CIDRs",
       message_template="Networks '{a}' ({cidr_a}) and '{b}' ({cidr_b}) have overlapping CIDRs"),
    _R("NET-020", RuleCategory.NETWORK_SANITY, ERR, 3,
       "Every network.cidr is a valid IPv4 or IPv6 CIDR",
       message_template="Network '{net_id}' has invalid CIDR '{cidr}'"),
    _R("NET-030", RuleCategory.NETWORK_SANITY, ERR, 3,
       "Multi-homed node's nets: entries reference distinct networks",
       message_template="Node '{node_id}' has duplicate network '{net_id}' in nets:"),
    _R("NET-040", RuleCategory.NETWORK_SANITY, ERR, 3,
       "Multi-homed node's IP is within the corresponding network's CIDR",
       message_template="Node '{node_id}' IP '{ip}' is not within network '{net_id}' CIDR '{cidr}'"),
    _R("NET-050", RuleCategory.NETWORK_SANITY, ERR, 3,
       "No two networks share the same VLAN ID",
       message_template="Networks '{a}' and '{b}' share VLAN ID {vlan}"),
    _R("NET-060", RuleCategory.NETWORK_SANITY, WARN, 3,
       "Two networks in different scenarios share a VLAN ID (multi-scenario; dormant by default)",
       message_template="Network '{net_id}' VLAN {vlan} is shared with scenario '{other_scenario}'"),
    _R("NET-070", RuleCategory.NETWORK_SANITY, ERR, 3,
       "Defense observes a network the host can't see",
       message_template="Defense '{defense_id}' host '{host_id}' has no NIC on observed net '{net_id}' and tap_from is not external-mirror"),
    _R("NET-080", RuleCategory.NETWORK_SANITY, ERR, 3,
       "Router node has at least one net: entry",
       message_template="Router '{node_id}' has 0 network attachments; a router needs ≥ 1 LAN net"),
    _R("NET-090", RuleCategory.NETWORK_SANITY, ERR, 3,
       "Switch has at least one net: entry (an addressed leg is a management SVI, allowed since the device tree, 2026-11-07)",
       message_template="Switch '{node_id}' configuration invalid: {detail}"),
    _R("NET-100", RuleCategory.NETWORK_SANITY, WARN, 3,
       "Orphan network (no nodes attached)",
       message_template="Network '{net_id}' has no nodes attached"),
    # NET-120 / NET-130 / NET-140: the device tree (`docs/design/
    # topology-layers.md` §10, Authority: user, 2026-11-07). NET 10 -> 13.
    _R("NET-120", RuleCategory.NETWORK_SANITY, ERR, 3,
       "No device uses one port name on two links",
       message_template="Device '{device}' port '{port}' is used by links[{first}] and links[{second}]",
       related=("STR-210",)),
    _R("NET-130", RuleCategory.NETWORK_SANITY, ERR, 3,
       "A link's two ends are different devices; an isolated network has exactly one non-isolated anchor with its CIDR",
       description=(
           "The twin-config expander's isolated-anchor rule, repeated on the IR: an "
           "`isolated: true` network is the same subnet on the far side of an L2 "
           "bridge and nothing else. NET-010 states the overlap rule for the "
           "non-isolated set."
       ),
       message_template="{detail}",
       related=("NET-010", "STR-210")),
    _R("NET-140", RuleCategory.NETWORK_SANITY, WARN, 3,
       "A kind: switch node is named by no links[] entry (parked, or the pre-device-tree spelling)",
       message_template="Switch '{node_id}' is wired by no link: add a links[] entry for it (a switch with only nets: is the pre-device-tree spelling)",
       related=("NET-090",)),
    # NET-110 "Tapped segment has at least one node attached" RETIRED
    # 2026-07-28 (`docs/WORK_ORDER_tap_eligibility.md`, Authority: user) --
    # `networks[].tap` no longer exists to check; a tap plan's `segment`
    # scope now resolves against the twin's FULL topology at bind time, not
    # a `tap:`-flagged subset, so there is no longer an author-declared
    # "tapped but empty" state to catch at validate time. The id is marked
    # deprecated and never reused (docs/VALIDATOR.md §3.3).

    # -------------------------------------------------------------------
    # OVR — Overlay (Pass 3, accumulate)
    # -------------------------------------------------------------------
    _R("OVR-010", RuleCategory.OVERLAY, ERR, 3,
       "Overlay binding references an overlay sub-section absent from overlays:",
       message_template="Reference to overlay sub-section '{section}' but overlays.{section} is not present"),
    _R("OVR-020", RuleCategory.OVERLAY, WARN, 3,
       "Overlay sub-section present but no bindings reference it",
       message_template="overlays.{section} is present but no entry is referenced anywhere"),
    _R("OVR-030", RuleCategory.OVERLAY, ERR, 3,
       "Identity overlay: groups and trusts reference defined entities",
       message_template="overlays.identity[{index}]: {detail}"),
    _R("OVR-040", RuleCategory.OVERLAY, ERR, 3,
       "Data overlay: classification is in the defined taxonomy (dormant in v1)",
       message_template="overlays.data[{index}].classification '{classification}' is not in the classification taxonomy (taxonomy dormant in v1)"),

    # -------------------------------------------------------------------
    # ACT — Activity (Pass 3, accumulate)
    # -------------------------------------------------------------------
    _R("ACT-010", RuleCategory.ACTIVITY, ERR, 3,
       "Activity in inline: mode has all required behavior-DSL fields",
       message_template="Activity '{activity_id}' inline mode missing required field(s): {missing}"),
    _R("ACT-020", RuleCategory.ACTIVITY, ERR, 3,
       "Activity overrides field exists in the referenced behavior library version",
       message_template="Activity '{activity_id}' overrides field '{field}' that does not exist in behavior '{behavior_ref}'"),
    _R("ACT-030", RuleCategory.ACTIVITY, WARN, 3,
       "Activity schedule shorter than the behavior's minimum-meaningful duration",
       message_template="Activity '{activity_id}' schedule window is shorter than behavior's minimum duration"),
    _R("ACT-040", RuleCategory.ACTIVITY, ERR, 3,
       "Activity has exactly one of behavior: or inline:",
       message_template="Activity '{activity_id}' must declare exactly one of behavior: or inline:; got {state}"),
    _R("ACT-050", RuleCategory.ACTIVITY, WARN, 3,
       "Per-activity seed silently ignored, UNLESS it seeds a jittered schedule.interval",
       message_template="Activity '{activity_id}' declares per-activity seed with no schedule.interval to seed; silently ignored (set schedule.interval for a jittered activity, or drop seed:)"),

    _R("ACT-060", RuleCategory.ACTIVITY, ERR, 3,
       "Activity schedule.interval is well-formed: both min and max present, min >= 1s floor, max >= min, mutually exclusive with once: true",
       message_template="Activity '{activity_id}' schedule.interval invalid: {detail}"),
    _R("ACT-070", RuleCategory.ACTIVITY, WARN, 3,
       "Jittered activity (schedule.interval set) should declare seed for reproducible replay",
       message_template="Activity '{activity_id}' has a schedule.interval (jittered cadence) but no seed: the fire sequence is non-reproducible across replay runs"),

    # -------------------------------------------------------------------
    # DEF — Defense (Pass 3, accumulate)
    # -------------------------------------------------------------------
    _R("DEF-010", RuleCategory.DEFENSE, ERR, 3,
       "Defense installed_on host's image must support the tool's install action kind",
       message_template="Defense '{defense_id}' installed on '{host_id}' but image does not support install kind '{kind}'"),
    _R("DEF-020", RuleCategory.DEFENSE, ERR, 3,
       "Defense with observes: has tap_from in {bridge, per-vm, external-mirror}",
       message_template="Defense '{defense_id}' observes nets but tap_from '{tap_from}' is invalid"),
    _R("DEF-030", RuleCategory.DEFENSE, ERR, 3,
       "Defense with tap_from: per-vm: the observed network has nodes attached",
       message_template="Defense '{defense_id}' uses tap_from: per-vm but observed net '{net_id}' has no nodes attached"),
    _R("DEF-040", RuleCategory.DEFENSE, WARN, 3,
       "Defense with tap_from: external-mirror requires sensor config outside the scenario",
       message_template="Defense '{defense_id}' uses tap_from: external-mirror — confirm sensor is configured"),
    _R("DEF-050", RuleCategory.DEFENSE, ERR, 3,
       "Defense with both observes: and installed_on: — bindings consistent",
       message_template="Defense '{defense_id}' bindings inconsistent: {detail}"),

    # -------------------------------------------------------------------
    # CLU — Cluster (Pass 4, planner-side; listed for completeness)
    # -------------------------------------------------------------------
    _R("CLU-010", RuleCategory.CLUSTER, ERR, 4,
       "At least one Proxmox node satisfies the union of host:* requirements",
       message_template="No Proxmox node satisfies host capability union for node '{node_id}': {caps}"),
    _R("CLU-020", RuleCategory.CLUSTER, ERR, 4,
       "Sum of RAM ≤ available × headroom",
       message_template="Proxmox node '{px_node}' would exceed RAM headroom: requested {req}, available {avail}"),
    _R("CLU-030", RuleCategory.CLUSTER, ERR, 4,
       "Sum of CPU ≤ available × headroom",
       message_template="Proxmox node '{px_node}' would exceed CPU headroom: requested {req}, available {avail}"),
    _R("CLU-040", RuleCategory.CLUSTER, ERR, 4,
       "Sum of disk ≤ free storage on configured pool",
       message_template="Storage pool '{pool}' would exceed free disk: requested {req}, available {avail}"),
    _R("CLU-050", RuleCategory.CLUSTER, ERR, 4,
       "VLAN IDs not in use by other scenarios / non-axRange resources",
       message_template="VLAN {vlan} for network '{net_id}' is already in use by '{owner}'"),
    _R("CLU-060", RuleCategory.CLUSTER, ERR, 4,
       "Every binding's template_vmid exists on the cluster",
       message_template="template_vmid {vmid} for image '{image_id}@{version}' not present on cluster"),
    _R("CLU-070", RuleCategory.CLUSTER, WARN, 4,
       "Cluster headroom after this deploy < 20%",
       message_template="Cluster headroom after this deploy < 20% on resource '{resource}'"),

    # -----------------------------------------------------------------------
    # FAB — Fabric (Pass 3, accumulate) — dynamic routing + overlay on routers
    # (fabric slice, docs/design/04-networking-capture.md, 2026-06-29)
    # -----------------------------------------------------------------------
    _R("FAB-010", RuleCategory.FABRIC, ERR, 3, "routing: is router-only",
       message_template="Node '{node_id}' declares routing: but kind is '{kind}' (fabric routing is router-only)"),
    _R("FAB-020", RuleCategory.FABRIC, ERR, 3, "routing.region resolves to a fabric region",
       message_template="Node '{node_id}' routing.region '{region}' does not resolve to a fabric.regions[].id"),
    _R("FAB-030", RuleCategory.FABRIC, ERR, 3, "routing.role is a supported role",
       message_template="Node '{node_id}' routing.role '{role}' is not one of spine|leaf|border|abr|area|provider|edge"),
    _R("FAB-040", RuleCategory.FABRIC, ERR, 3, "bgp.rr_clients_of references resolve",
       message_template="Node '{node_id}' routing.bgp.rr_clients_of references undefined router '{ref}'"),
    _R("FAB-050", RuleCategory.FABRIC, ERR, 3, "IS-IS region router declares routing.isis.net",
       message_template="Node '{node_id}' is in IS-IS region '{region}' but declares no routing.isis.net"),
    _R("FAB-060", RuleCategory.FABRIC, ERR, 3, "networks[].vni is valid and bridged",
       message_template="Network '{net_id}' vni problem: {detail}"),
    _R("FAB-070", RuleCategory.FABRIC, WARN, 3, "routing.loopback set when bgp present",
       message_template="Node '{node_id}' declares routing.bgp but no routing.loopback (router-id/update-source/VTEP source)"),
    _R("FAB-080", RuleCategory.FABRIC, ERR, 3, "fabric routers use a VyOS image",
       message_template="Node '{node_id}' is a fabric router but image '{image}' is not VyOS (this slice is VyOS-only)"),

    # -----------------------------------------------------------------------
    # PSN — Persona (catalog-side; docs/WORK_ORDER_persona_library.md,
    # docs/design/07-traffic-personas.md, 2026-07-24)
    #
    # Personas are twin-agnostic library entries (`catalog/personas/`),
    # not Scenario-IR content — no `bindings:`/node references a persona yet
    # (that binding is `twin_configs`/the future Traffic tier's job). So,
    # like CLU (pass 4, "planner-side; listed for completeness" — never
    # wired into engine.py's PASS_1/2/3_CHECKS lists), these rules are
    # registered here (shared Rule/Finding/Severity vocabulary, so
    # `axrange validate --explain PSN-0xx` and the rule-count audit both
    # work uniformly) but their checks run via
    # `axrange.validator.checks.personas.validate_persona()` /
    # `validate_catalog()` (that module reads the `axrange.personas`
    # registry — validator -> registry, never the reverse, same direction
    # `checks.references` uses for `axrange.sensors`), not the IR 4-pass
    # engine — there is no IR `ValidatorContext` to run them against.
    # `pass_=4` (not 3) is
    # DELIBERATE: it keeps PSN out of `tests/test_catalog.py`'s
    # `test_pass_3_check_count_matches_catalog` (which asserts
    # `len(PASS_3_CHECKS) == len(list_rules(pass_=3))` — true 1:1 wiring
    # for the categories that ARE in engine.py's pass 3), the same way
    # CLU already sits at pass 4 with no `PASS_4_CHECKS` list to keep in
    # sync at all.
    # -----------------------------------------------------------------------
    _R("PSN-010", RuleCategory.PERSONA, ERR, 4, "Persona id is present and non-empty",
       message_template="Persona is missing a non-empty id"),
    _R("PSN-020", RuleCategory.PERSONA, ERR, 4, "Persona os_family is windows or linux",
       message_template="Persona '{persona_id}' os_family '{os_family}' is not one of windows, linux"),
    # ENUM WIDENED 2026-09-02 (docs/WORK_ORDER_protocol_faithful_traffic.md,
    # Authority: user) with the `command` drive kind, and again 2026-09-21
    # (docs/WORK_ORDER_protocol_engines.md §5 E4, Authority: user) with
    # `browser` -- both additive and non-breaking, following the PSN-100/
    # PSN-110 precedent: the rule id, category, severity, pass and the
    # catalog's total rule COUNT (114) are all unchanged, and no previously
    # valid persona becomes invalid; only the closed set this rule polices
    # grew. The set itself lives in `personas.schema.DRIVE_KINDS`, which
    # `checks/personas.py` reads -- these strings are the human-readable
    # echo of it and must be kept in step.
    _R("PSN-030", RuleCategory.PERSONA, ERR, 4,
       "Persona drive is scripted, replay, real-service, command, or browser",
       message_template="Persona '{persona_id}' drive '{drive}' is not one of scripted, replay, real-service, command, browser"),
    _R("PSN-040", RuleCategory.PERSONA, ERR, 4,
       "Every emits[] entry names a known proto",
       description=(
           "Catches a typo'd/unknown protocol token in a persona's traffic "
           "signature (docs/design/07-traffic-personas.md) against the open "
           "but curated `axrange.personas.schema.KNOWN_PROTOS` vocabulary."
       ),
       message_template="Persona '{persona_id}' emits[{index}].proto '{proto}' is not a known protocol"),
    _R("PSN-050", RuleCategory.PERSONA, ERR, 4,
       "Every emits[] entry has a peer_role set (non-empty)",
       description=(
           "`peer_role` must be set to SOMETHING — a role the twin is "
           "expected to publish, or one of the two reserved external-egress "
           "tokens (`egress`, `internet`) — never empty/absent, or the emit "
           "has no target to drive traffic toward. This is presence-only; "
           "PSN-090 is the sibling check that the value, once present, is "
           "actually satisfiable (declared in the persona's own requires[])."
       ),
       message_template="Persona '{persona_id}' emits[{index}] has no peer_role set"),
    _R("PSN-060", RuleCategory.PERSONA, ERR, 4,
       "Every emits[] cadence is well-formed",
       message_template="Persona '{persona_id}' emits[{index}].cadence '{cadence}' is not diurnal, 24x7, cyclic:<duration>, or burst:<duration>"),
    _R("PSN-070", RuleCategory.PERSONA, ERR, 4,
       "Every emits[] volume is low, med, high, or bulk",
       description=(
           "`bulk` was added 2026-07-28 with the volume lever (a level now "
           "resolves message count AND bytes-per-message AND inter-message "
           "gap, not a message count alone) — it is the elephant-flow tier "
           "above `high`. See personas.drivers.scripted._VOLUME_PROFILE."
       ),
       message_template="Persona '{persona_id}' emits[{index}].volume '{volume}' is not one of low, med, high, bulk"),
    _R("PSN-080", RuleCategory.PERSONA, WARN, 4,
       "requires[] is satisfiable against a twin's published role set",
       description=(
           "Only meaningful once a persona is bound to a twin (the Traffic "
           "tier/`twin_configs`' job) — a no-op when no role set is supplied "
           "(context-free catalog validation), mirroring CLU-060's "
           "planner-side/cluster-context dependency."
       ),
       message_template="Persona '{persona_id}' requires {missing} not present in the twin's role set {roles}"),
    _R("PSN-090", RuleCategory.PERSONA, ERR, 4,
       "Every non-reserved, non-optional emits[].peer_role is declared in the persona's own requires[]",
       description=(
           "The catalog-checkable half of peer_role satisfiability (sibling "
           "to PSN-080's twin-role-set check, which needs an external "
           "`available_roles` and is a no-op without one): a persona can't "
           "emit traffic toward a HARD peer role (`egress-proxy`, "
           "`domain-controller`, ...) it doesn't also declare in `requires[]` "
           "— a persona's hard peers ARE its requirements. Exempt: the "
           "reserved external-egress tokens (`egress`, `internet`), which "
           "name outside-the-twin targets, not twin roles; and any emit "
           "marked `optional: true` (docs/WORK_ORDER_traffic_tier.md, "
           "2026-07-25 refinement) — an optional emit is a peer the persona "
           "talks to only IF PRESENT (a dedicated DNS resolver, a print "
           "server, ...), not something it structurally can't function "
           "without, so it need not be declared in `requires[]`. Runs with "
           "no twin at all (pure catalog-object validation), unlike PSN-080."
       ),
       message_template="Persona '{persona_id}' emits[{index}] targets peer_role '{peer_role}' which is not declared in requires[]"),
    _R("PSN-100", RuleCategory.PERSONA, ERR, 4,
       "Every emits[] transport is tcp or udp",
       description=(
           "`transport:` (2026-07-28) pins an emit's wire transport; an "
           "omitted value is DERIVED from `proto` "
           "(`personas.schema.default_transport` — udp for dns/ntp/snmp/"
           "syslog/netflow/dhcp/mdns/llmnr/nbns/ssdp/coap/rtp/stun/sip/ike/"
           "wireguard, tcp otherwise) and is always valid, so this rule only "
           "fires on an explicitly-authored bad value. Enum-checked here "
           "rather than in the loader, per the parser/validator split "
           "`personas.schema`'s docstring describes: a bad-but-well-shaped "
           "value must stay loadable so it can be exercised against the rule."
       ),
       message_template="Persona '{persona_id}' emits[{index}].transport '{transport}' is not one of tcp, udp"),
    _R("PSN-110", RuleCategory.PERSONA, ERR, 4,
       "Every emits[] peer_scope is one or all",
       description=(
           "`peer_scope:` (2026-07-28) controls emit fan-out: `one` (the "
           "default) drives a single resolved peer; `all` fans out across "
           "EVERY twin host carrying the role, which is what an authorized "
           "subnet-sweeping scanner or a peer-to-peer mesh does "
           "(`o2control.traffic._resolve_peer_sets` supplies the list, "
           "excluding the instance's own host). A `peer_scope: all` emit "
           "still resolves a single primary peer too, so the existing "
           "hard-unresolvable-role fail-loud is unchanged."
       ),
       message_template="Persona '{persona_id}' emits[{index}].peer_scope '{peer_scope}' is not one of one, all"),
]


RULES: dict[str, Rule] = {r.id: r for r in _RULES_LIST}


def get_rule(rule_id: str) -> Rule:
    if rule_id not in RULES:
        raise KeyError(f"Unknown rule: {rule_id!r}")
    return RULES[rule_id]


def list_rules(pass_: int | None = None, category: RuleCategory | None = None) -> list[Rule]:
    """Filtered, sorted-by-id rule list."""
    out = list(RULES.values())
    if pass_ is not None:
        out = [r for r in out if r.pass_ == pass_]
    if category is not None:
        out = [r for r in out if r.category == category]
    out.sort(key=lambda r: r.id)
    return out


# Self-check: catalog totals match the per-category tables in VALIDATOR §5.
#
# Note on the spec's headline "53 rules" count (VALIDATOR §1, §5 lead paragraph):
# the headline disagrees with what §5's per-category tables actually enumerate.
# Counting the tables directly gives 20 STR + 5 INC + 20 REF + 6 CAP + 6 LIF +
# 11 NET + 4 OVR + 5 ACT + 5 DEF = 82 IR-side rules, plus 7 CLU = 89 total.
# (REF 19->20 / total 86->87 with REF-200, the per-tap sensor-routing rule,
#  2026-06-03; STR 19->20 + NET 10->11 / total 87->89 with STR-190 (tap: shape)
#  + NET-110 (tapped bridge capturable), the dynamic-tap slice, 2026-06-19;
#  REF 20->21 / total 89->90 with REF-210, the typed-sensor rule, sensors design
#  2026-06-23; REF 21->23 / total 100->102 with REF-220/REF-230, the
#  overlays.adversary reference checks, A3 Stage A, 2026-07-11; REF 23->24 /
#  total 102->103 with REF-240, a code-review fix (kind:host target check),
#  2026-07-11; REF 24->25 / total 103->104 with REF-250, the sensor-agnostic-te
#  code-review fix F1 (WARNING that `sensors:` isn't stood up by a te deploy),
#  2026-07-13; +8 with the new PSN category (PSN-010..080, persona-catalog
#  rules — docs/WORK_ORDER_persona_library.md, catalog-side per the note at
#  the PSN block above, not part of the IR 4-pass Check pipeline) / total
#  104->112, 2026-07-24; +1 with PSN-090 (peer_role-in-requires[]
#  satisfiability, code-review fix splitting PSN-050's presence-only check
#  from the dangling/satisfiability concern) / total 112->113, 2026-07-24;
#  REF 25->26 / total 113->114 with REF-260, the Exercise-tier scope-shift
#  (docs/WORK_ORDER_exercise_tier.md — WARNING that a te deploy no longer
#  runs `layers.activity:`/`overlays.adversary:`), 2026-07-24; STR 20->20
#  (STR-190 retired, STR-200 added, net 0) + NET 11->10 (NET-110 retired) +
#  REF 26->25 (REF-200 retired) / total 114->112 with
#  `docs/WORK_ORDER_tap_eligibility.md` (Authority: user) — the twin
#  substrate becomes tap-agnostic; capture eligibility/scope/target moves
#  entirely into the standalone tap plan, 2026-07-28; STR 20->22 (STR-210
#  links shape, STR-220 provenance) + REF 25->27 (REF-270/280 link refs) +
#  NET 10->13 (NET-120 port reuse, NET-130 link ends + isolated anchor,
#  NET-140 parked switch) / total 114->121 with the device tree,
#  `docs/design/topology-layers.md` §10, Authority: user, 2026-11-07 — each
#  bumped atomically with the assertions below.)
# This implementation encodes what the §5 tables list (the actual contract); the
# §1/§5 headline numbers are stale and to be corrected via CHANGELOG.
_BY_CAT = {c: sum(1 for r in RULES.values() if r.category == c) for c in RuleCategory}
assert _BY_CAT[RuleCategory.STRUCTURAL] == 22, _BY_CAT  # -STR-190 +STR-200; +STR-210/220 device tree
assert _BY_CAT[RuleCategory.INCLUDE_CONSISTENCY] == 5, _BY_CAT
assert _BY_CAT[RuleCategory.REFERENCE_INTEGRITY] == 27, _BY_CAT  # -REF-200; +REF-270/280 device tree
assert _BY_CAT[RuleCategory.CAPABILITY] == 6, _BY_CAT
assert _BY_CAT[RuleCategory.LIFECYCLE] == 6, _BY_CAT
assert _BY_CAT[RuleCategory.NETWORK_SANITY] == 13, _BY_CAT  # -NET-110; +NET-120/130/140 device tree
assert _BY_CAT[RuleCategory.OVERLAY] == 4, _BY_CAT
assert _BY_CAT[RuleCategory.ACTIVITY] == 7, _BY_CAT  # +ACT-060/070, schedule executor 2026-07-10
assert _BY_CAT[RuleCategory.DEFENSE] == 5, _BY_CAT
assert _BY_CAT[RuleCategory.FABRIC] == 8, _BY_CAT  # fabric slice, 2026-06-29
assert _BY_CAT[RuleCategory.CLUSTER] == 7, _BY_CAT
# PSN-010..090 persona library 2026-07-24; +PSN-100/110 (transport +
# peer_scope enums) with the high-volume persona wave, 2026-07-28.
assert _BY_CAT[RuleCategory.PERSONA] == 11, _BY_CAT
assert sum(_BY_CAT.values()) == 121  # 114 + the device tree's 7 (STR-210/220, REF-270/280, NET-120/130/140), 2026-11-07
