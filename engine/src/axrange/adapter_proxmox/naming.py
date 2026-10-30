"""Deterministic Proxmox resource names per Model C (docs/ADAPTER.md §4).

Every infra op's external-side names are derived from `{plan_id, op_id}` so
re-running the same plan adopts the same resources. The functions here are the
single source of truth for those mappings; both pass-1 emission (where it
already pre-computes some) and the adapter (where most are computed at run
time) call into here.

**Resource ownership tags / blast-radius tiers** (`docs/design/05-o2-surface.md`
§6, `docs/WORK_ORDER_resource_tags.md`; Authority: user, 2026-07-08). Every
name this module produces carries a tier tag as a literal prefix so `destroy`
can sweep by tag instead of by shape-matching alone:

  - **`te`**  — a target-environment deploy's own resources (zones, vnets,
    VLAN underlay bridges, VMs). Swept by `destroy <deploy>`; cascades to
    `tap`.
  - **`tap`** — the tap-binding playout (per-tap mirror + source gretap).
    Own tier; today emitted inside the TE deploy, so a `te` destroy already
    cascades to it (relabel, not a re-plumb — see the design brief).
  - **`o2`**  — standing O2 surface resources, stood up separately by the
    O2 control layer (`axrange.o2control`, `docs/design/05-o2-surface.md`).
    The engine itself never produces `o2_*` names directly — `o2control` gets
    them by passing `tier="o2"` into the producers below (`zone_id_for_plan`,
    `vnet_id_for_net`, `underlay_bridge_for_plan`, `vm_name_for_host`; default
    `tier="te"` for every other caller). Never touched by a `te`/`tap` destroy
    (`planner/teardown.py`'s `_protected()` guard) UNLESS the destroy's OWN
    deploy is itself `o2`-tiered (`axrange o2 destroy`).
  - **`img`** — golden templates / base images (`axrange image build`).
    Build-time, standing, never destroy-swept by ANY tier's destroy.
  - **`tfc`** — the Traffic tier (`docs/design/02-twin-model.md`,
    `docs/WORK_ORDER_tier_split.md`): a benign traffic-profile deploy that
    binds personas onto a stood-up twin's published hosts. Outlives the
    Exercise tier; self-only on destroy (never cascaded INTO by anything, and
    never cascades OUT to `te`/`o2`/`img`/`exr`). Not produced by any live
    emission path yet (`persona_library`/`traffic_tier` work orders) — this
    module just reserves the tag + name shapes so the substrate is ready.
  - **`exr`** — the Exercise tier: an ephemeral attack/defense/measurement
    run against an instrumented twin. Self-only on destroy, same posture as
    `tfc`. Reserved ahead of `docs/WORK_ORDER_exercise_tier.md`.

There is also a seventh, non-tier bucket: **protected external names**
(`PROTECTED_EXTERNAL_NAMES` / `is_protected_external`, below) — operator-
managed nodes created BY HAND, outside axRange's deploy lifecycle entirely,
so they carry no tier-tag shape at all (they can't, since no producer here
ever names one). `XPress-SW`, the packetXpress sink/capture box, is the
built-in example. Like `img`, protection is unconditional — refused by
every tier's destroy including `o2`'s own.

Tiers are mutually exclusive by construction: every producer below emits a
name starting with a distinct literal prefix (`te`, `tev`, `tebr_`, `tapg_`,
`tapm-`, `o2z`, `o2v`, `o2br_`, `o2-`, `img-`, `tfc`, `tfcv`, `tfcbr_`, `tfc-`,
`exr`, `exrv`, `exrbr_`, `exr-`) chosen so no two tiers' shapes can ever
collide, even coincidentally:

  - `tev<hex>` is disjoint from `te<hex>` because the third character, `v`,
    is never a hex digit.
  - `tfc<hex>` (the Traffic zone shape) is disjoint from BOTH `te<hex>` and
    `tev<hex>` because its SECOND character, `f`, is never `e` (the second
    character every `te`-family shape requires) — a fully-anchored regex
    checks character 2 before it ever gets to character 3, so the mismatch
    is caught immediately regardless of what follows.
  - `tfcv<hex>` (the Traffic vnet shape) is in turn disjoint from `tfc<hex>`
    (the Traffic zone shape) the same way `tev`/`te` are: character 4 is the
    literal `v`, never a hex digit.
  - `tfcbr_*` (bridge) and `tfc-*` (VM name) are disjoint from the zone/vnet
    shapes above on LENGTH alone (their literal prefixes are longer, and the
    zone/vnet regexes are exact-length anchored) as well as content — same
    argument the existing `tebr_`/`te-` shapes already rely on.
  - `exr*` shapes are disjoint from EVERY OTHER tier's shapes on the FIRST
    character alone: `exr` starts with the literal `e`, which no other
    tier's shape ever starts with (`te`/`tev`/`tebr_`/`te-` start with `t`
    then `e` — two characters, not one — `tap*` and `tfc*` start with `t`
    then `a`/`f`; `o2*` starts with `o`; `img-` starts with `i`). Once the
    first character disagrees, an anchored regex can never match, so `exr`'s
    internal sub-shapes (zone/vnet/bridge/vm-name) only need to stay disjoint
    from EACH OTHER, which they do by the same `v`-at-a-fixed-position /
    length argument as `tfc`'s.
  - `tfc*` is disjoint from `tap*` (`tapg_`/`tapm-`) the same way: both start
    with `t`, but the SECOND character is `f` (tfc) vs `a` (tap) — never
    equal.

`test_adapter_proxmox.py` round-trips every producer against every predicate
to keep this invariant honest.

Constraints we honor:
  - Proxmox SDN zone id: <= 8 chars, [a-zA-Z0-9]
  - Proxmox SDN vnet id: <= 8 chars, [a-zA-Z0-9]
  - Linux/OVS bridge ifname: <= 15 chars (kernel IFNAMSIZ-1)
  - Proxmox VMID: 100 .. 999999999; we target 100000-999999 to avoid the
    template / cloud-init / hand-allocated ranges most clusters use
    (typical templates live around 9000-9999).
  - VM name: <= 63 chars; we keep them readable, prefixed `te-`.
"""
from __future__ import annotations

