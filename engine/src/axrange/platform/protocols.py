"""The deploy-platform seam — the five protocols of `docs/ARCHITECTURE.md` §13.

The engine reaches a deploy platform through exactly these surfaces; a
platform plugin (`adapters/<platform>/SPEC.md`) implements all five and the
closed factory (`axrange.platform.factory.adapter_for`) hands the engine one
bundle of them keyed by the target's `platform`. Nothing under `axrange/`
outside the factory module imports a platform's own package
(`tests/test_platform_layering.py` enforces it).

| Seam | Protocol | What the engine asks |
|---|---|---|
| ops | `Adapter` (defined in `axrange.planner.executor`) | run one catalog op, return its typed outputs |
| guest exec | `GuestExec` | exec and file in, against a guest by its platform handle |
| inventory | `Inventory` | guest → address and host, host facts, the platform's live tier-tagged resources |
| naming | `ResourceNamer` | tier-tagged names and the platform's stable per-op identity |
| teardown | `Teardown` | reverse-of-create under `planner/teardown.py`'s protect guard |

Every method set below is derived from what the engine ACTUALLY CALLED on the
Proxmox modules when the seam was extracted (2026-10-30), call sites listed
per protocol — not from what a second platform might want. Vocabulary is the
engine's (`docs/CONVENTIONS.md` §4): a guest is addressed by an opaque
`GuestHandle`, placement is a `host`. Where a name below still carries a
platform's word it is because a version-locked contract does (the state
store's resource kinds, `vmid` DTO fields — `adapters/proxmox/SPEC.md`
Notes); each is a confirm-first bump and is marked `# contract:` here.

Call-site parameters are POSITIONAL-ONLY (`/`) in every protocol: the
implementations name them in their own vocabulary (`node`/`vmid` on
Proxmox, `host`/`guest` on libvirt) and `runtime_checkable` compares
attribute names only, so a keyword call would TypeError on one platform and
not the other. Keyword-only parameters (`timeout_s`, `host`, `tier`, ...)
are the one place names ARE the contract, and `tests/test_platform_seam.py`
compares every implementation's `inspect.signature` against these.
"""
from __future__ import annotations

from typing import (
    Any,
    Callable,
    Iterable,
    Mapping,
    Optional,
    Protocol,
    Union,
    runtime_checkable,
)

# Import-light on purpose: this module is imported by every platform's
# config loader (for `ConfigError`) and by `planner/teardown.py` (through
# the factory) while `axrange.planner` is itself mid-import, so it must not
# import the planner. The ops seam, `Adapter`, stays where it is defined
# (`axrange.planner.executor`, with `AdapterError`/`AdapterTimeout`) and is
# referenced here by name only.

__all__ = [
    "ConfigError",
    "ExternalSensorSpec",
    "GuestExec",
    "GuestHandle",
    "GuestResult",
    "Inventory",
    "PlatformConfig",
    "ResourceNamer",
    "Teardown",
]


#: The platform's opaque per-guest identity, minted by
#: `ResourceNamer.guest_handle_for_op` and recorded by the planner. The
#: engine stores it, compares it and hands it back to the platform; it never
#: interprets it. Proxmox: the VMID (an int); libvirt: the deterministic
#: domain UUID (`adapters/libvirt/SPEC.md`). A union rather than a wrapper
#: so today's recorded handles (the `vmid` op input / state column —
#: contract: `docs/contracts/PLANNER.md`, `api/dtos.py`) ride through
#: unchanged.
GuestHandle = Union[int, str]


class ConfigError(Exception):
    """A platform's adapter config failed to load or validate (an unknown or
    missing key, an unreadable file). Raised by every platform's loader so
    the CLI/API catch one type (`cli/main.py`'s dispatch backstop)."""


@runtime_checkable
class GuestResult(Protocol):
    """What one in-guest command returned."""

    @property
    def exit_code(self) -> int: ...

    @property
    def stdout(self) -> str: ...

    @property
    def stderr(self) -> str: ...

    @property
    def ok(self) -> bool: ...


