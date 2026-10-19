"""Install an `aximprint cast` output into axRange's twin-config catalog under a STABLE id.

Why this exists (`docs/JOURNAL_corpapcn_fidelity.md`): a cast names its `twin_config.id` after
the PIT it came from (`pit-<hash>-<ts>`), which is right for provenance and fatal for iteration.
`axrange te deploy --update` finds "the standing deploy of THIS scenario" by id, so a re-cast —
whose PIT id always differs — looks like a brand-new scenario with no standing deploy, and every
iteration would be a full rebuild.

Rewriting the id to a fixed one is what makes the incremental path reachable at all. The original
PIT id is preserved in the description, so provenance is moved, never lost.

**This module holds the whole implementation** (2026-09-05). It began life as
`scripts/install_cast_as_twin.py`, which is now a thin wrapper around `install_cast()` — the same
function `aximprint install-cast` calls. One implementation, two entry points: the script is what
the CorpAPCN loop's own notes and runbooks invoke by path, and the subcommand is what an operator
reaches for; two copies of a body whose failure mode is *"a twin stands up looking healthy and
quietly under-generates"* is exactly the drift this file's own comments warn about elsewhere.

It lives in `synth/` rather than at the top level because it operates on a cast's output
directory and reuses `synth.personas.node_instances` — the same expansion rule the compiler used
to WRITE those files — so producer and installer share one rule by import rather than by
coincidence.

**Exit codes are three-valued and the split is load-bearing** (`LES-134`):

  * **0** — installed.
  * **2** — a usage or SHAPE problem: no cast directory, no `twin_config.yaml`, YAML that does
    not parse, a document with no `twin_config` mapping or no `id`. The caller pointed this at
    the wrong thing, and no cast would have worked.
  * **1** — a real FINDING in an otherwise well-formed cast: a traffic assignment naming a node
    that is not an instance of the twin, a persona with no file and no fallback, an `install:`
    script the config references and the folder does not carry. These are the failures that
    would otherwise land at `te plan` with the previous twin already destroyed.

A caller that only checks "nonzero" loses that distinction, which is the point of writing it
down: `2` means nothing was attempted, `1` means the cast is internally inconsistent.
"""
from __future__ import annotations

import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

import yaml

# ONE copy of the expansion rule, imported rather than restated. The cast now emits
# INSTANCE-level assignments where a node group's hosts are split between persona variants
# (`synth/personas.py::plan_assignments`), and this validates every emitted node id against the
# instance set it derives — so two copies of the rule drifting apart would fail a cast at install
# time with "is not a host in target twin", after the previous twin is already destroyed.
from aximprint.synth.personas import node_instances as _instances


class _ShapeError(Exception):
    """A usage/shape problem — exit 2. Never raised past `install_cast`, which reports it."""


def split_sensors(values: Iterable[str] | str | None) -> list[str]:
    """`--sensors` in either spelling, split by one rule.

    The script takes one comma-separated string (`--sensors A,B`) because that is what the
    fidelity loop's notes already invoke; the subcommand takes `nargs="+"` (`--sensors A B`)
    because that is the shape the CLI's own grid uses. Both funnel through here, so
    `--sensors A,B` also works on the subcommand and neither entry point owns a second idea of
    what a separator is.

    Order is preserved and duplicates are NOT removed: a tap binding's `sensors:` list is
    handed to axRange verbatim, and silently de-duplicating an operator's list would be this
    module editing a value it is only carrying.
    """
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    return [part.strip() for value in values for part in str(value).split(",") if part.strip()]


def _load_twin_config(cast: Path) -> dict:
    """The cast's own `twin_config.yaml`, or a `_ShapeError` naming what is wrong with it.

    A positive gate on every step, rather than letting a `FileNotFoundError` or a `KeyError`
    escape as a traceback: an operator running this between a destroy and a deploy needs to know
    whether they pointed at the wrong directory (fix: re-run with the right path) or produced a
    broken cast (fix: look at the cast).
    """
    src = cast / "twin_config.yaml"
    if not cast.is_dir():
        raise _ShapeError(f"{cast} is not a directory — expected an `aximprint cast --out` output")
    if not src.is_file():
        raise _ShapeError(
            f"{src} does not exist. A cast directory always carries `twin_config.yaml`; this "
            f"looks like the wrong directory, or a cast that did not finish")
    try:
        doc = yaml.safe_load(src.read_text())
    except yaml.YAMLError as exc:
        raise _ShapeError(f"{src}: not valid YAML ({exc})") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("twin_config"), dict):
        raise _ShapeError(f"{src}: expected a mapping with a `twin_config:` mapping in it")
    if not str(doc["twin_config"].get("id") or "").strip():
        raise _ShapeError(
            f"{src}: `twin_config.id` is empty. It is the PIT id this install rewrites, and it "
            f"is what the description preserves, so an absent one loses the cast's origin")
    return doc


