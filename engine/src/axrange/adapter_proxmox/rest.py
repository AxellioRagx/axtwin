"""REST backend for the Proxmox adapter.

Implements the 10 REST-routed ops from docs/ADAPTER.md §3 over Proxmox's
JSON API. Each op honors Model C (docs/ADAPTER.md §4): deterministic naming
via axrange.adapter_proxmox.naming, check-then-act on entry, partial-state
cleanup before redo.

The HTTP layer is intentionally `urllib.request` only — no `requests`
dependency. The slice runs against PVE 7.x / 8.x; differences across minor
versions surface during real-cluster bring-up and feed CHANGELOG-able
refinements per docs/ADAPTER.md §7.

Long-running operations (clone, hardware config that resizes disk) return a
PVE task UPID; `_wait_task` polls `/nodes/<node>/tasks/<upid>/status` and
raises AdapterError on non-OK terminal status.
"""
from __future__ import annotations

import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from axrange.adapter_proxmox.config import AdapterConfig
from axrange.adapter_proxmox.naming import (
    bridge_name_for_net,
    mac_for_nic,
    underlay_bridge_for_plan,
    vmid_for_op,
    vm_name_for_host,
    vnet_id_for_net,
    zone_id_for_plan,
)
from axrange.planner.dag import OpInstance
from axrange.planner.executor import AdapterError, AdapterTimeout, ExecutionContext


# PVE reports "create X: object already present" collisions with INCONSISTENT wording
# across SDN object types / versions: vnets & zones say "already exists", but SDN
# subnets (PVE 9.2) say "already defined". The `create-*` ops below are Model C — a
# re-run / resume must be a no-op — so an "already present" collision has to be tolerated
# as success. Matching only "already exists" missed the subnet wording and hard-failed
# create-subnet on a redeploy, halting the SDN apply subtree (GAP-004, dev45 2026-07-01).
# Keep this list broad; a false-positive here only ever turns a create into a no-op, which
# is the Model-C intent anyway.
_PVE_ALREADY_PRESENT = ("already exists", "already defined", "already registered")


def _is_already_present(exc: Exception) -> bool:
    """True if a PVE create error indicates the object already exists (any wording)."""
    msg = str(exc).lower()
    return any(phrase in msg for phrase in _PVE_ALREADY_PRESENT)


# configure-vm-hardware's disk resize is Model C: a resume/redeploy against an
# already-correctly-sized (or already-larger) disk must be a no-op, and PVE
# signals both cases as a resize FAILURE rather than a 2xx. But the prior blanket
# `except AdapterError: pass` swallowed EVERY resize error, including a real one
# (storage exhaustion, an invalid disk key) — that silently reported
# `{"configured": True}` and booted an undersized VM (code-review finding #1,
# 2026-07-07). Only these specific PVE wordings are the benign shrink/same-size
# no-op; anything else must propagate.
_RESIZE_NOOP_PHRASES = (
    "shrink",              # "shrinking disks is not supported"
    "same size",           # some PVE builds phrase a no-op resize this way
    "not larger",          # "... disk size ... is not larger than current size"
    "already this size",
)


def _is_resize_noop(exc: Exception) -> bool:
    """True if a PVE `/resize` failure is the benign shrink/same-size rejection
    Model C must swallow. False for any other error (must be re-raised)."""
    msg = str(exc).lower()
    return any(phrase in msg for phrase in _RESIZE_NOOP_PHRASES)


# Disk key axRange's hardware-config resize targets. All axRange-built
# templates boot from scsi0 (imagebuild/proxmox_build.py always attaches the
# base disk there), so it's a safe default — but hardcoding it blindly means a
# hand-built template with a different boot disk gets resized on the WRONG
# disk with no error (silent no-op against scsi0, which may not even exist).
# `_resolve_boot_disk_key` derives it from the live `qm config` when possible
# (code-review finding #1, 2026-07-07), falling back to `scsi0` when the
# config can't be read or carries no recognizable disk key.
_DISK_KEY_RE = re.compile(r"^(scsi|virtio|ide|sata)\d+$")
_DEFAULT_DISK_KEY = "scsi0"


def _resolve_boot_disk_key(client: RestClient, node: str, vmid: int) -> str:
    """Best-effort discovery of the VM's primary/boot disk key (e.g. `scsi0`)
    from its live PVE config, in preference order: the `bootdisk` field (older
    PVE), the first disk-shaped entry in `boot=order=...` (current PVE), then
    `scsi0` if present, then the lowest disk-shaped key present at all. Falls
    back to `_DEFAULT_DISK_KEY` on any read failure or when nothing
    recognizable is found — preserving the prior hardcoded behavior for every
    axRange-built template."""
    try:
        cfg_data = client.request("GET", f"/nodes/{node}/qemu/{vmid}/config")
    except AdapterError:
        return _DEFAULT_DISK_KEY
    if not isinstance(cfg_data, dict):
        return _DEFAULT_DISK_KEY
    bootdisk = cfg_data.get("bootdisk")
    if isinstance(bootdisk, str) and _DISK_KEY_RE.match(bootdisk):
        return bootdisk
    boot = cfg_data.get("boot")
    if isinstance(boot, str) and "order=" in boot:
        order = boot.split("order=", 1)[1].split(",", 1)[0]
        for key in order.split(";"):
            key = key.strip()
            if _DISK_KEY_RE.match(key) and key in cfg_data:
                return key
    disk_keys = sorted(
        k for k in cfg_data if isinstance(k, str) and _DISK_KEY_RE.match(k)
    )
    # Prefer scsi0 over whatever a plain alphabetical sort would otherwise
    # surface first (2026-07-07 review finding: `ide2` — a cloud-init drive,
    # not a boot disk — sorts ahead of `scsi0` since 'i' < 's'). Only
    # reachable when the template has neither `bootdisk` nor
    # `boot=order=...` set at all.
    if _DEFAULT_DISK_KEY in disk_keys:
        return _DEFAULT_DISK_KEY
    if disk_keys:
        return disk_keys[0]
    return _DEFAULT_DISK_KEY


# ---------------------------------------------------------------------------
# Low-level REST client
# ---------------------------------------------------------------------------


class RestClient:
    """Thin Proxmox API client.

    Auth is an API token: `Authorization: PVEAPIToken=user@realm!id=secret`.
    TLS verification follows `cfg.proxmox_verify_tls` — set false for the
    default self-signed PVE cert (slice default; tighten in prod).
    """

    def __init__(self, cfg: AdapterConfig) -> None:
        self.cfg = cfg
        if not cfg.proxmox_verify_tls:
            self._ssl_ctx = ssl._create_unverified_context()
        else:
            self._ssl_ctx = ssl.create_default_context()

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        timeout_s: int = 60,
    ) -> Any:
        """Issue one REST call. Returns the `data` field of the response.

        `timeout_s` (default 60, unchanged from before this parameter
        existed) bounds the whole request (connect + read). A best-effort
        caller doing live-resolution for a human-facing `info` command
        (`adapter_proxmox/resolve.py`) passes a short override so an
        unreachable cluster degrades quickly instead of hanging the
        terminal.

        Raises AdapterError on transport / non-2xx / Proxmox-error response.
        """
        if not self.cfg.proxmox_api_url:
            raise AdapterError(
                "PROXMOX_API_URL is not set; populate adapter.conf or env",
                error_class="adapter-config",
            )
        if not self.cfg.proxmox_api_token:
            raise AdapterError(
                "PROXMOX_API_TOKEN is not set; populate adapter.conf or env",
                error_class="adapter-config",
            )

        url = self.cfg.proxmox_api_url.rstrip("/") + path
        if params:
            url += "?" + urllib.parse.urlencode(params)

        data: bytes | None = None
        headers = {
            "Authorization": self.cfg.proxmox_api_token,
            "Accept": "application/json",
        }
        if body:
            # Flatten list-valued keys into repeated key=val pairs (Proxmox
            # API expects `command=cloud-init&command=status&...` for
            # array-typed parameters like agent/exec's `command`).
            items: list[tuple[str, str]] = []
            for k, v in body.items():
                if isinstance(v, (list, tuple)):
                    items.extend((k, str(x)) for x in v)
                else:
                    items.append((k, str(v)))
            data = urllib.parse.urlencode(items).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        req = urllib.request.Request(url, method=method, headers=headers, data=data)
        try:
            with urllib.request.urlopen(req, context=self._ssl_ctx, timeout=timeout_s) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            try:
                body_text = e.read().decode("utf-8", errors="replace")
            except Exception:
                body_text = ""
            raise AdapterError(
                f"proxmox REST {method} {path}: HTTP {e.code} {e.reason}; {body_text[:200]}",
                error_class="proxmox-api-http",
            ) from e
        except urllib.error.URLError as e:
            raise AdapterError(
                f"proxmox REST {method} {path}: transport error {e.reason}",
                error_class="proxmox-api-transport",
            ) from e

        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            raise AdapterError(
                f"proxmox REST {method} {path}: malformed JSON response",
                error_class="proxmox-api-json",
            ) from e

        # PVE wraps payload in {"data": ...} (and sometimes errors=...).
        if isinstance(parsed, dict):
            if "errors" in parsed and parsed["errors"]:
                raise AdapterError(
                    f"proxmox REST {method} {path}: errors={parsed['errors']}",
                    error_class="proxmox-api-error",
                )
            return parsed.get("data") if "data" in parsed else parsed
        return parsed

    def wait_task(self, node: str, upid: str, *, timeout_s: int = 600) -> None:
        """Poll `/nodes/<node>/tasks/<upid>/status` until done or timeout.

        PVE task `status` is "running" until done, then `exitstatus` is "OK"
        or an error string. Raises AdapterError on non-OK; AdapterTimeout on
        wait exhaustion.
        """
        deadline = time.time() + timeout_s
        path = f"/nodes/{node}/tasks/{urllib.parse.quote(upid, safe='')}/status"
        while time.time() < deadline:
            data = self.request("GET", path)
            if not isinstance(data, dict):
                time.sleep(2)
                continue
            if data.get("status") == "running":
                time.sleep(2)
                continue
            exit_status = data.get("exitstatus")
            if exit_status == "OK":
                return
            raise AdapterError(
                f"proxmox task {upid} ended with exitstatus={exit_status!r}",
                error_class="proxmox-task-failed",
            )
        raise AdapterTimeout(f"proxmox task {upid} did not finish in {timeout_s}s")