import hashlib
import re
from typing import Callable, Iterable

_ALPHANUM = re.compile(r"[^a-zA-Z0-9]")


def _short_plan_id(plan_id: str) -> str:
    """6-char hash slug for use in size-constrained names.

    plan_id values look like `<scenario>-<8hex>`; the trailing 8hex is the
    canonical identifying piece, but to handle adversarial inputs we always
    re-hash and take 6 chars of the digest. Result is `[a-z0-9]{6}`.
    """
    h = hashlib.sha256(plan_id.encode()).hexdigest()
    return h[:6]


_VALID_TIERS = ("te", "o2", "tfc", "exr")


def _check_tier(tier: str) -> None:
    if tier not in _VALID_TIERS:
        raise ValueError(f"unknown resource tier {tier!r}; expected one of {_VALID_TIERS}")


def check_tier(tier: str) -> None:
    """Public wrapper around `_check_tier` for callers OUTSIDE this module
    that want to reject a bad `tier=` value at the point of the mistake
    rather than later, deep inside a naming producer or `planner/teardown.py`
    mid-sweep (`docs/WORK_ORDER_tier_split.md` review, MUST-FIX #13) —
    e.g. `planner/state.py`'s `StateStore.create_deploy` calls this so a
    typo'd tier fails loud at deploy-creation time, not inside a future
    destroy."""
    _check_tier(tier)


def zone_id_for_plan(plan_id: str, *, tier: str = "te") -> str:
    """SDN zone id; 8 chars; format `te<6hex>` (`te` tier), `o2z<5hex>`
    (`o2` tier — the standing O2 surface, `docs/design/05-o2-surface.md`
    §4), `tfc<5hex>` (`tfc`, the Traffic tier), or `exr<5hex>` (`exr`, the
    Exercise tier). `tier` is a **deploy-scoped parameter** threaded from
    `Plan.tier` via `ExecutionContext.tier` (default `"te"`); `o2control` is
    the only caller that passes `tier="o2"` today, and `tfc`/`exr` are
    reserved ahead of their own work orders.

    Proxmox SDN zone names cap at 8 chars (`a-zA-Z0-9`).
    """
    _check_tier(tier)
    if tier == "o2":
        return o2_zone_id_for_component(plan_id)
    if tier == "tfc":
        return tfc_zone_id_for_component(plan_id)
    if tier == "exr":
        return exr_zone_id_for_component(plan_id)
    return f"te{_short_plan_id(plan_id)}"


def vnet_id_for_net(net_id: str, *, plan_id: str, tier: str = "te") -> str:
    """SDN vnet id; 8 chars; format `tev<5hex of "plan_id|net_id">` (`te`
    tier), `o2v<5hex>` (`o2` tier — routes to `o2_vnet_id_for_component`),
    `tfcv<4hex>` (`tfc`), or `exrv<4hex>` (`exr`).

    `tev` (not `te<hex>`) so vnet ids stay structurally disjoint from zone
    ids: the third character is the literal `v`, which a zone id's third
    character (always a hex digit) can never be. Same trick, one character
    later, for `tfcv`/`exrv` against `tfc`/`exr` (see the module docstring's
    disjointness argument).

    **`plan_id` is required, keyword-only, and has no default** (2026-07-27
    fix; `docs/PRODUCT_GAPS.md`/`docs/LESSONS.md` — see LES-143/LES-118's
    family). Before this fix the hash covered `net_id` ALONE, so two
    different twins with a same-named network (e.g. both authoring a `dmz`
    net) computed the IDENTICAL vnet id — `op_create_vnet`'s Model-C
    check-then-act (`adapter_proxmox/rest.py`) then let the SECOND twin's
    `create-vnet` silently ADOPT the first twin's live vnet, record it in
    its own op outputs, and later have its `destroy` delete the first
    twin's still-in-use vnet out from under it. This caused a real
    dev45 outage (2026-07-27): destroying a throwaway `dmz-perimeter` twin
    removed a vnet actually owned by a standing `corp-enterprise` twin,
    dropping network for four of its VMs. Every caller MUST now pass the
    plan/deploy-identifying `plan_id` it already has on hand (`ctx.plan_id`
    in the adapter, `plan.plan_id` in imagebuild) — a default would have
    silently reproduced the exact bug this fixes, so there isn't one.
    Sibling tier-scoped producers (`zone_id_for_plan`, `underlay_bridge_
    for_plan`) were already plan-scoped; this brings the vnet producer (and
    its `o2`/`tfc`/`exr` variants below) into line with them.
    """
    _check_tier(tier)
    if tier == "o2":
        return o2_vnet_id_for_component(plan_id, net_id)
    if tier == "tfc":
        return tfc_vnet_id_for_component(plan_id, net_id)
    if tier == "exr":
        return exr_vnet_id_for_component(plan_id, net_id)
    h = hashlib.sha256(f"{plan_id}|{net_id}".encode()).hexdigest()[:5]
    return f"tev{h}"


def underlay_bridge_for_plan(plan_id: str, *, tier: str = "te") -> str:
    """OVS **underlay** bridge name for a VLAN-zone scenario; <= 15 chars.

    SDN-native (2026-06-03): a VLAN zone has ONE underlay OVS bridge per zone,
    with each network a VLAN-tagged vnet on it. The underlay is created via
    `/nodes/<node>/network` (must be OVS — mirroring depends on it,
    `docs/ARCHITECTURE.md` §3) and referenced as the SDN VLAN zone's `bridge`.
    Format `tebr_<6hex of plan_id>` (`te` tier tag); underscore not hyphen
    (PVE/kernel-safe). `tier="o2"`/`"tfc"`/`"exr"` route to the matching
    `*_bridge_name_for_component` (the standing O2 surface and the Traffic/
    Exercise tiers are all Simple-zoned in practice today, but kept symmetric
    with the other producers).
    """
    _check_tier(tier)
    if tier == "o2":
        return o2_bridge_name_for_component(plan_id)
    if tier == "tfc":
        return tfc_bridge_name_for_component(plan_id)
    if tier == "exr":
        return exr_bridge_name_for_component(plan_id)
    return f"tebr_{_short_plan_id(plan_id)}"[:15]