def install_cast(cast: Path, root: Path, stable: str,
                 sensors: Sequence[str] = ()) -> int:
    """Install the cast at `cast` into `<root>/<stable>/`. Returns the exit code.

    Prints its report to stdout and its failures to stderr, because both entry points are CLIs
    and a second rendering of the same report is a second thing to keep in step.
    """
    cast, root = Path(cast), Path(root)
    sensors = list(sensors)
    try:
        doc = _load_twin_config(cast)
    except _ShapeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    tc = doc["twin_config"]
    origin = tc["id"]
    tc["id"] = stable
    tc["description"] = (
        f"[cast {origin}] " + str(tc.get("description", "")).strip()
    )

    out_dir = root / stable
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{stable}.yaml").write_text(
        yaml.safe_dump(doc, sort_keys=False, width=100)
    )

    # The traffic profile targets the twin by NAME, so it must follow the id too, or `tfc deploy`
    # resolves nothing. Same rewrite, same reason.
    #
    # It also has to be re-pointed from node GROUPS at node INSTANCES, which is the other half
    # and the one that actually blocks a deploy:
    #
    #   assignment 'node:generic-endpoint-10-1-1': node 'generic-endpoint-10-1-1'
    #   is not a host in target twin 'corpapcn-twin-0e4f7d58'
    #
    # The cast writes one assignment per node group, because a group is what its `twin_config`
    # holds; axRange deploys INSTANCES, and `tfc` binds a persona per host. This is the only
    # place that holds both documents, which is why the translation lives here and not as a
    # step somebody remembers -- the previous round's notes record exactly this expansion as
    # throwaway inline Python that never became a script, and `default_agents: false` was lost
    # the same way. An edit that exists only in the installed file is an edit the next re-cast
    # silently discards.
    tfc_src = cast / "tfc.yaml"
    n_assign = 0
    dropped_roles: list[str] = []
    if tfc_src.exists():
        tfc = yaml.safe_load(tfc_src.read_text())
        prof = tfc.get("traffic-profile") or {}
        if "target" in prof:
            prof["target"] = stable

        nodes = tc.get("nodes", []) or []
        group_instances = {str(n["name"]): _instances(n) for n in nodes}
        role_of_group = {str(n["name"]): n.get("role") for n in nodes}
        every_instance = {i for v in group_instances.values() for i in v}

        # A band whose own persona was never synthesized falls back to its role's BASE persona.
        # Without it that host generates nothing at all, which is worse than a failure: the
        # twin stands up looking healthy and quietly under-generates. The cast already applies
        # this rule; re-checking it here means a profile can never reference a persona file
        # that is not actually in the folder next to it.
        have = {f.stem for f in (cast / "personas").glob("*.yaml")} if (
            cast / "personas").is_dir() else set()

        def _persona_for(pid: str, role) -> str:
            if pid in have or not role:
                return pid
            base = f"cast-{role}"
            return base if base in have else pid

        expanded, roles_with_node_assignment = [], set()
        for a in prof.get("assignments", []) or []:
            target_node = a.get("node")
            if not target_node:
                expanded.append(a)
                continue
            role = role_of_group.get(str(target_node))
            if role:
                roles_with_node_assignment.add(role)
            # An already-expanded instance id passes through untouched, so re-running the
            # installer over its own output is a no-op rather than a second expansion.
            targets = group_instances.get(str(target_node), [str(target_node)])
            for inst in targets:
                expanded.append({**a, "node": inst,
                                 "persona": _persona_for(a.get("persona"), role)})

        # A ROLE-level assignment that survives alongside node-level ones for the SAME role
        # double-claims every host of it, and axRange permits exactly one persona instance per
        # host -- `tfc deploy` refuses the whole profile with "claimed by both 'role:X' and
        # 'node:Y'". A banded role must therefore go FULLY node-targeted; dropping the role
        # entry is what makes the split appliable rather than merely descriptive.
        kept = []
        for a in expanded:
            if a.get("role") and a["role"] in roles_with_node_assignment:
                dropped_roles.append(a["role"])
                continue
            kept.append(a)
        prof["assignments"] = kept
        n_assign = len(kept)

        # FAIL HERE, not after a twin is standing. Every emitted node id must be an instance the
        # expanded config actually contains; anything else is the exact refusal above, found at
        # a point where the remedy is free.
        bad = sorted({str(a["node"]) for a in kept
                      if a.get("node") and str(a["node"]) not in every_instance})
        if bad:
            print(f"ERROR: {len(bad)} traffic assignment(s) name a node that is not an instance "
                  f"of {stable}: {bad[:10]}{' ...' if len(bad) > 10 else ''}", file=sys.stderr)
            print("       `tfc deploy` would refuse with \"is not a host in target twin\".",
                  file=sys.stderr)
            return 1
        missing_p = sorted({str(a["persona"]) for a in kept
                            if have and a.get("persona") and str(a["persona"]) not in have})
        if missing_p:
            print(f"ERROR: {len(missing_p)} assignment(s) reference a persona with no file and "
                  f"no base-persona fallback: {missing_p}", file=sys.stderr)
            return 1

        (out_dir / "tfc-cast.yaml").write_text(yaml.safe_dump(tfc, sort_keys=False, width=100))

    # Personas ship WITH the twin config, in one folder, so the pair cannot drift apart -- a
    # twin whose personas live elsewhere can be deployed against the wrong ones.
    src_p = cast / "personas"
    n_personas = 0
    if src_p.is_dir():
        dst_p = out_dir / "personas"
        dst_p.mkdir(parents=True, exist_ok=True)
        for f in sorted(src_p.glob("*.yaml")):
            (dst_p / f.name).write_text(f.read_text())
            n_personas += 1

    # The tap plan targets the twin by NAME too, and for the same reason as `tfc`: left at the
    # cast's PIT id it resolves to no standing deploy. Copied rather than left behind because a
    # twin whose capture plan lives outside its folder is a twin somebody deploys untapped.
    tap_src = cast / "tap.yaml"
    n_bindings = 0
    if tap_src.exists():
        tap = yaml.safe_load(tap_src.read_text())
        plan = tap.get("tap-plan") or {}
        if "target" in plan:
            plan["target"] = stable
        bindings = plan.get("bindings") or []
        n_bindings = len(bindings)
        if sensors:
            for b in bindings:
                b["sensors"] = list(sensors)
        (out_dir / "tap-cast.yaml").write_text(yaml.safe_dump(tap, sort_keys=False, width=100))

    # INSTALL SCRIPTS. Not optional, and their absence is the worst-shaped failure this whole
    # module can produce.
    #
    # A cast's nodes carry `install: [{script: scripts/serve-<node>.sh}]`, and axRange reads
    # that file's TEXT at plan-BUILD time (`planner/pass1_emit.py::_read_script`, resolved
    # against the config dir). No validator rule checks that the file exists, so a twin
    # installed without this directory passes `te validate` with zero findings and then dies at
    # `te plan` with `EmissionError: cannot read script ...` and exit 2.
    #
    # That ordering is what makes it dangerous rather than merely annoying: the iteration
    # sequence is destroy -> cast -> install -> deploy, so the failure lands with the previous
    # twin already gone. The cast emits one script per served node group -- fourteen on the
    # current capture -- so a stale installed folder cannot cover them either.
    src_s = cast / "scripts"
    n_scripts = 0
    if src_s.is_dir():
        dst_s = out_dir / "scripts"
        dst_s.mkdir(parents=True, exist_ok=True)
        for f in sorted(src_s.iterdir()):
            if f.is_file():
                (dst_s / f.name).write_text(f.read_text())
                n_scripts += 1

    # Every `script:` the config references must now be on disk. Checked rather than assumed:
    # this is the one failure the caller cannot see until the old twin is already destroyed, so
    # it is worth catching here, where the remedy is free.
    missing = sorted({
        str(a["script"])
        for n in tc.get("nodes", []) or []
        for a in (n.get("install") or [])
        if isinstance(a, dict) and a.get("script")
        and not (out_dir / str(a["script"])).is_file()
    })
    if missing:
        print(f"ERROR: {len(missing)} install script(s) referenced by the twin config are not "
              f"in {out_dir}: {missing}", file=sys.stderr)
        print("       `te validate` would still pass; `te plan` would fail with exit 2.",
              file=sys.stderr)
        return 1

    # The IR topology bundle travels WITH the twin config, for the same reason the personas do:
    # a twin whose observed superset lives elsewhere can be viewed against the wrong capture.
    # Since `axr-topology/1.5` it also carries `cast_invocation`, which is what lets a consumer
    # holding only this folder re-run the cast that produced it instead of guessing at the flags.
    topo = cast / "topology.json"
    if topo.exists():
        (out_dir / f"{stable}.topology.json").write_text(topo.read_text())

    # THE PROVENANCE SIDECAR, for a stronger version of the same reason -- and it was missing
    # until 2026-09-04 (`docs/WORK_ORDER_reference_ingest.md` D9; found independently by
    # `axrange/docs/WORK_ORDER_fidelity_scorecard.md` §8.1 and its Console sibling, i.e. three
    # times before anyone shipped the one `if` that fixes it). Verified before writing it: no
    # installed cast on disk carried a sidecar, while every `cast` output directory did.
    #
    # What its absence cost is not tidiness. `twin_config` is `extra="forbid"`, so per-field
    # evidence CANNOT live in the YAML -- the sidecar is the only carrier there is. Without it
    # axRange re-derives every field's evidence class from the shape of the twin config, which
    # is precisely the imputed-as-observed hazard the class exists to prevent, and the
    # `population` block (observed vs emitted per node group, and the reconstruction identity
    # `placed_hosts == sum(observed)`) is unreadable, so the scorecard's population row has to
    # re-count rather than read.
    #
    # Copied VERBATIM, unlike the three documents above whose `target`/`id` are rewritten to the
    # stable name. Those are addressing; this is provenance, and its `pit_id` names the PIT this
    # twin was cast FROM -- rewriting that would destroy the only in-file record of the origin.
    # (Consequence for the consumer, recorded rather than papered over here: the installed file
    # is named `<stable>.provenance.json` but says `pit_id: pit-...` inside and carries no
    # `imprint_id` key at all. axRange's reader currently keys on `imprint_id`; the ruling is
    # that the compiler's shape is canonical and that reader adapts --
    # `docs/WORK_ORDER_reference_ingest.md` §8, cross-repo test seam.)
    prov = cast / "provenance.json"
    if prov.exists():
        (out_dir / f"{stable}.provenance.json").write_text(prov.read_text())

    print(f"{origin} -> {stable}")
    print(f"  {out_dir}/{stable}.yaml")
    if tfc_src.exists():
        note = f"{n_assign} assignment(s), node ids expanded to instances"
        if dropped_roles:
            note += f"; dropped {len(dropped_roles)} double-claiming role entr(y/ies) " \
                    f"{sorted(set(dropped_roles))}"
        print(f"  {out_dir}/tfc-cast.yaml  ({note})")
    if tap_src.exists():
        filled = f"sensors={sensors}" if sensors else "sensors UNFILLED — `tap deploy` will refuse"
        print(f"  {out_dir}/tap-cast.yaml  ({n_bindings} binding(s), {filled})")
    if topo.exists():
        print(f"  {out_dir}/{stable}.topology.json  (observed census + conversation graph)")
    if prov.exists():
        print(f"  {out_dir}/{stable}.provenance.json  (per-field evidence + population block)")
    else:
        # Named, not silent. A cast without a sidecar is a cast whose every evidence class the
        # consumer has to re-derive, and an operator should hear that at install time rather
        # than discover it in a scorecard that quietly re-counted.
        print("  WARNING: no provenance.json in the cast; per-field evidence will be absent",
              file=sys.stderr)
    if n_personas:
        print(f"  {out_dir}/personas/  ({n_personas} synthesized)")
        print(f"  -> run axRange with AXRANGE_PERSONAS_DIR={out_dir}/personas")
    if n_scripts:
        print(f"  {out_dir}/scripts/  ({n_scripts} install script(s); read at `te plan` time)")
    return 0
