"""`axrange te egress` -- read or flip a deployed twin's LIVE egress phase and
record it where `te info` and the tap/tfc containment gate read it
(docs/WORK_ORDER_corp_twin.md §11 step 2).

WHY THIS EXISTS
---------------
The containment gate (`cli.main._containment_gate`) and `te info` both read
the twin's posture off its `configure-router` op row
(`planner.egress.egress_posture_from_ops`): the `egress_phase` the router
REPORTED when that op ran. The runbook's containment step is a flip ON the
router (`axr-egress-phase.sh measure`), which that record never saw -- so
after a correct flip `te info` still said `provision` and the gate still
refused, and the only way through was `--allow-uncontained`, i.e. the gate
could never pass on its own merits.

This module closes that gap without a second source of truth: it runs the
router's OWN engine (`egress_render.EGRESS_ENGINE`, the script
`configure-router` installed) over the same QGA channel `configure-router`
used, reads back `--show` + `--healthcheck`, and merges the result into the
SAME op row's outputs (`StateStore.merge_op_outputs`). Every reader then sees
the live phase through the code path it already had.

FAIL CLOSED
-----------
`egress_phase` is written as `measure` ONLY when the engine says `measure`
AND its `--healthcheck` asserts (WAN drop present, sink policy route
present, the unobserved-address FIB probe lands on the sink) AND that
healthcheck's own `phase=` line names the same phase `--show` did (the two
are separate execs; a flip between them would otherwise pair one phase's
reading with the other's assertion). A `measure` that does not assert, or
whose healthcheck asserted a different phase, is recorded as
`measure-unverified`, which
`EgressPosture.contained` reads as NOT contained -- the gate refuses, `te
info` says OPEN. The raw readings ride alongside in `egress_live`.

The row is INVALIDATED before the guest is touched (`<action>-pending-
unverified` / `unknown-unverified`), and any exec failure -- a non-zero
`--show`, a QGA timeout, a transport exception -- records
`unknown-unverified` with `egress_live.error` and raises: no failure path can
leave an earlier `measure` standing.

The tap/tfc containment gate calls `egress_live(..., "show")` itself (one
live read per deploy attempt), so it refuses on the router's CURRENT state,
not on whatever was last recorded (docs/WORK_ORDER_corp_twin.md §11 step 2).

Reuse direction (LES-025): the GuestAgent comes from the caller (the CLI
builds the real one from `adapter_proxmox`; tests pass a fake), the engine
path/marker from `adapter_proxmox.egress_render` (pure constants), the row
lookup from `planner.opview`. Nothing here knows Proxmox or IR.
"""
from __future__ import annotations

import datetime as _dt
import time as _time
from dataclasses import dataclass, field
from typing import Any, Optional

#: The actions `te egress` accepts: a read, or a flip to one of the engine's
#: two phases (`egress_render.EGRESS_PHASES`).
ACTIONS: tuple[str, ...] = ("show", "measure", "provision")

#: What a `measure` whose healthcheck FAILED is recorded as. Anything but the
#: literal `measure` reads as uncontained (`EgressPosture.contained`).
UNVERIFIED_SUFFIX = "-unverified"


class ContainmentError(Exception):
    """A `te egress` that cannot run: no declared posture, no router VM id,
    a failed flip. The message names the deploy and the fix. `result` is the
    recorded read-back when there is one (a failed flip), else None."""

    result: Optional["EgressLiveResult"] = None


@dataclass
class EgressLiveResult:
    deploy_id: str
    router_host: str
    vmid: int
    action: str
    phase: Optional[str]          # what `--show` says the router is in
    since: Optional[str]          # when it entered that phase (engine's record)
    healthy: bool                 # `--healthcheck` exit 0
    recorded: str                 # the `egress_phase` written to the op row
    health_lines: list[str] = field(default_factory=list)
    read_at: Optional[str] = None

    @property
    def contained(self) -> bool:
        return self.recorded == "measure"

    def to_dict(self) -> dict[str, Any]:
        return {
            "deploy_id": self.deploy_id,
            "router_host": self.router_host,
            "vmid": self.vmid,
            "action": self.action,
            "phase": self.phase,
            "since": self.since,
            "healthy": self.healthy,
            "recorded": self.recorded,
            "contained": self.contained,
            "health": list(self.health_lines),
            "read_at": self.read_at,
        }


