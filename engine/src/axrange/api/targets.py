"""Cluster targets for `axrange serve` (Unified Console ruling §3.1,
`docs/reference/ui-consolidation/00-design-ruling.md`).

A **target** is one cluster the API can read: a name, the `adapter.conf` that
reaches it, and the state store holding its deploys. `state.db` has no
cluster column (schema v3 is kept -- the standing deploys must keep
working), so a deploy's cluster is simply *the target whose store it was
read from*. One store per target is therefore the rule; two targets sharing
one db would mis-attribute rows, and `load_targets_file` refuses it.

Config over flags: a YAML file named by `axrange serve --targets <file>` or
`AXRANGE_TARGETS`:

    targets:
      - name: dev45
        adapter_config: /etc/axrange/adapter.conf
        db: ~/.axrange/state.db
        nodes: [dev45]          # optional: PVE node names, for the placement check

The FIRST entry is the default target. With no file, today's single
`--adapter-config` + `--db` is the one implicit target, named after that
adapter.conf's `DEFAULT_NODE` (`default` when it has none or cannot load).

`settings_for_target` is the one seam every `?target=` read goes through: it
returns an `ApiSettings` copy pointing at that target's db + adapter.conf, so
the existing service functions run unchanged against it.
"""
from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from axrange.api.settings import ApiSettings, ApiSettingsError
from axrange.platform import DEFAULT_PLATFORM, UnknownPlatformError, check_platform

#: Name used for the implicit target when its adapter.conf names no node.
IMPLICIT_FALLBACK_NAME = "default"

#: Env vars that carry a cluster's IDENTITY. `load_config` lets env win over
#: the file, so with several targets any of these would silently point EVERY
#: target at one cluster (code review M1): a targets file refuses to start
#: while one is set.
CLUSTER_IDENTITY_ENV = (
    "PROXMOX_API_URL", "PROXMOX_API_TOKEN", "DEFAULT_NODE",
    "OVS_SSH_HOST", "OVS_SSH_USER", "OVS_SSH_KEY",
)

#: Env/setting for the implicit single target's protected deploys (comma list).
PROTECTED_ENV = "AXRANGE_PROTECTED_DEPLOYS"


@dataclass(frozen=True)
class Target:
    name: str
    adapter_config: Optional[str]
    db_path: str
    nodes: tuple[str, ...] = ()
    default: bool = False
    # True for the no-file, single-adapter.conf target.
    implicit: bool = False
    # Deploy ids (twins) no API write may touch without an explicit
    # `override_protection=<id>` (server-side protection, fix round 1).
    protected_deploys: tuple[str, ...] = ()
    # Further stores recording deploys on the SAME cluster (fix round 2): a
    # target is one cluster identity with one or more stores. `db_path` is
    # the primary -- where NEW deploys are written; reads search them all.
    extra_dbs: tuple[str, ...] = ()
    # `api/Spec.md` A4: the deploy platform this target casts onto, one of
    # the closed factory's set (`axrange.platform.PLATFORMS`); it selects the
    # adapter and the image-binding set `images/bindings/<platform>/`.
    # `DEFAULT_PLATFORM` when the targets file names none; an unknown value
    # is refused by name at load (serve start) and on write.
    platform: str = DEFAULT_PLATFORM

    @property
    def stores(self) -> tuple[str, ...]:
        return (self.db_path, *self.extra_dbs)


def store_labels(target: "Target") -> dict[str, str]:
    """path -> a short, stable label (the file name; the parent directory is
    prefixed only when two of the target's stores share a file name). The
    full path is published on `/api/targets`."""
    # Per store, the FEWEST trailing path components that no other store
    # shares (a file name alone, then parent/name, ...; the full path at worst).
    parts = [Path(p).parts for p in target.stores]
    out: dict[str, str] = {}
    for i, pp in enumerate(parts):
        others = [q for j, q in enumerate(parts) if j != i]
        depth = 1
        while depth < len(pp) and any(tuple(q[-depth:]) == tuple(pp[-depth:]) for q in others):
            depth += 1
        out[target.stores[i]] = "/".join(pp[-depth:]) if depth < len(pp) else str(Path(*pp))
    return out