@runtime_checkable
class GuestExec(Protocol):
    """A guest-control channel bound to one `host` and one guest OS family
    (`PlatformAdapters.guest_exec(host, os_family=...)`); each call names the
    guest by its handle. Exec and file-in only:

    * no file-out — no engine caller reads a file back (they `exec` a `cat`
      and read stdout);
    * no ping / wait-ready — readiness is the `wait-guest-agent` op's job
      inside each adapter (Proxmox `rest.py`, libvirt `adapter.py`), and the
      one engine-side liveness read is `Inventory.ping_guest`. Both adapters
      still expose `ping_once`/`wait_ready` on their channel objects for
      their own use; they are not part of the seam.

    Derived from the Proxmox `GuestAgent` calls at:
    `config_exec/backend.py` (every config-class op: `exec`, `shell_exec`,
    `write_file`), `o2control/traffic.py` (persona drivers), `o2control/
    exercise.py` (`_stop_exercise_processes`), `o2control/containment.py`
    (`NodeResolvingGuestAgent.exec`), `fidelity/runner.py` (the in-guest
    probe).
    """

    def exec(
        self, guest: GuestHandle, argv: list[str], /, *, timeout_s: int = 300,
    ) -> GuestResult:
        """Run `argv` in the guest; block until it exits or `timeout_s`."""

    def shell_exec(
        self, guest: GuestHandle, command: str, /, *, timeout_s: int = 300,
    ) -> GuestResult:
        """Run a shell command string in the guest's native shell (`sh -lc`
        on Linux, PowerShell on Windows)."""

    def write_file(
        self, guest: GuestHandle, path: str, content: str, /, *, mode: Optional[str] = None,
    ) -> None:
        """Write `content` to `path` in the guest (file-in). Content rides
        base64 inside the exec channel (`docs/CONVENTIONS.md` §4)."""


@runtime_checkable
class ExternalSensorSpec(Protocol):
    """A config-declared external sensor as the engine reads it
    (`o2control/taps.py:_external_to_inventory_entry`, `cli/main.py` and
    `api/service.py` info renders through `Inventory.external_sensor_facts`).
    `vmid` is the declared platform identity — contract: `adapters/proxmox/
    SPEC.md` Notes (external-sensor identity by VMID)."""

    @property
    def id(self) -> str: ...

    @property
    def vmid(self) -> int: ...

    @property
    def transport_ip(self) -> str: ...

    @property
    def type(self) -> str: ...

    @property
    def capture_iface(self) -> str: ...


@runtime_checkable
class PlatformConfig(Protocol):
    """The slice of a platform's adapter config the ENGINE reads. Everything
    else on the object (API URLs, SSH keys, storage names) is the adapter's
    own and stays behind the seam.

    `platform` is set by the loader (`axrange.platform.load_config`: the
    file's `PLATFORM=` key, `proxmox` when absent) and travels with the
    config, so `adapter_for_config(cfg)` picks the adapter without a second
    argument threaded through every call site. `endpoint`/`verify_tls`/
    `has_token`/`has_ssh_key` are the redacted facts `GET /api/targets`
    reports (`TargetDTO`); each adapter's config fills them from its own
    keys. `default_node` is the placement host when a plan does not choose
    (`host` in the engine's vocabulary; the attribute name is `adapter.conf`'s
    `DEFAULT_NODE` key — contract: `config/adapter.conf.example`).
    """

    @property
    def platform(self) -> str: ...

    @property
    def endpoint(self) -> str:
        """The platform API the adapter talks to (a Proxmox API URL, a
        libvirt URI); empty when the file names none."""

    @property
    def verify_tls(self) -> bool: ...

    @property
    def has_token(self) -> bool: ...

    @property
    def has_ssh_key(self) -> bool: ...

    @property
    def default_node(self) -> str: ...

    @property
    def default_password(self) -> str: ...

    @property
    def external_sensors(self) -> list[Any]: ...

    @property
    def protected_external_names(self) -> list[str]: ...

    @property
    def protected_external_vmids(self) -> list[int]: ...

    @property
    def loki_url(self) -> str:
        """`config_exec/backend.py`: the activity truth shipper's Loki, when
        the operator wired one; empty disables shipping."""

    @property
    def network_sensor_vmid(self) -> int:
        """`config_exec/backend.py` `deploy-network-sensor`: the appliance-
        adjacent network sensor by platform identity — contract:
        `adapters/proxmox/SPEC.md` Notes (sensor identity by VMID; libvirt
        would need a handle here, so its default `0` means "none")."""


