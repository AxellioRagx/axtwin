"""`axrange serve` — run the control-plane API (uvicorn).

Mirrors aDT's `axdt edit` shape: one verb that runs the FastAPI app and
(optionally) co-serves the SPA. Headless-capable. Default bind 127.0.0.1
(`docs/design/11-api-ui.md`); `--host 0.0.0.0` to expose (carry a token).
"""
from __future__ import annotations

import sys

from axrange.api.app import create_app
from axrange.api.settings import ApiSettings


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    db_path: str | None = None,
    scenarios_dir: str | None = None,
    images_root: str | None = None,
    adapter_config: str | None = None,
    api_token: str | None = None,
    spa_dir: str | None = None,
    twin_configs_dir: str | None = None,
    imprints_dir: str | None = None,
    targets_file: str | None = None,
    extra_dbs: list[str] | None = None,
    log: callable = lambda m: sys.stdout.write(m + "\n"),
) -> int:
    """Build settings from explicit args (CLI flags) falling back to env, then
    run uvicorn. Returns a process exit code."""
    settings = ApiSettings()
    if db_path:
        settings.db_path = db_path
    if scenarios_dir is not None:
        settings.scenarios_dir = scenarios_dir
    if images_root is not None:
        settings.images_root = images_root
    if adapter_config is not None:
        settings.adapter_config = adapter_config
    if api_token is not None:
        settings.api_token = api_token
    if spa_dir is not None:
        settings.spa_dir = spa_dir
    if twin_configs_dir is not None:
        # docs/WORK_ORDER_console_selector_and_stale_deploys.md: unset ->
        # ApiSettings' own default (AXRANGE_TWIN_CONFIGS_DIR env, else the
        # twinconfig registry's built-in search path, `<repo-root>/twin-configs`
        # then `<repo-root>/twins`) -- mirrors `scenarios_dir`'s threading above
        # so an operator can point `axrange serve` at a non-default catalog the
        # same way they already can for scenarios.
        settings.twin_configs_dir = twin_configs_dir
    if imprints_dir is not None:
        # docs/WORK_ORDER_console_canvas.md S1.8 -- threaded EXACTLY as
        # `twin_configs_dir` above: unset -> ApiSettings' own default
        # (AXRANGE_IMPRINTS_DIR env, else the package's Track-C fixtures
        # dir). This is what lets `--imprints-dir twins` point the Console at
        # the real cast bundles instead of the fixtures.
        settings.imprints_dir = imprints_dir

    if extra_dbs:
        # Fix round 2: more stores of the implicit target (same cluster).
        import os

        settings.extra_dbs = os.pathsep.join(extra_dbs)
    if targets_file is not None:
        # Unified Console ruling §3.1: the targets file's FIRST entry becomes
        # this process's db + adapter.conf (`create_app`), so an explicit
        # `--db`/`--adapter-config` alongside it is overridden -- said, not
        # silent.
        settings.targets_file = targets_file
    if settings.targets_file and (db_path or adapter_config):
        log("  note: --targets given -- the first target's db/adapter_config "
            "override --db/--adapter-config")
    from axrange.api.settings import ApiSettingsError

    try:
        app = create_app(settings)
    except ApiSettingsError as e:
        sys.stderr.write(f"axrange serve: {e}\n")
        return 2

    try:
        import uvicorn
    except ImportError:
        sys.stderr.write(
            "axrange serve: uvicorn not installed. Install the API extra:\n"
            "  pip install 'axrange[api]'   (or: pip install fastapi 'uvicorn[standard]')\n"
        )
        return 2

    if not settings.api_token and host != "127.0.0.1":
        log(
            "WARNING: binding to a non-loopback host with NO API token — write "
            "endpoints (deploy/run/destroy) are unauthenticated. Set "
            "AXRANGE_API_TOKEN or --api-token before exposing."
        )

    log(f"axrange serve: http://{host}:{port}  (db={settings.db_path})")
    for t in settings.targets:
        log(f"  target {t.name}{' (default)' if t.default else ''}: db={t.db_path} "
            f"adapter_config={t.adapter_config or '(default search)'}")
    if settings.imprints_dir:
        log(f"  imprints from {settings.imprints_dir}")
    if settings.spa_dir:
        log(f"  serving SPA from {settings.spa_dir}")
    else:
        log("  headless (no SPA); set --spa-dir / AXRANGE_SPA_DIR to co-serve a UI")

    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0
