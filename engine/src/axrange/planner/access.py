"""Twin access facts — the per-host "how do I get in" projection consumed by
`te info` (`docs/WORK_ORDER_access_facts.md` Part D) and its API peer
(`GET /api/deploys/{deploy_id}/access`, `docs/design/11-api-ui.md`).

Lives in the ENGINE (`axrange.planner`), not `axrange.cli` (LES-025's reuse-
downward direction, mirrored from `axrange.planner.inventory`'s
`build_twin_inventory` — the API is a `cli/`-class consumer of the SAME
derivation, never a second implementation, `docs/WORK_ORDER_tier_split.md`'s
own "the API and the CLI both call `build_twin_inventory`" precedent).
`axrange.cli.main` imports `te_info_from_ops`/`fill_access_facts` from here
(re-bound to its historical `_te_info_from_ops`/`_fill_access_facts` names so
existing tests keep working unchanged) and `axrange.api.service` imports the
same two functions directly — one derivation, two thin renderers.

Two-tier read, generalizing `axrange.o2control.control._inventory_from_ops`'s
pattern (do NOT invent a new data plane): a bare/`[id]` call reads a live
deploy's persisted ops (config + access facts, `StateStore.ops_for_deploy`);
a `<scenario.yaml>` call reads a freshly DRY-built `Plan`'s in-memory ops
(config only, no live vmid to resolve against) — `axrange.planner.opview.
op_fields` normalizes both shapes uniformly (LES-037).
"""
from __future__ import annotations

import dataclasses
from typing import Any, Optional


@dataclasses.dataclass
class TeHostInfo:
    host_id: str
    kind: str = "host"          # "host" | "router"
    vmid: Optional[int] = None  # live only (clone-vm's persisted output)
    ssh_user: Optional[str] = None
    ips: dict[str, str] = dataclasses.field(default_factory=dict)     # net_id -> ip
    wan_ip: Optional[str] = None  # live only, router (configure-router's discovered wan_ip)
    # `taps:` REMOVED here (docs/WORK_ORDER_tap_eligibility.md, Authority:
    # user, 2026-07-28) -- a te no longer records any tap-related state
    # (`record-tap-intent` is retired); capture intent is authored entirely
    # in the standalone tap plan, which `te info` never reaches into.
    # Access facts (docs/WORK_ORDER_access_facts.md Part D). `os_family` and
    # `explicit_password` are RAW facts read straight off `configure-cloud-init`'s
    # persisted/planned inputs; the `access_*` fields below are DERIVED at
    # render time (`fill_access_facts`) by combining them with the loaded
    # `AdapterConfig` -- kept as real dataclass fields (not computed only in
    # the text renderer) so `--json` / the API DTO carry them too.
    os_family: str = "linux"
    explicit_password: Optional[str] = None  # binding cloud_init.password, if set
    # `configure-router`'s own `router_os` input -- "vyos" or `None` (Debian/
    # nftables routers carry no `router_os` on THIS op; their os_family/
    # ssh_user instead come from their own `configure-cloud-init` op, same as
    # any other Linux host). Cheap VyOS signal for `fill_access_facts`
    # (SHOULD-FIX 2, code review 2026-07-25) -- VyOS gets NO configure-cloud-init
    # op at all (pass1_emit.py), so `ssh_user`/`os_family` never get set for it.
    router_os: Optional[str] = None
    access_ip: Optional[str] = None
    access_user: Optional[str] = None
    # ALWAYS the real, unmasked effective password -- masking (`--no-secrets`
    # / the API's `?secrets=true` gate) is a RENDER-time concern in each
    # surface, never here (`--json` / the raw DTO both carry the real value,
    # per Part D's own "it's operator-local" call). `None` for a router with
    # no cloud-init-sourced login at all (VyOS et al.) -- there IS no
    # default-password account to show.
    access_password: Optional[str] = None
    access_hint: Optional[str] = None


@dataclasses.dataclass
class TeSubnetInfo:
    net_id: str
    cidr: Optional[str] = None
    gateway: Optional[str] = None
    snat: bool = False