def bridge_name_for_net(net_id: str, *, tier: str = "te") -> str:
    """OVS bridge name; <= 15 chars; format `tebr_<sanitized-id>` trimmed
    (`te` tier tag).

    Constraints:
      - Linux IFNAMSIZ-1 = 15 chars max.
      - PVE rejects iface names with hyphens (`[a-zA-Z][a-zA-Z0-9_]+` per
        PVE::Network) — slice runs surfaced this 2026-05-29.
    Non-alphanumeric characters in net_id (including hyphens) are converted
    to underscores so the resulting name passes both kernel and PVE validation.
    An empty/all-non-alphanumeric net_id would otherwise sanitize to an empty
    body, producing bare `tebr_` — a name `is_te_bridge` (`{1,10}` after the
    prefix) then rejects, a latent producer/predicate mismatch (code review).
    Fall back to `x` so the producer never emits a name its own predicate
    refuses. `tier="o2"`/`"tfc"`/`"exr"` route to the matching
    `*_bridge_name_for_component`.
    """
    _check_tier(tier)
    if tier == "o2":
        return o2_bridge_name_for_component(net_id)
    if tier == "tfc":
        return tfc_bridge_name_for_component(net_id)
    if tier == "exr":
        return exr_bridge_name_for_component(net_id)
    sanitized = _ALPHANUM.sub("_", net_id) or "x"
    name = f"tebr_{sanitized}"
    return name[:15]


_VMID_MIN, _VMID_MAX = 100000, 999999


def vmid_for_op(plan_id: str, op_id: str) -> int:
    """Deterministic VMID in 100000-999999 (`te` tier — the te-VMID range).

    The hash collapses to a 6-decimal-digit number; this gives ~1m distinct
    VMIDs per cluster. Collisions inside one plan are detected by Model C
    check-then-act: `op_clone_vm` (rest.py) compares the existing VM's PVE
    name against `vm_name_for_host(plan_id, host_id)` before adopting it, and
    raises `error_class="vmid-collision-foreign"` on a mismatch instead of
    silently adopting a foreign VM (code-review fix, 2026-07-07). The range
    itself doubles as the secondary `img`-template exclusion guard (templates
    live ~9000-9999, well outside 100000-999999).
    """
    h = hashlib.sha256(f"{plan_id}:{op_id}".encode()).digest()
    # int.from_bytes is endian-stable; we mod into 6-digit range.
    n = int.from_bytes(h[:8], "big")
    return _VMID_MIN + (n % 900000)


def vm_name_for_host(plan_id: str, host_id: str, *, tier: str = "te") -> str:
    """VM display name; <= 63 chars; format `te-<6hex>-<host_id-trimmed>`
    (`te` tier tag), `o2-<host_id-trimmed>` when `tier="o2"` (routes to
    `o2_vm_name_for_component`), `tfc-<6hex>-<host_id-trimmed>` when
    `tier="tfc"`, or `exr-<6hex>-<host_id-trimmed>` when `tier="exr"`.

    Falls back to `x` for an empty-after-sanitize `host_id`, same as
    `bridge_name_for_net`'s `or "x"` guard — a truly empty `host_id`
    (adversarial input; never happens for a real IR node id) would otherwise
    produce a bare `te-<6hex>-` with nothing after the trailing hyphen, which
    `is_te_vm_name` (one-or-more-chars tail) then rejects: a producer emitting
    a name its OWN predicate refuses (found adversarially, `docs/WORK_ORDER_
    tier_split.md` review — the same bug class the module docstring already
    flagged for `bridge_name_for_net`, never propagated to this family until
    now)."""
    _check_tier(tier)
    if tier == "o2":
        return o2_vm_name_for_component(host_id)
    if tier == "tfc":
        return tfc_vm_name_for_component(plan_id, host_id)
    if tier == "exr":
        return exr_vm_name_for_component(plan_id, host_id)
    sanitized = _ALPHANUM.sub("-", host_id) or "x"
    base = f"te-{_short_plan_id(plan_id)}-{sanitized}"
    return base[:63]


def mac_for_nic(plan_id: str, host_id: str, nic_index: int) -> str:
    """Deterministic locally-administered unicast MAC for one VM NIC, keyed by
    `(plan_id, host_id, nic_index)`.

    Without an explicit `macaddr=`, PVE mints a fresh random MAC on every NIC
    (re)attach — so a re-attach (Model C re-run / redeploy) churns the MAC,
    breaking anything that identifies the guest by MAC (VyOS WAN-IP discovery
    via `arp-scan`, `docs/SLICE_ROUTER.md`; DHCP lease stability). Surfaced by
    code review 2026-07-07 (finding #8). `02:` sets the locally-administered +
    unicast bits — the same LAA convention `fabric_render._l3vni_rmac` uses
    (LES-010) — so the synthetic MAC never collides with a real vendor OUI.
    """
    h = hashlib.sha256(f"{plan_id}:{host_id}:{nic_index}".encode()).hexdigest()
    octets = [h[i : i + 2] for i in range(0, 10, 2)]
    return "02:" + ":".join(octets)


def gre_port_for_bridge(bridge_name: str) -> str:
    """OVS GRE-out port name, **unique per scenario bridge**.

    OVS interface names are globally unique within one ovs-vsctl instance, so a
    constant `gre-out` collides the moment a second tapped scenario brings up
    its own bridge (`add-port <bridge> gre-out` fails because `gre-out` is
    already attached to another scenario's bridge). Surfaced 2026-06-01 running
    a second tapped scenario alongside the slice on the lab cluster — exactly
    the "future per-tunnel naming when sensors fan out" this used to defer.

    Derive `tapg_<6hex of bridge_name>` (`tap` tier tag): <= 15 chars
    (IFNAMSIZ-1), underscore not hyphen (kernel/OVS-safe), deterministic so
    Model-C check-then-act and the post-start mirror hook agree on the port
    name.
    """
    h = hashlib.sha256(bridge_name.encode()).hexdigest()[:6]
    return f"tapg_{h}"


