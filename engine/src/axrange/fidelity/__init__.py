"""axrange.fidelity — the golden-twin **fidelity scorecard**
(`docs/design/13-golden-twin-fidelity.md` §6's *certify* box;
`docs/WORK_ORDER_fidelity_scorecard.md`).

A scorecard is what makes a build **golden**: per row, observed / referenced
/ imported / asserted against reproduced, with each row's evidence mix
published, stamped with the SPEC version it certifies against, written
beside the twin config it certifies and served over the API.

**Posture (a leaf, like `axrange.measurement`).** This package lives inside
`axrange` for reuse and testability, and the ENGINE PROPER NEVER IMPORTS IT.
Its own imports run strictly downward (LES-025): `axrange.planner`
(`inventory`, `state`, `opview`), `axrange.twinconfig`, `axrange.manifest`,
and -- from `runner.py` ONLY -- `axrange.adapter_proxmox`. It must not
import `axrange.o2control` (role overrides arrive as an argument, exactly
as `build_twin_inventory` takes them) and it must not import `aximprint`,
ever: it reads three files on the cross-repo seam.

`rows.py` and `probe.py` stay PURE so the eleven-row computation is
unit-testable from fixtures with no DB and no cluster; this module is the
thin builder that reads the state store, and `runner.py` is the one place
that holds a `GuestAgent`.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Optional

from axrange.fidelity.probe import ProbeReport
from axrange.fidelity.reference import (
    ReferenceBundle,
    ReferenceError,
    load_reference_bundle,
    resolve_bundle_dir,
)
from axrange.fidelity.rows import (
    SPEC_OCCUPANCY_BAR,
    AppliedInstall,
    GateInfo,
    Reproduced,
    compute_rows,
)
from axrange.fidelity.schema import SCORECARD_VERSION, Scorecard

__all__ = [
    "AppliedInstall",
    "GateInfo",
    "ProbeReport",
    "ReferenceBundle",
    "ReferenceError",
    "Reproduced",
    "SCORECARD_VERSION",
    "SPEC_OCCUPANCY_BAR",
    "Scorecard",
    "build_reproduced",
    "build_scorecard",
    "load_reference_bundle",
    "resolve_bundle_dir",
    "resolve_gate_info",
    "scorecard_output_path",
]

_COMPLETED = "completed"


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# The reproduced side
# ---------------------------------------------------------------------------
def build_reproduced(
    store: Any, deploy_id: str, *, manifests: Any = None,
    role_overrides: Optional[dict[str, str]] = None,
) -> Reproduced:
    """Read the twin back out of the state store.

    `hosts[]` comes from `planner.inventory.build_twin_inventory` -- REUSED,
    not re-derived (LES-025/LES-037): it is already the published binding
    contract `te inventory` and `GET /api/deploys/{id}/inventory` serve, and
    a second op scan here would be a second answer to the same question.

    Op rows are read only for row 3's applied installs, under three rules:
      * **filter to `COMPLETED`** -- `inputs_resolved_json` holds DESIRED
        inputs from plan-insert onward and is refreshed at attempt start, so
        neither its presence nor its content means *applied* (LES-159); and
        the filter is "is completed", never "is not destroyed"
        (`reset_incomplete_ops` reverts a `destroyed` row to `pending` on
        re-apply, LES-113);
      * **`.get(...)` everything** -- an older deploy's row shape is frozen
        until `--fresh` (LES-096), so an absent key degrades to empty, never
        raises;
      * **source coverage only from unconditional ops** -- `provision-app`/
        `run-script` are conditional by design (LES-095), which is why host
        existence comes from the inventory (unconditional `clone-vm`) and
        never from an app op's presence.
    """
    from axrange.planner.inventory import build_twin_inventory
    from axrange.planner.opview import op_fields

    deploy = store.get_deploy(deploy_id)
    if deploy is None:
        # `InventoryError`, not `ReferenceError`: "this deploy does not
        # exist" is the SAME condition `build_twin_inventory` raises for, and
        # the API maps it to 404 there. Raising a reference-class error here
        # would 400 a missing deploy on one endpoint and 404 it on its peer.
        from axrange.planner.inventory import InventoryError

        raise InventoryError(f"deploy not found: {deploy_id}")

    inv = build_twin_inventory(
        store, deploy_id, manifests=manifests, role_overrides=role_overrides,
    )

    applied: dict[str, AppliedInstall] = {}
    census: dict[str, dict[str, int]] = {}
    host_os_family: dict[str, str] = {}
    for op in store.ops_for_deploy(deploy_id):
        op_name, scope, inp, _out = op_fields(op)
        status = getattr(op, "status", None)
        status_s = (getattr(status, "value", str(status)) or "").lower()
        census.setdefault(op_name, {})
        census[op_name][status_s] = census[op_name].get(status_s, 0) + 1
        if op_name == "wait-os-ready":
            # The guest's REAL OS family, recorded by an unconditional op --
            # sourced exactly as `o2control/taps.py::_scan_te_inventory`
            # does, and read at ANY status: the probe needs to know a host is
            # Windows in order to EXCLUDE it as a peer, which is just as true
            # for a host whose wait op never completed. (`HostInfo.os` is a
            # different thing: an image->manifest lookup that is `None`
            # whenever no manifest registry is passed.)
            host = scope.get("host")
            osf = str(inp.get("os_family") or "").lower()
            if host and osf:
                host_os_family[str(host)] = osf
        if status_s != _COMPLETED:
            continue
        if op_name not in ("provision-app", "run-script"):
            continue
        host_id = inp.get("host_id") or scope.get("host")
        if not host_id:
            continue
        entry = applied.setdefault(str(host_id), AppliedInstall(host_id=str(host_id)))
        if op_name == "provision-app":
            actions = inp.get("install_actions")
            if isinstance(actions, list):
                entry.install_actions.extend(a for a in actions if isinstance(a, dict))
        else:
            name = inp.get("script_name")
            if name:
                entry.script_names.append(str(name))

    return Reproduced(
        deploy_id=deploy_id,
        scenario_id=deploy.scenario_id,
        plan_id=deploy.plan_id,
        ir_hash=deploy.ir_hash,
        deploy_status=getattr(deploy.status, "value", str(deploy.status)),
        tier=getattr(deploy, "tier", None),
        hosts=list(inv.hosts),
        applied=applied,
        op_census=census,
        host_os_family=host_os_family,
    )


# ---------------------------------------------------------------------------
# The deploy gate (§3.4)
# ---------------------------------------------------------------------------
def resolve_gate_info(
    *, config_id: Optional[str], ir_hash: Optional[str],
    catalog_dir: Any = None,
) -> GateInfo:
    """CONFIRM the twin's deploy threshold by re-expansion, never guess it.

    `twinconfig.confirm_deploy_gate` re-expands `config_id` and returns a
    record only when the expansion reproduces `ir_hash` byte for byte --
    which proves BOTH the threshold and the exact gated-out set. Anything
    else is `threshold: UNCONFIRMED`, and rows 1/2 decline to score against
    a set they cannot reproduce (spec §3.4). A `--param` override is
    persisted nowhere, which is why confirmation and not recomputation from
    the config's declared `params:` is the mechanism.
    """
    if not config_id or not ir_hash:
        return GateInfo(
            confirmed=False, config_id=config_id,
            reason="no twin config id / ir_hash to confirm against",
        )
    from axrange.twinconfig import confirm_deploy_gate

    record = confirm_deploy_gate(config_id, ir_hash, catalog_dir=catalog_dir)
    if record is None:
        return GateInfo(
            confirmed=False, config_id=config_id,
            reason=(
                f"re-expanding twin config {config_id!r} did not reproduce "
                "ir_hash -- the config has drifted since deploy, a --param "
                "override was used, or the config is not in the pinned catalog"
            ),
        )
    return GateInfo(
        confirmed=True,
        config_id=record.config_id,
        gate_active=record.gate_active,
        min_occupancy=record.min_occupancy if record.gate_active else None,
        min_role_confidence=(
            record.min_role_confidence if record.gate_active else None
        ),
        emitted_nodes=record.emitted_nodes,
        gated_nodes=tuple(
            g.name for g in record.gated_out if g.kind == "node"
        ),
        gated_segments=tuple(
            g.name for g in record.gated_out if g.kind == "segment"
        ),
    )


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------
def build_scorecard(
    *, reference: ReferenceBundle, reproduced: Reproduced, gate: GateInfo,
    probe_report: Optional[ProbeReport] = None,
    attestation: Optional[dict[str, Any]] = None,
    generated_at: Optional[str] = None,
    extra_warnings: Optional[list[str]] = None,
) -> Scorecard:
    """Assemble the document from already-built inputs. Pure: the caller
    owns the state store, the cluster and the clock (`generated_at` is
    injectable so a golden-output test can pin the whole document byte for
    byte)."""
    rows = compute_rows(
        reference, reproduced, gate,
        report=probe_report, attestation=attestation,
    )

    warnings = list(reference.warnings)
    if not gate.confirmed:
        warnings.append(
            "deploy threshold UNCONFIRMED: " + (gate.reason or "")
            + " -- rows 1 and 2 decline to score (spec §3.4)"
        )
    elif not gate.gate_active:
        warnings.append(
            f"twin config {gate.config_id!r} carries no observation: block, so "
            "this twin has NO recorded deploy threshold; rows 1 and 2 judge "
            f"against the spec's own {SPEC_OCCUPANCY_BAR:g} bar and the "
            "'observed, below deploy threshold' state is necessarily empty "
            "(this twin was cast under the old drop policy)"
        )
    if probe_report is not None:
        warnings.extend(probe_report.errors)
    warnings.extend(extra_warnings or [])

    # The reproduced side's own attachment set -- what `attach-vm-to-vnet`
    # recorded for hosts that actually cloned, which row 2 cross-checks the
    # cast's declared segment list against (S5).
    segments = sorted(reproduced.attached_segments)
    subject = {
        "deploy_id": reproduced.deploy_id,
        "scenario_id": reproduced.scenario_id,
        "plan_id": reproduced.plan_id,
        "ir_hash": reproduced.ir_hash,
        "deploy_status": reproduced.deploy_status,
        "tier": reproduced.tier,
        "hosts": len(reproduced.hosts),
        # Hosts whose `clone-vm` actually completed. Rows 1/2/3/5 score
        # against THIS number, not `hosts` -- a plan-time roster row is not a
        # deployed host (M5).
        "hosts_deployed": len(reproduced.deployed_host_ids),
        "planned_not_deployed": len(reproduced.planned_not_deployed),
        "segments": len(segments),
        "deploy_threshold": gate.to_dict(),
    }
    return Scorecard(
        generated_at=generated_at or _utc_now(),
        subject=subject,
        reference=reference.to_dict(),
        rows=rows,
        warnings=warnings,
        scorecard_version=SCORECARD_VERSION,
    )


def scorecard_output_path(
    bundle_dir: str | Path, bundle_id: str, deploy_id: str, *,
    out: Optional[str | Path] = None,
) -> Path:
    """§4.2: `twins/<twin-id>/generated/<twin-id>.scorecard.<deploy_id>.json`
    (`<bundle>/<twin-id>.scorecard...` in a legacy cast bundle --
    `axrange.twinconfig.bundle`).

    Takes the two STRINGS rather than a `ReferenceBundle` so a caller that
    already has a built document (`doc["reference"]["bundle"]`/
    `["bundle_id"]`) does not have to re-read the bundle off disk just to
    name the output file.

    Keyed on `deploy_id` deliberately -- a re-certification after
    `te deploy --update` is a NEW scorecard beside the old, not an
    overwrite, so a future drift report has two dated documents to diff.
    """
    if out:
        return Path(out)
    from axrange.twinconfig.bundle import bundle_artifact_path

    return bundle_artifact_path(bundle_dir, f"{bundle_id}.scorecard.{deploy_id}.json")
