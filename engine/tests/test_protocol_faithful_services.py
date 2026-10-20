"""Tests for `docs/WORK_ORDER_protocol_faithful_services.md` -- the axRange
half: **S1** (the declared listen set) and **S3** (a real DC, and Windows
members that answer, authored from a twin config).

Two properties are load-bearing and easy to lose, so each has its own test
rather than riding along as an assertion inside a bigger one:

  * **`node_listen` is emitted onto `clone-vm` ONLY when a host declares a
    listen set.** An unconditional key would appear on the NEW side of
    `plandiff._diff_fields` for every host of every standing twin (whose
    persisted rows have no such key), classify every `clone-vm` as Mutated,
    and make `te deploy --update` hard-refuse 100% of already-deployed
    content -- LES-185's failure shape exactly.
  * **The declared set changes no scorecard verdict.** A declaration is not
    evidence; rows 4 and 5 report it beside the observed set and keep
    measuring with `ss` and a protocol-aware probe.

No test here reaches a cluster (LES-208): every deploy runs through the real
`build_plan` -> `Executor` path against a fake adapter, which is also what
makes the inventory assertions read ACTUALLY PERSISTED op rows rather than a
hand-built stand-in for the serialization path (LES-068).
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from axrange.ir.parser import parse_ir
from axrange.planner import Executor, StateStore, build_plan
from axrange.planner.cluster import ClusterContext
from axrange.planner.inventory import HostListen, build_twin_inventory
from axrange.twinconfig import expand_twin_config, materialize_twin_config
from axrange.twinconfig.errors import TwinConfigShapeError
from axrange.twinconfig.expand import expand_twin_config_raw

FIXT_TWIN_CONFIGS = "tests/fixtures/twin-configs"
FIXT_ROOT = Path(__file__).resolve().parent / "fixtures"
AD_CAST_DIR = FIXT_ROOT / "twin-configs" / "ad-cast"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------
class _FakeAdapter:
    """Canned per-op outputs, one distinct vmid per host -- the same fake
    `tests/test_twin_inventory.py` and `tests/fidelity_support.py` use."""

    def __init__(self) -> None:
        self._next_vmid = 700100

    def run(self, op, resolved_inputs, ctx):
        if op.op_name == "clone-vm":
            self._next_vmid += 1
            return {"vmid": self._next_vmid}
        if op.op_name == "create-ovs-bridge":
            net = op.scope.get("net") or op.scope.get("scenario")
            return {"bridge_name": f"tebr_{net}"}
        if op.op_name == "create-vnet":
            return {"vnet_id": f"tev_{op.scope.get('net')}"}
        return {}


def _minimal_config(listen_by_node: dict[str, list[dict]] | None = None) -> dict:
    """A two-host twin config: `web` installs a package, `bare` installs
    nothing at all (the case that forces an app to be synthesized purely to
    carry `App.listen`)."""
    nodes = [
        {"name": "web", "role": "web-tier", "image": "debian-12-cloud@v1",
         "requires": ["linux"], "resources": {"cpu": 1, "ram": "1Gi", "disk": "10Gi"},
         "net": "seg", "install": [{"pkg": "nginx"}]},
        {"name": "bare", "role": "db-tier", "image": "debian-12-cloud@v1",
         "requires": ["linux"], "resources": {"cpu": 1, "ram": "1Gi", "disk": "10Gi"},
         "net": "seg"},
    ]
    for node in nodes:
        declared = (listen_by_node or {}).get(node["name"])
        if declared:
            node["listen"] = declared
    return {"twin_config": {
        "id": "listen-fixture",
        "default_agents": False,
        "segments": [{"name": "seg", "cidr": "10.71.0.0/24", "zone_type": "simple"}],
        "nodes": nodes,
    }}


def _plan_from_raw(raw: dict, manifests, *, plan_id: str | None = None):
    ir_dict, _params = expand_twin_config_raw(raw)
    parsed = parse_ir(text=_dump_yaml(ir_dict))
    plan = build_plan(
        parsed.ir, manifests, ClusterContext.single_node("px1"),
        raw_ir=parsed.raw, plan_id=plan_id,
    )
    plan.freeze()
    return plan


def _dump_yaml(ir_dict: dict) -> str:
    import io

    from ruamel.yaml import YAML

    buf = io.StringIO()
    YAML(typ="safe").dump(ir_dict, buf)
    return buf.getvalue()


def _deploy(plan, tmp_path, *, deploy_id="listen-1"):
    store = StateStore(str(tmp_path / "state.db"))
    store.create_deploy(
        deploy_id=deploy_id, scenario_id=plan.scenario_id,
        scenario_version=plan.scenario_version, scenario_schema_version=1,
        plan_id=plan.plan_id, ir_hash=plan.ir_hash, placement=plan.placement,
        tier="te",
    )
    adapter = _FakeAdapter()
    ex = Executor(plan, store, adapter, ClusterContext.single_node("px1"),
                  config_backend=adapter, deploy_id=deploy_id,
                  sleep_fn=lambda _: None)
    ex.persist_plan()
    result = ex.run()
    assert result.succeeded, result
    return store, deploy_id


# ===========================================================================
# S1 -- the twin-config passthrough
# ===========================================================================
def test_listen_passes_through_to_the_app_on_a_node_that_also_installs():
    ir, _ = expand_twin_config_raw(_minimal_config(
        {"web": [{"port": 80, "proto": "tcp"}, {"port": 443, "proto": "tcp"}]}
    ))
    apps = {a["id"]: a for a in ir["layers"]["apps"]}
    assert apps["web-app"]["install"] == [{"pkg": "nginx"}]
    assert apps["web-app"]["listen"] == [
        {"port": 80, "proto": "tcp"}, {"port": 443, "proto": "tcp"},
    ]


def test_a_node_with_listen_and_no_install_still_gets_an_app(manifests):
    """`App` is the only IR object with a `listen:` field, so a
    declare-but-install-nothing host needs one synthesized. It must add
    NOTHING to the DAG: an app with no install actions and no script emits no
    op at all."""
    raw = _minimal_config({"bare": [{"port": 5432, "proto": "tcp"}]})
    ir, _ = expand_twin_config_raw(raw)
    apps = {a["id"]: a for a in ir["layers"]["apps"]}
    assert "install" not in apps["bare-app"]
    assert apps["bare-app"]["listen"] == [{"port": 5432, "proto": "tcp"}]
    assert {"app": "bare-app", "host": "bare"} in ir["bindings"]

    with_listen = _plan_from_raw(raw, manifests, plan_id="pinned")
    without = _plan_from_raw(_minimal_config(), manifests, plan_id="pinned")
    assert set(with_listen.ops) == set(without.ops)
    assert set(with_listen.barriers) == set(without.barriers)


def test_the_assertion_shaped_add_host_record_round_trips():
    """`docs/design/14-operator-assertions.md` §3's `add: {kind: host, ...}`
    record carries `listen: [{port, proto}]` verbatim -- the shape an
    operator assertion arrives in must expand without a translation step
    (design note 14 §5, "an `add: host` record's `listen:` becomes the twin's
    declared listen set")."""
    asserted = [{"port": 88, "proto": "tcp"}, {"port": 389, "proto": "tcp"},
                {"port": 445, "proto": "tcp"}]
    ir, _ = expand_twin_config_raw(_minimal_config({"bare": asserted}))
    apps = {a["id"]: a for a in ir["layers"]["apps"]}
    assert apps["bare-app"]["listen"] == asserted


@pytest.mark.parametrize("bad,expect", [
    ([{"port": 99999, "proto": "tcp"}], "out of range"),
    ([{"port": 0, "proto": "tcp"}], "out of range"),
    ([{"port": 88, "proto": "sctp"}], "not one of"),
    ([{"port": 88}], "proto"),
    ([{"proto": "tcp"}], "port"),
    ([{"port": 88, "proto": "tcp"}, {"port": 88, "proto": "TCP"}], "duplicate"),
    ("80/tcp", "listen"),
])
def test_malformed_listen_is_a_loud_shape_error_naming_the_field(bad, expect):
    """Every rejection names `listen` and the offending field, so the message
    points at what to edit rather than at a pydantic list index (LES-211)."""
    with pytest.raises(TwinConfigShapeError) as e:
        expand_twin_config_raw(_minimal_config({"bare": bad}))
    msg = str(e.value)
    assert "listen" in msg
    assert expect in msg


def test_an_empty_listen_list_is_refused_at_the_author():
    """review S6: every layer downstream reads ABSENT as "this twin makes no
    claim about this host", and there is no second meaning for "claims to
    serve nothing" -- so `listen: []` would silently collapse into the first.
    Refuse it where it is written (the `tap_points: []` precedent)."""
    raw = _minimal_config()
    raw["twin_config"]["nodes"][1]["listen"] = []
    with pytest.raises(TwinConfigShapeError) as e:
        expand_twin_config_raw(raw)
    msg = str(e.value)
    assert "listen" in msg and "empty" in msg
    assert "omit the key" in msg


def test_a_param_templated_port_survives_a_strict_load(tmp_path):
    """review S5: `registry.load_twin_config` (used by `twin-config show` AND
    by `fidelity.reference.load_reference_bundle`) reads a config BEFORE
    `${PARAM}` substitution, so an `int`-annotated port would refuse the
    whole scorecard over a field the reader only displays. `port: Any` +
    `_check_listen`'s `int()` keeps the strict load working and the
    EXPANSION-time refusal loud."""
    from axrange.twinconfig.registry import load_twin_config

    catalog = tmp_path / "catalog" / "parm"
    catalog.mkdir(parents=True)
    (catalog / "parm.yaml").write_text(
        "twin_config:\n"
        "  id: parm\n"
        "  params: {P: 8443}\n"
        "  segments: [{name: s, cidr: 10.75.0.0/24}]\n"
        "  nodes:\n"
        "    - name: n\n"
        "      image: debian-12-cloud@v1\n"
        "      requires: [linux]\n"
        "      net: s\n"
        '      listen: [{port: "${P:8443}", proto: tcp}]\n'
    )
    # The strict, un-substituted load does not raise...
    cfg = load_twin_config("parm", catalog_dir=str(catalog.parent))
    assert cfg.nodes[0].listen[0].port == "${P:8443}"
    # ...and expansion resolves it to a real int.
    raw = {"twin_config": {
        "id": "parm", "params": {"P": 8443},
        "segments": [{"name": "s", "cidr": "10.75.0.0/24"}],
        "nodes": [{"name": "n", "image": "debian-12-cloud@v1",
                   "requires": ["linux"], "net": "s",
                   "listen": [{"port": "${P:8443}", "proto": "tcp"}]}],
    }}
    ir, _ = expand_twin_config_raw(raw)
    assert ir["layers"]["apps"][0]["listen"] == [{"port": 8443, "proto": "tcp"}]
    # ...while a NON-numeric literal is still refused, loudly, by field name.
    raw["twin_config"]["nodes"][0]["listen"] = [{"port": "eighty", "proto": "tcp"}]
    with pytest.raises(TwinConfigShapeError, match="listen"):
        expand_twin_config_raw(raw)


def test_the_declared_vocabulary_agrees_with_the_probe_that_consumes_it():
    """LES-037: the expander may not import `axrange.fidelity`, and the probe
    may not import `axrange.twinconfig` -- so their two independent
    port/transport vocabularies are asserted to agree HERE, in the one place
    that can see both. A declared listen the probe cannot parse is a fact
    published for nobody."""
    from axrange.fidelity.probe import parse_listen_port
    from axrange.twinconfig.expand import LISTEN_TRANSPORTS, _MAX_PORT

    for transport in LISTEN_TRANSPORTS:
        assert parse_listen_port(f"443/{transport}") == (443, transport)
    # The probe accepts EXACTLY these, no more.
    for other in ("sctp", "icmp", "", "tcp6"):
        assert parse_listen_port(f"443/{other}") is None
        assert other not in LISTEN_TRANSPORTS
    # ...and the same range on both sides.
    assert parse_listen_port(f"{_MAX_PORT}/tcp") == (_MAX_PORT, "tcp")
    assert parse_listen_port(f"{_MAX_PORT + 1}/tcp") is None
    assert parse_listen_port("0/tcp") is None


def test_a_listen_declaring_app_bound_to_two_hosts_is_refused_at_emit(manifests):
    """review S4. `_emit_apps` takes the FIRST binding and silently drops the
    rest, so a second `{app, host}` entry provisions nothing while looking
    like it does. `App.listen` makes that newly WRONG rather than merely
    incomplete: the declared set fans out over EVERY binding, so a multi-bound
    app would publish ports for a host that installs nothing -- the twin's own
    inventory claiming a service it was never asked to stand up."""
    raw = _minimal_config({"web": [{"port": 80, "proto": "tcp"}]})
    ir_dict, _params = expand_twin_config_raw(raw)
    ir_dict["bindings"].append({"app": "web-app", "host": "bare"})
    parsed = parse_ir(text=_dump_yaml(ir_dict))
    with pytest.raises(Exception) as e:
        build_plan(parsed.ir, manifests, ClusterContext.single_node("px1"),
                   raw_ir=parsed.raw)
    msg = str(e.value)
    assert "web-app" in msg and "2 hosts" in msg
    assert "bare" in msg and "web" in msg
    assert "listen" in msg


def test_a_multi_bound_app_without_listen_still_plans(manifests):
    """The refusal above is NARROW on purpose, and this pins the narrowness.

    Multi-bound apps exist in shipped content -- `target-environments/
    enterprise-multisite/scenario.yaml` binds `db-engine`, `etl-runner` and
    `branch-file` to two hosts each, deliberately per their own comments --
    and only the first binding of each has ever been provisioned. That is a
    real pre-existing defect, but refusing it here would make a shipped
    scenario un-plannable, which is a bigger and different change than this
    build (LES-185, from the other direction: shipped content is what caught
    the first, broader cut of this refusal).
    """
    raw = _minimal_config()   # neither node declares listen:
    ir_dict, _params = expand_twin_config_raw(raw)
    ir_dict["bindings"].append({"app": "web-app", "host": "bare"})
    parsed = parse_ir(text=_dump_yaml(ir_dict))
    plan = build_plan(parsed.ir, manifests, ClusterContext.single_node("px1"),
                      raw_ir=parsed.raw)
    plan.freeze()
    provisioned = {
        op.scope["host"] for op in plan.ops.values() if op.op_name == "provision-app"
    }
    # Still only the FIRST binding -- documented, unchanged, and recorded as
    # a gap rather than silently repaired here.
    assert provisioned == {"web"}


def test_shipped_multi_bound_apps_still_plan(manifests):
    """The same property against the real content that found it (LES-185):
    `enterprise-multisite` must keep planning."""
    scenario = (
        Path(__file__).resolve().parents[2]
        / "catalog" / "target-environments" / "enterprise-multisite" / "scenario.yaml"
    )
    if not scenario.is_file():
        pytest.skip("shipped scenario absent")
    parsed = parse_ir(path=str(scenario))
    plan = build_plan(
        parsed.ir, manifests, ClusterContext.single_node("px1"),
        raw_ir=parsed.raw, scenario_dir=str(scenario.parent),
    )
    plan.freeze()
    assert plan.ops


def test_listen_is_normalized_to_a_lowercase_transport():
    ir, _ = expand_twin_config_raw(_minimal_config({"bare": [{"port": 53, "proto": "UDP"}]}))
    apps = {a["id"]: a for a in ir["layers"]["apps"]}
    assert apps["bare-app"]["listen"] == [{"port": 53, "proto": "udp"}]


def test_a_declared_listen_set_moves_the_ir_hash():
    """The pin tests in `tests/test_twinconfig_catalog.py` say every shipped
    config's `ir_hash`/`plan_id` is unmoved. That is only meaningful if a
    declared set WOULD move it -- otherwise a regression that dropped
    `App.listen` from the expanded IR entirely would keep all six pins green
    while silently un-wiring the whole feature.

    It is also the reason the deploy gate re-confirms by `ir_hash`: editing a
    declaration is a real content change, not an annotation.
    """
    from axrange.planner.dag import hash_ir

    none = hash_ir(expand_twin_config_raw(_minimal_config())[0])
    p80 = hash_ir(expand_twin_config_raw(
        _minimal_config({"bare": [{"port": 80, "proto": "tcp"}]}))[0])
    p81 = hash_ir(expand_twin_config_raw(
        _minimal_config({"bare": [{"port": 81, "proto": "tcp"}]}))[0])
    assert none != p80, "declaring a listen set must change the IR"
    assert p80 != p81, "changing the declared PORT must change the IR"


# ===========================================================================
# S1 -- emission: `node_listen` on clone-vm, and ONLY when declared
# ===========================================================================
def _clone_inputs(plan) -> dict[str, dict]:
    return {
        op.scope["host"]: op.inputs_resolved
        for op in plan.ops.values() if op.op_name == "clone-vm"
    }


def test_clone_vm_carries_the_declared_listen_set(manifests):
    plan = _plan_from_raw(
        _minimal_config({"web": [{"port": 80, "proto": "tcp"}]}), manifests,
    )
    assert _clone_inputs(plan)["web"]["node_listen"] == [{"port": 80, "proto": "tcp"}]


def test_clone_vm_omits_node_listen_entirely_when_nothing_is_declared(manifests):
    """The KEY must be absent, not `None`. `plandiff` compares `ABSENT`
    against `None` as a difference, so an unconditional key would classify
    every standing twin's `clone-vm` as Mutated and make `te deploy --update`
    refuse on content that has not changed at all."""
    plan = _plan_from_raw(_minimal_config(), manifests)
    for host, inputs in _clone_inputs(plan).items():
        assert "node_listen" not in inputs, host


def test_several_apps_on_one_host_merge_and_dedupe_their_declarations():
    """`_declared_listen_by_host`'s own documented contract, and a path the
    twin-config expander can never reach (it synthesizes exactly ONE app per
    node) -- so a hand-authored IR is the only way to exercise it, and
    without this test it is untested code behind an unreachable shape
    (LES-001's family).

    Four properties in one join, each of which has failed somewhere before:
    entries merge across bindings in declaration order; `(port, proto)`
    de-dupes CASE-INSENSITIVELY (`443/TCP` and `443/tcp` are one fact, not
    two, or the scorecard double-counts a declaration); a `proto`-less entry
    defaults to `tcp`; and a `port`-less entry is SKIPPED rather than taking
    the deploy down over an informational field.
    """
    from axrange.planner.pass1_emit import _declared_listen_by_host

    parsed = parse_ir(text="""
scenario: {id: multi-app, version: "1", schema_version: 1}
layers:
  infra:
    networks: [{id: seg, cidr: 10.93.0.0/24, zone_type: simple}]
    nodes:
      - {id: h1, kind: host, image: debian-12-cloud@v1, requires: [linux],
         resources: {cpu: 1, ram: 1Gi, disk: 10Gi}, nets: [{net: seg}]}
      - {id: h2, kind: host, image: debian-12-cloud@v1, requires: [linux],
         resources: {cpu: 1, ram: 1Gi, disk: 10Gi}, nets: [{net: seg}]}
  apps:
    - {id: a1, listen: [{port: 80, proto: tcp}, {port: 443, proto: TCP}]}
    - {id: a2, listen: [{port: 443, proto: tcp}, {port: 53, proto: udp}, {port: 9}]}
    - {id: a3, listen: [{proto: tcp}]}
bindings:
  - {app: a1, host: h1}
  - {app: a2, host: h1}
  - {app: a3, host: h1}
""")
    merged = _declared_listen_by_host(parsed.ir)
    assert merged == {"h1": [
        {"port": 80, "proto": "tcp"},
        {"port": 443, "proto": "tcp"},    # a1's `TCP` and a2's `tcp` are ONE
        {"port": 53, "proto": "udp"},
        {"port": 9, "proto": "tcp"},      # no `proto:` -> tcp
    ]}
    # a3 declared nothing usable, and h2 is bound to nothing at all: neither
    # may appear with an empty list (`None`/absent is the honest answer).
    assert "h2" not in merged


def test_a_standing_twin_with_no_declared_listen_still_diffs_clean(manifests):
    """The regression the test above protects, measured end to end through
    the real `diff_plan`: persisted rows minted BEFORE this field existed
    (simulated by stripping the key, which is also what LES-096's resume path
    leaves behind) must classify Unchanged, never Mutated."""
    import json

    from axrange.planner.plandiff import diff_plan
    from axrange.planner.state import OpStatus, barrier_to_row, op_to_row
    from axrange.planner.dag import OpInstance

    plan = _plan_from_raw(_minimal_config(), manifests, plan_id="standing")
    rows = []
    for node in plan.all_nodes().values():
        row = (op_to_row(node, deploy_id="d") if isinstance(node, OpInstance)
               else barrier_to_row(node, deploy_id="d"))
        inputs = json.loads(row.inputs_resolved_json)
        inputs.pop("node_listen", None)   # a pre-field deploy has no such key
        row.inputs_resolved_json = json.dumps(inputs)
        row.status = OpStatus.COMPLETED
        rows.append(row)

    diff = diff_plan(plan, rows)
    assert not diff.mutated, diff.mutations
    assert diff.is_noop


def test_changing_a_declared_listen_set_is_a_named_update_refusal(manifests):
    """The POSITIVE half of the `--update` story (review S9). The negative
    half (a twin that declares nothing diffs clean) is above; this pins what
    happens when a declaration actually CHANGES on a standing twin: exactly
    that host's `clone-vm` classifies Mutated, on field `node_listen`, with
    both values in the refusal -- and `--reapply` declines it by name.

    It is a deliberate refusal, not a gap: the published inventory is read
    back off the persisted `clone-vm` row, so silently tolerating the change
    would leave `te inventory` and the scorecard reporting the OLD declared
    set against a twin that now claims a different one. If a future slice
    ever widens the tolerance, this test is what makes that a decision.
    """
    from axrange.planner.plandiff import (
        diff_plan,
        reapply_ineligibility_reason,
        reapplyable_ops,
    )
    from axrange.planner.state import OpStatus, barrier_to_row, op_to_row
    from axrange.planner.dag import OpInstance

    standing = _plan_from_raw(
        _minimal_config({"web": [{"port": 80, "proto": "tcp"}]}),
        manifests, plan_id="standing",
    )
    rows = []
    for node in standing.all_nodes().values():
        row = (op_to_row(node, deploy_id="d") if isinstance(node, OpInstance)
               else barrier_to_row(node, deploy_id="d"))
        row.status = OpStatus.COMPLETED
        rows.append(row)

    # The edit: `web` now also declares 443/tcp. `plan_id` is PINNED, exactly
    # as `--update` pins the standing deploy's (LES-126).
    edited = _plan_from_raw(
        _minimal_config({"web": [{"port": 80, "proto": "tcp"},
                                 {"port": 443, "proto": "tcp"}]}),
        manifests, plan_id="standing",
    )
    diff = diff_plan(edited, rows)

    mutated = {edited.get(op_id).scope.get("host"): op_id for op_id in diff.mutated}
    assert set(mutated) == {"web"}, diff.mutations
    op_id = mutated["web"]
    assert edited.get(op_id).op_name == "clone-vm"
    assert set(diff.mutations[op_id]) == {"node_listen"}
    old_v, new_v = diff.mutations[op_id]["node_listen"]
    assert old_v == [{"port": 80, "proto": "tcp"}]
    assert new_v == [{"port": 80, "proto": "tcp"}, {"port": 443, "proto": "tcp"}]

    # `--reapply` declines it, and says why in terms of the op it DOES cover.
    assert reapplyable_ops(edited, diff) == frozenset()
    reason = reapply_ineligibility_reason("clone-vm", ["node_listen"])
    assert reason and "run-script" in reason


# ===========================================================================
# S1 -- the published inventory
# ===========================================================================
def test_inventory_publishes_the_declared_listen_set(tmp_path, manifests):
    plan = _plan_from_raw(
        _minimal_config({
            "web": [{"port": 80, "proto": "tcp"}],
            "bare": [{"port": 5432, "proto": "tcp"}, {"port": 53, "proto": "udp"}],
        }),
        manifests,
    )
    store, deploy_id = _deploy(plan, tmp_path)
    try:
        inv = build_twin_inventory(store, deploy_id, manifests=manifests)
    finally:
        store.close()
    by_id = {h.host_id: h for h in inv.hosts}
    assert by_id["web"].listen == [HostListen(port=80, proto="tcp")]
    assert by_id["bare"].listen == [
        HostListen(port=5432, proto="tcp"), HostListen(port=53, proto="udp"),
    ]
    # `--json` (the CLI) and the API DTO read the SAME dict shape.
    hosts = {h["host_id"]: h for h in inv.to_dict()["hosts"]}
    assert hosts["web"]["listen"] == [{"port": 80, "proto": "tcp"}]


def test_a_host_that_declares_nothing_publishes_none_never_an_empty_list(
    tmp_path, manifests,
):
    """`None` = "this twin makes no claim about what this host serves";
    `[]` = "it claims to serve nothing". Every twin deployed before this
    field existed can only honestly say the first."""
    plan = _plan_from_raw(_minimal_config({"web": [{"port": 80, "proto": "tcp"}]}),
                          manifests)
    store, deploy_id = _deploy(plan, tmp_path)
    try:
        inv = build_twin_inventory(store, deploy_id, manifests=manifests)
    finally:
        store.close()
    by_id = {h.host_id: h for h in inv.hosts}
    assert by_id["bare"].listen is None
    assert by_id["bare"].listen != []


def test_inventory_degrades_to_none_on_a_deploy_that_predates_the_field(
    tmp_path, manifests,
):
    """LES-096: an already-`completed` `clone-vm` never sees a newly-added
    input, so a resumed deploy publishes the field as absent FOREVER until a
    `--fresh` redeploy. It must degrade to `None`, never raise."""
    plan = _plan_from_raw(_minimal_config({"web": [{"port": 80, "proto": "tcp"}]}),
                          manifests)
    store, deploy_id = _deploy(plan, tmp_path)
    try:
        import json

        with store._conn as conn:      # noqa: SLF001 -- simulating an old row
            for row in store.ops_for_deploy(deploy_id):
                if row.op_name != "clone-vm":
                    continue
                inputs = json.loads(row.inputs_resolved_json)
                inputs.pop("node_listen", None)
                conn.execute(
                    "UPDATE ops SET inputs_resolved_json = ? "
                    "WHERE deploy_id = ? AND op_id = ?",
                    (json.dumps(inputs), deploy_id, row.op_id),
                )
        inv = build_twin_inventory(store, deploy_id, manifests=manifests)
    finally:
        store.close()
    assert all(h.listen is None for h in inv.hosts)


def test_a_malformed_persisted_listen_entry_is_skipped_not_raised(
    tmp_path, manifests,
):
    """A published contract must degrade, not crash, on a row it cannot
    parse -- the same posture every other `.get(...)` read in that module
    takes."""
    import json

    plan = _plan_from_raw(_minimal_config({"web": [{"port": 80, "proto": "tcp"}]}),
                          manifests)
    store, deploy_id = _deploy(plan, tmp_path)
    try:
        with store._conn as conn:      # noqa: SLF001
            for row in store.ops_for_deploy(deploy_id):
                if row.op_name != "clone-vm" or row.scope_json.find("web") < 0:
                    continue
                inputs = json.loads(row.inputs_resolved_json)
                inputs["node_listen"] = [
                    {"proto": "tcp"}, "80/tcp", {"port": "eighty", "proto": "tcp"},
                    {"port": 443, "proto": "tcp"},
                ]
                conn.execute(
                    "UPDATE ops SET inputs_resolved_json = ? "
                    "WHERE deploy_id = ? AND op_id = ?",
                    (json.dumps(inputs), deploy_id, row.op_id),
                )
        inv = build_twin_inventory(store, deploy_id, manifests=manifests)
    finally:
        store.close()
    by_id = {h.host_id: h for h in inv.hosts}
    assert by_id["web"].listen == [HostListen(port=443, proto="tcp")]


def test_api_inventory_dto_carries_the_declared_listen_set(tmp_path, manifests):
    """The API endpoint and `te inventory --json` publish the same data --
    the divergence `planner/inventory.py` records (`node` vs `vm_name`) is
    the reason this parity is asserted rather than assumed."""
    from axrange.api import service
    from axrange.api.settings import ApiSettings

    plan = _plan_from_raw(_minimal_config({"web": [{"port": 80, "proto": "tcp"}]}),
                          manifests)
    store, deploy_id = _deploy(plan, tmp_path)
    store.close()
    settings = ApiSettings(
        db_path=str(tmp_path / "state.db"), images_root=str(FIXT_ROOT),
        adapter_config="/nonexistent.conf",
    )
    dto = service.deploy_inventory(settings, deploy_id)
    by_id = {h.host_id: h for h in dto.hosts}
    assert [(li.port, li.proto) for li in by_id["web"].listen] == [(80, "tcp")]
    assert by_id["bare"].listen is None


def test_te_inventory_prints_the_declared_listen_set(tmp_path, manifests, capsys):
    from axrange.cli.main import main

    plan = _plan_from_raw(
        _minimal_config({"web": [{"port": 80, "proto": "tcp"},
                                 {"port": 443, "proto": "tcp"}]}),
        manifests,
    )
    store, deploy_id = _deploy(plan, tmp_path)
    store.close()
    rc = main(["te", "inventory", deploy_id, "--db", str(tmp_path / "state.db")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "listen=80/tcp,443/tcp" in out
    # A host that declares nothing prints no `listen=` at all -- never an
    # invented empty one.
    bare_line = next(ln for ln in out.splitlines() if ln.strip().startswith("- bare "))
    assert "listen=" not in bare_line


# ===========================================================================
# S1 -- the scorecard reports the delta and decides nothing with it
# ===========================================================================
def test_declared_vs_observed_reports_both_directions(tmp_path):
    from tests.fidelity_support import MINI_CAST, build_mini_deploy
    from axrange.fidelity.runner import scorecard_for_deploy

    store, deploy_id = build_mini_deploy(tmp_path, deploy_id="declared-delta")
    try:
        doc = scorecard_for_deploy(
            store, deploy_id, reference_dir=MINI_CAST, probe=False,
        ).to_dict()
    finally:
        store.close()
    row5 = doc["rows"][4]["measured"]
    row4 = doc["rows"][3]["measured"]
    # web-tier declares 80 only, of an observed {80, 443}; db-tier declares
    # 5432 (observed) + 8080 (never observed).
    assert row5["declared_listen_hosts"] == 3
    assert row5["declared_listen_facts"] == 4
    assert {(f["host"], f["port"]) for f in row5["declared_not_observed_facts"]} == {
        ("db-tier", 8080),
    }
    assert {(f["host"], f["port"]) for f in row5["observed_not_declared_facts"]} == {
        ("web-tier01", 443), ("web-tier02", 443),
    }
    # Row 4 publishes the same fragment (it is one helper, not two).
    for key in ("declared_listen_hosts", "declared_not_observed", "observed_not_declared"):
        assert row4[key] == row5[key]


def test_the_declared_set_changes_no_verdict(tmp_path):
    """The whole point of row 5 is that a REAL protocol response is the
    evidence. Declaring a port must not move a verdict one step closer to
    passing -- so the same twin, scored with and without its declarations,
    produces identical row verdicts."""
    from tests.fidelity_support import MINI_CAST, build_mini_deploy
    import json

    from axrange.fidelity.runner import scorecard_for_deploy

    def _verdicts(deploy_id, *, strip: bool) -> list[str]:
        work = tmp_path / deploy_id
        work.mkdir()
        store, did = build_mini_deploy(work, deploy_id=deploy_id)
        try:
            if strip:
                with store._conn as conn:   # noqa: SLF001
                    for row in store.ops_for_deploy(did):
                        if row.op_name != "clone-vm":
                            continue
                        inputs = json.loads(row.inputs_resolved_json)
                        inputs.pop("node_listen", None)
                        conn.execute(
                            "UPDATE ops SET inputs_resolved_json = ? "
                            "WHERE deploy_id = ? AND op_id = ?",
                            (json.dumps(inputs), did, row.op_id),
                        )
            doc = scorecard_for_deploy(
                store, did, reference_dir=MINI_CAST, probe=False,
            ).to_dict()
        finally:
            store.close()
        return [r["verdict"] for r in doc["rows"]], doc["verdict"]

    declared, declared_doc = _verdicts("with-declared", strip=False)
    stripped, stripped_doc = _verdicts("no-declared", strip=True)
    assert declared == stripped
    assert declared_doc == stripped_doc


def _declared_only_reproduced():
    """A reproduced side that DECLARES two ports on one deployed host, built
    without a state store (the row functions are pure readers)."""
    from axrange.fidelity.rows import Reproduced
    from axrange.planner.inventory import HostInfo, HostListen

    return Reproduced(
        deploy_id="d",
        hosts=[HostInfo(
            host_id="web-tier01", vm_status="completed", role="web-tier",
            listen=[HostListen(port=80, proto="tcp"),
                    HostListen(port=443, proto="tcp")],
        )],
    )


def _reference_without_a_topology_export():
    """The mini-cast bundle's twin config with its topology export declared
    ABSENT -- the shape `load_reference_bundle` produces for a bundle that
    ships no `*.topology.json` at all."""
    import dataclasses

    from axrange.fidelity.reference import ABSENT, load_reference_bundle
    from tests.fidelity_support import MINI_CAST

    return dataclasses.replace(
        load_reference_bundle(MINI_CAST),
        topology_export=ABSENT, observed_nodes=[], observed_segments=[],
        observed_edges=[],
    )


# All four cells of the (declared x observed) present/absent matrix. Three of
# them are ABSENCES, and an absence must never be published as a finding
# (LES-089/LES-220) -- the failure mode is asymmetric and easy to miss in
# only one direction, which is why the matrix is enumerated rather than
# spot-checked.
def test_declared_present_observed_present_is_the_only_scoring_cell():
    from axrange.fidelity.reference import load_reference_bundle
    from axrange.fidelity.rows import GateInfo, declared_vs_observed_listen
    from tests.fidelity_support import MINI_CAST

    out = declared_vs_observed_listen(
        load_reference_bundle(MINI_CAST), _declared_only_reproduced(), GateInfo(),
    )
    assert out["declared_listen_hosts"] == 1
    # 80 was observed for this group, 443 was observed on the SIBLING host of
    # the same (role, segment) group -- both resolve to this candidate.
    assert out["declared_not_observed"] == 0
    assert "reported as absent" not in out["declared_note"]


def test_a_twin_that_declares_nothing_reports_absence_not_a_mismatch():
    """LES-220: name the state after the mechanism. A twin with no
    declarations at all (every twin deployed before this field existed) must
    report zero counts and say why -- not report its whole observed listen
    set as `observed_not_declared`, which reads as a finding."""
    from axrange.fidelity.reference import load_reference_bundle
    from axrange.fidelity.rows import GateInfo, Reproduced, declared_vs_observed_listen
    from tests.fidelity_support import MINI_CAST

    # A REAL reference (the observed side is fully populated) against a
    # reproduced side that declares nothing: the comparison must report
    # ABSENCE, not turn every observed listen into a mismatch.
    out = declared_vs_observed_listen(
        load_reference_bundle(MINI_CAST), Reproduced(deploy_id="d"), GateInfo(),
    )
    assert out["declared_listen_hosts"] == 0
    assert out["observed_not_declared"] == 0
    assert "no deployed host declares a listen set" in out["declared_note"]
    assert out["declared_not_observed_facts"] == []


def test_a_reference_with_no_observed_side_reports_absence_not_a_mismatch():
    """The MIRROR of the test above, and the one that actually bit (review
    M1): with no topology export the observed set is empty BY CONSTRUCTION,
    so differencing against it would publish every declared port as
    `declared_not_observed` -- a manufactured finding that grows with how
    thoroughly the twin was authored."""
    from axrange.fidelity.rows import GateInfo, declared_vs_observed_listen

    rep = _declared_only_reproduced()
    out = declared_vs_observed_listen(
        _reference_without_a_topology_export(), rep, GateInfo(),
    )
    # The DECLARED side is still reported -- it is a fact about the twin and
    # does not depend on the reference.
    assert out["declared_listen_hosts"] == 1
    assert out["declared_listen_facts"] == 2
    # ...but nothing is called a mismatch.
    assert out["declared_not_observed"] == 0
    assert out["observed_not_declared"] == 0
    assert out["declared_not_observed_facts"] == []
    assert "no topology export" in out["declared_note"]
    assert "never as a mismatch" in out["declared_note"]


def test_neither_side_present_reports_both_absences():
    from axrange.fidelity.rows import GateInfo, Reproduced, declared_vs_observed_listen

    out = declared_vs_observed_listen(
        _reference_without_a_topology_export(), Reproduced(deploy_id="d"), GateInfo(),
    )
    assert out["declared_listen_hosts"] == 0
    assert out["declared_not_observed"] == out["observed_not_declared"] == 0
    assert "AND" in out["declared_note"]   # both absences named, not just one


def test_every_branch_publishes_the_same_key_set():
    """The scorecard document is meant to be DIFFED (`schema.py` publishes
    every key always, for exactly that reason), so a `measured` fragment
    whose SHAPE depends on its content is a diff full of key churn."""
    from axrange.fidelity.reference import load_reference_bundle
    from axrange.fidelity.rows import GateInfo, Reproduced, declared_vs_observed_listen
    from tests.fidelity_support import MINI_CAST

    real = load_reference_bundle(MINI_CAST)
    empty_ref = _reference_without_a_topology_export()
    declared, bare = _declared_only_reproduced(), Reproduced(deploy_id="d")
    shapes = [
        set(declared_vs_observed_listen(ref, rep, GateInfo()))
        for ref, rep in ((real, declared), (real, bare),
                         (empty_ref, declared), (empty_ref, bare))
    ]
    assert all(shape == shapes[0] for shape in shapes), shapes
    assert {"declared_not_observed_facts", "declared_not_observed_facts_count",
            "observed_not_declared_facts", "observed_not_declared_facts_count",
            } <= shapes[0]


# ===========================================================================
# S3 -- a real DC and joined members, from a TWIN CONFIG
# ===========================================================================
def _ad_plan(manifests, tmp_path, *, config_id="ad-cast", mutate=None):
    """Expand + plan the `ad-cast` fixture, optionally mutating its raw dict
    first (for the negative cases)."""
    from axrange.twinconfig.registry import load_twin_config_raw

    if mutate is None:
        out = tmp_path / f"{config_id}.ir.yaml"
        materialize_twin_config(config_id, catalog_dir=FIXT_TWIN_CONFIGS, out_path=out)
        parsed = parse_ir(path=str(out))
    else:
        raw, _path = load_twin_config_raw(config_id, catalog_dir=FIXT_TWIN_CONFIGS)
        raw = copy.deepcopy(raw)
        mutate(raw["twin_config"])
        ir_dict, _params = expand_twin_config_raw(raw)
        parsed = parse_ir(text=_dump_yaml(ir_dict))
    plan = build_plan(
        parsed.ir, manifests, ClusterContext.single_node("px1"),
        raw_ir=parsed.raw, scenario_dir=str(AD_CAST_DIR),
    )
    plan.freeze()
    return plan


def _ops(plan, name):
    return [o for o in plan.ops.values() if o.op_name == name]


def test_ad_cast_validates(manifests, tmp_path):
    """The fixture is real content, not a shape that only the planner
    tolerates (LES-185's rule, applied to a fixture that stands in for
    shipped cast content)."""
    from axrange.validator import validate_full

    out = tmp_path / "ad-cast.ir.yaml"
    materialize_twin_config("ad-cast", catalog_dir=FIXT_TWIN_CONFIGS, out_path=out)
    result = validate_full(str(out), manifests)
    assert not result.errors, [f"{f.rule_id}: {f.message}" for f in result.errors]


def test_twin_config_dc_emits_provision_wait_and_the_domain_ready_barrier(
    manifests, tmp_path,
):
    """The work order's §9.1 test 5, from the TWIN-CONFIG path (the shipped
    `role: adds` precedent is a hand-authored `twin-configs/` config; a cast
    authors it through the expander)."""
    plan = _ad_plan(manifests, tmp_path)

    prov = _ops(plan, "provision-app")
    assert len(prov) == 1
    assert prov[0].scope == {"app": "axdc01-app", "host": "axdc01"}
    assert prov[0].inputs_resolved["install_actions"] == [{"role": "adds"}]
    assert prov[0].inputs_resolved["domain_fqdn"] == "corp.axr.lab"
    assert prov[0].inputs_resolved["os_family"] == "windows"

    wdr = _ops(plan, "wait-domain-ready")
    assert len(wdr) == 1
    assert wdr[0].scope == {"domain": "corp.axr.lab", "dc_app": "axdc01-app"}

    barriers = [b for b in plan.barriers.values() if b.barrier_name == "domain-ready"]
    assert len(barriers) == 1
    assert barriers[0].scope == {"domain": "corp.axr.lab"}
    # provision-app -> wait-domain-ready -> domain-ready all resolve.
    assert prov[0].op_id in set(plan.predecessors(wdr[0].op_id))
    assert wdr[0].op_id in set(plan.predecessors(barriers[0].op_id))


def test_the_dc_declares_the_observed_listen_set_under_the_lab_realm(
    manifests, tmp_path,
):
    """D4 (Authority: user): the realm is the lab default `corp.axr.lab`, NOT
    the observed `axellio.dom` -- only the DC's short hostname follows the
    observed `axdc01`. The declared ports are the observed ones, minus the
    dynamic RPC allocations (GAP-064), which are never faked."""
    plan = _ad_plan(manifests, tmp_path)
    declared = _clone_inputs(plan)["axdc01"]["node_listen"]
    ports = {(li["port"], li["proto"]) for li in declared}
    assert {(88, "tcp"), (389, "tcp"), (445, "tcp"), (3268, "tcp"),
            (53, "tcp"), (53, "udp"), (123, "udp")} <= ports
    assert not any(p >= 49152 for p, _ in ports)
    prov = _ops(plan, "provision-app")[0]
    assert prov.inputs_resolved["domain_fqdn"] == "corp.axr.lab"


def _ad_cast_dc_ip() -> str:
    """`axdc01`'s auto-allocated address, read from the expanded IR.

    ONE derivation shared by the two join tests: they assert different things
    about the same address (the planner threads it; the renderer bakes it in
    as a literal), and two copies of a nested comprehension over
    `nodes[].nets[]` is exactly where a `for n in ... for n in n.nets`
    shadowing slips in.
    """
    ir_dict, _params = expand_twin_config("ad-cast", catalog_dir=FIXT_TWIN_CONFIGS)
    ir = parse_ir(text=_dump_yaml(ir_dict)).ir
    dc = next(n for n in ir.layers.infra.nodes if n.id == "axdc01")
    ip = next((leg.ip for leg in dc.nets if leg.ip), None)
    assert ip, "the DC must have an allocated address to point DNS at"
    return ip


def test_members_join_the_domain_pointing_dns_at_the_dc(manifests, tmp_path):
    """`join-host-to-domain` already points the member's DNS at the DC in the
    guest (`config_exec/render.py::render_join_to_domain` sets
    `Set-DnsClientServerAddress` on every physical, up NIC), so the twin
    needs no separate resolver for a join to resolve -- what it needs is for
    the planner to thread the DC's real address, which is what this asserts.
    """
    plan = _ad_plan(manifests, tmp_path)
    joins = _ops(plan, "join-host-to-domain")
    assert {o.scope["host"] for o in joins} == {"corp-workstation01", "corp-workstation02"}
    dc_ip = _ad_cast_dc_ip()
    for op in joins:
        assert op.inputs_resolved["dc_ip"] == dc_ip
        assert op.inputs_resolved["domain_fqdn"] == "corp.axr.lab"
        assert op.inputs_resolved["os_family"] == "windows"
        # Gated on the DC actually serving the domain.
        preds = set(plan.predecessors(op.op_id))
        assert any(
            plan.get(p) is not None
            and getattr(plan.get(p), "barrier_name", None) == "domain-ready"
            for p in preds
        )


def test_the_join_script_bakes_the_dc_ip_as_a_literal(manifests, tmp_path):
    """The test above proves the PLANNER threads the DC's allocated IP into
    `join-host-to-domain`'s inputs. This one carries it one step further, to
    the artifact that actually runs in the guest: `render_join_to_domain`
    must emit that IP as a LITERAL in the `Set-DnsClientServerAddress` call.

    Worth its own assertion because the two halves fail independently -- a
    correct `dc_ip` input reaching a renderer that never used it would leave
    every member resolving the domain against nothing, and the op inputs
    would look perfect in the state store while the join failed on-cluster.
    Containment matters too: the member must be handed an ADDRESS, never a
    name it would have to resolve to find the resolver.
    """
    from axrange.config_exec.render import render_join_to_domain

    plan = _ad_plan(manifests, tmp_path)
    dc_ip = _ad_cast_dc_ip()
    for op in _ops(plan, "join-host-to-domain"):
        script = render_join_to_domain(
            op.inputs_resolved["domain_fqdn"],
            dc_ip=op.inputs_resolved["dc_ip"], admin_password="pw",
        )
        dns_line = next(
            ln for ln in script.splitlines() if "Set-DnsClientServerAddress" in ln
        )
        assert f"'{dc_ip}'" in dns_line, dns_line
        assert "corp.axr.lab" not in dns_line, (
            "the resolver must be an address, not a name the guest resolves"
        )


def test_the_rdp_script_is_a_windows_run_script_after_host_ready(manifests, tmp_path):
    """3389/tcp is observed on four source hosts and is OFF by default. The
    enabling script is delivered by the EXISTING Windows `run-script` path
    (`powershell -File`), which is why it is PowerShell and not a POSIX
    heredoc -- the failure that killed the superseded responder approach."""
    plan = _ad_plan(manifests, tmp_path)
    scripts = _ops(plan, "run-script")
    assert {o.scope["host"] for o in scripts} == {
        "corp-workstation01", "corp-workstation02",
    }
    for op in scripts:
        assert op.inputs_resolved["os_family"] == "windows"
        content = op.inputs_resolved["script_content"]
        assert not content.startswith("#!"), "a Windows script must not carry a shebang"
        assert "fDenyTSConnections" in content
        assert "Enable-NetFirewallRule" in content
        # Fail-loud, not fire-and-forget (LES-001): the script asserts the
        # listener is actually up rather than trusting four exit codes.
        assert "throw" in content

        # The two properties that make the fail-loud check CORRECT rather
        # than merely present. Both were written deliberately and neither is
        # visible in a green op, so without a pin either can be reverted to
        # its obvious-looking form and the suite stays green while the twin
        # stops serving 3389 (locale case) or fails a correct host (race
        # case). Asserted against EXECUTABLE lines, since the script's own
        # comments name the rejected alternative.
        code = [
            ln for ln in content.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        fw = [ln for ln in code if "Enable-NetFirewallRule" in ln]
        assert len(fw) == 1, fw
        # Addressed by RESOURCE ID, not by the localized display name:
        # `-DisplayGroup 'Remote Desktop'` matches nothing on a non-English
        # image and enables zero rules with exit 0 -- a silent no-serve.
        assert "-Group '@FirewallAPI.dll,-28752'" in fw[0], fw[0]
        assert "-DisplayGroup" not in fw[0], fw[0]

        # A BOUNDED poll, not a single immediate read: TermService creates
        # the RDP-Tcp listener asynchronously after the registry flip, so an
        # immediate `Get-NetTCPConnection` is a race that fails a CORRECT
        # host. Bounded so it still fails loudly (LES-001) rather than
        # hanging when the host genuinely never listens.
        assert any("AddSeconds(" in ln for ln in code), "no deadline"
        loop = [ln for ln in code if ln.startswith("while ")]
        assert len(loop) == 1 and "Get-Date" in loop[0], loop
        assert any("Start-Sleep" in ln for ln in code), "a busy-wait, not a poll"
        assert any("Get-NetTCPConnection" in ln and "3389" in ln for ln in code)
        host = op.scope["host"]
        host_ready = next(
            b.op_id for b in plan.barriers.values()
            if b.barrier_name == "host-ready" and b.scope.get("host") == host
        )
        assert host_ready in set(plan.predecessors(op.op_id))


def test_role_adds_without_provides_domain_is_a_loud_emission_error(
    manifests, tmp_path,
):
    """§9.1 test 5's second half, through the twin-config path
    (`pass1_emit.py`'s `role: adds` + no `provides:` refusal)."""
    def _drop_provides(tc):
        for node in tc["nodes"]:
            node.pop("provides", None)

    with pytest.raises(Exception) as e:
        _ad_plan(manifests, tmp_path, mutate=_drop_provides)
    msg = str(e.value).lower()
    assert "provides" in msg and "domain" in msg


def test_a_non_adds_windows_role_is_still_refused_at_emit(manifests, tmp_path):
    """§9.1 test 6: the fail-loud paths hold. `role: iis` is declared by the
    fixture image's `install_support`, so this is the EMITTER refusing, not a
    capability check."""
    def _swap_role(tc):
        for node in tc["nodes"]:
            if node.get("install") == [{"role": "adds"}]:
                node["install"] = [{"role": "iis"}]

    with pytest.raises(Exception) as e:
        _ad_plan(manifests, tmp_path, mutate=_swap_role)
    assert "adds" in str(e.value).lower()