@runtime_checkable
class Inventory(Protocol):
    """Live, read-only facts about one target: where a guest is and how to
    reach it, whether the platform answers at all, and the platform's live
    tier-tagged resources. The `guest_*` reads are best-effort and NEVER
    raise (an `info` render must still print what it already knows when the
    platform is down); the others RAISE `AdapterError` so a health read can
    tell "unreachable" from "nothing there".

    Derived from the Proxmox `resolve.py` calls at: `planner/access.py`
    (`resolve_vm_ip`), `o2control/containment.py` (`resolve_vm_node`),
    `api/service.py` (`cluster_version`, `cluster_vm_resources`, `qga_ping`,
    `external_sensor_facts`), `cli/main.py` (`external_sensor_facts`),
    `fidelity/runner.py` and `planner/access.py` (the "is the platform
    configured at all" short-circuit).
    """

    def is_configured(self) -> bool:
        """True when the adapter config names a platform endpoint to ask.
        Best-effort reads answer `None` without one; `platform_version`
        raises."""

    def guest_address(self, guest: GuestHandle, /, *, host: Optional[str] = None) -> Optional[str]:
        """The guest's live management IP, or `None` on any failure."""

    def guest_host(self, guest: GuestHandle, /) -> Optional[str]:
        """The host the guest currently lives on, or `None` (the caller picks
        its own fallback, typically `default_node`)."""

    def platform_version(self, *, timeout_s: int = 5) -> dict[str, Any]:
        """The cheapest authenticated read, as a reachability probe. Raises."""

    def live_guests(self, *, timeout_s: int = 5) -> list[dict[str, Any]]:
        """Every guest the platform reports, one row per guest as the platform
        shapes it (`name`, the handle, `node`, `status`). Raises. The row
        keys are the platform's — contract: `api/service.py`'s unrecorded-VM
        and health sweeps read `vmid`/`node`."""

    def ping_guest(
        self, guest: GuestHandle, /, *, host: Optional[str] = None, timeout_s: int = 5,
    ) -> None:
        """One short-timeout liveness ping for a health read. Raises."""

    def external_sensor_facts(self, *, only_ids: Optional[set[str]] = None) -> list[dict[str, Any]]:
        """Each declared external sensor's live name and address, best-effort
        per entry (`resolved_*` keys `None` on failure, declared keys always)."""