def gre_port_for_bridge_sensor(bridge_name: str, sensor_id: str) -> str:
    """gretap netdev name, unique per **(bridge, sensor)** (per-tap sensor routing,
    2026-06-03). Under Option 1 (2026-06-04) this names the keyed **gretap
    netdev** the per-tap `tc` mirror targets (`ip link add <name> type gretap`);
    pre-Option-1 it was the OVS GRE-out port name.

    Generalizes `gre_port_for_bridge`: a bridge can fan out one tunnel per
    *distinct target sensor* among its tapped hosts, so the name must distinguish
    them too (netdev / OVS interface names are both globally unique). Derive
    `tapg_<6hex of "bridge|sensor">` (`tap` tier tag): <= 15 chars (IFNAMSIZ-1),
    underscore-safe, deterministic for Model-C re-assert.
    """
    h = hashlib.sha256(f"{bridge_name}|{sensor_id}".encode()).hexdigest()[:6]
    return f"tapg_{h}"


def gre_key_for_bridge_sensor(bridge_name: str, sensor_id: str) -> int:
    """Per-**(bridge, sensor)** GRE tunnel key (32-bit, non-zero).

    Generalizes `gre_key_for_bridge` for per-tap sensor routing (2026-06-03): with
    multiple tunnels possibly leaving one bridge to different sensors (and many
    bridges converging on one sensor), each tunnel needs a distinct key so unkeyed
    tunnels don't collide at the OVS datapath. Deterministic; non-zero. Each
    target sensor terminates on its own flow-based gretap to decap any key
    (`docs/ARCHITECTURE.md` §4, `o2/tcpdump-sensor/README.md`).
    """
    n = int(hashlib.sha256(f"{bridge_name}|{sensor_id}".encode()).hexdigest()[:8], 16)
    return n or 1


def gre_key_for_bridge(bridge_name: str) -> int:
    """Per-bridge GRE tunnel key (32-bit, non-zero) for sender-side coexistence
    of multiple GRE tunnels to one sensor.

    Two unkeyed (key=0) OVS GRE tunnels to the same remote_ip collide at the
    datapath — only one egresses. Surfaced 2026-06-01 with 2+ tapped bridges
    (concurrent scenarios, or one scenario with 2+ tapped networks); a distinct
    key per bridge lets them coexist on the wire. Deterministic so Model-C
    re-asserts the same key. Non-zero (0 == "no key" on the receive side);
    32-bit to fit the GRE key field.

    The sensor's capture interface must be a **flow-based** gretap
    (`ip link add … type gretap external`) to decapsulate any key — see
    `docs/ARCHITECTURE.md` §4 and `o2/tcpdump-sensor/README.md`. Capture stays a single
    merged stream; per-scenario *attribution* by key is still deferred
    (OP-MIR-030 `register-mirror-source`, ID reserved).
    """
    n = int(hashlib.sha256(bridge_name.encode()).hexdigest()[:8], 16)
    return n or 1


def mirror_name_for_vmid(vmid: int) -> str:
    """The per-VM OVS mirror's internal name (`tap` tier tag). ARCHITECTURE
    §288 convention: `tapm-<vmid>`. Bounded by OVS's identifier length
    (>= 64 chars OK)."""
    return f"tapm-{vmid}"


# ---------------------------------------------------------------------------
# Standing O2 surface producers — called by `axrange.o2control` (A1,
# `docs/design/05-o2-surface.md`) via the tier-aware producers above
# (`zone_id_for_plan(..., tier="o2")` etc.) when it stands up the surface
# deploy. The engine itself never calls these directly
# (`docs/design/05-o2-surface.md`, LES-025) — only `o2control`, which is an
# engine *client*.
# ---------------------------------------------------------------------------
def o2_zone_id_for_component(component_id: str) -> str:
    """Standing O2 SDN zone id; 8 chars; format `o2z<5hex>` (`o2` tier tag).

    `o2z` (not `o2v`/`o2<hex>`) keeps zone ids structurally disjoint from
    both O2 vnet ids (third char `v`) and `te`-tier zone ids (third char is
    always a hex digit for `te<6hex>`, never the literal `z`)."""
    h = hashlib.sha256(component_id.encode()).hexdigest()[:5]
    return f"o2z{h}"


def o2_vnet_id_for_component(plan_id: str, component_id: str) -> str:
    """Standing O2 vnet id; 8 chars; format `o2v<5hex of "plan_id|component_id">`
    (`o2` tier tag). `plan_id` is required (2026-07-27 fix, see `vnet_id_for_net`'s
    docstring) so two `o2`-tiered deploys with a same-named component don't
    collide on one vnet id — the same latent bug the `te` producer had, one
    tier over. Called by `vnet_id_for_net(..., tier="o2")`; not called
    directly by the engine."""
    h = hashlib.sha256(f"{plan_id}|{component_id}".encode()).hexdigest()[:5]
    return f"o2v{h}"


def o2_bridge_name_for_component(component_id: str) -> str:
    """Standing O2 bridge name; <= 15 chars; format `o2br_<6hex>` (`o2` tier
    tag). Reserved for the future O2 control layer — not called by the
    engine."""
    h = hashlib.sha256(component_id.encode()).hexdigest()[:6]
    return f"o2br_{h}"[:15]


def o2_vm_name_for_component(component_id: str) -> str:
    """Standing O2 VM name; <= 63 chars; format `o2-<sanitized component_id>`
    (`o2` tier tag). Reserved for the future O2 control layer — not called
    by the engine.

    Falls back to `x` for an empty-after-sanitize `component_id` — see
    `vm_name_for_host`'s docstring for the bug class this closes (a bare
    `o2-` is rejected by `is_o2_vm_name`'s own one-or-more-chars tail)."""
    sanitized = _ALPHANUM.sub("-", component_id) or "x"
    return f"o2-{sanitized}"[:63]