def _parse_show(text: str) -> dict[str, str]:
    """`phase=measure since=2026-... sink=10.x mode=default` -> a dict. The
    engine prints ONE such line (`egress_render._ENGINE_TEMPLATE`, `--show`)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "phase=" not in line:
            continue
        for tok in line.split():
            if "=" in tok:
                k, v = tok.split("=", 1)
                out[k] = v
        break
    return out


def _router_op_and_vmid(store: Any, deploy_id: str) -> tuple[Any, str, int]:
    """The declaring `configure-router` row, its host, and that host's VM id
    (from the host's `clone-vm` outputs -- the persisted `configure-router`
    inputs never carry the runtime-resolved `vmid`)."""
    from axrange.planner.opview import op_fields

    get_deploy = getattr(store, "get_deploy", None)
    row = get_deploy(deploy_id) if get_deploy is not None else None
    dstatus = getattr(row, "status", None)
    if str(getattr(dstatus, "value", dstatus)).lower() == "destroyed":
        raise ContainmentError(
            f"deploy {deploy_id} is destroyed -- there is no router to read or flip "
            "(its VM ids may already belong to another deploy)"
        )
    ops = store.ops_for_deploy(deploy_id)
    cr_row = None
    host: Optional[str] = None
    for op in ops:
        name, scope, inputs, _out = op_fields(op)
        if name == "configure-router" and isinstance(inputs.get("egress"), dict):
            cr_row, host = op, scope.get("host")
            break
    if cr_row is None or not host:
        raise ContainmentError(
            f"deploy {deploy_id}: no router carries an `egress:` posture -- this twin "
            "declares no containment, so there is no phase to read or flip"
        )
    status = getattr(cr_row, "status", None)
    if str(getattr(status, "value", status)).lower() != "completed":
        raise ContainmentError(
            f"deploy {deploy_id}: configure-router[{host}] has not completed "
            f"(status {getattr(status, 'value', status)}) -- the egress engine is not "
            "installed on the router yet; finish the deploy first"
        )
    for op in ops:
        name, scope, _inp, out = op_fields(op)
        if name == "clone-vm" and scope.get("host") == host and out.get("vmid") is not None:
            return cr_row, str(host), int(out["vmid"])
    raise ContainmentError(
        f"deploy {deploy_id}: no recorded VM id for router {host!r} (its clone-vm "
        "row has no vmid output)"
    )


def _record(store: Any, deploy_id: str, op_id: str, host: str, patch: dict[str, Any]) -> None:
    """Merge `patch` onto the router's `configure-router` row, or fail loud:
    a live read-back that silently did not land is the record still saying
    whatever it said before -- possibly `measure`."""
    try:
        merged = store.merge_op_outputs(deploy_id, op_id, patch)
    except ValueError as e:  # a corrupt outputs record (StateStore refuses it)
        raise ContainmentError(
            f"deploy {deploy_id}: cannot record the egress read-back on "
            f"configure-router[{host}]: {e}"
        ) from e
    if not merged:
        raise ContainmentError(
            f"deploy {deploy_id}: could not record the egress read-back on "
            f"configure-router[{host}] (op row {op_id!r} vanished) -- the recorded "
            "phase is unchanged; re-run `axrange te egress` once the row exists"
        )


def egress_live(
    store: Any, deploy_id: str, action: str, *, ga: Any,
    timeout_s: int = 300, now: Optional[str] = None,
) -> EgressLiveResult:
    """Read (`action="show"`) or flip (`"measure"`/`"provision"`) the twin's
    router egress phase in-guest, then record the live result on its
    `configure-router` row. `ga` is a `GuestAgent`-shaped object (`exec(vmid,
    argv, timeout_s=)` -> `.exit_code/.stdout/.stderr`).

    `timeout_s` is ONE overall budget for every exec of this call (the flip,
    `--show`, `--healthcheck`): each exec gets what is left of it, and an
    exhausted budget before an exec fails closed like any exec failure.

    INVALIDATE FIRST: before touching the guest the row is rewritten to a
    NOT-contained value (`<action>-pending-unverified` for a flip,
    `unknown-unverified` for a read), so a crash, a QGA timeout or an
    exception anywhere below leaves the record saying "not contained" --
    never a stale `measure`. Only a completed read-back that asserts
    overwrites it with `measure`."""
    from axrange.adapter_proxmox.egress_render import EGRESS_ENGINE, EGRESS_OK_MARKER

    if action not in ACTIONS:
        raise ContainmentError(f"unknown action {action!r}; one of {list(ACTIONS)}")
    cr_row, host, vmid = _router_op_and_vmid(store, deploy_id)
    read_at = now or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    via = f"te egress {action}"
    where = f"router {host} (vmid {vmid})"

    pending = (f"{action}-pending{UNVERIFIED_SUFFIX}" if action != "show"
               else f"unknown{UNVERIFIED_SUFFIX}")
    _record(store, deploy_id, cr_row.op_id, host, {
        "egress_phase": pending,
        "egress_live": {"phase": None, "since": None, "healthy": False,
                        "read_at": read_at, "via": via, "pending": True},
    })

    def _fail(msg: str) -> ContainmentError:
        _record(store, deploy_id, cr_row.op_id, host, {
            "egress_phase": f"unknown{UNVERIFIED_SUFFIX}",
            "egress_live": {"phase": None, "since": None, "healthy": False,
                            "read_at": read_at, "via": via, "error": msg},
        })
        return ContainmentError(f"{msg}; recorded egress_phase=unknown{UNVERIFIED_SUFFIX}")

    deadline = _time.monotonic() + timeout_s

    def _left(step: str) -> int:
        left = int(deadline - _time.monotonic())
        if left < 1:
            raise _fail(f"{where}: the {timeout_s} s budget ran out before `{step}`")
        return left

    flip_error: Optional[str] = None
    try:
        if action != "show":
            res = ga.exec(vmid, [EGRESS_ENGINE, action], timeout_s=_left(action))
            out = (res.stdout or "") + (res.stderr or "")
            if res.exit_code != 0 or f"{EGRESS_OK_MARKER} phase={action}" not in out:
                # The engine forces itself CLOSED on a failed transition, so the
                # guest is contained -- but the flip did not happen. Fall through
                # to the read-back so the record says what the router IS in.
                flip_error = (
                    f"{where}: `{EGRESS_ENGINE} {action}` failed "
                    f"(exit {res.exit_code}): {out.strip()[-300:]}"
                )
        show = ga.exec(vmid, [EGRESS_ENGINE, "--show"], timeout_s=_left("--show"))
        if show.exit_code != 0:
            raise _fail(
                f"{where}: `{EGRESS_ENGINE} --show` exited {show.exit_code}: "
                f"{((show.stdout or '') + (show.stderr or '')).strip()[-300:]}"
            )
        health = ga.exec(vmid, [EGRESS_ENGINE, "--healthcheck"],
                         timeout_s=_left("--healthcheck"))
    except ContainmentError:
        raise
    except Exception as e:  # noqa: BLE001 -- any QGA/transport failure: fail CLOSED
        raise _fail(f"{where}: egress engine unreachable over QGA: "
                    f"{type(e).__name__}: {e}") from e

    fields = _parse_show(show.stdout or "")
    phase = fields.get("phase")
    since = fields.get("since")
    healthy = health.exit_code == 0
    health_lines = [
        ln for ln in ((health.stdout or "") + (health.stderr or "")).splitlines() if ln.strip()
    ]
    # The healthcheck asserts the phase IT resolved (its first line); only an
    # assertion of the phase `--show` reported verifies that phase.
    health_phase = _parse_show(health.stdout or "").get("phase")
    if phase and healthy and health_phase == phase:
        recorded = phase
    else:
        recorded = f"{phase or 'unknown'}{UNVERIFIED_SUFFIX}"

    _record(store, deploy_id, cr_row.op_id, host, {
        "egress_phase": recorded,
        "egress_live": {
            "phase": phase, "since": since, "healthy": healthy,
            "health_phase": health_phase, "read_at": read_at, "via": via,
        },
    })
    result = EgressLiveResult(
        deploy_id=deploy_id, router_host=host, vmid=vmid, action=action,
        phase=phase, since=since, healthy=healthy, recorded=recorded,
        health_lines=health_lines, read_at=read_at,
    )
    if flip_error is not None:
        err = ContainmentError(
            f"{flip_error}; recorded the read-back instead: egress_phase={recorded}"
        )
        err.result = result  # the read-back IS complete: callers may render it
        raise err
    return result


# ---------------------------------------------------------------------------
# The tap/tfc containment GATE's verdict, shared by the CLI (`cli.main.
# _containment_gate`) and the per-host persona apply (`traffic.
# apply_host_personas`, reached from the API) -- one decision rule, two
# transports (LES-037). The CLI keeps its own wording and exit codes.
# ---------------------------------------------------------------------------
@dataclass
class ContainmentVerdict:
    """`contained` is True only on an asserted live `measure`. `why` is the
    operator-facing reason when it is not; `live` the read-back, if any."""

    deploy_id: str
    contained: bool
    why: str = ""
    live: Optional[EgressLiveResult] = None


def containment_verdict(
    store: Any, deploy_id: str, *, ga: Any = None, timeout_s: int = 60,
    ga_factory: Optional[Any] = None,
) -> Optional[ContainmentVerdict]:
    """None when the twin declares no `egress:` posture (nothing to gate).
    Otherwise ONE live `egress_live(..., "show")` read (which records itself
    on the router row) and the verdict: any failure to read is NOT contained
    -- an unknown phase is not a contained one.

    `ga` or `ga_factory` (called only when there IS a posture, so a caller
    need not build a guest agent -- or read the posture itself -- for the
    common undeclared twin, S10)."""
    from axrange.planner.egress import egress_posture_from_ops

    posture = egress_posture_from_ops(store.ops_for_deploy(deploy_id))
    if posture is None:
        return None
    try:
        if ga is None:
            ga = ga_factory() if ga_factory is not None else None
        if ga is None:
            raise ContainmentError("no guest agent to read the router with")
        live = egress_live(store, deploy_id, "show", ga=ga, timeout_s=timeout_s)
    except Exception as e:  # noqa: BLE001 -- ANY failure to read is "not contained"
        return ContainmentVerdict(
            deploy_id=deploy_id, contained=False,
            why=f"its router's live egress phase could not be read ({e})",
        )
    if live.contained:
        return ContainmentVerdict(deploy_id=deploy_id, contained=True, live=live)
    why = (f"its router's LIVE egress phase reads '{live.recorded}' "
           f"(`--show` phase={live.phase or '?'}, healthcheck "
           f"{'ok' if live.healthy else 'FAILED'}, read {live.read_at})")
    return ContainmentVerdict(deploy_id=deploy_id, contained=False, why=why, live=live)


class NodeResolvingGuestAgent:
    """`GuestAgent.exec` on whichever node the VM actually lives on: the
    router's node is resolved from its vmid (`/cluster/resources`, via
    `adapter_proxmox.resolve.resolve_vm_node`), falling back to `fallback`
    (`--node`, else `DEFAULT_NODE`) only when that lookup is unavailable. A
    router placed off the default node would otherwise read as "unreachable"
    -- i.e. NOT contained -- on every gate. (Moved here from `cli.main` so
    the API path builds the identical agent; the CLI name is an alias.)"""

    def __init__(self, cfg: Any, fallback: str) -> None:
        from axrange.adapter_proxmox.rest import RestClient

        self.cfg, self.fallback = cfg, fallback
        self.client = RestClient(cfg)
        self._nodes: dict[int, str] = {}

    def node_for(self, vmid: int) -> str:
        from axrange.adapter_proxmox.resolve import resolve_vm_node

        if vmid not in self._nodes:
            self._nodes[vmid] = (
                resolve_vm_node(self.cfg, vmid, client=self.client) or self.fallback
            )
        return self._nodes[vmid]

    def exec(self, vmid: int, argv: list[str], timeout_s: int = 300) -> Any:
        from axrange.adapter_proxmox.guest_agent import GuestAgent

        return GuestAgent(self.client, self.node_for(vmid)).exec(
            vmid, argv, timeout_s=timeout_s
        )