@runtime_checkable
class ResourceNamer(Protocol):
    """Tier-tagged resource names (`docs/CONVENTIONS.md` §2) and the
    platform's stable per-op guest identity. Pure functions of plan id, op
    id, tier and component — no config, no I/O — which is why a module can
    satisfy this protocol as well as a class.

    Derived from the Proxmox `naming.py` calls at: `planner/inventory.py`
    (`TIER_SHAPE_CHECKS`, `vm_name_for_host`), `planner/teardown.py`
    (`TIER_PREDICATES`, `TIER_SHAPE_CHECKS`, `is_img`, `is_protected_external`,
    `is_tap_gretap`, `is_te_vmid`, `underlay_bridge_for_plan`),
    `planner/state.py` (`check_tier`), `o2control/taps.py`
    (`gre_port_for_bridge_sensor`), `imagebuild/builder.py`
    (`img_vm_name_for_image`), `imagebuild/conformance.py`
    (`underlay_bridge_for_plan`, `vmid_for_op`, `vnet_id_for_net`,
    `zone_id_for_plan`), `api/service.py` (`is_*_vm_name`, `vmid_for_op`).
    """

    # ---- tier table -------------------------------------------------------
    def check_tier(self, tier: str, /) -> None:
        """Refuse an unrecognized deploy tier by name (fail closed)."""

    def tier_predicates(self) -> Mapping[str, Callable[[str], bool]]:
        """tier -> "is this name any of that tier's shapes" — every tier a
        name can be shape-matched against, for the foreign-tier refusal
        loop."""

    def tier_shape_checks(self) -> Mapping[str, tuple[Callable[[str], bool], ...]]:
        """deploy tier -> `(segment_ok, zone_ok, guest_name_ok)` positive
        gates for that tier's own resources."""

    # ---- names ------------------------------------------------------------
    def zone_id_for_plan(self, plan_id: str, /, *, tier: str = "te") -> str: ...

    def vnet_id_for_net(self, net_id: str, /, *, plan_id: str, tier: str = "te") -> str: ...

    def underlay_bridge_for_plan(self, plan_id: str, /, *, tier: str = "te") -> str: ...

    def vm_name_for_host(self, plan_id: str, host_id: str, /, *, tier: str = "te") -> str: ...

    def gre_port_for_bridge_sensor(self, bridge_name: str, sensor_id: str, /) -> str: ...

    def img_vm_name_for_image(self, image: str, /) -> str: ...

    # ---- the platform's per-op guest identity -----------------------------
    def guest_handle_for_op(self, plan_id: str, op_id: str, /) -> GuestHandle:
        """The deterministic guest identity a clone op materializes
        (Proxmox: `vmid_for_op`; libvirt: a UUID5 of `{plan_id, op_id}`)."""

    def is_managed_guest_handle(self, guest: GuestHandle, /) -> bool:
        """True when `guest` is a handle this engine mints (the teardown
        guard's range check; Proxmox: `is_te_vmid`)."""

    # ---- shape predicates the engine reads directly -----------------------
    def is_img(self, name: str, /) -> bool: ...

    def is_protected_external(self, name: str, extra: Iterable[str] = (), /) -> bool: ...

    def is_tap_gretap(self, name: str, /) -> bool: ...

    def is_te_vm_name(self, name: str, /) -> bool: ...

    def is_o2_vm_name(self, name: str, /) -> bool: ...

    def is_tfc_vm_name(self, name: str, /) -> bool: ...

    def is_exr_vm_name(self, name: str, /) -> bool: ...


@runtime_checkable
class Teardown(Protocol):
    """Reverse-of-create operations, one per resource kind the state store
    records; `planner/teardown.py` owns the ordering, the tier-shape guards
    and the state transition and calls these under its protect guard. Each
    is check-then-act: a missing resource is success (``"absent"``), a
    removed one is ``"removed"``, a hard failure raises.

    Method names follow the state store's resource kinds
    (`vm | mirror | gretap | vnet | zone | sdn-apply | ovs-bridge`) —
    contract: `docs/contracts/PLANNER.md` / `adapters/proxmox/SPEC.md` Notes
    ("neutral kinds"); they are renamed with that bump, not before.

    Derived from `planner/teardown.py`'s `TeardownBackend` calls
    (`destroy_deploy`, `destroy_ops_scoped`), which every tier's destroy
    (`cli/main.py`, `api/service.py`, `o2control/{control,taps,traffic,
    exercise}.py`) reaches through.
    """

    def vm_name(self, host: str, guest: GuestHandle, /) -> Optional[str]:
        """The guest's current platform display name, for the protect guard;
        `None` if absent."""

    def destroy_vm(self, host: str, guest: GuestHandle, /) -> str: ...

    def delete_mirror_snippets(self, host: str, guest: GuestHandle, /) -> str: ...

    def delete_gretap(self, dev: str, /) -> str: ...

    def delete_vnet(self, vnet_id: str, /) -> str: ...  # contract: kind `vnet`

    def delete_sdn_zone(self, zone_id: str, /) -> str: ...  # contract: kind `zone`

    def apply_sdn(self) -> str: ...  # contract: kind `sdn-apply`

    def delete_ovs_bridge(self, host: str, bridge_name: str, /) -> str: ...  # contract: kind `ovs-bridge`