# ---------------------------------------------------------------------------
# Traffic tier producers (`tfc`, docs/design/02-twin-model.md) — called
# via the tier-aware producers above (`zone_id_for_plan(..., tier="tfc")`
# etc.) by the future `persona_library`/`traffic_tier` work orders. The
# engine's own emission (`pass1_emit.py`) never passes `tier="tfc"` today —
# these are reserved ahead of that build, same posture the `o2_*` family had
# before `o2control` existed.
#
# Zone/bridge follow the o2 family's shape (single `component_id` argument,
# hashed alone) — that's safe here because `zone_id_for_plan`/
# `underlay_bridge_for_plan` always pass `plan_id` ITSELF as `component_id`,
# which is already per-deploy-scoped, same as `te`'s own zone/bridge
# producers. Vnet and VM names are the two exceptions, and for the SAME
# reason: `vnet_id_for_net`/`vm_name_for_host` are called with a `net_id`/
# `host_id` that is NOT itself plan-scoped (many deploys can share a network
# or host NAME), so `tfc_vnet_id_for_component`/`tfc_vm_name_for_component`
# take BOTH `plan_id` and the component id (like `te`'s own `vnet_id_for_net`/
# `vm_name_for_host`, not o2's single-argument shape) because `tfc`, like
# `te`, is a MULTI-DEPLOY tier — many concurrent traffic-profile deploys can
# exist, so a bare `net_id`/`host_id` (e.g. "dmz"/"attacker-01") is not
# enough to keep vnet ids/VM names disjoint across them (2026-07-27 fix —
# the vnet producer was the one family member that missed this originally,
# see `vnet_id_for_net`'s docstring)
# the way it is for `o2` (a single, permanent standing surface with no
# cross-deploy collision to worry about).
# ---------------------------------------------------------------------------
def tfc_zone_id_for_component(component_id: str) -> str:
    """Traffic-tier SDN zone id; 8 chars; format `tfc<5hex>` (`tfc` tier tag).

    `tfc` (not `te`/`tev`) keeps zone ids disjoint from every `te`-family
    shape on the SECOND character alone (`f` vs `e`) — see the module
    docstring."""
    h = hashlib.sha256(component_id.encode()).hexdigest()[:5]
    return f"tfc{h}"


def tfc_vnet_id_for_component(plan_id: str, component_id: str) -> str:
    """Traffic-tier SDN vnet id; 8 chars; format
    `tfcv<4hex of "plan_id|component_id">` (`tfc` tier tag). Disjoint from
    `tfc_zone_id_for_component`'s shape the same way `tev`/`te` are: the 4th
    character is the literal `v`, never a hex digit.

    `plan_id` is required (2026-07-27 fix, see `vnet_id_for_net`'s docstring)
    — unlike the zone/bridge producers in this family, the vnet producer is
    NOT safe to key on `component_id` alone: two `tfc` deploys can legitimately
    share a same-named net (`traffic_tier` is a multi-deploy tier, same as
    `te`), and a bare-`component_id` hash would let the second collide onto
    the first's vnet, same bug class as the `te` producer had."""
    h = hashlib.sha256(f"{plan_id}|{component_id}".encode()).hexdigest()[:4]
    return f"tfcv{h}"


def tfc_bridge_name_for_component(component_id: str) -> str:
    """Traffic-tier OVS bridge name; <= 15 chars; format `tfcbr_<6hex>`
    (`tfc` tier tag)."""
    h = hashlib.sha256(component_id.encode()).hexdigest()[:6]
    return f"tfcbr_{h}"[:15]


def tfc_vm_name_for_component(plan_id: str, host_id: str) -> str:
    """Traffic-tier VM name; <= 63 chars; format
    `tfc-<6hex of plan_id>-<sanitized host_id>` (`tfc` tier tag) — mirrors
    `vm_name_for_host`'s own double-keyed shape (see the section docstring
    above for why this producer, unlike its o2 sibling, takes `plan_id` too).

    Falls back to `x` for an empty-after-sanitize `host_id` — see
    `vm_name_for_host`'s docstring for the bug class this closes."""
    sanitized = _ALPHANUM.sub("-", host_id) or "x"
    base = f"tfc-{_short_plan_id(plan_id)}-{sanitized}"
    return base[:63]


# ---------------------------------------------------------------------------
# Exercise tier producers (`exr`, docs/design/02-twin-model.md) —
# reserved ahead of `docs/WORK_ORDER_exercise_tier.md`, same posture as the
# `tfc` family above (including the double-keyed VM name).
# ---------------------------------------------------------------------------
def exr_zone_id_for_component(component_id: str) -> str:
    """Exercise-tier SDN zone id; 8 chars; format `exr<5hex>` (`exr` tier
    tag). Disjoint from EVERY other tier's shape on the FIRST character
    alone (`e`, which no other producer in this module ever starts a name
    with) — see the module docstring."""
    h = hashlib.sha256(component_id.encode()).hexdigest()[:5]
    return f"exr{h}"


def exr_vnet_id_for_component(plan_id: str, component_id: str) -> str:
    """Exercise-tier SDN vnet id; 8 chars; format
    `exrv<4hex of "plan_id|component_id">` (`exr` tier tag). Disjoint from
    `exr_zone_id_for_component`'s shape the same way `tfcv`/`tfc` are: the
    4th character is the literal `v`, never a hex digit.

    `plan_id` is required, same rationale as `tfc_vnet_id_for_component`
    (2026-07-27 fix, see `vnet_id_for_net`'s docstring) — `exr` is likewise a
    multi-deploy tier, so a bare-`component_id` hash would let two
    concurrent Exercise runs with a same-named net collide onto one vnet."""
    h = hashlib.sha256(f"{plan_id}|{component_id}".encode()).hexdigest()[:4]
    return f"exrv{h}"


def exr_bridge_name_for_component(component_id: str) -> str:
    """Exercise-tier OVS bridge name; <= 15 chars; format `exrbr_<6hex>`
    (`exr` tier tag)."""
    h = hashlib.sha256(component_id.encode()).hexdigest()[:6]
    return f"exrbr_{h}"[:15]