@dataclass
class TargetFacts:
    """A target plus its (redacted) adapter.conf facts. `error` is set, and
    the facts blank, when that adapter.conf fails to load."""

    target: Target
    node: str = ""
    api_url: str = ""
    verify_tls: bool = False
    has_token: bool = False
    has_ssh_key: bool = False
    error: Optional[str] = None
    cfg: Any = field(default=None, repr=False)

    @property
    def known_nodes(self) -> tuple[str, ...]:
        """PVE node names this target is known to run on: the file's
        `nodes:` when given, else its adapter.conf `DEFAULT_NODE`."""
        if self.target.nodes:
            return self.target.nodes
        return (self.node,) if self.node else ()


class UnknownTargetError(LookupError):
    def __init__(self, name: str, known: list[str]) -> None:
        super().__init__(f"unknown target {name!r}")
        self.name = name
        self.known = known


def load_targets_file(path: str | Path) -> list[Target]:
    """Parse + validate a targets file. Raises `ApiSettingsError` naming the
    problem (a bad file fails `axrange serve` at startup, never later)."""
    from ruamel.yaml import YAML

    from axrange.ir.yaml_util import YamlLoadError, load_yaml_raw

    p = Path(path).expanduser()
    if not p.is_file():
        raise ApiSettingsError(f"targets file {p} does not exist")
    try:
        raw = load_yaml_raw(p, loader=YAML(typ="safe"))
    except YamlLoadError as e:
        raise ApiSettingsError(f"targets file {p}: {e}") from e
    entries = raw.get("targets") if isinstance(raw, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ApiSettingsError(
            f"targets file {p}: needs a non-empty top-level 'targets:' list"
        )
    out: list[Target] = []
    names: set[str] = set()
    dbs: dict[str, str] = {}
    for i, e in enumerate(entries):
        where = f"targets file {p}: targets[{i}]"
        if not isinstance(e, dict):
            raise ApiSettingsError(f"{where} must be a mapping")
        unknown = sorted(set(e) - {
            "name", "adapter_config", "db", "extra_dbs", "nodes", "protected_deploys",
            "platform",
        })
        if unknown:
            raise ApiSettingsError(f"{where}: unknown key(s) {unknown}")
        name = e.get("name")
        if not isinstance(name, str) or not name.strip() or "/" in name:
            raise ApiSettingsError(f"{where}: 'name' must be a non-empty string without '/'")
        if name in names:
            raise ApiSettingsError(f"{where}: duplicate target name {name!r}")
        db = e.get("db")
        if not isinstance(db, str) or not db.strip():
            raise ApiSettingsError(f"{where} ({name}): 'db' (a state.db path) is required")
        # Resolved, so `./s.db` and `/abs/s.db` compare equal (M1).
        db_path = str(Path(db).expanduser().resolve())
        if db_path in dbs:
            # One store per cluster is what makes "the target a row was read
            # from" mean "the cluster it is on" -- a shared db would silently
            # attribute one cluster's deploys to both.
            raise ApiSettingsError(
                f"{where} ({name}): db {db_path} is already target {dbs[db_path]!r}'s "
                "-- each target needs its own state store"
            )
        ac = e.get("adapter_config")
        if ac is not None and (not isinstance(ac, str) or not ac.strip()):
            raise ApiSettingsError(f"{where} ({name}): 'adapter_config' must be a path")
        if ac is None and len(entries) > 1:
            # Without it `load_config` falls back to /etc/axrange/adapter.conf
            # -- another target's cluster -- while reading THIS target's db
            # (code review M1: a write could land on the wrong cluster).
            raise ApiSettingsError(
                f"{where} ({name}): 'adapter_config' is required when the file "
                "names more than one target (the default search path would "
                "point this target at another cluster)"
            )
        extra_raw = e.get("extra_dbs") or []
        if not isinstance(extra_raw, list) or not all(isinstance(x, str) and x.strip() for x in extra_raw):
            raise ApiSettingsError(f"{where} ({name}): 'extra_dbs' must be a list of paths")
        extra: list[str] = []
        for x in extra_raw:
            xp = str(Path(x).expanduser().resolve())
            if xp == db_path or xp in extra or xp in dbs:
                owner = name if (xp == db_path or xp in extra) else dbs[xp]
                raise ApiSettingsError(
                    f"{where} ({name}): store {xp} is already listed (target {owner!r}) "
                    "-- each store belongs to exactly one target, once"
                )
            extra.append(xp)
        protected = e.get("protected_deploys") or []
        if not isinstance(protected, list) or not all(isinstance(x, str) and x for x in protected):
            raise ApiSettingsError(
                f"{where} ({name}): 'protected_deploys' must be a list of deploy ids"
            )
        nodes = e.get("nodes") or []
        if not isinstance(nodes, list) or not all(isinstance(n, str) and n for n in nodes):
            raise ApiSettingsError(f"{where} ({name}): 'nodes' must be a list of node names")
        try:
            platform = check_platform(e.get("platform"))
        except UnknownPlatformError as ex:
            raise ApiSettingsError(f"{where} ({name}): {ex}") from None
        names.add(name)
        dbs[db_path] = name
        for xp in extra:
            dbs[xp] = name
        out.append(Target(
            name=name, adapter_config=str(Path(ac).expanduser()) if ac else None,
            db_path=db_path, nodes=tuple(nodes), default=(i == 0),
            protected_deploys=tuple(protected), extra_dbs=tuple(extra),
            platform=platform,
        ))
    return out


def validate_stores(targets: list[Target]) -> None:
    """Startup check of every store (fix round 3, S1/S2): an EXTRA store must
    exist (a mistyped path would otherwise silently show nothing), and every
    store that exists must open read-only at the current schema. A missing
    PRIMARY is fine -- the first deploy creates it. Raises `ApiSettingsError`
    naming the store; nothing is written."""
    from axrange.planner.state import StateStore

    for t in targets:
        for path in t.stores:
            p = Path(path)
            if not p.is_file():
                if path != t.db_path:
                    raise ApiSettingsError(
                        f"target {t.name!r}: extra store {path} does not exist"
                    )
                continue
            try:
                StateStore(path, readonly=True).close()
            except Exception as e:  # noqa: BLE001 -- reported at startup, by name
                raise ApiSettingsError(
                    f"target {t.name!r}: store {path} cannot be read: {e}"
                ) from e


def validate_targets(targets: list[Target], *, env: Optional[dict[str, str]] = None) -> None:
    """Startup checks that need more than the file (code review M1): no
    cluster-identity env var may be set, and no two targets may resolve to
    the same Proxmox API URL. Raises `ApiSettingsError`."""
    import os

    env = os.environ if env is None else env
    set_vars = [k for k in CLUSTER_IDENTITY_ENV if env.get(k)]
    if set_vars:
        raise ApiSettingsError(
            f"refusing a targets file while {', '.join(set_vars)} "
            f"{'is' if len(set_vars) == 1 else 'are'} set in the environment: "
            "adapter.conf values from the environment override EVERY target's "
            "file, so all targets would point at one cluster -- unset them"
        )
    seen: dict[str, str] = {}
    for t in targets:
        f = target_facts(t)
        if f.error:
            raise ApiSettingsError(f"target {t.name!r}: {f.error}")
        url = (f.api_url or "").rstrip("/")
        if not url:
            continue
        if url in seen:
            raise ApiSettingsError(
                f"targets {seen[url]!r} and {t.name!r} resolve to the same Proxmox "
                f"API ({url}) -- one cluster, two stores: refused"
            )
        seen[url] = t.name


def _load_cfg(
    adapter_config: Optional[str], *, platform: str = DEFAULT_PLATFORM,
) -> tuple[Any, Optional[str]]:
    """`(cfg, None)` through `platform`'s own loader, or `(None, error)`."""
    from axrange.platform import load_config

    try:
        return load_config(adapter_config, platform=platform), None
    except Exception as e:  # noqa: BLE001 -- reported on the target, never raised
        return None, f"adapter config load failed: {e}"


def implicit_target(settings: ApiSettings) -> Target:
    """The no-file target: `settings`' own db + adapter.conf, named after its
    DEFAULT_NODE; protected ids from `settings.protected_deploys`."""
    cfg, _err = _load_cfg(settings.adapter_config)
    name = (cfg.default_node if cfg is not None else "") or IMPLICIT_FALLBACK_NAME
    return Target(
        name=name, adapter_config=settings.adapter_config, db_path=settings.db_path,
        default=True, implicit=True,
        protected_deploys=tuple(
            x.strip() for x in (settings.protected_deploys or "").split(",") if x.strip()
        ),
        # Resolved before comparing (S2): `./s.db` and `/abs/s.db` are one store.
        extra_dbs=tuple(
            p for p in dict.fromkeys(
                str(Path(x).expanduser().resolve())
                for x in (settings.extra_dbs or "").split(os.pathsep) if x.strip()
            ) if p != str(Path(settings.db_path).expanduser().resolve())
        ),
    )


def resolve_targets(settings: ApiSettings) -> list[Target]:
    """Every target this process serves: the file's, or the implicit one
    (computed once by `create_app` and kept on `settings.targets`, S11)."""
    if settings.targets:
        return list(settings.targets)
    return [implicit_target(settings)]


#: Parsed adapter.conf facts per target, filled once per process (S11) --
#: `create_app` validated them at startup; a changed adapter.conf needs a
#: serve restart, the same as every other setting.
_FACTS_CACHE: dict[tuple, TargetFacts] = {}


def target_facts(target: Target) -> TargetFacts:
    if target.implicit:
        # One file read; not cached so the implicit target always reflects
        # the env/adapter.conf this process sees (tests rely on it too).
        return _target_facts_uncached(target)
    key = (target.name, target.adapter_config, target.db_path)
    hit = _FACTS_CACHE.get(key)
    if hit is not None and hit.target == target:
        return hit
    f = _target_facts_uncached(target)
    if f.error is None:
        _FACTS_CACHE[key] = f
    return f


def _target_facts_uncached(target: Target) -> TargetFacts:
    cfg, err = _load_cfg(target.adapter_config, platform=target.platform)
    if cfg is None:
        return TargetFacts(target=target, error=err)
    return TargetFacts(
        target=target, node=cfg.default_node or "", api_url=cfg.proxmox_api_url or "",
        verify_tls=bool(cfg.proxmox_verify_tls), has_token=bool(cfg.proxmox_api_token),
        has_ssh_key=bool(cfg.ovs_ssh_key), cfg=cfg,
    )


def get_target(settings: ApiSettings, name: Optional[str]) -> Target:
    """The named target, or the default one for `None`/empty. Raises
    `UnknownTargetError`."""
    targets = resolve_targets(settings)
    if not name:
        return next((t for t in targets if t.default), targets[0])
    for t in targets:
        if t.name == name:
            return t
    raise UnknownTargetError(name, [t.name for t in targets])


def settings_for_target(settings: ApiSettings, name: Optional[str]) -> ApiSettings:
    """An `ApiSettings` copy aimed at target `name` (db + adapter.conf). For
    the implicit target this is `settings` itself -- byte-for-byte today's
    behaviour when no targets file is configured."""
    t = get_target(settings, name)
    if t.implicit:
        return settings
    return dataclasses.replace(settings, db_path=t.db_path, adapter_config=t.adapter_config)


def placement_warnings(placement: dict[str, Any], facts: TargetFacts) -> list[str]:
    """The cluster cross-check: every PVE node a deploy's `placement_json`
    names must be one this target runs on. A mismatch is surfaced, never
    hidden -- it means the row was read from one cluster's store but placed
    on another's node (a copied db, or a shared one)."""
    known = set(facts.known_nodes)
    if not known or not placement:
        return []
    odd = sorted({str(n) for n in placement.values() if n and str(n) not in known})
    if not odd:
        return []
    return [
        f"placement names PVE node(s) {', '.join(odd)} but target "
        f"{facts.target.name!r} runs on {', '.join(sorted(known))} -- this deploy "
        "may belong to another cluster"
    ]