# ---------------------------------------------------------------------------
# Resource existence-checks — module-level, `RestClient`-only (no `RestBackend`
# instance required), so any consumer with just a client can reuse them instead
# of re-deriving the same GET+swallow-404 shape. `RestBackend` below is a thin
# wrapper over these for its own check-then-act call sites. Originally
# instance-private methods on `RestBackend`; `adapter_proxmox/teardown.py`
# already hand-rolls its own copies of `_vnet_exists`/`_zone_exists`/
# `_bridge_exists` for the reverse direction, and a later planner-side wave
# wants the same shapes — exposed here as the single source of truth
# (code-review finding #5, 2026-07-07).
# ---------------------------------------------------------------------------


def zone_exists(client: RestClient, zone_id: str) -> bool:
    """True if the SDN zone `zone_id` is defined in `/cluster/sdn/zones`."""
    try:
        data = client.request("GET", f"/cluster/sdn/zones/{zone_id}")
    except AdapterError:
        return False
    return bool(data)


def bridge_exists(client: RestClient, node: str, bridge_name: str) -> bool:
    """True if `bridge_name` is defined in `node`'s `/nodes/<node>/network`."""
    try:
        data = client.request("GET", f"/nodes/{node}/network/{bridge_name}")
    except AdapterError:
        return False
    return bool(data)


def vnet_exists(client: RestClient, vnet_id: str) -> bool:
    """True if the SDN vnet `vnet_id` is defined in `/cluster/sdn/vnets`."""
    try:
        data = client.request("GET", f"/cluster/sdn/vnets/{vnet_id}")
    except AdapterError:
        return False
    return bool(data)


def subnet_exists(client: RestClient, vnet_id: str, cidr: str) -> bool:
    """True if vnet `vnet_id` already carries a subnet for `cidr`. PVE keys the
    subnet object id off the cidr (`<zone>-<a.b.c.d>-<mask>`), so we match on
    the `cidr` field, falling back to the dashed cidr embedded in the id."""
    try:
        subs = client.request("GET", f"/cluster/sdn/vnets/{vnet_id}/subnets") or []
    except AdapterError:
        return False
    dashed = cidr.replace("/", "-")
    for s in subs if isinstance(subs, list) else []:
        if not isinstance(s, dict):
            continue
        if s.get("cidr") == cidr:
            return True
        sid = str(s.get("subnet") or s.get("id") or "")
        if dashed in sid:
            return True
    return False


def vm_status(
    client: RestClient, node: str, vmid: int, *, timeout_s: int | None = None
) -> dict[str, Any] | None:
    """The VM's `/status/current` payload, or None if it doesn't exist / the
    call fails (Model C check-then-act: "no status" == "not present").

    `timeout_s` is `None` by default and, when `None`, is NOT forwarded to
    `client.request` at all (not even as the real `RestClient`'s own 60s
    default) -- this keeps every existing call site's `client.request(...)`
    invocation byte-for-byte unchanged, which matters because a fair number
    of tests pass a hand-written fake `RestClient` whose `request()` doesn't
    accept a `timeout_s` kwarg. Only `adapter_proxmox/resolve.py`'s
    best-effort `info`-time lookups pass an explicit short override.
    """
    try:
        if timeout_s is None:
            data = client.request("GET", f"/nodes/{node}/qemu/{vmid}/status/current")
        else:
            data = client.request(
                "GET", f"/nodes/{node}/qemu/{vmid}/status/current", timeout_s=timeout_s
            )
    except AdapterError:
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# Backend dispatcher
# ---------------------------------------------------------------------------