def exr_vm_name_for_component(plan_id: str, host_id: str) -> str:
    """Exercise-tier VM name; <= 63 chars; format
    `exr-<6hex of plan_id>-<sanitized host_id>` (`exr` tier tag) — see
    `tfc_vm_name_for_component`'s docstring for why this takes `plan_id`
    too, unlike the o2 family.

    Falls back to `x` for an empty-after-sanitize `host_id` — see
    `vm_name_for_host`'s docstring for the bug class this closes."""
    sanitized = _ALPHANUM.sub("-", host_id) or "x"
    base = f"exr-{_short_plan_id(plan_id)}-{sanitized}"
    return base[:63]


def img_vm_name_for_image(image: str) -> str:
    """Golden template VM name; <= 63 chars; format `img-<image-slug>`
    (`img` tier tag), e.g. `img-debian-12-cloud`, `img-suricata-sensor`."""
    sanitized = _ALPHANUM.sub("-", image).strip("-") or "image"
    return f"img-{sanitized}"[:63]


# ---------------------------------------------------------------------------
# Deterministic-naming predicates — "is this name/id one axRange would have
# produced, and in which blast-radius tier" (code-review finding #6,
# 2026-07-07; retagged to te/tap/o2/img, docs/WORK_ORDER_resource_tags.md).
# `planner/teardown.py` imports these as the single source of truth instead
# of re-deriving the regex/range by hand.
#
# Mutual exclusivity: every regex below is fully anchored (`^...$`) and keyed
# off a literal prefix/character that no other tier's producer can emit —
# see the module docstring for the disjointness argument. `test_adapter_
# proxmox.py::test_tier_predicates_are_mutually_exclusive` round-trips this.
# ---------------------------------------------------------------------------
_RE_TE_ZONE = re.compile(r"^te[0-9a-f]{6}$")                # zone_id_for_plan
_RE_TE_VNET = re.compile(r"^tev[0-9a-f]{5}$")                # vnet_id_for_net
_RE_TE_BRIDGE = re.compile(r"^tebr_[a-zA-Z0-9_]{1,10}$")     # underlay_bridge_for_plan / bridge_name_for_net
_RE_TE_VM_NAME = re.compile(r"^te-[0-9a-f]{6}-[a-zA-Z0-9-]+$")  # vm_name_for_host
_RE_TAP_GRETAP = re.compile(r"^tapg_[0-9a-f]{6}$")           # gre_port_for_bridge[_sensor]
_RE_TAP_MIRROR = re.compile(r"^tapm-[0-9]+$")                # mirror_name_for_vmid
_RE_O2_ZONE = re.compile(r"^o2z[0-9a-f]{5}$")                # o2_zone_id_for_component
_RE_O2_VNET = re.compile(r"^o2v[0-9a-f]{5}$")                # o2_vnet_id_for_component
_RE_O2_BRIDGE = re.compile(r"^o2br_[0-9a-f]{1,6}$")          # o2_bridge_name_for_component
_RE_O2_VM_NAME = re.compile(r"^o2-[a-zA-Z0-9-]+$")           # o2_vm_name_for_component
_RE_IMG_VM_NAME = re.compile(r"^img-[a-zA-Z0-9-]+$")         # img_vm_name_for_image
_RE_TFC_ZONE = re.compile(r"^tfc[0-9a-f]{5}$")               # tfc_zone_id_for_component
_RE_TFC_VNET = re.compile(r"^tfcv[0-9a-f]{4}$")              # tfc_vnet_id_for_component
_RE_TFC_BRIDGE = re.compile(r"^tfcbr_[0-9a-f]{1,6}$")        # tfc_bridge_name_for_component
_RE_TFC_VM_NAME = re.compile(r"^tfc-[0-9a-f]{6}-[a-zA-Z0-9-]+$")  # tfc_vm_name_for_component
_RE_EXR_ZONE = re.compile(r"^exr[0-9a-f]{5}$")               # exr_zone_id_for_component
_RE_EXR_VNET = re.compile(r"^exrv[0-9a-f]{4}$")              # exr_vnet_id_for_component
_RE_EXR_BRIDGE = re.compile(r"^exrbr_[0-9a-f]{1,6}$")        # exr_bridge_name_for_component
_RE_EXR_VM_NAME = re.compile(r"^exr-[0-9a-f]{6}-[a-zA-Z0-9-]+$")  # exr_vm_name_for_component


def is_te_zone(name: str) -> bool:
    """True if `name` matches the `zone_id_for_plan` shape (`te<6hex>`)."""
    return bool(_RE_TE_ZONE.match(name))


def is_te_vnet(name: str) -> bool:
    """True if `name` matches the `vnet_id_for_net` shape (`tev<5hex>`)."""
    return bool(_RE_TE_VNET.match(name))


def is_te_bridge(name: str) -> bool:
    """True if `name` matches the `underlay_bridge_for_plan` /
    `bridge_name_for_net` shape (`tebr_<...>`)."""
    return bool(_RE_TE_BRIDGE.match(name))


def is_te_vm_name(name: str) -> bool:
    """True if `name` matches the `vm_name_for_host` shape
    (`te-<6hex>-<host>`)."""
    return bool(_RE_TE_VM_NAME.match(name)) and len(name) <= 63


def is_te_vmid(vmid: int) -> bool:
    """True if `vmid` falls in the deterministic range `vmid_for_op` targets
    (100000-999999). `vmid_for_op` is tier-agnostic (it hashes `plan_id`,
    which already differs between a `te` and an `o2` plan) — this range is
    shared by every engine-managed VM regardless of tier and just doubles as
    the secondary `img`-template exclusion guard; a resource's TIER is
    determined by its NAME shape (`is_te_vm_name`/`is_o2_vm_name`), not by
    which numeric bucket its vmid falls in."""
    return _VMID_MIN <= vmid <= _VMID_MAX


def is_tap_gretap(name: str) -> bool:
    """True if `name` matches the gretap netdev shape (`tapg_<6hex>`)
    produced by `gre_port_for_bridge`/`gre_port_for_bridge_sensor`."""
    return bool(_RE_TAP_GRETAP.match(name))


