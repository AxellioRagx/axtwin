"""API runtime settings — where the control plane reads engine inputs from.

Config-over-flags (the project convention): every knob is an attribute here,
resolved from environment variables (and overridable by `axrange serve` flags,
which set these before `create_app`). Nothing in the API hard-codes a path.
"""
from __future__ import annotations

import os

from axrange.api import recompile as _rc


class ApiSettingsError(ValueError):
    """A setting is present but unusable. A TYPED error rather than the
    `ValueError` a bare `float()` raises, so `axrange serve` can report which
    KEY is wrong instead of showing an operator a traceback whose only clue
    is the word `float` (round 3 nit)."""


def _timeout_from_env() -> float:
    raw = os.environ.get(_rc.SETTING_TIMEOUT)
    if not raw or not raw.strip():
        return _rc.DEFAULT_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        raise ApiSettingsError(
            f"{_rc.SETTING_TIMEOUT}={raw!r} is not a number of seconds "
            f"(default {_rc.DEFAULT_TIMEOUT_S:g}). It bounds how long a hung "
            "compiler holds the per-imprint apply lock, so it is refused "
            "rather than silently defaulted"
        ) from None
    if value <= 0:
        raise ApiSettingsError(
            f"{_rc.SETTING_TIMEOUT}={raw!r} must be positive -- a zero or "
            "negative timeout would make every apply fail instantly"
        )
    return value
from dataclasses import dataclass, field
from pathlib import Path


def default_db_path() -> str:
    """The shared default state-store location (`AXRANGE_DB` or
    `~/.axrange/state.db`). Code-review finding #7 (2026-07-07): this used to
    be independently recomputed in `cli/main.py` as `_DEFAULT_DB_PATH`; both
    surfaces now call this one function.
    """
    return os.environ.get("AXRANGE_DB") or str(Path.home() / ".axrange" / "state.db")