class RestBackend:
    """Routes REST-backed ops to their concrete REST calls."""

    def __init__(self, cfg: AdapterConfig, *, client: RestClient | None = None) -> None:
        self.cfg = cfg
        self.client = client or RestClient(cfg)

    def dispatch(
        self,
        op: OpInstance,
        resolved_inputs: dict[str, Any],
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        fn = _OP_TO_METHOD.get(op.op_name)
        if fn is None:
            raise AdapterError(
                f"REST backend has no implementation for {op.op_name!r}",
                error_class="adapter-unsupported",
            )
        return fn(self, op, resolved_inputs, ctx)

    # ---- OP-NET ---------------------------------------------------------

    def op_create_ovs_bridge(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        """Create the bridge(s) backing the scenario networks (SDN-native, 2026-06-03).

        Runs *before* create-sdn-zone so a VLAN zone can reference an existing
        underlay bridge.

        - **VLAN zone** (`zone_type == "vlan"`): create ONE OVS underlay bridge
          per zone via `/nodes/<node>/network` (type=OVSBridge) and commit the
          node network so it exists for the zone to reference. The underlay is
          kept OVS because that path is live-verified and the VLAN zone uses it
          as its backing bridge; capture itself no longer depends on it — under
          Option 1 (2026-06-04) the mirror is a per-tap `tc` mirror on the VM
          tap, not an `ovs-vsctl` port-mirror on this bridge.
        - **Simple zone**: the SDN vnet itself materializes the per-network
          bridge (a PVE Linux bridge), so there is no separate OVS bridge to
          create. We return the vnet's bridge name for downstream consumers.
          Capture works here too: the per-tap `tc` mirror (Option 1) binds on the
          VM tap on this Linux vnet bridge — the earlier "Simple-zone capture
          won't work" caveat is resolved (CHANGELOG 2026-06-04).
        """
        zone_type = (inp.get("zone_type") or "simple").lower()
        target_node = (
            (ctx.placement and next(iter(ctx.placement.values()), ""))
            or ctx.cluster.default_node
        )
        if not target_node:
            raise AdapterError(
                "create-ovs-bridge: no target_node available "
                "(ctx.cluster.default_node)",
                error_class="adapter-input",
            )

        if zone_type != "vlan":
            net_id = inp.get("net_id") or op.scope.get("net")
            if not net_id:
                raise AdapterError(
                    "create-ovs-bridge: net_id missing for Simple zone",
                    error_class="adapter-input",
                )
            # Simple zone: the vnet (created in op_create_vnet) is the bridge.
            return {"bridge_name": vnet_id_for_net(net_id, plan_id=ctx.plan_id, tier=ctx.tier)}

        bridge_name = underlay_bridge_for_plan(ctx.plan_id, tier=ctx.tier)
        if self._bridge_exists(target_node, bridge_name):
            return {"bridge_name": bridge_name}
        body = {"iface": bridge_name, "type": "OVSBridge", "autostart": 1}
        try:
            self.client.request("POST", f"/nodes/{target_node}/network", body=body)
        except AdapterError as e:
            if not _is_already_present(e):
                raise
        # Commit the node network so the underlay exists before the VLAN zone
        # POST references it. `PUT /nodes/<node>/network` is ASYNC (returns a
        # reloadnetworkall UPID); we must WAIT for it, else the OVS underlay
        # bridge isn't live when the later apply-sdn-config (`PUT /cluster/sdn`)
        # regenerates /etc/network/interfaces.d/sdn — PVE's SDN generator then
        # silently skips the vnet (its underlay bridge "doesn't exist yet") and
        # writes an EMPTY sdn file, so the vnet bridge never materializes and
        # start-vm fails with "bridge '<vnet>' does not exist". Surfaced live on
        # dev45 (PVE 9.2, 2026-06-18): the bridge appeared eventually (async) but
        # after the SDN apply had already run, so every fresh deploy lost the race.
        try:
            upid = self.client.request("PUT", f"/nodes/{target_node}/network")
            if isinstance(upid, str) and upid.startswith("UPID:"):
                self.client.wait_task(
                    target_node, upid, timeout_s=op.timeout_s or 120
                )
        except (AdapterError, AdapterTimeout):
            pass
        return {"bridge_name": bridge_name}

    def op_create_sdn_zone(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        """Create the per-scenario SDN zone in `/cluster/sdn` (SDN-native,
        2026-06-03 — retires the slice-MVP direct-bridge bypass).

        Zone type is auto-derived upstream (OP-NET-010) and arrives as
        `zone_type`. A VLAN zone references the OVS underlay bridge created by
        create-ovs-bridge; a Simple zone needs no bridge (PVE creates a bridge
        per vnet). Model C: check-then-act on the zone id.
        """
        zone_id = zone_id_for_plan(ctx.plan_id, tier=ctx.tier)
        zone_type = (inp.get("zone_type") or "simple").lower()
        if self._zone_exists(zone_id):
            return {"zone_id": zone_id, "zone_type": zone_type}
        body: dict[str, Any] = {"type": zone_type, "zone": zone_id}
        if zone_type == "vlan":
            body["bridge"] = (
                inp.get("bridge_name")
                or underlay_bridge_for_plan(ctx.plan_id, tier=ctx.tier)
            )
        try:
            self.client.request("POST", "/cluster/sdn/zones", body=body)
        except AdapterError as e:
            if not _is_already_present(e):
                raise
        return {"zone_id": zone_id, "zone_type": zone_type}

    def op_create_vnet(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        """Create one SDN vnet in the scenario zone (SDN-native, 2026-06-03).

        The vnet carries `tag=<vlan>` in a VLAN zone (PVE applies the VLAN at
        the vnet's bridge, so the VM NIC needs no tag — see op_attach_vm_to_vnet);
        in a Simple zone it is untagged. PVE materializes a bridge named after
        the vnet that VMs attach to. Model C: check-then-act on the vnet id.
        """
        net_id = inp.get("net_id") or op.scope.get("net")
        if not net_id:
            raise AdapterError(
                "create-vnet: net_id missing", error_class="adapter-input"
            )
        vnet_id = vnet_id_for_net(net_id, plan_id=ctx.plan_id, tier=ctx.tier)
        zone_id = inp.get("zone_id") or zone_id_for_plan(ctx.plan_id, tier=ctx.tier)
        if self._vnet_exists(vnet_id):
            return {"vnet_id": vnet_id}
        body: dict[str, Any] = {"vnet": vnet_id, "zone": zone_id}
        vlan = inp.get("vlan")
        if vlan is not None:
            body["tag"] = int(vlan)
        try:
            self.client.request("POST", "/cluster/sdn/vnets", body=body)
        except AdapterError as e:
            if not _is_already_present(e):
                raise
        return {"vnet_id": vnet_id}

    def op_create_subnet(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        """Define a PVE SDN subnet on the network's vnet with a gateway and
        source-NAT for internet/apt egress (router slice Phase 1,
        docs/SLICE_ROUTER.md). The subnet is staged here and committed by the
        cluster-wide apply (op_apply_sdn_config).

        Model C: check-then-act on the subnet id (like create-zone/vnet) — the
        subnet ID is deterministic per-plan, so a resume / redeploy MUST no-op an
        existing one. The `_is_already_present` catch is a backstop for the TOCTOU
        window (GAP-004: a subnet-only reliance on the string backstop hard-failed
        on PVE's "already defined" wording and halted the SDN apply).
        """
        net_id = inp.get("net_id") or op.scope.get("net")
        cidr = inp.get("cidr")
        if not net_id or not cidr:
            raise AdapterError(
                "create-subnet: net_id/cidr missing", error_class="adapter-input"
            )
        vnet_id = vnet_id_for_net(net_id, plan_id=ctx.plan_id, tier=ctx.tier)
        if self._subnet_exists(vnet_id, cidr):
            return {"subnet_id": cidr, "vnet_id": vnet_id}
        body: dict[str, Any] = {"subnet": cidr, "type": "subnet"}
        gateway = inp.get("gateway")
        if gateway:
            body["gateway"] = gateway
        if inp.get("snat"):
            body["snat"] = 1
        try:
            self.client.request(
                "POST", f"/cluster/sdn/vnets/{vnet_id}/subnets", body=body
            )
        except AdapterError as e:
            if not _is_already_present(e):
                raise
        return {"subnet_id": cidr, "vnet_id": vnet_id}

    def op_apply_sdn_config(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        """Commit all staged SDN definitions with one cluster-wide apply
        (`PUT /cluster/sdn`) — SDN-native, 2026-06-03.

        `PUT /cluster/sdn` is ASYNCHRONOUS — it returns the UPID of a
        `reloadnetworkall` task that regenerates `/etc/network/interfaces.d/sdn`
        and ifreloads. The per-vnet bridge (e.g. a VLAN zone's Linux vnet bridge
        `vn*` over its OVS underlay) does not exist until that task finishes, so
        we must WAIT for it; otherwise a fast follow-on `start-vm` fails with
        "bridge '<vnet>' does not exist". Surfaced live on dev45 (PVE 9.2,
        2026-06-18): the prior `sleep 2` only covered the tap re-assert and lost
        the race on a slower reconcile — start-vm failed for both Windows clones
        until the reload caught up. We poll for the reload task's completion
        (best-effort: a non-UPID return falls back to a short settle sleep).

        Post-apply, re-assert any running VM's tap iface on the axrange-managed
        (`te`-tier) OVS bridge(s). PVE's network reload can briefly down/up
        bridges; the pve-bridge hook only runs at VM start, so a re-deploy
        adopting already-running VMs could lose tap attachments. Carried over
        from the 2026-05-29 bring-up. **Watch item (live-verify on pxtwin1):**
        confirm whether an SDN apply still detaches running taps under
        SDN-native — if it does not, retire this re-assert; if it does, keep
        it (the scan already targets the `tebr_*` OVS bridges, which now
        means the VLAN underlay).
        """
        def _apply_and_wait() -> None:
            upid = self.client.request("PUT", "/cluster/sdn")
            if isinstance(upid, str) and upid.startswith("UPID:"):
                node = (
                    upid.split(":", 2)[1]
                    if upid.count(":") >= 2
                    else ctx.cluster.default_node
                )
                try:
                    self.client.wait_task(node, upid, timeout_s=op.timeout_s or 180)
                except AdapterTimeout:
                    pass  # fall through; the verify below decides if we retry

        # Apply, then verify the vnet bridges actually materialized. PVE's SDN
        # generator SILENTLY SKIPS a vnet whose underlay OVS bridge isn't live at
        # generation time, writing an empty /etc/network/interfaces.d/sdn — so a
        # following start-vm fails with "bridge '<vnet>' does not exist" (dev45
        # PVE 9.2, 2026-06-18). create-ovs-bridge now waits for its own reload,
        # which should close the window; this is a self-healing belt-and-braces:
        # if the SDN config stages vnets but none made it into the generated
        # file, re-apply (the underlay has settled by now) up to a few times.
        from axrange.adapter_proxmox.ssh import run_ssh

        _apply_and_wait()
        for _ in range(3):
            staged = run_ssh(
                self.cfg,
                "grep -c '^vnet:' /etc/pve/sdn/vnets.cfg 2>/dev/null || echo 0",
                timeout_s=15,
            )
            generated = run_ssh(
                self.cfg,
                "grep -c '^iface vn' /etc/network/interfaces.d/sdn 2>/dev/null || echo 0",
                timeout_s=15,
            )
            try:
                n_staged = int((staged or "0").strip().splitlines()[-1])
                n_gen = int((generated or "0").strip().splitlines()[-1])
            except (ValueError, IndexError):
                break
            if n_staged == 0 or n_gen >= n_staged:
                break  # nothing staged, or all vnets generated -> good
            time.sleep(3)
            _apply_and_wait()

        # Re-assert tap ports on all `tebr_*` (axrange-managed, `te` tier) OVS
        # bridges for every currently-running VM whose net<i> config
        # references one. Scoped to tebr_* so we don't touch non-axrange
        # bridges (e.g. vmbr0).
        scan_cmd = r"""
set +e
# Wait briefly for the network reload to settle before scanning.
sleep 2
for br in $(ovs-vsctl list-br 2>/dev/null | grep '^tebr_'); do
  current_ports=$(ovs-vsctl list-ports "$br" 2>/dev/null)
  for vmid in $(qm list 2>/dev/null | awk '$3 == "running" {print $1}'); do
    cfg=$(qm config "$vmid" 2>/dev/null)
    for i in 0 1 2 3; do
      net=$(echo "$cfg" | grep -E "^net${i}:")
      [ -z "$net" ] && continue
      echo "$net" | grep -q "bridge=${br}" || continue
      tap="tap${vmid}i${i}"
      ip link show "$tap" >/dev/null 2>&1 || continue
      if ! echo "$current_ports" | grep -qx "$tap"; then
        tag=$(echo "$net" | grep -oE 'tag=[0-9]+' | cut -d= -f2)
        if [ -n "$tag" ]; then
          ovs-vsctl --may-exist add-port "$br" "$tap" tag="$tag"
        else
          ovs-vsctl --may-exist add-port "$br" "$tap"
        fi
        echo "  re-attached $tap to $br${tag:+ (tag=$tag)}"
      fi
    done
  done
done
exit 0
"""
        run_ssh(self.cfg, scan_cmd, timeout_s=60)
        return {"applied": True}

    # ---- OP-HOST infra ------------------------------------------------

    def op_clone_vm(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        target_node = inp.get("target_node") or ctx.cluster.default_node
        template_vmid = inp.get("template_vmid")
        if template_vmid is None:
            raise AdapterError(
                "clone-vm: template_vmid missing — check image binding",
                error_class="adapter-input",
            )
        host_id = op.scope.get("host") or ""
        new_vmid = vmid_for_op(ctx.plan_id, op.op_id)
        new_name = vm_name_for_host(ctx.plan_id, host_id, tier=ctx.tier)

        # Check-then-act: is a VM with this VMID already present?
        existing = self._vm_status(target_node, new_vmid)
        if existing is not None:
            # Adopt ONLY if it's OURS — name-guard against a foreign VM sitting
            # on this deterministic VMID (a hash collision, or a hand-created/
            # reused id on the cluster). Adopting on VMID presence alone would
            # let follow-on ops (configure-vm-hardware, start-vm, ...) mutate
            # and boot someone else's VM (code-review finding #2, 2026-07-07).
            # `status/current` reports the VM's PVE display name; a missing
            # field (some mocks / older PVE) can't disprove ownership, so it
            # falls through to adopt as before.
            existing_name = existing.get("name")
            if existing_name and existing_name != new_name:
                raise AdapterError(
                    f"clone-vm: vmid={new_vmid} already exists with name "
                    f"{existing_name!r}, expected {new_name!r} for host "
                    f"{host_id!r} (plan={ctx.plan_id!r}) — refusing to adopt a "
                    "VM axRange did not create at this deterministic VMID.",
                    error_class="vmid-collision-foreign",
                )
            return {"vmid": new_vmid}

        clone_mode = inp.get("clone_mode") or "linked"
        body: dict[str, Any] = {
            "newid": new_vmid,
            "name": new_name,
        }
        if clone_mode == "linked":
            body["full"] = 0
        else:
            body["full"] = 1
        upid = self.client.request(
            "POST",
            f"/nodes/{target_node}/qemu/{template_vmid}/clone",
            body=body,
        )
        if isinstance(upid, str):
            self.client.wait_task(target_node, upid, timeout_s=op.timeout_s or 600)
        return {"vmid": new_vmid}

    def op_configure_vm_hardware(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        vmid = int(inp["vmid"])
        target_node = ctx.cluster.default_node
        body: dict[str, Any] = {}
        if inp.get("cpu") is not None:
            body["cores"] = int(inp["cpu"])
        if inp.get("ram") is not None:
            body["memory"] = _parse_mem_mb(str(inp["ram"]))
        if inp.get("machine"):
            body["machine"] = inp["machine"]
        if inp.get("bios"):
            body["bios"] = inp["bios"]
        if inp.get("agent") is True:
            body["agent"] = 1
        elif inp.get("agent") is False:
            body["agent"] = 0
        if body:
            self.client.request(
                "PUT", f"/nodes/{target_node}/qemu/{vmid}/config", body=body
            )
        # Disk resize is a separate endpoint when needed.
        if inp.get("disk"):
            disk_size = _normalize_pve_disk_size(str(inp["disk"]))
            disk_key = _resolve_boot_disk_key(self.client, target_node, vmid)
            try:
                self.client.request(
                    "PUT",
                    f"/nodes/{target_node}/qemu/{vmid}/resize",
                    body={"disk": disk_key, "size": disk_size},
                )
            except AdapterError as e:
                # Resize is Model C idempotent: PVE rejects a shrink / no-ops a
                # same-size request, and BOTH surface as an error rather than a
                # 2xx — only THOSE wordings are swallowed. Any other failure
                # (storage exhaustion, an invalid disk key) must propagate, not
                # silently report `{"configured": True}` over an undersized VM
                # (code-review finding #1, 2026-07-07).
                if not _is_resize_noop(e):
                    raise
        return {"configured": True}

    def op_configure_cloud_init(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        """Render a cicustom user-data snippet, scp it to /var/lib/vz/snippets/
        on the Proxmox node, then attach via `cicustom=user=...`.

        Why a custom snippet instead of the default PVE-generated user-data:
        Debian/Ubuntu cloud-cloud images ship qemu-guest-agent INSTALLED but
        NOT ENABLED. Our wait-guest-agent / wait-os-ready ops both depend on
        QGA being reachable. The snippet's runcmd does
        `systemctl enable --now qemu-guest-agent` on first boot.

        We render the full user-data ourselves (instead of overriding only
        `user-data` and trusting PVE for `network-data`) because PVE's
        cicustom path REPLACES the user-data slot entirely. Hostname, user,
        and authorized key go in the snippet; network-data is still PVE-
        auto-generated from ipconfig0.

        Mixed transport (REST for the VM config + SSH for the snippet file)
        is acceptable per ADAPTER.md §3 — snippet upload has no Proxmox API
        endpoint today, same forced-SSH reason as the OVS mirror plane.
        """
        vmid = int(inp["vmid"])
        target_node = ctx.cluster.default_node

        ci_user = inp.get("ci_user") or "axrange"
        hostname = inp.get("hostname") or f"host-{vmid}"
        # Deploy-wide default password (docs/WORK_ORDER_access_facts.md Part A,
        # Authority: user): a binding's cloud_init.password (an EXTRA field,
        # not part of the closed IMAGE_MANIFEST cloud_init schema -- passed
        # through as-is, `password` key threaded by pass1_emit's ci_inputs)
        # wins if set; else the deploy-wide default.
        password = inp.get("password") or self.cfg.default_password

        # W1.3: Windows clones specialize via a cloudbase-init <powershell> snippet,
        # not Linux cloud-config (docs/SLICE_WINDOWS_AD.md §5).
        if (inp.get("os_family") or "linux").lower() == "windows":
            return self._configure_windows_first_boot(
                vmid, target_node, hostname, inp.get("ip_config") or {},
                password=password,
            )

        # Read the operator's pubkey (same one OVS_SSH_KEY's pair lives at).
        pubkey_text = ""
        pubkey_path = getattr(self.cfg, "guest_ssh_pubkey_file", "") or ""
        if pubkey_path:
            try:
                from pathlib import Path as _Path
                pubkey_text = _Path(pubkey_path).expanduser().read_text().strip()
            except OSError:
                pubkey_text = ""

        # Render + scp the user-data snippet.
        userdata = _render_userdata(
            hostname=hostname, user=ci_user, ssh_pubkey=pubkey_text,
            password=password,
        )
        snippet_filename = f"axr-userdata-{vmid}.yaml"
        snippet_path = f"/var/lib/vz/snippets/{snippet_filename}"
        # SshBackend helpers — imported lazily to avoid module cycle.
        from axrange.adapter_proxmox.ssh import run_ssh, scp_to_remote

        run_ssh(self.cfg, "mkdir -p /var/lib/vz/snippets", timeout_s=10)
        scp_to_remote(self.cfg, userdata, snippet_path, timeout_s=20)

        # Build the PVE config body.
        body: dict[str, Any] = {
            "ciuser": ci_user,
            "cicustom": f"user=local:snippets/{snippet_filename}",
        }

        # ipconfig0..N — one per attached NIC, in emission/NIC order. Single-homed
        # hosts get just ipconfig0 (unchanged); multi-homed routers get one per
        # LAN net + a DHCP WAN (`ip: "dhcp"`). Router slice Phase 2. Shared with
        # the Windows first-boot path below (finding #3 dedup, 2026-07-07).
        body.update(_build_ipconfig_body(inp.get("ip_config") or {}))

        if hostname:
            body["searchdomain"] = "axrange.local"
            body["nameserver"] = "1.1.1.1"

        if pubkey_text:
            body["sshkeys"] = urllib.parse.quote(pubkey_text + "\n", safe="")

        self.client.request(
            "PUT", f"/nodes/{target_node}/qemu/{vmid}/config", body=body
        )
        return {"cloud_init_attached": True}

    def _configure_windows_first_boot(
        self, vmid: int, target_node: str, hostname: str, ip_config: dict[str, Any],
        *, password: str | None = None,
    ) -> dict[str, Any]:
        """W1.3: a Windows clone's first-boot specialization. Renders a
        `<powershell>` cloudbase-init user-data snippet (Administrator password,
        ComputerName, QGA, os-ready marker), scp's it, and attaches it via
        cicustom + ciuser=Administrator. Same NIC ipconfig as the Linux path
        (PVE sets the NIC IPs regardless of guest OS). Live-verify pending (W1.5):
        cloudbase-init's datasource/format against template 102 is the
        "resolve by building" piece.

        `password` (docs/WORK_ORDER_access_facts.md Part A) is the caller's
        already-resolved effective password (explicit input, if any, or
        `cfg.default_password`) -- falls back to `cfg.default_password`
        directly if the caller omits it (no per-binding override channel
        exists for the Windows local Administrator account today)."""
        from axrange.adapter_proxmox.ssh import run_ssh, scp_to_remote

        pw = password or self.cfg.default_password
        userdata = _render_windows_userdata(
            hostname=hostname, admin_password=pw
        )
        snippet_filename = f"axr-userdata-{vmid}.yaml"
        snippet_path = f"/var/lib/vz/snippets/{snippet_filename}"
        run_ssh(self.cfg, "mkdir -p /var/lib/vz/snippets", timeout_s=10)
        scp_to_remote(self.cfg, userdata, snippet_path, timeout_s=20)

        body: dict[str, Any] = {
            "ciuser": "Administrator",
            "cipassword": pw,
            "cicustom": f"user=local:snippets/{snippet_filename}",
        }
        body.update(_build_ipconfig_body(ip_config or {}))

        self.client.request(
            "PUT", f"/nodes/{target_node}/qemu/{vmid}/config", body=body
        )
        return {"cloud_init_attached": True}

    def op_attach_vm_to_vnet(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        vmid = int(inp["vmid"])
        target_node = ctx.cluster.default_node
        nic_index = int(inp.get("nic_index", 0))
        vnet_id = inp.get("vnet_id")
        if not vnet_id:
            raise AdapterError(
                "attach-vm-to-vnet: vnet_id missing",
                error_class="adapter-input",
            )
        # vnet_id is the SDN vnet's bridge (PVE materializes a bridge named
        # after the vnet). Under SDN-native the vnet carries the VLAN (VLAN
        # zone), so the NIC takes NO tag — pass1 sends vlan=None. The guard is
        # kept defensively: a stray tag here would double-tag over the vnet.
        #
        # A deterministic macaddr= is REQUIRED here: without one, PVE mints a
        # fresh random MAC on every (re)attach, so a Model C re-run/redeploy
        # churns the NIC's MAC — breaking VyOS WAN-IP discovery by MAC
        # (docs/SLICE_ROUTER.md `discover_guest_ip_by_mac`) and DHCP lease
        # stability (code-review finding #8, 2026-07-07).
        host_id = op.scope.get("host") or ""
        mac = mac_for_nic(ctx.plan_id, host_id, nic_index)
        nic_spec = f"virtio={mac},bridge={vnet_id}"
        vlan = inp.get("vlan")
        if vlan is not None:
            nic_spec += f",tag={int(vlan)}"
        body = {f"net{nic_index}": nic_spec}
        self.client.request(
            "PUT", f"/nodes/{target_node}/qemu/{vmid}/config", body=body
        )
        return {"nic_attached": True}

    def op_start_vm(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        vmid = int(inp["vmid"])
        target_node = ctx.cluster.default_node
        # Check-then-act.
        status = self._vm_status(target_node, vmid)
        if status and status.get("status") == "running":
            return {"started": True}
        upid = self.client.request(
            "POST", f"/nodes/{target_node}/qemu/{vmid}/status/start"
        )
        if isinstance(upid, str):
            self.client.wait_task(target_node, upid, timeout_s=op.timeout_s or 120)
        return {"started": True}

    def op_wait_guest_agent(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        # QGA ping (POST, not GET — GET returns HTTP 501; surfaced 2026-05-29).
        # Shared with config-class execution via the GuestAgent helper.
        from axrange.adapter_proxmox.guest_agent import GuestAgent

        vmid = int(inp["vmid"])
        ga = GuestAgent(self.client, ctx.cluster.default_node)
        ga.wait_ready(vmid, timeout_s=op.timeout_s or 180)
        return {"agent_ready": True}

    def op_wait_os_ready(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        """Poll guest readiness via the qemu-guest-agent exec channel.

        ADAPTER.md §3 originally classed this as an orchestrator-side SSH
        probe, but the orchestrator can't reach the scenario's isolated SDN
        networks; the agent-exec path proxies a command into the guest and
        reports the exit code with no scenario-side reachability (the §7
        empirical refinement, CHANGELOG 2026-05-29). Probe: `cloud-init status
        --wait` — returns 0 once cloud-init has completed (IP set, SSH up,
        etc.). exit 2 == cloud-init "recoverable error" but the status command
        itself ran; treated as ready to avoid wedging on benign warnings.

        Uses the same GuestAgent helper config-class execution uses
        (`axrange.adapter_proxmox.guest_agent`). Transient agent drops are
        covered by the executor's per-op retry, not an inner loop.
        """
        from axrange.adapter_proxmox.guest_agent import GuestAgent

        vmid = int(inp["vmid"])
        budget = int(inp.get("first_boot_budget") or op.timeout_s or 600)
        os_family = (inp.get("os_family") or "linux").lower()
        ga = GuestAgent(
            self.client, ctx.cluster.default_node, os_family=os_family
        )

        ga.wait_ready(vmid, timeout_s=budget)

        if os_family == "windows":
            # Windows has no `cloud-init status --wait`. The strong readiness signal
            # is the os-ready MARKER, dropped once the clone is OUT of OOBE and
            # specialized (W0 clone unattend FirstLogonCommands / W1.3 cloudbase-init).
            # QGA answers even at the OOBE "Hi there" screen, so a bare QGA/CIM probe
            # is too weak — it let an unspecialized box through and broke W2a's DC
            # promotion (live-verify dev45 2026-06-17). Poll the marker within budget.
            deadline = time.time() + budget
            last = "marker absent"
            while time.time() < deadline:
                res = ga.shell_exec(
                    vmid,
                    f"if (Test-Path '{WINDOWS_OSREADY_MARKER}') {{ exit 0 }} else {{ exit 1 }}",
                    timeout_s=60,
                )
                if res.ok:
                    # Rename to the IR node id first (reboots if changed), then
                    # pin the static IP — the rename-reboot would otherwise
                    # re-DHCP the NIC and undo the IP. Both are QGA-applied
                    # because the template ships no cloudbase-init (dev45 §5).
                    self._apply_windows_hostname(ga, vmid, inp)
                    self._apply_windows_static_ip(ga, vmid, inp)
                    return {"os_ready": True}
                last = f"marker {WINDOWS_OSREADY_MARKER} absent (guest not specialized)"
                time.sleep(10)
            raise AdapterError(
                f"wait-os-ready: vmid={vmid} (windows) {last} after {budget}s",
                error_class="windows-os-not-ready",
            )

        res = ga.exec(vmid, ["cloud-init", "status", "--wait"], timeout_s=budget)
        if res.exit_code in (0, 2):
            return {"os_ready": True}
        raise AdapterError(
            f"wait-os-ready: vmid={vmid} cloud-init exited with code "
            f"{res.exit_code}; out-data={(res.stdout or res.stderr)[:200]}",
            error_class="cloud-init-failed",
        )

    def _windows_boot_id(self, ga, vmid: int) -> str:
        """Guest LastBootUpTime ticks — a monotonic reboot fingerprint. Empty
        when the guest isn't answering (mid-reboot). Mirrors the config backend."""
        try:
            res = ga.shell_exec(
                vmid,
                "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.Ticks",
                timeout_s=60,
            )
            return res.stdout.strip() if res.ok else ""
        except (AdapterError, AdapterTimeout):
            return ""

    def _wait_windows_reboot(
        self, ga, vmid: int, prev_boot_id: str, *, timeout_s: int = 600
    ) -> None:
        """Block until the guest reboots and QGA answers again (boot id advances,
        or a down->up transition with no baseline). Mirrors the config backend."""
        deadline = time.time() + timeout_s
        saw_down = False
        while time.time() < deadline:
            cur = self._windows_boot_id(ga, vmid)
            if not cur:
                saw_down = True
            elif prev_boot_id and cur != prev_boot_id:
                return
            elif not prev_boot_id and saw_down:
                return
            time.sleep(10)
        raise AdapterTimeout(
            f"wait-os-ready: vmid={vmid} did not reboot back in {timeout_s}s"
        )

    def _apply_windows_hostname(
        self, ga, vmid: int, inp: dict[str, Any]
    ) -> None:
        """Rename a specialized Windows clone to its IR node id over QGA, before
        the static-IP apply + any domain join (docs/SLICE_WINDOWS_AD.md §5). The
        first-boot path doesn't apply the node id as the ComputerName (no
        cloudbase-init), so without this a clone keeps its OOBE `WIN-xxxx` name
        and an AD join lands under that random name rather than the friendly node
        id (dev45 2026-06-18). A rename needs a reboot; we drive it and wait for
        the guest to return. No-op when the planner didn't thread a hostname or
        the name already matches (idempotent)."""
        hostname = inp.get("hostname")
        if not hostname:
            return
        from axrange.config_exec.render import (
            ConfigRenderError,
            render_windows_set_hostname,
        )

        try:
            script = render_windows_set_hostname(str(hostname))
        except ConfigRenderError:
            return  # nothing renamable -> leave the OOBE name
        prev_boot = self._windows_boot_id(ga, vmid)
        res = ga.shell_exec(vmid, script, timeout_s=120)
        if not res.ok:
            raise AdapterError(
                f"wait-os-ready: vmid={vmid} hostname rename exited "
                f"{res.exit_code}: {(res.stdout or res.stderr)[:200]}",
                error_class="windows-hostname-failed",
            )
        if "AXR_RENAMED yes" not in (res.stdout or ""):
            return  # already named correctly -> no reboot needed
        try:
            ga.shell_exec(vmid, "Restart-Computer -Force", timeout_s=30)
        except (AdapterError, AdapterTimeout):
            pass  # the reboot races the QGA reply; the wait below is the gate
        self._wait_windows_reboot(ga, vmid, prev_boot, timeout_s=600)

    def _apply_windows_static_ip(
        self, ga, vmid: int, inp: dict[str, Any]
    ) -> None:
        """Pin a specialized Windows clone's NIC to its IR static IP over QGA
        (docs/SLICE_WINDOWS_AD.md §5). No-op when the planner didn't thread a
        static IP (DHCP nets, or a single-homed net with no ip). The template's
        first-boot path leaves the NIC on DHCP and ships no cloudbase-init to
        consume PVE's ipconfigN, so without this an isolated-net clone sits on
        APIPA and can't reach its peers — which blocks AD join (the DC must be
        reachable). Idempotent (render exits early if the address is present).
        Surfaced live on dev45 2026-06-18 (first two-Windows-host scenario)."""
        static_ip = inp.get("static_ip")
        if not static_ip:
            return
        from axrange.config_exec.render import (
            ConfigRenderError,
            render_windows_static_ip,
        )

        try:
            script = render_windows_static_ip(
                str(static_ip),
                prefix=int(inp.get("static_prefix") or 24),
                gateway=inp.get("static_gateway"),
            )
        except ConfigRenderError as e:
            raise AdapterError(
                f"wait-os-ready: vmid={vmid} static-ip render failed: {e}",
                error_class="windows-static-ip-failed",
            )
        res = ga.shell_exec(vmid, script, timeout_s=120)
        if not res.ok:
            raise AdapterError(
                f"wait-os-ready: vmid={vmid} static-ip apply exited "
                f"{res.exit_code}: {(res.stdout or res.stderr)[:200]}",
                error_class="windows-static-ip-failed",
            )

    def op_configure_router(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        """Configure a `kind: router` VM (router slice Phase 2, Debian; VyOS-
        swappable). The WAN NIC (uplink bridge) was attached at boot and DHCP-
        configured by cloud-init; here we enable IPv4 forwarding and masquerade
        LAN->WAN over the QGA channel, so the router's LAN segments get
        internet/apt egress + inter-segment routing.

        **Reboot-persistent (2026-06-24 fix).** The forwarding + NAT are installed
        as a systemd oneshot (`axr-router-nat.service`, After=network-online) that
        re-detects the WAN and re-applies on every boot — a base `debian-12-cloud`
        clone can reboot once on first boot, which previously wiped the runtime
        `ip_forward`/NAT and silently broke LAN egress (CHANGELOG 2026-06-24). We
        also no longer apt-install `nftables` at configure time (egress-fragile and
        a no-op without `nft`): the helper prefers `nft` if present, else falls back
        to `iptables` (present on the base image). Both immediate-apply and the
        boot service are idempotent. Default forward policy is permissive — per-rule
        firewall/segmentation policy is a follow-on (IR extension).
        """
        from axrange.adapter_proxmox.guest_agent import GuestAgent

        vmid = int(inp["vmid"])

        # VyOS (agent:false, no QGA): apply interfaces + source-NAT via SSH `set`
        # commands (vyos-ssh-set) instead of the QGA/nftables path below.
        if inp.get("router_os") == "vyos":
            return self._configure_vyos_router_ssh(op, inp, ctx)

        script = ROUTER_NAT_SCRIPT
        ga = GuestAgent(self.client, ctx.cluster.default_node)
        ga.wait_ready(vmid, timeout_s=op.timeout_s or 300)
        res = ga.exec(vmid, ["/bin/bash", "-c", script], timeout_s=op.timeout_s or 300)
        out = (res.stdout or "") + (res.stderr or "")
        if res.exit_code != 0 or "AXR_ROUTER_OK" not in out:
            raise AdapterError(
                f"configure-router: vmid={vmid} setup failed (exit "
                f"{res.exit_code}): {out[:300]}",
                error_class="router-config-failed",
            )
        result: dict[str, Any] = {"configured": True}
        phase = self._apply_egress_policy(vmid, inp.get("egress"), ga, op)
        if phase is not None:
            result["egress_phase"] = phase
        return result

    def _apply_egress_policy(
        self,
        vmid: int,
        egress: Any,
        ga: Any,
        op: OpInstance,
    ) -> str | None:
        """Install + apply the router's egress phase policy (E1,
        `docs/WORK_ORDER_protocol_engines.md`; renderer in `egress_render.py`).

        `egress` absent → returns None and touches NOTHING: a router with no
        declared policy keeps exactly today's permissive masquerade, so every
        shipped twin config is unaffected. When it IS declared, `provision` means
        real apt egress and `measure` means the twin's sink is the only thing off
        the twin — and an omitted phase inside a declared policy is `measure`
        (default CLOSED, `egress_render.normalize_phase`).

        Delivered as ONE self-contained installer written with `GuestAgent.
        write_file` (base64-in-command — LES on the PVE-9.2 `input-data` wide-char
        regression) and then executed, rather than pasted into an `exec` arg:
        the script is ~17 KB of shell.
        """
        from axrange.adapter_proxmox.egress_render import (
            EGRESS_INSTALLER,
            EGRESS_OK_MARKER,
            EgressRenderError,
            normalize_egress,
            render_install,
        )

        try:
            ctx = normalize_egress(egress)
        except EgressRenderError as exc:
            raise AdapterError(
                f"configure-router: vmid={vmid} egress policy is malformed: {exc}",
                error_class="egress-policy-invalid",
            ) from exc
        if ctx is None:
            return None

        installer = render_install(ctx)
        ga.write_file(vmid, EGRESS_INSTALLER, installer, mode="0755")
        res = ga.exec(
            vmid, ["/bin/sh", EGRESS_INSTALLER], timeout_s=op.timeout_s or 300
        )
        out = (res.stdout or "") + (res.stderr or "")
        expected = f"{EGRESS_OK_MARKER} phase={ctx['phase']}"
        if res.exit_code != 0 or expected not in out:
            # Fail loud: the in-guest engine has already forced itself closed on
            # any failed transition, so the twin is contained — but the deploy
            # must not report a phase it did not actually reach.
            raise AdapterError(
                f"configure-router: vmid={vmid} egress phase "
                f"{ctx['phase']!r} did not apply (exit {res.exit_code}): "
                f"{out[-300:]}",
                error_class="egress-phase-failed",
            )
        return str(ctx["phase"])

    def _configure_vyos_router_ssh(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        """Configure a VyOS router over SSH (vyos-ssh-set; router slice Phase 2).

        VyOS has no QGA, and the generic ISO ships no cloud-init, so config is
        delivered the way axDigitalTwin does it: a flat list of `set` commands
        executed through VyOS's vbash `script-template`. The template self-boots
        eth0=DHCP on the uplink (vmbr0) + sshd + the operator key, so the router
        is SSH-reachable on a DHCP address shortly after start-vm. We:

          1. read the WAN NIC (net0) MAC from the VM config,
          2. resolve its DHCP IP via arp-scan on the PVE host (no QGA to ask),
          3. SSH in as the binding user and apply LAN interface addresses,
             source-NAT masquerade out eth0, name-servers, and static routes.

        netN->ethN is deterministic on the template (hw-id stripped), so the WAN
        is eth0 and LAN net i is eth(i+1); the planner passes the eth name per
        LAN iface. Idempotent: VyOS `commit` is a no-op on an unchanged config
        (it exits non-zero with "No configuration changes" — treated as success);
        errors are detected by stderr markers, not exit code. Step 3's envelope +
        error-detection order is shared with `configure-routing`
        (`ssh.apply_vyos_sets`, finding #4 dedup, 2026-07-07).
        """
        from axrange.adapter_proxmox.ssh import (
            apply_vyos_sets,
            discover_guest_ip_by_mac,
            run_ssh_guest,
        )

        vmid = int(inp["vmid"])

        # E1 egress phase policy is NOT implemented for VyOS. Refuse loudly rather
        # than apply the interfaces/NAT half and silently skip the containment half
        # (LES-001): VyOS 1.3 and 1.4 disagree about firewall syntax (`firewall
        # name` vs `firewall ipv4 forward filter`), so a guessed rule set would
        # `commit` clean on one release and contain nothing on the other — a twin
        # that believes it is closed while it talks to the real internet.
        if inp.get("egress") is not None:
            raise AdapterError(
                f"configure-router(vyos): vmid={vmid} egress phase policy is not "
                "implemented for VyOS (docs/WORK_ORDER_protocol_engines.md §E1 — "
                "the Debian/nftables branch is the shipped shape). Use a Debian "
                "router on a twin that measures, or implement the vbash branch.",
                error_class="egress-phase-unsupported-os",
            )

        target_node = ctx.cluster.default_node
        ssh_user = inp.get("ci_user") or "vyos"
        wan_eth = inp.get("wan_eth") or "eth0"
        wan_bridge = inp.get("wan_bridge") or "vmbr0"
        lan_ifaces = inp.get("lan_ifaces") or []
        nameservers = inp.get("nameservers") or []
        static_routes = inp.get("static_routes") or []

        # 1. WAN NIC MAC from the VM config (net0 for VyOS routers).
        cfg_data = self.client.request(
            "GET", f"/nodes/{target_node}/qemu/{vmid}/config"
        )
        net0 = (cfg_data or {}).get("net0", "") if isinstance(cfg_data, dict) else ""
        mac = ""
        for field in str(net0).split(","):
            if "=" in field:
                k, v = field.split("=", 1)
                if k.strip().lower() in ("virtio", "e1000", "vmxnet3", "rtl8139"):
                    mac = v.strip()
                    break
        if not mac:
            raise AdapterError(
                f"configure-router(vyos): vmid={vmid} no WAN MAC in net0={net0!r}",
                error_class="router-config-failed",
            )

        # 2. Resolve the WAN DHCP IP via arp-scan on the host (polls for boot).
        wan_ip = discover_guest_ip_by_mac(self.cfg, mac, bridge=wan_bridge)

        # 3. Build the flat `set` script and apply via vbash script-template.
        sets: list[str] = []
        for lif in lan_ifaces:
            eth = lif.get("eth")
            addr = lif.get("address")
            if eth and addr:
                sets.append(f"set interfaces ethernet {eth} address '{addr}'")
        sets.append(f"set nat source rule 100 outbound-interface name '{wan_eth}'")
        sets.append("set nat source rule 100 translation address 'masquerade'")
        for ns in nameservers:
            sets.append(f"set system name-server '{ns}'")
        for r in static_routes:
            to, via = r.get("to"), r.get("via")
            if to and via:
                sets.append(f"set protocols static route {to} next-hop {via}")

        # Shared envelope + LES-005 error-detection order with configure-routing
        # (finding #4, 2026-07-07) — see `ssh.apply_vyos_sets` docstring.
        apply_vyos_sets(
            self.cfg, wan_ip, ssh_user, sets,
            op_label=f"configure-router(vyos): vmid={vmid}",
            timeout_s=op.timeout_s or 180,
        )
        # Discover the WAN default gateway — the fabric tenant-VRF egress leak
        # (configure-routing) routes 0.0.0.0/0 to it with next-hop-vrf default.
        wan_gw = ""
        try:
            _rc, gw_out = run_ssh_guest(
                self.cfg, wan_ip, ssh_user, "ip -4 route show default", timeout_s=20
            )
            toks = gw_out.split()
            if "via" in toks:
                wan_gw = toks[toks.index("via") + 1]
        except Exception:  # noqa: BLE001 — egress leak is best-effort
            wan_gw = ""
        return {
            "configured": True,
            "router_os": "vyos",
            "wan_ip": wan_ip,
            "wan_gw": wan_gw,
            "wan_iface": wan_eth,
            "lan_ifaces": [lif.get("eth") for lif in lan_ifaces],
        }

    # ---- Helpers -----------------------------------------------------------
    # Thin wrappers over the module-level existence-checks above (finding #5)
    # — kept as methods so every `self._foo_exists(...)` call site above reads
    # unchanged; `self.client` is threaded through.

    def _zone_exists(self, zone_id: str) -> bool:
        return zone_exists(self.client, zone_id)

    def _bridge_exists(self, node: str, bridge_name: str) -> bool:
        return bridge_exists(self.client, node, bridge_name)

    def _vnet_exists(self, vnet_id: str) -> bool:
        return vnet_exists(self.client, vnet_id)

    def _subnet_exists(self, vnet_id: str, cidr: str) -> bool:
        return subnet_exists(self.client, vnet_id, cidr)

    def _vm_status(self, node: str, vmid: int) -> dict[str, Any] | None:
        return vm_status(self.client, node, vmid)


# The Debian router first-boot NAT + forwarding installer `configure-router`
# runs over the guest channel -- a PURE string, guest-side only, so the
# libvirt adapter (`axrange.adapter_libvirt`) runs the identical script
# (`adapters/libvirt/SPEC.md`: configure-router "unchanged (guest side)").
ROUTER_NAT_SCRIPT = (
    "set -e\n"
    "wan=$(ip route show default 2>/dev/null | awk '/default/{print $5; exit}')\n"
    '[ -n "$wan" ] || { echo AXR_ROUTER_FAIL no-WAN-default-route; exit 1; }\n'
    # Reboot-persistent helper: re-detects the WAN at boot, enables
    # forwarding, and installs the masquerade (nft if present, else
    # iptables — both work on the base image; no apt needed). Idempotent.
    "cat > /usr/local/sbin/axr-router-nat.sh <<'EOS'\n"
    "#!/bin/sh\n"
    "sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1 || true\n"
    "W=$(ip route show default 2>/dev/null | awk '/default/{print $5; exit}')\n"
    '[ -n "$W" ] || exit 0\n'
    "if command -v nft >/dev/null 2>&1; then\n"
    "  nft add table ip axrnat 2>/dev/null || true\n"
    "  nft 'add chain ip axrnat postrouting { type nat hook postrouting priority 100 ; }' 2>/dev/null || true\n"
    '  nft list chain ip axrnat postrouting 2>/dev/null | grep -q masquerade || nft add rule ip axrnat postrouting oifname "$W" masquerade\n'
    "else\n"
    '  iptables -t nat -C POSTROUTING -o "$W" -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -o "$W" -j MASQUERADE\n'
    "fi\n"
    "EOS\n"
    "chmod +x /usr/local/sbin/axr-router-nat.sh\n"
    "echo net.ipv4.ip_forward=1 > /etc/sysctl.d/99-axr-router.conf\n"
    # systemd oneshot so forwarding + NAT survive a router reboot.
    "cat > /etc/systemd/system/axr-router-nat.service <<'EOS'\n"
    "[Unit]\n"
    "Description=axRange router NAT + IPv4 forwarding\n"
    "After=network-online.target\n"
    "Wants=network-online.target\n"
    "[Service]\n"
    "Type=oneshot\n"
    "ExecStart=/usr/local/sbin/axr-router-nat.sh\n"
    "RemainAfterExit=yes\n"
    "[Install]\n"
    "WantedBy=multi-user.target\n"
    "EOS\n"
    "systemctl daemon-reload >/dev/null 2>&1 || true\n"
    "systemctl enable axr-router-nat.service >/dev/null 2>&1 || true\n"
    # Apply now — don't wait for a reboot.
    "/usr/local/sbin/axr-router-nat.sh\n"
    'echo AXR_ROUTER_OK wan=$wan forward=$(cat /proc/sys/net/ipv4/ip_forward)\n'
)


# The Windows local Administrator password now comes from
# `cfg.default_password` (docs/WORK_ORDER_access_facts.md Part A) --
# threaded through `op_configure_cloud_init`/`_configure_windows_first_boot`
# above; there is no module-level constant any more.

# The W1 os-ready marker: dropped once a Windows clone is OUT of OOBE and
# specialized (by the W0 clone-side sysprep unattend's FirstLogonCommands, and by
# the W1.3 cloudbase-init snippet). wait-os-ready (W1.2) polls it — a *specialized*
# signal, not just "QGA answers" (true even at the OOBE screen — the weak signal
# that masked the W0 OOBE gap; live-verify dev45 2026-06-17). Must match
# _OSREADY_MARKER in imagebuild/bake_windows.py.
WINDOWS_OSREADY_MARKER = r"C:\Windows\Temp\axr-osready"


def _netbios(hostname: str) -> str:
    """A valid NetBIOS computer name (<=15 chars, alnum + hyphen) from a host id."""
    import re as _re

    s = _re.sub(r"[^A-Za-z0-9-]", "-", hostname).strip("-")[:15].strip("-")
    return s or "axr-host"


def _render_windows_userdata(*, hostname: str, admin_password: str) -> str:
    """Windows clone first-boot user-data for cloudbase-init (W1.3,
    docs/SLICE_WINDOWS_AD.md §5). cloudbase-init (baked into the template by W0)
    runs a `<powershell>` user-data block on first boot; this sets the
    Administrator password, the ComputerName (NetBIOS <=15), ensures the QEMU
    guest agent runs, and drops an os-ready marker. The exact cloudbase-init
    datasource/format is a live-verify ("resolve by building") item — W1.5.
    """
    cn = _netbios(hostname)
    pw = admin_password.replace("'", "''")  # PowerShell single-quote escape
    return (
        "<powershell>\n"
        "$ErrorActionPreference='SilentlyContinue'\n"
        f"net user Administrator '{pw}'\n"
        f"Rename-Computer -NewName '{cn}' -Force\n"
        "Start-Service QEMU-GA\n"
        "Set-Service QEMU-GA -StartupType Automatic\n"
        f"New-Item -Path '{WINDOWS_OSREADY_MARKER}' -ItemType File -Force | Out-Null\n"
        "</powershell>\n"
    )


def _render_userdata(*, hostname: str, user: str, ssh_pubkey: str, password: str) -> str:
    """Produce a #cloud-config YAML body the slice writes to a PVE snippet.

    Includes:
      - hostname for the guest
      - the binding-declared user (e.g. `debian`) with NOPASSWD sudo
      - the operator's SSH pubkey (skipped if empty) AND a password (console +
        SSH-by-password login, docs/WORK_ORDER_access_facts.md Part A) --
        `lock_passwd: false` + `ssh_pwauth: true` are what make password
        login possible at all; without them cloud-init locks the account and
        sshd never gets PasswordAuthentication turned on for it
      - runcmd to enable + start qemu-guest-agent (the load-bearing reason
        this snippet exists — see op_configure_cloud_init docstring)

    The password is delivered via the top-level `chpasswd` module (not PVE's
    own `cipassword` VM-config field): `cicustom` REPLACES PVE's generated
    user-data wholesale (see the docstring above on `op_configure_cloud_init`),
    so a `cipassword` set on the VM config would be silently ignored here --
    exactly like `ciuser` is (kept in the PUT body anyway, for the PVE UI's
    own display, but the actual user is the one this snippet creates).
    """
    # `chpasswd`'s `list:` is a literal block scalar (`|`) -- the `user:pass`
    # line is NOT re-parsed as YAML, so a password containing `:`/`#`/`!` is
    # safe verbatim (no YAML quoting needed) as long as it has no embedded
    # newline (cloud-init cloud-config passwords are single-line by
    # construction; the deploy-wide default and any binding override both
    # satisfy that).
    lines: list[str] = [
        "#cloud-config",
        f"hostname: {hostname}",
        "manage_etc_hosts: true",
        "users:",
        f"  - name: {user}",
        "    sudo: ALL=(ALL) NOPASSWD:ALL",
        "    shell: /bin/bash",
        "    groups: [sudo]",
        "    lock_passwd: false",
    ]
    if ssh_pubkey:
        lines += [
            "    ssh_authorized_keys:",
            f"      - {ssh_pubkey}",
        ]
    lines += [
        "chpasswd:",
        "  list: |",
        f"    {user}:{password}",
        "  expire: false",
        "ssh_pwauth: true",
    ]
    # `systemctl start` (not `enable --now`): on Debian/Ubuntu cloud images
    # qemu-guest-agent ships as a static unit (no [Install] section), so
    # `enable` is a no-op and the package post-install does not auto-start
    # it. Just `start` works reliably and survives the cloud-init final
    # phase. Persistence across guest reboots is irrelevant for the slice
    # (deploy lifecycle = boot to ready; no reboot loop modeled).
    lines += [
        "runcmd:",
        "  - [ systemctl, start, qemu-guest-agent ]",
    ]
    return "\n".join(lines) + "\n"


def _build_ipconfig_body(ip_config: dict[str, Any]) -> dict[str, str]:
    """Build the `ipconfigN` PVE config-body keys from a resolved `ip_config`
    map (`net_id -> {ip, cidr, gateway} | "dhcp"`), one per attached NIC in
    emission/NIC order.

    Shared by the Linux (`op_configure_cloud_init`) and Windows
    (`_configure_windows_first_boot`) first-boot paths — PVE sets the NIC IP
    via `ipconfigN` regardless of guest OS, so both branches built the
    identical loop independently (code-review finding #3 dedup, 2026-07-07).

    **Indexed by LEG, not by addressed leg** (2026-09-26, docs/
    WORK_ORDER_corp_twin.md §10 P0): `N` is the entry's POSITION in
    `ip_config`, which the planner keeps aligned with `netN` -- an
    address-less leg (an L2 bridge port) ahead of an addressed one arrives as
    a `{"ip": None}` placeholder, which emits no key here but still consumes
    its index. Counting only the addressed entries would put a later leg's
    address on the bridge port's NIC.
    """
    body: dict[str, str] = {}
    for i, (_net, net_cfg) in enumerate(ip_config.items()):
        ip = net_cfg.get("ip") if isinstance(net_cfg, dict) else net_cfg
        if not ip:
            # A placeholder: this NIC has no address, but it IS net{i}.
            continue
        if ip == "dhcp":
            body[f"ipconfig{i}"] = "ip=dhcp"
            continue
        prefix = "24"
        cidr = net_cfg.get("cidr") if isinstance(net_cfg, dict) else None
        if isinstance(cidr, str) and "/" in cidr:
            prefix = cidr.split("/", 1)[1]
        gw = net_cfg.get("gateway") if isinstance(net_cfg, dict) else None
        ipconfig = f"ip={ip}/{prefix}"
        if gw:
            ipconfig += f",gw={gw}"
        body[f"ipconfig{i}"] = ipconfig
    return body


_PVE_DISK_SIZE_RE = re.compile(r"^(\d+(?:\.\d+)?)([KMGT]i?)?$")


def _normalize_pve_disk_size(raw: str) -> str:
    """Convert an IR disk-size string (binary units, the same `resources.ram`
    convention: `10Gi`, `512Mi`, `2Ti`) to the decimal-suffix form PVE's
    `/resize` `size` param requires. PVE's regex is `\\+?\\d+(\\.\\d+)?[KMGT]?`
    — it does NOT accept a trailing `i`, so `10Gi` sent as-is 400s
    (`value does not match the regex pattern`, confirmed live on dev45).

    Unlike `_parse_mem_mb` (which collapses everything to an integer MiB
    count), this keeps PVE's own K/M/G/T suffix rather than converting units
    — resize accepts any of them directly, so there's no need to pick one.

    Accepts: `10Gi`->`10G`, `512Mi`->`512M`, `2Ti`->`2T`, `1024Ki`->`1024K`
    (binary units, `i` stripped); `10G`, `10240` (already PVE-valid) pass
    through unchanged; an optional leading `+` (PVE relative grow) is
    preserved. Raises `AdapterError(error_class="adapter-input")` on
    anything that can't be coerced — better a loud plan-time-adjacent
    failure than silently sending PVE a value its regex will reject anyway.
    """
    s = raw.strip()
    sign = ""
    if s.startswith("+"):
        sign = "+"
        s = s[1:]
    m = _PVE_DISK_SIZE_RE.match(s)
    if not m:
        raise AdapterError(
            f"disk size {raw!r} is not a valid PVE resize size "
            "(expected e.g. '10Gi', '10G', or a bare integer, "
            "optionally prefixed with '+')",
            error_class="adapter-input",
        )
    number, suffix = m.group(1), (m.group(2) or "")
    if suffix.endswith("i"):
        suffix = suffix[0]
    return f"{sign}{number}{suffix}"


def _parse_mem_mb(spec: str) -> int:
    """Convert k8s-style memory strings to MiB.

    Accepts: 4Gi, 4G, 512Mi, 512M, 1024 (bytes-MB ambiguous; assume MiB).
    Out: integer megabytes as Proxmox expects.
    """
    s = spec.strip()
    if s.endswith("Gi"):
        return int(float(s[:-2]) * 1024)
    if s.endswith("G"):
        return int(float(s[:-1]) * 1024)
    if s.endswith("Mi"):
        return int(float(s[:-2]))
    if s.endswith("M"):
        return int(float(s[:-1]))
    return int(float(s))


# Op-name -> RestBackend method. Defined after methods are defined.
_OP_TO_METHOD: dict[str, Any] = {
    "create-sdn-zone": RestBackend.op_create_sdn_zone,
    "create-subnet": RestBackend.op_create_subnet,
    "create-ovs-bridge": RestBackend.op_create_ovs_bridge,
    "create-vnet": RestBackend.op_create_vnet,
    "apply-sdn-config": RestBackend.op_apply_sdn_config,
    "clone-vm": RestBackend.op_clone_vm,
    "configure-vm-hardware": RestBackend.op_configure_vm_hardware,
    "configure-cloud-init": RestBackend.op_configure_cloud_init,
    "attach-vm-to-vnet": RestBackend.op_attach_vm_to_vnet,
    "start-vm": RestBackend.op_start_vm,
    "wait-guest-agent": RestBackend.op_wait_guest_agent,
    "wait-os-ready": RestBackend.op_wait_os_ready,
    "configure-router": RestBackend.op_configure_router,
}