def is_tap_mirror(name: str) -> bool:
    """True if `name` matches the per-VM OVS mirror shape (`tapm-<vmid>`)
    produced by `mirror_name_for_vmid`."""
    return bool(_RE_TAP_MIRROR.match(name))


def is_o2_zone(name: str) -> bool:
    """True if `name` matches the standing-O2 zone shape (`o2z<5hex>`)."""
    return bool(_RE_O2_ZONE.match(name))


def is_o2_vnet(name: str) -> bool:
    """True if `name` matches the standing-O2 vnet shape (`o2v<5hex>`)."""
    return bool(_RE_O2_VNET.match(name))


def is_o2_bridge(name: str) -> bool:
    """True if `name` matches the standing-O2 bridge shape (`o2br_<hex>`)."""
    return bool(_RE_O2_BRIDGE.match(name))


def is_o2_vm_name(name: str) -> bool:
    """True if `name` matches the standing-O2 VM name shape (`o2-<...>`)."""
    return bool(_RE_O2_VM_NAME.match(name)) and len(name) <= 63


def is_o2(name: str) -> bool:
    """True if `name` matches ANY `o2_*` shape — the teardown protect-guard
    convenience predicate (`docs/WORK_ORDER_resource_tags.md` §3)."""
    return (
        is_o2_zone(name) or is_o2_vnet(name) or is_o2_bridge(name)
        or is_o2_vm_name(name)
    )


def is_te(name: str) -> bool:
    """True if `name` matches ANY `te_*` shape — the tier-generic aggregate
    predicate `planner/teardown.py`'s six-tier `_protected()` matrix keys off
    (`docs/WORK_ORDER_tier_split.md`), mirroring `is_o2`/`is_tap`/`is_tfc`/
    `is_exr`."""
    return (
        is_te_zone(name) or is_te_vnet(name) or is_te_bridge(name)
        or is_te_vm_name(name)
    )


def is_tap(name: str) -> bool:
    """True if `name` matches ANY `tap_*` shape (gretap or mirror) — the
    tier-generic aggregate predicate, mirroring `is_te`/`is_o2`/`is_tfc`/
    `is_exr`."""
    return is_tap_gretap(name) or is_tap_mirror(name)


def is_tfc_zone(name: str) -> bool:
    """True if `name` matches the Traffic-tier zone shape (`tfc<5hex>`)."""
    return bool(_RE_TFC_ZONE.match(name))


def is_tfc_vnet(name: str) -> bool:
    """True if `name` matches the Traffic-tier vnet shape (`tfcv<4hex>`)."""
    return bool(_RE_TFC_VNET.match(name))


def is_tfc_bridge(name: str) -> bool:
    """True if `name` matches the Traffic-tier bridge shape (`tfcbr_<hex>`)."""
    return bool(_RE_TFC_BRIDGE.match(name))


def is_tfc_vm_name(name: str) -> bool:
    """True if `name` matches the Traffic-tier VM name shape
    (`tfc-<6hex>-<host>`)."""
    return bool(_RE_TFC_VM_NAME.match(name)) and len(name) <= 63


def is_tfc(name: str) -> bool:
    """True if `name` matches ANY `tfc_*` shape — the teardown protect-guard
    convenience predicate for the Traffic tier."""
    return (
        is_tfc_zone(name) or is_tfc_vnet(name) or is_tfc_bridge(name)
        or is_tfc_vm_name(name)
    )


def is_exr_zone(name: str) -> bool:
    """True if `name` matches the Exercise-tier zone shape (`exr<5hex>`)."""
    return bool(_RE_EXR_ZONE.match(name))


def is_exr_vnet(name: str) -> bool:
    """True if `name` matches the Exercise-tier vnet shape (`exrv<4hex>`)."""
    return bool(_RE_EXR_VNET.match(name))


def is_exr_bridge(name: str) -> bool:
    """True if `name` matches the Exercise-tier bridge shape (`exrbr_<hex>`)."""
    return bool(_RE_EXR_BRIDGE.match(name))


def is_exr_vm_name(name: str) -> bool:
    """True if `name` matches the Exercise-tier VM name shape
    (`exr-<6hex>-<host>`)."""
    return bool(_RE_EXR_VM_NAME.match(name)) and len(name) <= 63


def is_exr(name: str) -> bool:
    """True if `name` matches ANY `exr_*` shape — the teardown protect-guard
    convenience predicate for the Exercise tier."""
    return (
        is_exr_zone(name) or is_exr_vnet(name) or is_exr_bridge(name)
        or is_exr_vm_name(name)
    )


# ---------------------------------------------------------------------------
# The canonical tier table (`docs/WORK_ORDER_tier_split.md` review, MUST-FIX
# #5) — exported so `planner/teardown.py`'s blast-radius guard derives BOTH
# its "which foreign tiers exist to refuse" half (`TIER_PREDICATES`) and its
# "what does THIS tier's own resource look like" half (`TIER_SHAPE_CHECKS`)
# from ONE table this module owns, instead of hand-maintaining matching
# tables in two files that can silently drift. The failure mode a drift
# produces is the worst kind for a blast-radius guard: a tier wired into the
# producers above but forgotten in a guard-side table produces names NO
# predicate owns, so the foreign-tier refusal loop refuses NOTHING for it and
# every OTHER tier's destroy may sweep it — silent and permissive. The
# assertions below run at IMPORT TIME (not just in a test) so a future
# half-wired tier fails the moment this module is imported, not later on a
# live cluster.
# ---------------------------------------------------------------------------
TIER_PREDICATES: dict[str, Callable[[str], bool]] = {
    "te": is_te, "tap": is_tap, "o2": is_o2, "tfc": is_tfc, "exr": is_exr,
}
"""Every tier this module can shape-match a name against, INCLUDING `tap`
(a resource-shape tag, not a `deploys.tier` value — see `_VALID_TIERS`) —
this is the table `planner/teardown.py`'s `_protected()` loops over to
refuse a FOREIGN tier's resource. `img`/protected-external are deliberately
absent: they are refused UNCONDITIONALLY (even by their own would-be tier,
which doesn't exist for `img` anyway), never looped over as a "foreign tier
relative to own_tier" case."""