@dataclass
class ApiSettings:
    """Resolved API configuration.

    Mirrors the CLI's input resolution (`--db`, `--images`, `--adapter-config`)
    so the API and CLI agree on where state + scenarios + adapter config live.
    """

    # State store (planner SQLite). The deploy/op read endpoints query this;
    # the deploy endpoint writes it. Same default as `axrange --db`.
    db_path: str = field(default_factory=default_db_path)

    # Directory holding `scenario.yaml` files the API can list/resolve. Each
    # scenario id maps to <scenarios_dir>/<id>.yaml or <scenarios_dir>/<id>/scenario.yaml.
    scenarios_dir: str = field(
        default_factory=lambda: os.environ.get("AXRANGE_SCENARIOS_DIR", "")
    )

    # Images root (manifests + bindings). Defaults per-scenario to the scenario's
    # parent dir, matching the CLI; an explicit root overrides.
    images_root: str = field(
        default_factory=lambda: os.environ.get("AXRANGE_IMAGES_ROOT", "")
    )

    # Adapter config path (adapter.conf). None → the loader's default search
    # (`adapter_proxmox.config.default_adapter_conf_paths`: /etc/axrange, then
    # the checkout's config/, then ./). plan/deploy/targets need this.
    adapter_config: str | None = field(
        default_factory=lambda: os.environ.get("AXRANGE_ADAPTER_CONFIG") or None
    )

    # Auth token for WRITE endpoints (deploy/run/destroy). Reads stay open on a
    # trusted bind (`docs/design/11-api-ui.md`). Empty → writes are open too
    # (dev default on 127.0.0.1); set AXRANGE_API_TOKEN to require it.
    api_token: str = field(
        default_factory=lambda: os.environ.get("AXRANGE_API_TOKEN", "")
    )

    # Optional SPA directory (Phase 4 co-serve). Empty → headless (the AI core /
    # CI use it this way; `docs/design/11-api-ui.md`).
    spa_dir: str = field(
        default_factory=lambda: os.environ.get("AXRANGE_SPA_DIR", "")
    )

    # Twin-config catalog root (`twins/<id>/te.yaml`, or a legacy
    # `twin-configs/<id>/<id>.yaml` -- docs/WORK_ORDER_twin_configs.md,
    # docs/WORK_ORDER_twin_consolidation.md). Empty -> `axrange.twinconfig.
    # registry`'s own default search path (`<repo-root>/twin-configs` then
    # `<repo-root>/twins`), same as the CLI's unset `--twins-dir`.
    # `AXRANGE_TWINS_DIR` wins over the older `AXRANGE_TWIN_CONFIGS_DIR`.
    twin_configs_dir: str = field(
        default_factory=lambda: (
            os.environ.get("AXRANGE_TWINS_DIR")
            or os.environ.get("AXRANGE_TWIN_CONFIGS_DIR", "")
        )
    )

    # Imprint-fixture catalog root (aximprint-integration Track C,
    # docs/WORK_ORDER_aximprint_console.md) -- read-only belief-store
    # fixtures under `<root>/<id>/` (point it at `twins/` for the real cast
    # bundles -- `axrange.twinconfig.bundle` reads both layouts). Empty ->
    # `axrange.api.imprints`'s own default (this package's `fixtures/imprints`
    # dir), same unset-means-package-default posture as `twin_configs_dir`.
    imprints_dir: str = field(
        default_factory=lambda: os.environ.get("AXRANGE_IMPRINTS_DIR", "")
    )

    # The Console's APPLY path (`docs/WORK_ORDER_console_canvas.md` S1.5,
    # decision D2 (a)): recompiling a cast means running aximprint's own CLI
    # as a subprocess. BOTH are required and neither is guessed -- an unset
    # key makes `POST /api/imprints/{id}/apply` refuse with 503 NAMING the
    # key, rather than shelling out to a `aximprint` that may not be the one
    # this operator meant or reading somebody else's belief store.
    # The key STRINGS are spelled once, in `axrange.api.recompile`, and
    # imported here (LES-105) -- the refusal names them, so a second literal
    # is a second place for the name in the message to stop matching the name
    # in the environment.
    aximprint_bin: str = field(
        default_factory=lambda: os.environ.get(_rc.SETTING_BIN, "")
    )
    aximprint_store: str = field(
        default_factory=lambda: os.environ.get(_rc.SETTING_STORE, "")
    )
    # Seconds. The real cast is fast (measured 0.74 s for 786 nodes), so the
    # default is `recompile.DEFAULT_TIMEOUT_S` (120) rather than a number
    # nobody had timed; the cap exists so a hung compiler releases the
    # per-imprint apply lock instead of holding it forever.
    #
    # **Apply is SYNCHRONOUS**, so a reverse proxy in front of `axrange serve`
    # needs its read timeout >= this value (nginx `proxy_read_timeout`). A
    # proxy that gives up first 504s a compile that is still running, and the
    # SPA is left holding a stale etag for a save that DID land.
    aximprint_timeout_s: float = field(
        default_factory=lambda: _timeout_from_env()
    )

    # Cluster targets file (Unified Console ruling §3.1, `axrange.api.
    # targets`): named `{name, adapter_config, db}` entries, the first one
    # the default. Empty -> ONE implicit target (`db_path` + `adapter_config`
    # above), named after its DEFAULT_NODE -- today's behaviour exactly.
    # Loaded by `create_app`, which also points `db_path`/`adapter_config`
    # at the default target so every non-targeted route reads the same one.
    targets_file: str = field(
        default_factory=lambda: os.environ.get("AXRANGE_TARGETS", "")
    )
    # The parsed targets (`targets.Target`); filled by `create_app`.
    targets: list = field(default_factory=list)

    # Further state stores of the IMPLICIT single target (fix round 2): the
    # same cluster, deploys recorded in another db (e.g. a twin deployed with
    # `--db /home/x/other.state.db`). `os.pathsep`-separated; reads search
    # them all, new deploys still go to `db_path`. A targets file carries
    # `extra_dbs:` per entry instead.
    extra_dbs: str = field(
        default_factory=lambda: os.environ.get("AXRANGE_EXTRA_DBS", "")
    )

    # Server-side protected deploys for the IMPLICIT single target (a targets
    # file carries `protected_deploys:` per entry instead): comma-separated
    # twin deploy ids no API write may touch without `override_protection=<id>`.
    protected_deploys: str = field(
        default_factory=lambda: os.environ.get("AXRANGE_PROTECTED_DEPLOYS", "")
    )

    def scenario_path(self, scenario_id: str) -> Path | None:
        """Resolve a scenario id to its `scenario.yaml`, or None.

        Guards against path traversal: a scenario id may not contain path
        separators or `..` — it indexes files under scenarios_dir only.
        """
        if not self.scenarios_dir:
            return None
        if not scenario_id or "/" in scenario_id or "\\" in scenario_id or ".." in scenario_id:
            return None
        base = Path(self.scenarios_dir)
        # <dir>/<id>.yaml  or  <dir>/<id>/scenario.yaml
        candidates = [
            base / f"{scenario_id}.yaml",
            base / f"{scenario_id}.yml",
            base / scenario_id / "scenario.yaml",
            base / scenario_id / "scenario.yml",
        ]
        for c in candidates:
            if c.is_file():
                return c
        return None

    def list_scenarios(self) -> list[str]:
        """List scenario ids discoverable under scenarios_dir."""
        if not self.scenarios_dir:
            return []
        base = Path(self.scenarios_dir)
        if not base.is_dir():
            return []
        ids: set[str] = set()
        for p in base.glob("*.yaml"):
            ids.add(p.stem)
        for p in base.glob("*.yml"):
            ids.add(p.stem)
        for d in base.iterdir():
            if d.is_dir() and (
                (d / "scenario.yaml").is_file() or (d / "scenario.yml").is_file()
            ):
                ids.add(d.name)
        return sorted(ids)