@dataclasses.dataclass
class TeInfo:
    scenario_id: str
    plan_id: Optional[str] = None
    deploy_id: Optional[str] = None
    status: Optional[str] = None
    ops_total: int = 0
    ops_done: int = 0
    hosts: list[TeHostInfo] = dataclasses.field(default_factory=list)
    subnets: list[TeSubnetInfo] = dataclasses.field(default_factory=list)
    # The twin's CONTAINMENT POSTURE (GAP-101) as a plain dict -- `None` when
    # the twin declares none. A dict rather than the `EgressPosture` object so
    # `dataclasses.asdict(info)` (`te info --json`) keeps working unchanged;
    # the object's own `to_dict()` is the single shape, shared with
    # `te inventory` (LES-037 -- one derivation, two renderers).
    #
    # Populated on BOTH tiers of this read: a live deploy's persisted rows
    # give a real `phase_applied`, while a `<scenario.yaml>` preview (ops
    # built dry, never run) shows the DECLARED phase with `phase_applied:
    # null` -- which is the honest answer for a twin that is not deployed.
    egress: Optional[dict[str, Any]] = None


def te_info_from_ops(ops: Any, *, scenario_id: str, plan_id: Optional[str]) -> TeInfo:
    from axrange.planner.egress import egress_posture_from_ops
    from axrange.planner.opview import op_fields

    # Materialized: this function scans `ops` twice now (the main loop below
    # + `egress_posture_from_ops`), and a caller may legitimately hand us a
    # one-shot view (`Plan.ops.values()`) or generator.
    ops = list(ops)
    hosts: dict[str, TeHostInfo] = {}
    subnets: dict[str, TeSubnetInfo] = {}
    ops_total = 0
    ops_done = 0

    def host(hid: str) -> TeHostInfo:
        return hosts.setdefault(hid, TeHostInfo(host_id=hid))

    for op in ops:
        ops_total += 1
        op_status = getattr(op, "status", None)  # only an OpRow carries this
        if op_status is not None and getattr(op_status, "value", op_status) == "completed":
            ops_done += 1

        name, scope, inputs, outputs = op_fields(op)
        if name == "clone-vm":
            h = scope.get("host")
            vmid = outputs.get("vmid")
            if h and vmid is not None:
                try:
                    host(str(h)).vmid = int(vmid)
                except (TypeError, ValueError):
                    pass
        elif name == "configure-cloud-init":
            h = scope.get("host")
            if h:
                hi = host(str(h))
                hi.ssh_user = inputs.get("ci_user")
                hi.os_family = (inputs.get("os_family") or "linux").lower()
                hi.explicit_password = inputs.get("password")
        elif name == "attach-vm-to-vnet":
            h = scope.get("host")
            if not h:
                continue
            net_id = scope.get("net") or inputs.get("net_id")
            hi = host(str(h))
            # M2 audit (`docs/WORK_ORDER_tap_scopes.md`): this module scans
            # `attach-vm-to-vnet` ops DIRECTLY (never `build_twin_inventory`'s
            # `HostNet`/`external` marker), and already special-cased
            # `__wan__` before that change -- its own `ips` dict never
            # carried it (the WAN IP is unresolved at plan time anyway;
            # `configure-router`'s discovered `wan_ip`, below, is the real
            # source `fill_access_facts` prefers). No change needed here.
            if net_id == "__wan__":
                hi.kind = "router"
            else:
                ip = inputs.get("ip")
                if net_id and ip:
                    hi.ips[str(net_id)] = str(ip)
        elif name == "configure-router":
            h = scope.get("host")
            if h:
                hi = host(str(h))
                hi.kind = "router"
                wan_ip = outputs.get("wan_ip")
                if wan_ip:
                    hi.wan_ip = str(wan_ip)
                router_os = inputs.get("router_os")
                if router_os:
                    hi.router_os = str(router_os)
        elif name == "create-subnet":
            net_id = scope.get("net") or inputs.get("net_id")
            if net_id:
                subnets[str(net_id)] = TeSubnetInfo(
                    net_id=str(net_id), cidr=inputs.get("cidr"),
                    gateway=inputs.get("gateway"), snat=bool(inputs.get("snat")),
                )
        # `record-tap-intent` branch REMOVED here (docs/WORK_ORDER_tap_eligibility.md,
        # Authority: user, 2026-07-28) -- the op is retired; a te deploy never
        # records tap state to read back.

    posture = egress_posture_from_ops(ops)
    return TeInfo(
        scenario_id=scenario_id, plan_id=plan_id, ops_total=ops_total, ops_done=ops_done,
        hosts=sorted(hosts.values(), key=lambda h: h.host_id),
        subnets=sorted(subnets.values(), key=lambda s: s.net_id),
        egress=(posture.to_dict() if posture is not None else None),
    )


