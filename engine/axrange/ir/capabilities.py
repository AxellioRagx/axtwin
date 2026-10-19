"""Closed capability + action keyword sets from IR_SCHEMA §4.1 and §7.

These are intentionally hardcoded; adding a keyword is a CHANGELOG-able change
to IR_SCHEMA, not a code-only edit. Unknown keywords are validator errors
(STR-110, STR-130).
"""
from __future__ import annotations

import re

# IR_SCHEMA §1.4: human-meaningful ID regex.
ID_REGEX = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")

# IR_SCHEMA §7.1 — OS family (3, bare, mutually exclusive).
OS_FAMILY_KEYWORDS: frozenset[str] = frozenset({"windows", "linux", "bsd"})

# IR_SCHEMA §7.2 — host capabilities.
HOST_CAPABILITIES: frozenset[str] = frozenset(
    {
        "host:nested-virt",
        "host:tpm",
        "host:secure-boot",
        "host:uefi",
        "host:gpu",
        "host:cpu-aes-ni",
        "host:hugepages",
    }
)

# IR_SCHEMA §7.3 — kernel/runtime capabilities.
KERNEL_CAPABILITIES: frozenset[str] = frozenset(
    {
        "kernel:edr-hooks",
        "kernel:ebpf",
        "kernel:audit",
        "kernel:container-runtime",
        "kernel:dotnet-clr",
        "kernel:powershell-5plus",
    }
)

# All 16 capability keywords. Closed for v1.
ALL_CAPABILITIES: frozenset[str] = (
    OS_FAMILY_KEYWORDS | HOST_CAPABILITIES | KERNEL_CAPABILITIES
)
assert len(ALL_CAPABILITIES) == 16, "Capability taxonomy must contain exactly 16 keywords"


# IR_SCHEMA §4.1 — closed 6-action install set.
# Note: pkg and pkg@version share the same dict key (`pkg`); pkg@version is
# distinguished by the presence of a `version` field. So we model 5 distinct
# top-level action keys here, and the action-kind classifier in install_action_kind()
# disambiguates pkg vs pkg@version.
INSTALL_ACTION_KEYS: frozenset[str] = frozenset(
    {"pkg", "config", "role", "container", "script"}
)

# Logical action-kind names (the 6 named in IR_SCHEMA §4.1, used by
# install_support: cross-checks and rule CAP-020).
INSTALL_ACTION_KINDS: frozenset[str] = frozenset(
    {"pkg", "pkg@version", "config", "role", "container", "script"}
)


def install_action_kind(action: dict) -> str | None:
    """Classify an install-action dict to one of the 6 closed kinds.

    Returns None if the action is malformed.
    """
    if not isinstance(action, dict):
        return None
    keys = set(action.keys())
    if "pkg" in keys:
        return "pkg@version" if "version" in keys else "pkg"
    for key in ("config", "role", "container", "script"):
        if key in keys:
            return key
    return None


# IR_SCHEMA §3.2 — node kinds.
NODE_KINDS: frozenset[str] = frozenset({"host", "router", "switch"})

# IR_SCHEMA §8.1 — identity entry types.
IDENTITY_TYPES: frozenset[str] = frozenset({"user"})

# IR_SCHEMA §8.3 — defense tap_from modes.
TAP_FROM_MODES: frozenset[str] = frozenset({"bridge", "per-vm", "external-mirror"})

# IR_SCHEMA §1.3 — overlay sub-section names.
OVERLAY_SECTIONS: frozenset[str] = frozenset(
    {"identity", "data", "defense", "adversary"}
)  # + adversary: A3 Stage A (docs/design/08-exercise-measurement.md), 2026-07-11


def is_valid_id(s: object) -> bool:
    return isinstance(s, str) and bool(ID_REGEX.match(s))


def split_image_ref(ref: str) -> tuple[str, str | None]:
    """Split `image-id` or `image-id@version` → (id, version|None)."""
    if "@" in ref:
        head, _, tail = ref.partition("@")
        return head, tail or None
    return ref, None


def split_behavior_ref(ref: str) -> tuple[str, str | None]:
    """Split `behavior-id` or `behavior-id@version` → (id, version|None).

    Same shape as image refs; separate name for documentation.
    """
    return split_image_ref(ref)