TIER_SHAPE_CHECKS: dict[str, tuple[Callable[[str], bool], Callable[[str], bool], Callable[[str], bool]]] = {
    "te": (is_te_vnet, is_te_zone, is_te_vm_name),
    "o2": (is_o2_vnet, is_o2_zone, is_o2_vm_name),
    "tfc": (is_tfc_vnet, is_tfc_zone, is_tfc_vm_name),
    "exr": (is_exr_vnet, is_exr_zone, is_exr_vm_name),
}
"""(vnet_ok, zone_ok, vm_name_ok) for each DEPLOY tier (`_VALID_TIERS` —
`te`/`o2`/`tfc`/`exr`). `tap`/`img` are intentionally absent: `deploys.tier`
(`planner/state.py`) is always one of `_VALID_TIERS`, so a destroy's OWN tier
never needs a `tap`/`img` shape-check triple. `planner/teardown.py` adds the
GRETAP slot itself, derived from ITS OWN cascade-policy table
(`_CASCADE_ALLOWED_FOREIGN`) rather than from a naming-shape fact this module
should own — whether a tier is ALLOWED to touch `tap_*` resources is a
teardown/cascade decision, not a naming decision."""

# Coverage assertions — run at IMPORT TIME, not just in a test (LES-088: fail
# CLOSED; a silently-drifted tier table is the exact "fail open" failure mode
# a blast-radius guard must never have).
assert set(_VALID_TIERS) == set(TIER_SHAPE_CHECKS), (
    "naming.py: _VALID_TIERS and TIER_SHAPE_CHECKS have drifted out of "
    "lockstep -- every deploy tier the producers route to must have a "
    "shape-check triple here, and vice versa"
)
assert set(_VALID_TIERS) <= set(TIER_PREDICATES), (
    "naming.py: every deploy tier in _VALID_TIERS must have an aggregate "
    "predicate in TIER_PREDICATES -- planner/teardown.py's foreign-tier "
    "refusal loop keys off this exact table"
)


def is_img_vm_name(name: str) -> bool:
    """True if `name` matches the golden-template VM name shape
    (`img-<slug>`) produced by `img_vm_name_for_image`."""
    return bool(_RE_IMG_VM_NAME.match(name)) and len(name) <= 63


def is_img(name: str) -> bool:
    """True if `name` matches ANY `img_*` shape — the teardown protect-guard
    convenience predicate (`docs/WORK_ORDER_resource_tags.md` §3)."""
    return is_img_vm_name(name)


# Operator-managed nodes that live OUTSIDE every axRange tier entirely — hand-
# created, never produced by any producer in this module, and therefore not
# describable by a shape-match at all. `XPress-SW` is the packetXpress
# software sink/capture box (`pktxpress-*` already appears as a tap-sensor
# endpoint name elsewhere in the test suite): an operator stands it up once,
# by hand, and every `te`/`tap`/`o2` deploy's taps point AT it — it must
# outlive all three tiers. `adapter.conf`'s `PROTECTED_EXTERNAL_NAMES` lets an
# operator extend this set without a code change; `is_protected_external`
# below takes that extra set as a parameter (this module stays config-free —
# `adapter_proxmox/config.py` owns parsing the conf key).
PROTECTED_EXTERNAL_NAMES: frozenset[str] = frozenset({"XPress-SW"})


def is_protected_external(name: str, extra: Iterable[str] = ()) -> bool:
    """True if `name` is an operator-managed node the teardown must NEVER
    remove — the built-in `PROTECTED_EXTERNAL_NAMES` (e.g. the packetXpress
    'XPress-SW' sink/capture box) plus any operator `extra` from
    adapter.conf's `PROTECTED_EXTERNAL_NAMES`. Unconditional, like `is_img`:
    refused by every tier including `o2`, since a sink node outlives both the
    `te` substrate it captures and the `o2` surface it feeds.

    Both `name` and each `extra` entry are `.strip()`-ped before comparing
    (review hardening: a stray-whitespace PVE display name must not silently
    slip past the guard) — but the match stays CASE-SENSITIVE; the declared
    value must match the PVE display name exactly, aside from surrounding
    whitespace."""
    if not name:
        return False
    stripped = name.strip()
    if not stripped:
        return False
    return (
        stripped in PROTECTED_EXTERNAL_NAMES
        or stripped in {e.strip() for e in extra}
    )


# ---------------------------------------------------------------------------
# `axrange.platform.protocols.ResourceNamer` — this module satisfies the
# engine's naming seam structurally (`adapters/proxmox/SPEC.md` Seam table).
# The tier tables are reached through accessors (a Protocol member must be
# callable to be checked at runtime) and the two platform-identity names are
# the neutral spellings of `vmid_for_op` / `is_te_vmid`: a `GuestHandle` is
# the VMID here, a domain UUID on libvirt.
# ---------------------------------------------------------------------------
def tier_predicates() -> dict[str, Callable[[str], bool]]:
    """`TIER_PREDICATES` (`ResourceNamer.tier_predicates`)."""
    return TIER_PREDICATES


def tier_shape_checks() -> dict[str, tuple[Callable[[str], bool], ...]]:
    """`TIER_SHAPE_CHECKS` (`ResourceNamer.tier_shape_checks`)."""
    return TIER_SHAPE_CHECKS


def guest_handle_for_op(plan_id: str, op_id: str) -> int:
    """`ResourceNamer.guest_handle_for_op`: the VMID (`vmid_for_op`)."""
    return vmid_for_op(plan_id, op_id)


def is_managed_guest_handle(guest: int | str) -> bool:
    """`ResourceNamer.is_managed_guest_handle`: `is_te_vmid` over the
    handle; a non-numeric handle is never one of ours."""
    try:
        return is_te_vmid(int(guest))
    except (TypeError, ValueError):
        return False
