"""The default-agent-set registry (A3 Stage A,
docs/design/08-exercise-measurement.md).

Each TE host node carries, by default, an instrumentation baseline: a Wazuh
host agent (the existing `overlays.defense` mechanism, `install-defense-agent`)
and a Prometheus node-exporter (`install-agent`, OP-OVL-070). A THIRD agent,
Sandcat (the Caldera adversary agent), is deliberately NOT a blanket default --
it is declared per node via `overlays.adversary[].targets:` (design brief §3:
"Sandcat + Wazuh are the red/blue defaults, declared through their overlays
so they can be placed/scoped per node"). This module is the small registry
that resolves which nodes get which UNCONDITIONAL default, and lets a node
opt out.

**ON BY DEFAULT** (user decision, 2026-07-11): the sweep fires unconditionally
on every TE `kind: host` node unless `scenario.default_agents: false` (the
scenario-level escape hatch) or the individual node opts out
(`node.default_agents_off`). Kept separate from `overlays.defense`'s existing
explicit `installed_on:`/`observes:` entries -- both mechanisms can coexist on
one host UNLESS the host already carries an explicit entry for that same tool
kind, in which case the default is skipped (dedup, code-review mandatory fix,
2026-07-11 -- two `install-defense-agent` ops would both write
`/var/ossec/etc/ossec.conf`; see `hosts_with_explicit_defense_tool`).

Pure data/lookup helpers only -- no adapter/config_exec/planner imports (see
the package docstring); consumed by `planner/pass1_emit.py`.
"""
from __future__ import annotations

from typing import Any

# The unconditional defaults (independent of any overlays.defense entry).
# "adversary" is deliberately excluded here -- it stays overlay-declared,
# never blanket (see module docstring).
UNCONDITIONAL_DEFAULT_KINDS: tuple[str, ...] = ("defense", "exporter")

# The tool `install-defense-agent` installs for a synthesized default-defense
# entry (reuses the SAME render/backend path as a hand-authored
# overlays.defense entry -- docs/SLICE_OVERLAY.md §4b -- so it never diverges).
DEFAULT_DEFENSE_TOOL = "wazuh-agent"


def default_defense_entry(host_id: str) -> dict[str, Any]:
    """A synthesized DefenseEntry-shaped dict for `host_id`'s baked-in Wazuh
    agent -- shape-compatible with an authored `overlays.defense` entry, so
    the planner can hand it straight to the existing `install-defense-agent`
    emission/render/backend path unmodified."""
    return {
        "id": f"default-defense-{host_id}",
        "tool": DEFAULT_DEFENSE_TOOL,
        "installed_on": [host_id],
    }


def default_exporter_entry(host_id: str) -> dict[str, Any]:
    """A synthesized agent-entry dict for `host_id`'s baked-in Prometheus
    node-exporter, consumed by `install-agent` (agent_kind="exporter")."""
    return {"id": f"default-exporter-{host_id}"}


def is_opted_out(node: Any, kind: str) -> bool:
    """True if `node` (an `ir.schema.Node`) opts out of default-agent `kind`
    via `node.default_agents_off`. Tolerant of a bare/untyped object (dict or
    Node) so callers don't need a type-check first."""
    off = getattr(node, "default_agents_off", None)
    if off is None and isinstance(node, dict):
        off = node.get("default_agents_off")
    return kind in (off or [])


def resolve_default_kinds(node: Any) -> list[str]:
    """The unconditional default-agent kinds (`UNCONDITIONAL_DEFAULT_KINDS`)
    that still apply to `node` after its per-node opt-outs."""
    return [k for k in UNCONDITIONAL_DEFAULT_KINDS if not is_opted_out(node, k)]


# Tool names an author might use in a hand-authored `overlays.defense` entry
# that already cover the "exporter" default (dedup, mandatory fix 2026-07-11).
# No IR block declares an *explicit* node-exporter today (only the default
# sweep produces one via `install-agent`), but an author CAN route one
# through `overlays.defense`'s generic unknown-tool fallback
# (`install-defense-agent`, docs/SLICE_OVERLAY.md §4b) -- recognized here so
# the default sweep doesn't double it up if that ever happens.
EXPORTER_TOOL_ALIASES: tuple[str, ...] = (
    "node-exporter", "prometheus-node-exporter", "windows_exporter",
)


def hosts_with_explicit_defense_tool(ir: Any, *tool_names: str) -> set[str]:
    """Every host already covered by a hand-authored `overlays.defense` entry
    whose `tool:` is one of `tool_names` (LES-034 -- every entry's every
    `installed_on:` host, not just the first entry/first host). Empty set if
    the IR has no `overlays.defense` at all."""
    hosts: set[str] = set()
    overlays = getattr(ir, "overlays", None)
    if overlays is None:
        return hosts
    for entry in (overlays.defense or []):
        if (entry.tool or "") in tool_names:
            hosts.update(entry.installed_on or [])
    return hosts