def fill_access_facts(info: TeInfo, cfg: Any, *, live_resolve: bool = True) -> None:
    """Populate every host's `access_*` fields (docs/WORK_ORDER_access_facts.md
    Part D) in place. Effective IP, per host, in priority order:
      1. a router's discovered `wan_ip` (configure-router's persisted output)
      2. any persisted/planned static net IP (`h.ips`)
      3. (only if `live_resolve`) a best-effort live Proxmox lookup by vmid
      4. `"(unresolved)"`

    Login user: `"Administrator"` for a Windows host, else the binding's
    `ci_user` (or the adapter's `"axrange"` default). Effective password:
    the binding's explicit `cloud_init.password` if set, else
    `cfg.default_password` -- ALWAYS the real value; masking is a render-
    time concern in each surface (`cmd_te_info`'s `--no-secrets`, the API's
    `?secrets=true` gate), not stored here.

    EXCEPTION -- a router with NO `configure-cloud-init` op (VyOS;
    pass1_emit.py skips cloud-init entirely for it, config goes over SSH
    `vbash` instead) never gets a `ci_user`/default-password account at all:
    it boots self-configured with SSH + the operator key baked into the
    template. Detected cheaply via `h.ssh_user` being unset on a `kind ==
    "router"` host (only `configure-cloud-init` ever sets `ssh_user` --
    a Debian/nftables router DOES get that op and so is unaffected). In that
    case `access_password` stays `None` (no default-password hint shown) and
    `access_hint` is a key-auth hint instead of `ssh <user>@<ip>` -- `"ssh
    vyos@<ip> (key auth)"` when `h.router_os == "vyos"` is the cheap signal
    (`configure-router`'s own `router_os` input), else a generic
    `"(<ip>) (key auth -- no default password)"` (code review SHOULD-FIX 2,
    2026-07-25).

    Live resolution is best-effort and NEVER raises (`resolve_vm_ip`
    degrades to `None` on any adapter failure) -- `live_resolve=False` lets
    a caller skip the network call entirely (e.g. a not-yet-deployed
    scenario.yaml preview has no live vmid to resolve against anyway).
    """
    from axrange.platform import adapter_for_config

    # The inventory seam (`Inventory.guest_address`, best-effort, never
    # raises); not even built when the caller opted out of live reads.
    inventory = adapter_for_config(cfg).inventory if live_resolve else None

    for h in info.hosts:
        ip: Optional[str] = None
        if h.kind == "router" and h.wan_ip:
            ip = h.wan_ip
        elif h.ips:
            ip = sorted(h.ips.items())[0][1]
        if not ip and inventory is not None and h.vmid is not None and inventory.is_configured():
            ip = inventory.guest_address(h.vmid)
        h.access_ip = ip or "(unresolved)"

        if h.kind == "router" and not h.ssh_user:
            h.access_password = None
            if h.router_os == "vyos":
                h.access_user = "vyos"
                h.access_hint = f"ssh vyos@{h.access_ip}  (key auth)"
            else:
                h.access_user = None
                h.access_hint = f"({h.access_ip})  (key auth — no default password)"
            continue

        if h.os_family == "windows":
            h.access_user = "Administrator"
        else:
            h.access_user = h.ssh_user or "axrange"

        h.access_password = h.explicit_password or cfg.default_password

        if h.os_family == "windows":
            h.access_hint = f"rdp {h.access_ip}  (user={h.access_user})"
        else:
            h.access_hint = f"ssh {h.access_user}@{h.access_ip}"
