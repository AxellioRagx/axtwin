"""axrange.platform — the deploy-platform seam (`docs/ARCHITECTURE.md` §13).

`protocols` holds the five surfaces the engine reaches a platform through;
`factory` holds the closed set of platforms and `adapter_for`. The engine
imports from here; a platform's own package (`axrange.adapter_proxmox`
today, `adapters/<platform>/` once each is its own package) is imported
only by `factory`. The ops seam, `Adapter`, is defined in
`axrange.planner.executor` (this package stays import-light; see
`protocols.py`).
"""
from __future__ import annotations

from axrange.platform.factory import (
    DEFAULT_PLATFORM,
    PLATFORMS,
    PlatformAdapters,
    UnknownPlatformError,
    adapter_for,
    check_platform,
    default_namer,
    load_config,
    namer_for,
)
from axrange.platform.protocols import (
    ConfigError,
    ExternalSensorSpec,
    GuestExec,
    GuestHandle,
    GuestResult,
    Inventory,
    PlatformConfig,
    ResourceNamer,
    Teardown,
)

__all__ = [
    "DEFAULT_PLATFORM",
    "PLATFORMS",
    "ConfigError",
    "ExternalSensorSpec",
    "GuestExec",
    "GuestHandle",
    "GuestResult",
    "Inventory",
    "PlatformAdapters",
    "PlatformConfig",
    "ResourceNamer",
    "Teardown",
    "UnknownPlatformError",
    "adapter_for",
    "check_platform",
    "default_namer",
    "load_config",
    "namer_for",
]
