"""libvirt resource naming -- the engine's tier-tagged shapes, plus the
platform's own identities (`adapters/libvirt/SPEC.md` Seam table, "resource
naming").

The tier-tagged NAME shapes (`docs/CONVENTIONS.md` §2: `te<6hex>` zone,
`tev<5hex>` vnet, `tebr_*` bridge, `te-<6hex>-<host>` VM, the `o2`/`tfc`/
`exr`/`img`/`tap` families, the predicate and shape tables) are the engine's,
not a platform's; they are implemented once in
`axrange.adapter_proxmox.naming` as pure string functions and RE-EXPORTED
here rather than duplicated, so both platforms produce byte-identical names
for the same plan and the teardown guard's tables cannot drift. (Moving that
module to a neutral home is the relocation decision noted in
`docs/ARCHITECTURE.md` §13.)

What IS libvirt's own here:

* `guest_handle_for_op` -- the Proxmox VMID becomes a deterministic domain
  UUID: UUID5 of `{plan_id}:{op_id}` under a fixed axRange namespace, as a
  string. Re-running the same plan derives the same UUID, so `clone-vm`'s
  check-then-act adopts its own domain (`lookupByUUIDString`) and never a
  hand-made one.
* `is_managed_guest_handle` -- the structural counterpart of the VMID range
  check: a string that parses as a VERSION-5 UUID. libvirt and every GUI
  mint random (version-4) UUIDs for hand-made domains, so this is the
  secondary guard; the tier-tagged NAME is the primary one, exactly as on
  Proxmox.
* the per-guest file and netdev names derived from the handle alone
  (overlay volume, seed ISO, mirror hook, tap names), so a teardown can
  find them without the domain's XML.
"""
from __future__ import annotations

import hashlib
import uuid

from axrange.adapter_proxmox.naming import (  # noqa: F401 -- re-exported shapes
    PROTECTED_EXTERNAL_NAMES,
    TIER_PREDICATES,
    TIER_SHAPE_CHECKS,
    bridge_name_for_net,
    check_tier,
    gre_key_for_bridge_sensor,
    gre_port_for_bridge_sensor,
    img_vm_name_for_image,
    is_exr,
    is_exr_vm_name,
    is_img,
    is_img_vm_name,
    is_exr_bridge,
    is_o2,
    is_o2_bridge,
    is_o2_vm_name,
    is_protected_external,
    is_tap,
    is_tap_gretap,
    is_te,
    is_te_bridge,
    is_te_vm_name,
    is_te_vnet,
    is_te_zone,
    is_tfc,
    is_tfc_bridge,
    is_tfc_vm_name,
    mac_for_nic,
    tier_predicates,
    tier_shape_checks,
    underlay_bridge_for_plan,
    vm_name_for_host,
    vnet_id_for_net,
    zone_id_for_plan,
)

__all__ = [
    "AXRANGE_GUEST_NAMESPACE",
    "guest_handle_for_op",
    "hook_script_path",
    "is_managed_guest_handle",
    "overlay_volume_name",
    "seed_iso_name",
    "tap_name_for_nic",
    # re-exports (the engine's shapes)
    "PROTECTED_EXTERNAL_NAMES", "TIER_PREDICATES", "TIER_SHAPE_CHECKS",
    "bridge_name_for_net", "check_tier", "gre_key_for_bridge_sensor",
    "gre_port_for_bridge_sensor", "img_vm_name_for_image", "is_exr",
    "is_exr_bridge", "is_exr_vm_name", "is_img", "is_img_vm_name", "is_o2", "is_o2_bridge", "is_o2_vm_name",
    "is_protected_external", "is_tap", "is_tap_gretap", "is_te", "is_te_bridge",
    "is_te_vm_name", "is_te_vnet", "is_te_zone", "is_tfc", "is_tfc_bridge", "is_tfc_vm_name",
    "mac_for_nic", "tier_predicates", "tier_shape_checks",
    "underlay_bridge_for_plan", "vm_name_for_host", "vnet_id_for_net",
    "zone_id_for_plan",
]

#: The fixed namespace every axRange-minted domain UUID hangs off. Itself
#: derived deterministically (UUID5 of a literal), so there is no magic
#: constant to copy wrong; it never changes once a plan has been cast.
AXRANGE_GUEST_NAMESPACE: uuid.UUID = uuid.uuid5(uuid.NAMESPACE_DNS, "guest.axrange")

#: Mirror hooks live in libvirt's per-driver hook directory, one script per
#: guest (libvirt >= 6.5 runs every script in `qemu.d/`).
HOOK_DIR = "/etc/libvirt/hooks/qemu.d"


def guest_handle_for_op(plan_id: str, op_id: str) -> str:
    """`ResourceNamer.guest_handle_for_op`: the deterministic domain UUID
    (string form) a `clone-vm` op materializes."""
    return str(uuid.uuid5(AXRANGE_GUEST_NAMESPACE, f"{plan_id}:{op_id}"))


def is_managed_guest_handle(guest: int | str) -> bool:
    """`ResourceNamer.is_managed_guest_handle`: a version-5 UUID string. An
    int (a Proxmox VMID) or a random version-4 UUID is never one of ours."""
    if not isinstance(guest, str):
        return False
    try:
        return uuid.UUID(guest).version == 5
    except (ValueError, AttributeError, TypeError):
        return False


def _short(guest: str, n: int = 8) -> str:
    return hashlib.sha256(str(guest).encode()).hexdigest()[:n]


def overlay_volume_name(guest: str) -> str:
    """The per-guest qcow2 overlay volume in the storage pool (backing file =
    the template volume). Keyed by the handle so teardown finds it blind."""
    return f"axr-{guest}-disk0.qcow2"


def seed_iso_name(guest: str) -> str:
    """The per-guest NoCloud seed ISO in the storage pool."""
    return f"axr-{guest}-seed.iso"


def hook_script_path(guest: str) -> str:
    """The per-guest `tc` mirror hook under libvirt's `qemu.d/`."""
    return f"{HOOK_DIR}/axrange-mirror-{guest}"


def tap_name_for_nic(guest: str, nic_index: int) -> str:
    """Deterministic tap netdev name for one guest NIC, set as the interface's
    `<target dev=...>` so the mirror hook addresses a known name instead of
    libvirt's run-to-run `vnetN`. <= 15 chars (IFNAMSIZ-1); must not start
    with `vnet`/`vif`/`macvtap` (libvirt reserves those prefixes)."""
    return f"axt{_short(guest)}i{int(nic_index)}"[:15]
