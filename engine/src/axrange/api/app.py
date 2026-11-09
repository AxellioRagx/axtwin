"""FastAPI application — the axRange control-plane API (`docs/design/11-api-ui.md`).

Thin HTTP layer over `service.py`: routes parse/validate request shapes, call the
service (which wraps the engine), and let `ApiError` map to HTTP status. No engine
logic lives here.

Auth posture (`api/Spec.md` A8, `axrange.api.auth`): WRITE endpoints go through
one dependency that dispatches on `AXRANGE_AUTH` (`none` | `proxy` | `token`);
reads are open on a trusted bind. Default bind is `127.0.0.1` (set by
`axrange serve`).
"""
from __future__ import annotations

import secrets
from typing import Optional

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from axrange.api import auth as _auth
from axrange.api import console_files as _console_files  # noqa: F401 -- see below
from axrange.api import discovery as _discovery
from axrange.api import dtos, service
from axrange.api.service import ApiError
from axrange.api.settings import ApiSettings


#: GAP-089: the apply-response field whose value `GET .../diff?since=` takes.
#: Named ONCE, here, and interpolated into the parameter description below --
#: the previous description read as "the apply response's
#: `code_fingerprint_before`", a value that 404s because it is an aximprint
#: build stamp and names no history snapshot. A description that names a field
#: is testable, so `tests/test_api_console.py` drives the route with
#: `apply.json()[DIFF_SINCE_FIELD]` and asserts the published description names
#: this constant -- the prose and the working value cannot drift apart again.
DIFF_SINCE_FIELD = "fingerprint_before"

DIFF_SINCE_DESCRIPTION = (
    f"The apply response's `{DIFF_SINCE_FIELD}` -- the bundle CONTENT "
    "fingerprint a previous APPLY produced on this host, which is the only "
    "kind of value that names a history snapshot (404 otherwise). NOT the "
    "same response's `pit_code_fingerprint_*` or `cast_code_fingerprint_*`: "
    "those are aximprint build stamps, name no snapshot, and 404 here."
)


def _identity(policy: _auth.AuthPolicy, request: Request) -> Optional[_auth.Identity]:
    """Who this request is under the deployment's policy, or None."""
    return _auth.identify(
        policy, client_host=request.client.host if request.client else None,
        headers=request.headers,
    )


def _is_authorized(policy: _auth.AuthPolicy, request: Request) -> bool:
    """The SAME check `_require_write_token`'s dependency enforces, factored
    out so a READ endpoint that only needs auth CONDITIONALLY (the
    access-facts `?secrets=true` gate, a twin-config's `document`) can reuse
    it without pulling in the whole write-endpoint dependency (which would
    require an identity unconditionally, even for the masked/default read).
    """
    return _identity(policy, request) is not None


def _require_write_token(settings: ApiSettings):
    """Dependency factory: the mode dispatcher for every write (A8). Resolves
    the policy ONCE; a request that no mode accepts is 401 with the text
    naming what the deployment expects. The accepted identity is left on
    `request.state.user` / `request.state.auth_mode` for a write that
    records provenance.
    """
    policy = settings.auth_policy()

    def _dep(request: Request) -> None:
        identity = _identity(policy, request)
        if identity is None:
            raise HTTPException(status_code=401, detail=_auth.refusal(policy))
        request.state.user = identity.user
        request.state.auth_mode = identity.mode

    _dep.policy = policy  # type: ignore[attr-defined]
    return _dep


def _ro(fn):
    """Run a READ route with every state-store open read-only (fix round 2:
    reads must not write -- no DDL, no `user_version` stamp, no new
    `-wal`/`-shm` files, never a created store). `functools.wraps` keeps the
    signature FastAPI inspects."""
    import functools

    from axrange.planner.state import readonly_opens

    @functools.wraps(fn)
    def _wrapped(*args, **kwargs):
        with readonly_opens():
            return fn(*args, **kwargs)

    return _wrapped


def create_app(settings: Optional[ApiSettings] = None) -> FastAPI:
    settings = settings or ApiSettings()
    if settings.targets_file and not settings.targets:
        # Fail at startup on a bad targets file (ApiSettingsError), never on
        # the first request. The DEFAULT target's db + adapter.conf become
        # this process's own, so a route that takes no `?target=` (tiers,
        # imprints, validate/plan) reads the same cluster the Range shows
        # first.
        from axrange.api.targets import (
            load_targets_file, register_labs, validate_stores, validate_targets,
        )

        targets = load_targets_file(settings.targets_file)
        register_labs(targets)     # lab sensors merge into each config (targets-and-lab §2.1)
        validate_targets(targets)  # identity env vars, duplicate API URLs (M1)
        validate_stores(targets)   # every store readable, extras exist (S1/S2)
        settings.targets = targets
        settings.db_path = settings.targets[0].db_path
        settings.adapter_config = settings.targets[0].adapter_config
    elif not settings.targets:
        # The implicit target, computed ONCE (S11: no adapter.conf re-parse
        # per request).
        from axrange.api.targets import implicit_target, validate_stores

        settings.targets = [implicit_target(settings)]
        validate_stores(settings.targets)  # S1/S2
    else:
        # Targets handed in already parsed (an embedding caller, the tests):
        # their labs still have to reach `load_config`.
        from axrange.api.targets import register_labs

        register_labs(list(settings.targets))
    app = FastAPI(
        title="axRange control-plane API",
        version="1",
        description="UI-agnostic control plane over the axRange engine (Phase 2).",
    )
    # Stash settings + an overridable deploy runner on app.state so tests can
    # inject a fake adapter / synchronous background without monkeypatching.
    app.state.settings = settings
    app.state.deploy_runner = None        # None -> service default (Proxmox)
    app.state.deploy_background = None     # None -> service default (daemon thread)
    app.state.teardown_factory = None      # None -> service default (ProxmoxTeardown)
    app.state.o2_adapter = None            # None -> service default (Proxmox); test seam
    app.state.o2_config_backend = None     # None -> service default (ConfigBackend); test seam
    app.state.o2_teardown_backend = None   # None -> service default (ProxmoxTeardown); test seam
    app.state.tap_adapter = None           # None -> service default (Proxmox); test seam
    app.state.tap_config_backend = None    # None -> service default (ConfigBackend); test seam
    app.state.tap_teardown_backend = None  # None -> service default (ProxmoxTeardown); test seam
    app.state.tfc_persona_backend = None   # None -> service default (PersonaBackend); test seam
    app.state.tfc_teardown_backend = None  # None -> service default (ProxmoxTeardown); test seam
    app.state.exr_config_backend = None    # None -> service default (ConfigBackend); test seam
    app.state.exr_teardown_backend = None  # None -> service default (ProxmoxTeardown); test seam
    # None -> service default (`recompile.SubprocessRecompile`, D2 (a)).
    # Injected by tests so the suite NEVER runs the real aximprint binary,
    # and the seam a helper/watcher (D2 (b)/(c)) drops into later.
    app.state.recompile_backend = None
    # Discovery runs (`api/Spec.md` A1): None -> `discovery.SubprocessDiscovery`
    # over AXRANGE_AXIMPRINT_BIN; tests inject a fake so the suite never runs
    # aximprint unless a test chooses to. The run table lives for the life
    # of this app AND in the discovery DB (`settings.discovery_db`, the
    # file the sources share), so the list survives a restart; a run the
    # old process left active is marked failed on open. `discovery_background`
    # None -> a daemon thread.
    app.state.discovery_backend = None
    app.state.discovery_background = None
    app.state.discovery_runs = _discovery.DiscoveryRuns(db_path=settings.discovery_db)
    # A2: the Apply / Reset in flight per deploy (progress + cancel), in-process.
    from axrange.api import edits as _edits

    app.state.config_exits = _edits.ConfigExits()
    # Discovery sources (`api/Spec.md` A7): None -> `discovery_sources.SourceStore`
    # over `settings.discovery_db`, opened on first use; tests inject a store on
    # a temp path. `discovery_tunnel` None -> the real `SshTunnel`; tests inject a
    # factory that forwards nowhere, so the suite never runs ssh.
    app.state.discovery_sources = None
    app.state.discovery_tunnel = None
    # Touched at STARTUP, not on the first save (round 3 nit). `console_files`
    # derives the `axr-assertions/1` closed sets from a vendored JSON artifact
    # at import time and raises `ImportError` if that file is missing -- which
    # a mis-built wheel would otherwise surface as a 500 on an operator's
    # first `PUT .../assertions`, hours after `axrange serve` reported ready.
    assert _console_files.ASSERTIONS_SCHEMA

    write_token = _require_write_token(settings)  # ApiSettingsError on a bad AXRANGE_AUTH
    policy: _auth.AuthPolicy = write_token.policy
    app.state.auth_policy = policy

    # Unified Console ruling §3.1: every deploy-scoped route takes
    # `?target=<cluster target name>` (default: the default target) and runs
    # the unchanged service function against that target's db + adapter.conf.
    # NOT on /api/tfc, /api/taps, /api/exr, where `target` already means
    # the twin (a te deploy id or twin name).
    _TARGET_QUERY = Query(
        default=None,
        description="Cluster target name (GET /api/targets). Default: the "
        "default target.",
    )

    # Server-side protected deploys (fix round 1): every write aimed at a
    # protected twin or its tap/tfc/exr siblings needs this, naming it.
    _OVERRIDE_QUERY = Query(
        default=None,
        description="Required to write to a PROTECTED deploy: its twin deploy id "
        "(423 otherwise).",
    )

    def _for_deploy(target: Optional[str], deploy_id: Optional[str]) -> ApiSettings:
        """`_for(target)`, then the store of that target holding `deploy_id`
        (fix round 2: a target may have several stores)."""
        return service.locate(_for(target), deploy_id=deploy_id)

    def _for_twin(*, deploy_id: Optional[str] = None, twin: Optional[str] = None) -> ApiSettings:
        """The default target's store holding `deploy_id`, else the twin
        `twin` names -- for the tier routes, whose `target` is a twin."""
        return service.locate(settings, deploy_id=deploy_id, twin=twin)

    def _for(target: Optional[str]) -> ApiSettings:
        from axrange.api.targets import UnknownTargetError, settings_for_target

        try:
            return settings_for_target(settings, target)
        except UnknownTargetError as e:
            raise ApiError(
                404, f"unknown target {e.name!r}", detail={"targets": e.known},
            ) from e

    @app.exception_handler(ApiError)
    async def _api_error_handler(_request: Request, exc: ApiError):
        return JSONResponse(
            status_code=exc.status,
            content={"error": exc.message, "detail": exc.detail},
        )

    # ---- health / scenarios ----
    @app.get("/api/health", response_model=dtos.HealthDTO)
    @_ro
    def health() -> dtos.HealthDTO:
        return service.health(settings)

    @app.get("/api/whoami", response_model=dtos.WhoamiDTO)
    def whoami(request: Request) -> dtos.WhoamiDTO:
        """Who the caller is under this deployment's write policy (A8), so
        the console shows its read-only state and a name without probing a
        write. Open like every read."""
        identity = _identity(policy, request)
        return dtos.WhoamiDTO(
            user=identity.user if identity else None,
            can_write=identity is not None,
            mode=identity.mode if identity else policy.mode_label,
            signin_url=policy.signin_url,
        )

    @app.get("/api/scenarios", response_model=dtos.ScenarioListDTO)
    @_ro
    def scenarios() -> dtos.ScenarioListDTO:
        return service.list_scenarios(settings)

    # `source` (M1, code review, `docs/WORK_ORDER_console_selector_and_
    # stale_deploys.md`): pins base-graph resolution to exactly one catalog
    # (`scenario`|`twin-config`|`imprint`) instead of the id-only chain,
    # which silently picks the FIRST match on a cross-catalog id collision
    # (this repo's own fixtures: `corp-enterprise` is both a shipped
    # twin-config and a Track-C imprint). Omit for the original chain
    # (back-compat).
    _SOURCE_QUERY = Query(
        default=None,
        description="Pin resolution to exactly one catalog: scenario|"
        "twin-config|imprint. Omit for the scenario->twin-config->imprint "
        "fallback chain.",
    )

    @app.get("/api/scenarios/{scenario_id}/topology", response_model=dtos.TopologyDTO)
    @_ro
    def topology(
        scenario_id: str,
        deploy_id: Optional[str] = Query(default=None),
        source: Optional[str] = _SOURCE_QUERY,
        target: Optional[str] = _TARGET_QUERY,
    ) -> dtos.TopologyDTO:
        return service.topology(
            _for_deploy(target, deploy_id), scenario_id, deploy_id=deploy_id, source=source,
        )

    @app.get(
        "/api/scenarios/{scenario_id}/nodes/{node_id}",
        response_model=dtos.NodeDetailDTO,
    )
    @_ro
    def node_detail(
        scenario_id: str,
        node_id: str,
        deploy_id: Optional[str] = Query(default=None),
        live: bool = Query(default=False),
        source: Optional[str] = _SOURCE_QUERY,
        target: Optional[str] = _TARGET_QUERY,
    ) -> dtos.NodeDetailDTO:
        return service.node_detail(
            _for_deploy(target, deploy_id), scenario_id, node_id, deploy_id=deploy_id, live=live,
            source=source,
        )

    # ---- topology export (the aximprint cast-topology-export artifact,
    # `docs/CHANGELOG.md` 2026-08-27) -- Logical/Physical's actual source;
    # see `service.topology_export`'s docstring. 404 doubles as "unknown
    # id" and "known id, no export yet" -- the caller treats both as
    # not-applicable, never falls back to `/topology`'s lossy projection. ----
    @app.get(
        "/api/scenarios/{scenario_id}/topology-export",
        response_model=dtos.TopologyExportDTO,
    )
    @_ro
    def topology_export(
        scenario_id: str,
        source: Optional[str] = Query(
            default=None,
            description="Pin resolution to exactly one catalog: "
            "twin-config|imprint (never scenario -- a raw scenario was "
            "never cast from a capture). Omit to try twin-config then "
            "imprint.",
        ),
    ) -> dtos.TopologyExportDTO:
        return service.topology_export(settings, scenario_id, source=source)

    # ---- twin-configs (`docs/WORK_ORDER_twin_configs.md`) -- an API peer of
    # `axrange twin-config list|show`, not a second catalog reader. ----
    @app.get("/api/twin-configs", response_model=dtos.TwinConfigListDTO)
    @_ro
    def twin_configs() -> dtos.TwinConfigListDTO:
        return service.list_twin_configs(settings)

    @app.get(
        "/api/twin-configs/{config_id}", response_model=dtos.TwinConfigDetailDTO,
    )
    @_ro
    def twin_config_detail(config_id: str, request: Request) -> dtos.TwinConfigDetailDTO:
        # The bundle `document` (lab passwords inside) only for an identity
        # the policy accepts -- the `?secrets=true` rule; the summary stays open.
        return service.twin_config_detail(
            settings, config_id, include_document=_is_authorized(policy, request),
        )

    # ---- twin-config write (`api/Spec.md` A3): Save-As and Persist/Save.
    # The baseline is written here and nowhere else. ----
    @app.post(
        "/api/twin-configs",
        response_model=dtos.TwinConfigDetailDTO,
        status_code=201,
        dependencies=[Depends(write_token)],
    )
    def twin_config_create(req: dtos.TwinConfigWriteRequest) -> dtos.TwinConfigDetailDTO:
        return service.twin_config_create(settings, req)

    @app.put(
        "/api/twin-configs/{config_id}",
        response_model=dtos.TwinConfigDetailDTO,
        dependencies=[Depends(write_token)],
    )
    def twin_config_write(
        config_id: str, req: dtos.TwinConfigWriteRequest,
    ) -> dtos.TwinConfigDetailDTO:
        return service.twin_config_write(settings, config_id, req)

    # ---- twins (`docs/design/imprint-twin-split.md`): the catalog of
    # deployable twin definitions -- cast from an imprint, or authored. These
    # routes lived under `/api/imprints` until 2026-10-28; the handler and
    # DTO names still say "imprint" (follow-on §7). axRange reads only the
    # bundle's artifact files, never aximprint itself. ----
    @app.get("/api/twins", response_model=dtos.ImprintListDTO)
    @_ro
    def imprints() -> dtos.ImprintListDTO:
        return service.list_imprints(settings, backend=app.state.discovery_backend)

    @app.get("/api/twins/{imprint_id}", response_model=dtos.ImprintSummaryDTO)
    @_ro
    def imprint_detail(imprint_id: str) -> dtos.ImprintSummaryDTO:
        return service.imprint_summary(settings, imprint_id)

    @app.get("/api/twins/{imprint_id}/tree", response_model=dtos.TwinTreeDTO)
    @_ro
    def twin_tree(imprint_id: str) -> dtos.TwinTreeDTO:
        return service.twin_tree(settings, imprint_id)

    @app.get(
        "/api/twins/{imprint_id}/provenance",
        response_model=dtos.ImprintProvenanceDTO,
    )
    @_ro
    def imprint_provenance(imprint_id: str) -> dtos.ImprintProvenanceDTO:
        return service.imprint_provenance(settings, imprint_id)

    @app.delete(
        "/api/twins/{imprint_id}",
        status_code=204,
        response_class=Response,
        dependencies=[Depends(write_token)],
    )
    def imprint_delete(imprint_id: str) -> Response:
        service.delete_imprint(settings, imprint_id, runs=app.state.discovery_runs)
        return Response(status_code=204)

    # ---- discovery runs (`api/Spec.md` A1; root `CLAUDE.md` critical path 1)
    # -- a real `aximprint` ingest -> materialize -> cast -> install, started
    # from the console, landing an imprint in the catalog the routes above
    # read. The POST is a write (it creates a bundle) and carries the token.
    @app.get("/api/discovery/plugins", response_model=dtos.DiscoveryPluginListDTO)
    @_ro
    def discovery_plugins() -> dtos.DiscoveryPluginListDTO:
        return service.list_discovery_plugins(
            settings, backend=app.state.discovery_backend,
        )

    @app.post(
        "/api/discovery/runs",
        response_model=dtos.DiscoveryRunDTO,
        status_code=202,
        dependencies=[Depends(write_token)],
    )
    def discovery_run_create(req: dtos.DiscoveryRunRequest) -> dtos.DiscoveryRunDTO:
        return service.create_discovery_run(
            settings, req, runs=app.state.discovery_runs,
            backend=app.state.discovery_backend,
            background=app.state.discovery_background,
        )

    @app.get("/api/discovery/runs", response_model=dtos.DiscoveryRunListDTO)
    @_ro
    def discovery_runs_list() -> dtos.DiscoveryRunListDTO:
        return service.list_discovery_runs(runs=app.state.discovery_runs)

    @app.get("/api/discovery/runs/{run_id}", response_model=dtos.DiscoveryRunDTO)
    @_ro
    def discovery_run_detail(run_id: str) -> dtos.DiscoveryRunDTO:
        return service.discovery_run_detail(run_id, runs=app.state.discovery_runs)

    @app.post(
        "/api/discovery/runs/{run_id}/cancel",
        response_model=dtos.DiscoveryRunDTO,
        status_code=202,
        dependencies=[Depends(write_token)],
    )
    def discovery_run_cancel(run_id: str) -> dtos.DiscoveryRunDTO:
        return service.cancel_discovery_run(run_id, runs=app.state.discovery_runs)

    @app.delete(
        "/api/discovery/runs/{run_id}",
        status_code=204,
        response_class=Response,
        dependencies=[Depends(write_token)],
    )
    def discovery_run_delete(run_id: str) -> Response:
        service.delete_discovery_run(run_id, runs=app.state.discovery_runs)
        return Response(status_code=204)

    # ---- imprints (`docs/design/imprint-twin-split.md`): what discovery
    # produced -- the PITs in the API's IR store, each with a renamable name,
    # the operator's assertions, and a cast action that lands a twin in the
    # catalog. `/api/discovery/pits` is kept as an alias of the list and the
    # rename. Every write runs the aximprint subprocess, so it is a plain
    # `def` on a worker thread.
    @app.get("/api/imprints", response_model=dtos.PitListDTO)
    @app.get("/api/discovery/pits", response_model=dtos.PitListDTO, include_in_schema=False)
    @_ro
    def imprints_list() -> dtos.PitListDTO:
        return service.list_pits(settings, backend=app.state.discovery_backend)

    @app.get("/api/imprints/{pit}", response_model=dtos.PitDTO)
    @_ro
    def imprint_pit_detail(pit: str) -> dtos.PitDTO:
        return service.pit_detail(settings, pit, backend=app.state.discovery_backend)

    @app.patch(
        "/api/imprints/{pit}",
        response_model=dtos.PitDTO,
        dependencies=[Depends(write_token)],
    )
    @app.patch(
        "/api/discovery/pits/{pit}",
        response_model=dtos.PitDTO,
        dependencies=[Depends(write_token)],
        include_in_schema=False,
    )
    def imprint_pit_rename(pit: str, req: dtos.PitRenameRequest) -> dtos.PitDTO:
        return service.rename_pit(
            settings, pit, req, backend=app.state.discovery_backend,
            runs=app.state.discovery_runs,
        )

    @app.post(
        "/api/imprints/{pit}/copy",
        response_model=dtos.PitDTO,
        status_code=201,
        dependencies=[Depends(write_token)],
    )
    def imprint_pit_copy(pit: str, req: dtos.PitCopyRequest) -> dtos.PitDTO:
        return service.copy_pit(settings, pit, req, backend=app.state.discovery_backend)

    @app.delete(
        "/api/imprints/{pit}",
        status_code=204,
        response_class=Response,
        dependencies=[Depends(write_token)],
    )
    def imprint_pit_delete(pit: str) -> Response:
        service.delete_pit(settings, pit, backend=app.state.discovery_backend,
                           runs=app.state.discovery_runs)
        return Response(status_code=204)

    @app.get("/api/imprints/{pit}/assertions", response_model=dtos.AssertionsDocDTO)
    @_ro
    def imprint_pit_assertions(response: Response, pit: str) -> dtos.AssertionsDocDTO:
        doc = service.pit_assertions(settings, pit, backend=app.state.discovery_backend)
        if doc.etag:
            response.headers["ETag"] = f'"{doc.etag}"'
        return doc

    @app.put(
        "/api/imprints/{pit}/assertions",
        response_model=dtos.AssertionsDocDTO,
        dependencies=[Depends(write_token)],
    )
    def put_imprint_pit_assertions(
        response: Response, pit: str, body: dict = Body(...),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
    ) -> dtos.AssertionsDocDTO:
        doc = service.put_pit_assertions(
            settings, pit, body, if_match=if_match, backend=app.state.discovery_backend,
        )
        if doc.etag:
            response.headers["ETag"] = f'"{doc.etag}"'
        return doc

    @app.post(
        "/api/imprints/{pit}/cast",
        response_model=dtos.CastResponseDTO,
        status_code=201,
        dependencies=[Depends(write_token)],
    )
    def imprint_pit_cast(pit: str, req: dtos.CastRequestDTO) -> dtos.CastResponseDTO:
        return service.cast_pit(
            settings, pit, req, backend=app.state.discovery_backend,
            runs=app.state.discovery_runs,
        )

    # ---- discovery sources (`api/Spec.md` A7) -- a saved way to reach a
    # sensor's query API (endpoint + direct/ssh-tunnel access + credential),
    # probed and run from the console. Reads never return a secret. Every
    # write, the probe included (it opens a tunnel with the server's key), is
    # token-gated.
    def _sources():
        if app.state.discovery_sources is None:
            from axrange.api.discovery_sources import SourceStore

            app.state.discovery_sources = SourceStore(settings.discovery_db)
        return app.state.discovery_sources

    @app.get("/api/discovery/sources", response_model=dtos.DiscoverySourceListDTO)
    @_ro
    def discovery_sources_list() -> dtos.DiscoverySourceListDTO:
        return service.list_discovery_sources(store=_sources())

    @app.post(
        "/api/discovery/sources",
        response_model=dtos.DiscoverySourceDTO,
        status_code=201,
        dependencies=[Depends(write_token)],
    )
    def discovery_source_create(req: dtos.DiscoverySourceWrite) -> dtos.DiscoverySourceDTO:
        return service.create_discovery_source(
            settings, req, store=_sources(), backend=app.state.discovery_backend,
        )

    @app.get("/api/discovery/sources/{source_id}", response_model=dtos.DiscoverySourceDTO)
    @_ro
    def discovery_source_detail(source_id: str) -> dtos.DiscoverySourceDTO:
        return service.discovery_source_detail(source_id, store=_sources())

    @app.put(
        "/api/discovery/sources/{source_id}",
        response_model=dtos.DiscoverySourceDTO,
        dependencies=[Depends(write_token)],
    )
    def discovery_source_update(
        source_id: str, req: dtos.DiscoverySourceUpdate,
    ) -> dtos.DiscoverySourceDTO:
        return service.update_discovery_source(
            settings, source_id, req, store=_sources(), backend=app.state.discovery_backend,
        )

    @app.delete(
        "/api/discovery/sources/{source_id}",
        status_code=204,
        response_class=Response,
        dependencies=[Depends(write_token)],
    )
    def discovery_source_delete(source_id: str) -> Response:
        service.delete_discovery_source(
            source_id, store=_sources(), runs=app.state.discovery_runs,
        )
        return Response(status_code=204)

    @app.post(
        "/api/discovery/sources/{source_id}/probe",
        response_model=dtos.DiscoveryProbeDTO,
        dependencies=[Depends(write_token)],
    )
    def discovery_source_probe(source_id: str) -> dtos.DiscoveryProbeDTO:
        return service.probe_discovery_source(
            settings, source_id, store=_sources(), backend=app.state.discovery_backend,
            tunnel_factory=app.state.discovery_tunnel,
        )

    @app.post(
        "/api/discovery/sources/{source_id}/runs",
        response_model=dtos.DiscoveryRunDTO,
        status_code=202,
        dependencies=[Depends(write_token)],
    )
    def discovery_source_run_create(
        source_id: str, req: dtos.DiscoverySourceRunRequest,
    ) -> dtos.DiscoveryRunDTO:
        return service.create_discovery_source_run(
            settings, source_id, req, store=_sources(), runs=app.state.discovery_runs,
            backend=app.state.discovery_backend,
            background=app.state.discovery_background,
            tunnel_factory=app.state.discovery_tunnel,
        )

    # ---- the Console canvas (`docs/WORK_ORDER_console_canvas.md` S1;
    # `docs/design/15-console-canvas.md` §10) -- the imprint-scoped read +
    # edit surface for the assertions SPA. Reads are open on a trusted bind
    # like every other read here; the three MUTATING routes (assertions PUT,
    # layout PUT, apply POST) carry the write token. ----
    @app.get(
        "/api/twins/{imprint_id}/topology",
        response_model=dtos.TopologyExportDTO,
    )
    @_ro
    def imprint_topology(imprint_id: str) -> dtos.TopologyExportDTO:
        """S1.1, decision D1 (a): an ALIAS of `GET /api/scenarios/{id}/
        topology-export?source=imprint` -- same DTO, same reader, an
        imprint-shaped URL (`service.imprint_topology` delegates; there is no
        second implementation)."""
        return service.imprint_topology(settings, imprint_id)

    @app.get("/api/twins/{imprint_id}/cast", response_model=dtos.CastSummaryDTO)
    @_ro
    def imprint_cast(imprint_id: str) -> dtos.CastSummaryDTO:
        return service.imprint_cast(settings, imprint_id)

    @app.get(
        "/api/twins/{imprint_id}/assertions",
        response_model=dtos.AssertionsDocDTO,
    )
    @_ro
    def imprint_assertions(response: Response, imprint_id: str) -> dtos.AssertionsDocDTO:
        doc = service.imprint_assertions(settings, imprint_id)
        if doc.etag:
            # BOTH channels: the HTTP header for a well-behaved client, and
            # the same value in the body (the DTO's `etag`) so a proxy that
            # strips `ETag`/`If-Match` cannot turn a guarded write into an
            # unguarded one (work order §3's own reasoning).
            response.headers["ETag"] = f'"{doc.etag}"'
        return doc

    @app.put(
        "/api/twins/{imprint_id}/assertions",
        response_model=dtos.AssertionsDocDTO,
        dependencies=[Depends(write_token)],
    )
    def put_imprint_assertions(
        response: Response, imprint_id: str, body: dict = Body(...),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
    ) -> dtos.AssertionsDocDTO:
        doc = service.put_imprint_assertions(
            settings, imprint_id, body, if_match=if_match,
        )
        if doc.etag:
            response.headers["ETag"] = f'"{doc.etag}"'
        return doc

    @app.get("/api/twins/{imprint_id}/layout", response_model=dtos.LayoutDocDTO)
    @_ro
    def imprint_layout(response: Response, imprint_id: str) -> dtos.LayoutDocDTO:
        doc = service.imprint_layout(settings, imprint_id)
        if doc.etag:
            response.headers["ETag"] = f'"{doc.etag}"'
        return doc

    @app.put(
        "/api/twins/{imprint_id}/layout",
        response_model=dtos.LayoutDocDTO,
        dependencies=[Depends(write_token)],
    )
    def put_imprint_layout(
        response: Response, imprint_id: str, body: dict = Body(...),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
    ) -> dtos.LayoutDocDTO:
        doc = service.put_imprint_layout(settings, imprint_id, body, if_match=if_match)
        if doc.etag:
            response.headers["ETag"] = f'"{doc.etag}"'
        return doc

    @app.post(
        "/api/twins/{imprint_id}/apply",
        response_model=dtos.ImprintApplyResponse,
        dependencies=[Depends(write_token)],
    )
    def imprint_apply(
        imprint_id: str, req: dtos.ImprintApplyRequest = Body(...),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
    ) -> dtos.ImprintApplyResponse:
        """Save + recompile (D2 (a)). A plain `def`, so Starlette runs it in
        a worker thread -- the compiler subprocess never blocks the event
        loop. `app.state.recompile_backend` is the test seam: injected, no
        real binary is ever executed by the suite."""
        return service.imprint_apply(
            settings, imprint_id, req, if_match=if_match,
            backend=app.state.recompile_backend,
        )

    @app.get("/api/twins/{imprint_id}/diff", response_model=dtos.ImprintDiffDTO)
    @_ro
    def imprint_diff(
        imprint_id: str,
        since: str = Query(..., description=DIFF_SINCE_DESCRIPTION),
    ) -> dtos.ImprintDiffDTO:
        return service.imprint_diff(settings, imprint_id, since=since)

    # ---- imprints: "deploy as twin" (Track C slice 2, PART A) -- a PURE
    # preview (`/plan`, no auth: same pure/no-mutation posture as `/api/
    # plan`) and the real, lease-guarded deploy (`/deploy`, write-token
    # gated like every other `/api/deploys`-class write below). Both reuse
    # the SAME engine plumbing `/api/deploys` uses (`service.imprint_plan`/
    # `imprint_deploy` are thin wrappers, not a second implementation) --
    # `imprint_deploy` shares `create_deploy`'s test seams
    # (`app.state.deploy_runner`/`deploy_background`) one-for-one. ----
    @app.post(
        "/api/twins/{imprint_id}/plan", response_model=dtos.PlanSummaryDTO,
    )
    @_ro
    def imprint_plan(
        imprint_id: str, req: Optional[dtos.ImprintPlanRequest] = Body(default=None),
        target: Optional[str] = _TARGET_QUERY,
    ) -> dtos.PlanSummaryDTO:
        body = req or dtos.ImprintPlanRequest()
        return service.imprint_plan(
            settings, imprint_id, target_node=body.target_node, params=body.params,
            target=target,
        )

    @app.post(
        "/api/twins/{imprint_id}/deploy",
        response_model=dtos.CreateDeployResponse,
        dependencies=[Depends(write_token)],
    )
    def imprint_deploy(
        imprint_id: str, req: Optional[dtos.ImprintDeployRequest] = Body(default=None),
        override_protection: Optional[str] = Query(default=None),
        target: Optional[str] = _TARGET_QUERY,
    ) -> dtos.CreateDeployResponse:
        body = req or dtos.ImprintDeployRequest()
        return service.imprint_deploy(
            settings, imprint_id, body,
            runner=app.state.deploy_runner,
            background=app.state.deploy_background,
            override_protection=override_protection,
            tap_adapter=app.state.tap_adapter,
            tap_config_backend=app.state.tap_config_backend,
            persona_backend=app.state.tfc_persona_backend,
            target=target,
        )

    # ---- validate / plan (pure) ----
    @app.post("/api/validate", response_model=dtos.ValidateResultDTO)
    @_ro
    def validate(body: dict, target: Optional[str] = _TARGET_QUERY) -> dtos.ValidateResultDTO:
        # `dtos.ValidateRequest` is the shape: exactly one of scenario /
        # config / imprint (+ params) -- fix round 2 -- OR a `document`
        # (`api/Spec.md` A3: the editor validates before Save / Apply).
        if body.get("document") is not None:
            others = sorted(k for k in ("scenario", "config", "imprint", "params") if body.get(k))
            if others:
                raise ApiError(400, f"'document' cannot be combined with {others}")
            from pydantic import ValidationError

            try:
                doc = dtos.TwinConfigWriteRequest.model_validate(body["document"])
            except ValidationError as e:
                raise ApiError(400, f"'document' must be {{id, files}}: {e.errors()[0].get('msg')}") from e
            return service.validate_document(settings, doc, target=target)
        return service.validate(
            settings, body.get("scenario"), config=body.get("config"),
            imprint=body.get("imprint"), params=_params_of(body), target=target,
        )

    @app.post("/api/plan", response_model=dtos.PlanSummaryDTO)
    @_ro
    def plan(body: dict, target: Optional[str] = _TARGET_QUERY) -> dtos.PlanSummaryDTO:
        # `?target=` (docs/design/targets-and-lab.md §2.2): plan against that
        # target and attach its preflight.
        return service.plan(
            settings, body.get("scenario"),
            target_node=body.get("target_node"), config=body.get("config"),
            imprint=body.get("imprint"), params=_params_of(body), target=target,
        )

    def _params_of(body: dict) -> Optional[dict[str, str]]:
        raw = body.get("params")
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise ApiError(400, "'params' must be an object of NAME: value")
        # Values must be STRINGS, exactly as `POST /api/deploys` validates
        # them (S4): a preview must not accept what the deploy refuses.
        bad = sorted(k for k, v in raw.items() if not isinstance(v, str))
        if bad:
            raise ApiError(
                400, f"'params' values must be strings (got non-strings for {bad})",
            )
        return dict(raw)

    # ---- deploys (read) ----
    # `status` (`docs/WORK_ORDER_console_selector_and_stale_deploys.md`):
    # unset -> DEFAULT excludes `destroyed` (an unfiltered Console shows only
    # live deploys); an explicit `destroyed` or `all` reaches full history
    # (matching what `list-deploys` already shows), and any other single
    # `DeployStatus` value narrows to exactly that status. Validated in
    # `service.list_deploys` (400 on an unrecognized value), not here --
    # routes stay parse-only.
    @app.get("/api/deploys", response_model=dtos.DeployListDTO)
    @_ro
    def deploys(
        limit: int = Query(default=50, ge=1, le=500),
        status: Optional[str] = Query(
            default=None,
            description="Filter by deploy status (running|completed|failed|"
            "cancelled|destroyed), or 'all' for no filtering. Default: "
            "excludes 'destroyed' (only live deploys).",
        ),
        target: Optional[str] = _TARGET_QUERY,
    ) -> dtos.DeployListDTO:
        return service.list_deploys(_for(target), limit=limit, status=status)

    @app.get("/api/deploys/{deploy_id}", response_model=dtos.DeployDetailDTO)
    @_ro
    def deploy(deploy_id: str, target: Optional[str] = _TARGET_QUERY) -> dtos.DeployDetailDTO:
        return service.deploy_detail(_for_deploy(target, deploy_id), deploy_id)

    # `docs/WORK_ORDER_tier_split.md` review SHOULD-FIX #10 -- an API peer of
    # `axrange te inventory`, not a second implementation (both call
    # `axrange.planner.inventory.build_twin_inventory`).
    @app.get("/api/deploys/{deploy_id}/inventory", response_model=dtos.TwinInventoryDTO)
    @_ro
    def deploy_inventory(
        deploy_id: str, target: Optional[str] = _TARGET_QUERY,
    ) -> dtos.TwinInventoryDTO:
        return service.deploy_inventory(_for_deploy(target, deploy_id), deploy_id)

    # `docs/WORK_ORDER_fidelity_scorecard.md` §4.2 -- an API peer of
    # `axrange te scorecard`, not a second implementation (both call
    # `axrange.fidelity.runner.scorecard_for_deploy`). Probing is OPT-IN
    # here and default-on in the CLI: the probe writes a temporary script
    # into twin guests, which a bare GET should not do unasked.
    @app.get("/api/deploys/{deploy_id}/scorecard", response_model=dtos.ScorecardDTO)
    @_ro
    def deploy_scorecard(
        deploy_id: str,
        probe: bool = Query(
            False,
            description="Run the in-guest protocol probe (row 5). Default "
            "false: the artifact-only rows are served without touching a guest, "
            "and row 5 then reads not-computed with its denominator.",
        ),
        include_deferred: bool = Query(
            False,
            description="Also run row 4's in-guest listen check (reported; "
            "never enters the gate set).",
        ),
        reference: Optional[str] = Query(
            None,
            description="Cast bundle directory. Default: "
            "./twins/<scenario-id> (when it carries a cast's generated/ export).",
        ),
        target: Optional[str] = _TARGET_QUERY,
    ) -> dtos.ScorecardDTO:
        return service.deploy_scorecard(
            _for_deploy(target, deploy_id), deploy_id, probe=probe,
            include_deferred=include_deferred, reference_dir=reference,
        )

    # `docs/WORK_ORDER_access_facts.md` -- an API peer of `axrange te info`'s
    # access block, not a second implementation (both call
    # `axrange.planner.access.te_info_from_ops`/`fill_access_facts`).
    # Passwords are MASKED by default (mirroring the CLI's `--no-secrets`
    # default-on posture, but stricter: the API has no local-operator
    # assumption a CLI session does) -- `?secrets=true` reveals the real
    # values, gated behind the SAME bearer-token check the write endpoints
    # use whenever a token is configured (so an unauthenticated reader can
    # never pull effective passwords off a token-protected deployment).
    @app.get("/api/deploys/{deploy_id}/access", response_model=dtos.AccessFactsDTO)
    @_ro
    def deploy_access(
        request: Request,
        deploy_id: str,
        reveal_secrets: bool = Query(
            False, alias="secrets",
            description="Reveal real passwords instead of masking them. "
            "Requires the bearer token when one is configured.",
        ),
        target: Optional[str] = _TARGET_QUERY,
    ) -> dtos.AccessFactsDTO:
        if reveal_secrets and not _is_authorized(policy, request):
            raise HTTPException(
                status_code=401,
                detail=f"{_auth.refusal(policy)} (for ?secrets=true)",
            )
        return service.deploy_access_facts(
            _for_deploy(target, deploy_id), deploy_id, reveal_secrets=reveal_secrets,
        )

    # ---- deploys (write) ----
    @app.post(
        "/api/deploys",
        response_model=dtos.CreateDeployResponse,
        dependencies=[Depends(write_token)],
    )
    def create_deploy(
        req: dtos.CreateDeployRequest, target: Optional[str] = _TARGET_QUERY,
        override_protection: Optional[str] = _OVERRIDE_QUERY,
    ) -> dtos.CreateDeployResponse:
        return service.create_deploy(
            _for(target), req,
            runner=app.state.deploy_runner,
            background=app.state.deploy_background,
            override_protection=override_protection,
        )

    @app.post(
        "/api/deploys/{deploy_id}/run",
        response_model=dtos.RunResponse,
        dependencies=[Depends(write_token)],
    )
    def run(deploy_id: str, target: Optional[str] = _TARGET_QUERY) -> dtos.RunResponse:
        return service.run_scenario(_for_deploy(target, deploy_id), deploy_id)

    @app.delete(
        "/api/deploys/{deploy_id}",
        response_model=dtos.DestroyResponse,
        dependencies=[Depends(write_token)],
    )
    def destroy(
        deploy_id: str,
        allow_destroy: bool = Query(
            False,
            description="Required confirmation. Accepted here as a query param "
            "because some HTTP intermediaries (incl. Grafana's datasource proxy) "
            "drop request bodies on DELETE; the body field is honored too.",
        ),
        req: Optional[dtos.DestroyRequest] = Body(default=None),
        target: Optional[str] = _TARGET_QUERY,
        override_protection: Optional[str] = _OVERRIDE_QUERY,
    ) -> dtos.DestroyResponse:
        # Confirmation may arrive via the query param OR the body — take either,
        # so a body-stripping proxy can't turn an authorized destroy into a 400.
        effective = bool(allow_destroy or (req.allow_destroy if req else False))
        in_store = _for_deploy(target, deploy_id)
        service.guard_protected(in_store, deploy_id=deploy_id, override=override_protection)
        return service.destroy_deploy(
            in_store, deploy_id, dtos.DestroyRequest(allow_destroy=effective),
            teardown_factory=app.state.teardown_factory,
            cascade_adapter=app.state.tap_adapter,
            cascade_persona_backend=app.state.tfc_persona_backend,
        )

    # ---- O2 endpoint resolution + external-sensor facts (`docs/WORK_ORDER_
    # access_facts.md` Part B/C) -- a DEDICATED endpoint (review guidance),
    # kept separate from `/api/o2/surface` so `SurfaceStatusDTO` stays a
    # stable, secret-free shape. Same masking/auth posture as
    # `/api/deploys/{id}/access` above. ----
    @app.get("/api/o2/info", response_model=dtos.O2InfoDTO)
    @_ro
    def o2_info(
        request: Request,
        reveal_secrets: bool = Query(
            False, alias="secrets",
            description="Reveal real external-sensor passwords instead of "
            "masking them. Requires the bearer token when one is configured.",
        ),
    ) -> dtos.O2InfoDTO:
        if reveal_secrets and not _is_authorized(policy, request):
            raise HTTPException(
                status_code=401,
                detail=f"{_auth.refusal(policy)} (for ?secrets=true)",
            )
        return service.o2_info(settings, reveal_secrets=reveal_secrets)

    # ---- O2 surface (A1, docs/design/05-o2-surface.md) ----
    @app.get("/api/o2/surface", response_model=dtos.SurfaceStatusDTO)
    @_ro
    def o2_surface_status(
        deploy_id: Optional[str] = Query(default=None),
    ) -> dtos.SurfaceStatusDTO:
        return service.o2_surface_status(settings, deploy_id=deploy_id)

    @app.post(
        "/api/o2/surface",
        response_model=dtos.SurfaceStatusDTO,
        dependencies=[Depends(write_token)],
    )
    def o2_surface_up(req: dtos.SurfaceUpRequest) -> dtos.SurfaceStatusDTO:
        return service.o2_surface_up(
            settings, req,
            adapter=app.state.o2_adapter, config_backend=app.state.o2_config_backend,
        )

    @app.delete(
        "/api/o2/surface",
        response_model=dtos.SurfaceDownResponse,
        dependencies=[Depends(write_token)],
    )
    def o2_surface_down(
        allow_destroy: bool = Query(
            False,
            description="Required confirmation (query OR body — see the "
            "/api/deploys/{id} DELETE endpoint's note on DELETE-body drops).",
        ),
        deploy_id: Optional[str] = Query(default=None),
        req: Optional[dtos.SurfaceDownRequest] = Body(default=None),
    ) -> dtos.SurfaceDownResponse:
        effective = bool(allow_destroy or (req.allow_destroy if req else False))
        effective_deploy_id = deploy_id or (req.deploy_id if req else None)
        return service.o2_surface_down(
            settings,
            dtos.SurfaceDownRequest(
                allow_destroy=effective, deploy_id=effective_deploy_id
            ),
            teardown_backend=app.state.o2_teardown_backend,
        )

    # ---- O2 taps (A2, docs/design/06-taps-sensors.md) ----
    # Read peer of `axrange tap status` (concise) / `tap info` (verbose). One
    # `verbose` option toggles the two shapes: concise -> TapPlanStatusDTO,
    # verbose -> TapInfoDTO (per-binding sensors + resolved external-sensor
    # access facts). `target`/`deploy_id` resolve like the CLI (a twin id ->
    # its applied tap deploy). Passwords in the verbose view are MASKED unless
    # `?secrets=true` AND an authorized token (same gate as the access
    # endpoint, LES-134).
    @app.get("/api/taps", response_model=None)
    @_ro
    def o2_tap_get(
        request: Request,
        target: Optional[str] = Query(default=None),
        deploy_id: Optional[str] = Query(default=None),
        verbose: bool = Query(
            False,
            description="Full tap info (per-binding sensors + access facts) "
            "instead of the concise status summary.",
        ),
        reveal_secrets: bool = Query(
            False, alias="secrets",
            description="Reveal external-sensor passwords (verbose only). "
            "Requires the bearer token when one is configured.",
        ),
    ):
        if not verbose:
            return service.o2_tap_status(
                _for_twin(deploy_id=deploy_id, twin=target), target=target, deploy_id=deploy_id,
            )
        if reveal_secrets and not _is_authorized(policy, request):
            raise HTTPException(
                status_code=401,
                detail=f"{_auth.refusal(policy)} (for ?secrets=true)",
            )
        return service.o2_tap_info(
            _for_twin(deploy_id=deploy_id, twin=target), target=target, deploy_id=deploy_id,
            reveal_secrets=reveal_secrets,
        )

    @app.post(
        "/api/taps",
        response_model=dtos.TapPlanStatusDTO,
        dependencies=[Depends(write_token)],
    )
    def o2_tap_apply(
        req: dtos.TapApplyRequest, override_protection: Optional[str] = _OVERRIDE_QUERY,
    ) -> dtos.TapPlanStatusDTO:
        in_store = _for_twin(twin=req.target)
        service.guard_protected(in_store, twin=req.target, override=override_protection)
        return service.o2_tap_apply(
            in_store, req,
            adapter=app.state.tap_adapter,
            config_backend=app.state.tap_config_backend,
        )

    @app.delete(
        "/api/taps",
        response_model=dtos.TapDestroyResponse,
        dependencies=[Depends(write_token)],
    )
    def o2_tap_destroy(
        allow_destroy: bool = Query(
            False,
            description="Required confirmation (query OR body — see the "
            "/api/deploys/{id} DELETE endpoint's note on DELETE-body drops).",
        ),
        target: Optional[str] = Query(default=None),
        deploy_id: Optional[str] = Query(default=None),
        req: Optional[dtos.TapDestroyRequest] = Body(default=None),
        override_protection: Optional[str] = _OVERRIDE_QUERY,
    ) -> dtos.TapDestroyResponse:
        effective = bool(allow_destroy or (req.allow_destroy if req else False))
        effective_target = target or (req.target if req else None)
        effective_deploy_id = deploy_id or (req.deploy_id if req else None)
        in_store = _for_twin(deploy_id=effective_deploy_id, twin=effective_target)
        service.guard_protected(
            in_store, deploy_id=effective_deploy_id, twin=effective_target,
            override=override_protection,
        )
        return service.o2_tap_destroy(
            in_store,
            dtos.TapDestroyRequest(
                allow_destroy=effective, target=effective_target,
                deploy_id=effective_deploy_id,
            ),
            teardown_backend=app.state.tap_teardown_backend,
            adapter=app.state.tap_adapter,
        )

    # ---- tfc (the Traffic tier, docs/WORK_ORDER_traffic_tier.md) ----
    @app.get("/api/tfc", response_model=dtos.TrafficPlanStatusDTO)
    @_ro
    def tfc_status(
        target: Optional[str] = Query(default=None),
        deploy_id: Optional[str] = Query(default=None),
    ) -> dtos.TrafficPlanStatusDTO:
        return service.tfc_status(
            _for_twin(deploy_id=deploy_id, twin=target), target=target, deploy_id=deploy_id,
        )

    @app.post(
        "/api/tfc",
        response_model=dtos.TrafficPlanStatusDTO,
        dependencies=[Depends(write_token)],
    )
    def tfc_apply(
        req: dtos.TrafficApplyRequest, override_protection: Optional[str] = _OVERRIDE_QUERY,
    ) -> dtos.TrafficPlanStatusDTO:
        in_store = _for_twin(twin=req.target or req.twin)
        service.guard_protected(
            in_store, twin=req.target or req.twin, override=override_protection,
        )
        return service.tfc_apply(
            in_store, req, persona_backend=app.state.tfc_persona_backend,
        )

    @app.delete(
        "/api/tfc",
        response_model=dtos.TrafficDestroyResponse,
        dependencies=[Depends(write_token)],
    )
    def tfc_destroy(
        allow_destroy: bool = Query(
            False,
            description="Required confirmation (query OR body — see the "
            "/api/deploys/{id} DELETE endpoint's note on DELETE-body drops).",
        ),
        target: Optional[str] = Query(default=None),
        deploy_id: Optional[str] = Query(default=None),
        req: Optional[dtos.TrafficDestroyRequest] = Body(default=None),
        override_protection: Optional[str] = _OVERRIDE_QUERY,
    ) -> dtos.TrafficDestroyResponse:
        effective = bool(allow_destroy or (req.allow_destroy if req else False))
        effective_target = target or (req.target if req else None)
        effective_deploy_id = deploy_id or (req.deploy_id if req else None)
        in_store = _for_twin(deploy_id=effective_deploy_id, twin=effective_target)
        service.guard_protected(
            in_store, deploy_id=effective_deploy_id, twin=effective_target,
            override=override_protection,
        )
        return service.tfc_destroy(
            in_store,
            dtos.TrafficDestroyRequest(
                allow_destroy=effective, target=effective_target,
                deploy_id=effective_deploy_id,
            ),
            teardown_backend=app.state.tfc_teardown_backend,
            persona_backend=app.state.tfc_persona_backend,
        )

    # ---- exr (the Exercise tier, docs/WORK_ORDER_exercise_tier.md) ----
    @app.get("/api/exr", response_model=dtos.ExerciseStatusDTO)
    @_ro
    def exr_status(
        target: Optional[str] = Query(default=None),
        deploy_id: Optional[str] = Query(default=None),
    ) -> dtos.ExerciseStatusDTO:
        return service.exr_status(
            _for_twin(deploy_id=deploy_id, twin=target), target=target, deploy_id=deploy_id,
        )

    @app.post(
        "/api/exr",
        response_model=dtos.ExerciseStatusDTO,
        dependencies=[Depends(write_token)],
    )
    def exr_apply(
        req: dtos.ExerciseApplyRequest, override_protection: Optional[str] = _OVERRIDE_QUERY,
    ) -> dtos.ExerciseStatusDTO:
        in_store = _for_twin(twin=req.target or req.twin)
        service.guard_protected(
            in_store, twin=req.target or req.twin, override=override_protection,
        )
        return service.exr_apply(
            in_store, req, config_backend=app.state.exr_config_backend,
        )

    @app.delete(
        "/api/exr",
        response_model=dtos.ExerciseDestroyResponse,
        dependencies=[Depends(write_token)],
    )
    def exr_destroy(
        allow_destroy: bool = Query(
            False,
            description="Required confirmation (query OR body — see the "
            "/api/deploys/{id} DELETE endpoint's note on DELETE-body drops).",
        ),
        target: Optional[str] = Query(default=None),
        deploy_id: Optional[str] = Query(default=None),
        req: Optional[dtos.ExerciseDestroyRequest] = Body(default=None),
        override_protection: Optional[str] = _OVERRIDE_QUERY,
    ) -> dtos.ExerciseDestroyResponse:
        effective = bool(allow_destroy or (req.allow_destroy if req else False))
        effective_target = target or (req.target if req else None)
        effective_deploy_id = deploy_id or (req.deploy_id if req else None)
        in_store = _for_twin(deploy_id=effective_deploy_id, twin=effective_target)
        service.guard_protected(
            in_store, deploy_id=effective_deploy_id, twin=effective_target,
            override=override_protection,
        )
        return service.exr_destroy(
            in_store,
            dtos.ExerciseDestroyRequest(
                allow_destroy=effective, target=effective_target,
                deploy_id=effective_deploy_id,
            ),
            teardown_backend=app.state.exr_teardown_backend,
        )

    # ---- targets ----
    @app.get("/api/targets", response_model=dtos.TargetListDTO)
    @_ro
    def targets() -> dtos.TargetListDTO:
        return service.list_targets(settings)

    @app.get("/api/targets/{name}/health", response_model=dtos.TargetHealthDTO)
    def target_health(name: str) -> dtos.TargetHealthDTO:
        # `docs/design/targets-and-lab.md` §2.2: one status per lab
        # component, cached 15 s server-side (`axrange.api.labhealth`).
        from axrange.api import labhealth
        from axrange.api.targets import UnknownTargetError, get_target, target_facts

        try:
            t = get_target(settings, name)
        except UnknownTargetError as e:
            raise ApiError(
                404, f"unknown target {e.name!r}", detail={"targets": e.known},
            ) from e
        return labhealth.target_health(t, target_facts(t))

    # ---- Unified Console run-time surface (ruling §3.1-§3.3; contract:
    # docs/reference/ui-consolidation/04-api-contract.md) ----
    app.state.range_probe = None          # None -> live `/version` read; test seam
    app.state.health_sweep = None         # None -> live `/cluster/resources`; test seam
    app.state.health_qga_ping = None      # None -> live QGA ping; test seam
    app.state.tfc_host_background = None  # None -> daemon thread; test seam
    app.state.containment_ga = None       # None -> NodeResolvingGuestAgent; test seam
    app.state.live_caches = service.LiveCaches()  # S8 min-interval caches

    @app.get("/api/range", response_model=dtos.RangeDTO)
    @_ro
    def range_view(
        live: bool = Query(
            True, description="Probe each target's reachability (one /version "
            "read each). false -> reachability is 'unknown'.",
        ),
    ) -> dtos.RangeDTO:
        return service.range_view(
            settings, live=live, probe=app.state.range_probe, caches=app.state.live_caches,
            sweep=app.state.health_sweep,
        )

    @app.get("/api/deploys/{deploy_id}/hosts", response_model=dtos.TwinHostsDTO)
    @_ro
    def deploy_hosts(
        deploy_id: str, target: Optional[str] = _TARGET_QUERY,
    ) -> dtos.TwinHostsDTO:
        return service.deploy_hosts(_for_deploy(target, deploy_id), deploy_id)

    @app.get("/api/deploys/{deploy_id}/health", response_model=dtos.DeployHealthDTO)
    @_ro
    def deploy_health(
        deploy_id: str,
        host: Optional[str] = Query(
            default=None, description="Also read this host's QGA reachability "
            "and persona heartbeats (live, per host, on demand).",
        ),
        target: Optional[str] = _TARGET_QUERY,
    ) -> dtos.DeployHealthDTO:
        return service.deploy_health(
            _for_deploy(target, deploy_id), deploy_id, host=host,
            sweep=app.state.health_sweep, qga_ping=app.state.health_qga_ping,
            persona_backend=app.state.tfc_persona_backend,
            caches=app.state.live_caches,
        )

    @app.get("/api/personas", response_model=dtos.PersonaListDTO)
    @_ro
    def personas(
        twin: Optional[str] = Query(
            default=None, description="Twin id: add its local personas "
            "(twins/<id>/personas/), resolved for this request only.",
        ),
    ) -> dtos.PersonaListDTO:
        return service.list_personas(settings, twin=twin)

    @app.post(
        "/api/deploys/{deploy_id}/hosts/{host_id}/personas/plan",
        response_model=dtos.HostPersonasPlanDTO,
    )
    @_ro
    def host_personas_plan(
        deploy_id: str, host_id: str,
        req: Optional[dtos.HostPersonasRequest] = Body(default=None),
        target: Optional[str] = _TARGET_QUERY,
    ) -> dtos.HostPersonasPlanDTO:
        return service.host_personas_plan(
            _for_deploy(target, deploy_id), deploy_id, host_id,
            req or dtos.HostPersonasRequest(),
        )

    @app.put(
        "/api/deploys/{deploy_id}/hosts/{host_id}/personas",
        response_model=dtos.HostPersonasApplyResponse,
        dependencies=[Depends(write_token)],
    )
    def host_personas_apply(
        deploy_id: str, host_id: str, req: dtos.HostPersonasRequest,
        target: Optional[str] = _TARGET_QUERY,
        override_protection: Optional[str] = _OVERRIDE_QUERY,
    ) -> dtos.HostPersonasApplyResponse:
        return service.host_personas_apply(
            _for_deploy(target, deploy_id), deploy_id, host_id, req,
            persona_backend=app.state.tfc_persona_backend,
            background=app.state.tfc_host_background,
            containment_ga=app.state.containment_ga,
            override_protection=override_protection,
        )

    @app.get("/api/deploys/{deploy_id}/tfc/profile", response_model=None)
    @_ro
    def tfc_profile(
        deploy_id: str,
        format: str = Query(
            default="json", description="json (default) or yaml (the raw "
            "traffic-profile document, text/yaml).",
        ),
        target: Optional[str] = _TARGET_QUERY,
    ):
        doc = service.tfc_profile(_for_deploy(target, deploy_id), deploy_id)
        if format == "yaml":
            return Response(content=doc.yaml, media_type="text/yaml")
        if format != "json":
            raise ApiError(400, f"unknown format {format!r}; expected json or yaml")
        return doc

    # ---- the per-deploy working copy (`api/Spec.md` A2): the edit model's
    # Apply and Reset exits. Attribute changes reach the running twin through
    # the SAME tier playouts `POST /api/tfc` / `POST /api/taps` use, with the
    # same test seams; a topology change is refused as `requires-replan`. ----
    @app.get("/api/deploys/{deploy_id}/config", response_model=dtos.DeployConfigDTO)
    @_ro
    def deploy_config(
        deploy_id: str, target: Optional[str] = _TARGET_QUERY,
    ) -> dtos.DeployConfigDTO:
        return service.deploy_config(
            _for_deploy(target, deploy_id), deploy_id, exits=app.state.config_exits,
        )

    @app.put(
        "/api/deploys/{deploy_id}/config",
        response_model=dtos.DeployConfigDTO,
        dependencies=[Depends(write_token)],
    )
    def deploy_config_put(
        deploy_id: str, req: dtos.DeployConfigWriteRequest,
        target: Optional[str] = _TARGET_QUERY,
        override_protection: Optional[str] = _OVERRIDE_QUERY,
    ) -> dtos.DeployConfigDTO:
        in_store = _for_deploy(target, deploy_id)
        service.guard_protected(in_store, deploy_id=deploy_id, override=override_protection)
        return service.deploy_config_put(in_store, deploy_id, req)

    @app.post(
        "/api/deploys/{deploy_id}/config/apply",
        response_model=dtos.DeployConfigApplyDTO,
        dependencies=[Depends(write_token)],
    )
    def deploy_config_apply(
        deploy_id: str,
        req: Optional[dtos.DeployConfigApplyRequest] = Body(default=None),
        target: Optional[str] = _TARGET_QUERY,
        override_protection: Optional[str] = _OVERRIDE_QUERY,
    ) -> dtos.DeployConfigApplyDTO:
        in_store = _for_deploy(target, deploy_id)
        service.guard_protected(in_store, deploy_id=deploy_id, override=override_protection)
        return service.deploy_config_apply(
            in_store, deploy_id, req or dtos.DeployConfigApplyRequest(),
            persona_backend=app.state.tfc_persona_backend,
            tap_adapter=app.state.tap_adapter,
            tap_config_backend=app.state.tap_config_backend,
            tap_teardown_backend=app.state.tap_teardown_backend,
            exits=app.state.config_exits,
        )

    @app.post(
        "/api/deploys/{deploy_id}/tiers/{tier}",
        response_model=dtos.TierStartDTO,
        status_code=202,
        dependencies=[Depends(write_token)],
    )
    def deploy_tier_start(
        deploy_id: str, tier: str,
        req: Optional[dtos.TierStartRequest] = Body(default=None),
        target: Optional[str] = _TARGET_QUERY,
        override_protection: Optional[str] = _OVERRIDE_QUERY,
    ) -> dtos.TierStartDTO:
        """Stand up one follow-on tier (tap | tfc) on a completed te deploy
        from the twin's own default (the canvas's Start tap / Start tfc)."""
        in_store = _for_deploy(target, deploy_id)
        service.guard_protected(in_store, deploy_id=deploy_id, override=override_protection)
        return service.start_tier(
            in_store, deploy_id, tier, req or dtos.TierStartRequest(),
            background=app.state.deploy_background,
            tap_adapter=app.state.tap_adapter,
            tap_config_backend=app.state.tap_config_backend,
            persona_backend=app.state.tfc_persona_backend,
        )

    @app.post(
        "/api/deploys/{deploy_id}/config/reset",
        response_model=dtos.DeployConfigResetDTO,
        dependencies=[Depends(write_token)],
    )
    def deploy_config_reset(
        deploy_id: str,
        req: Optional[dtos.DeployConfigResetRequest] = Body(default=None),
        target: Optional[str] = _TARGET_QUERY,
        override_protection: Optional[str] = _OVERRIDE_QUERY,
    ) -> dtos.DeployConfigResetDTO:
        in_store = _for_deploy(target, deploy_id)
        service.guard_protected(in_store, deploy_id=deploy_id, override=override_protection)
        return service.deploy_config_reset(
            in_store, deploy_id, req or dtos.DeployConfigResetRequest(),
            persona_backend=app.state.tfc_persona_backend,
            tap_adapter=app.state.tap_adapter,
            tap_config_backend=app.state.tap_config_backend,
            tap_teardown_backend=app.state.tap_teardown_backend,
            tfc_teardown_backend=app.state.tfc_teardown_backend,
            exits=app.state.config_exits,
        )

    @app.post(
        "/api/deploys/{deploy_id}/config/cancel",
        response_model=dtos.ConfigExitCancelDTO,
        dependencies=[Depends(write_token)],
    )
    def deploy_config_cancel(
        deploy_id: str, target: Optional[str] = _TARGET_QUERY,
    ) -> dtos.ConfigExitCancelDTO:
        """Stop the Apply or Reset in flight on this deploy at its next op
        boundary; 409 when none is (in this process)."""
        return service.deploy_config_cancel(
            _for_deploy(target, deploy_id), deploy_id, exits=app.state.config_exits,
        )

    # ---- pcaps (deferred) ----
    @app.get("/api/pcaps", response_model=dtos.PcapListDTO)
    @_ro
    def pcaps() -> dtos.PcapListDTO:
        return service.list_pcaps(settings)

    # ---- optional SPA co-serve (Phase 4) ----
    _maybe_mount_spa(app, settings)

    return app


#: Path suffixes a built SPA serves as FILES. A request for one of these that
#: misses stays a 404 -- serving `index.html` in its place would answer a
#: broken build with an HTML document and a 200, and the browser's only clue
#: would be a MIME-type error (`docs/WORK_ORDER_console_canvas.md` S2).
#:
#: An extension list rather than "the last segment contains a dot", because
#: the Console's own client routes are FULL of dots: a segment route is
#: `/imprints/<id>/segments/10.80.20.0-24`, whose last segment would read as
#: an asset request under a dot heuristic and 404 on every reload.
#:
#: Two residuals, stated rather than hidden. **A client route whose LAST
#: segment is data ending in one of these suffixes** (an entity key spelled
#: `hostname:report.css`) would not fall back and would 404 on reload; and
#: **an asset with NO extension** (an extension-less `manifest`, a font
#: without a suffix) falls back to `index.html` with a 200 instead of 404ing,
#: because this rule can only see the name. Both trades are deliberate: a
#: data-derived URL segment can collide with an asset extension either way,
#: this build ships no extension-less assets, and a broken asset silently
#: answering 200 with HTML costs more debugging than one unreachable deep
#: link. Adding an extension-less asset means adding it to a deny-list here.
_SPA_ASSET_SUFFIXES = frozenset(
    {
        "js", "mjs", "cjs", "css", "map", "json", "txt", "xml", "wasm",
        "ico", "png", "jpg", "jpeg", "gif", "svg", "webp", "avif",
        "woff", "woff2", "ttf", "otf", "eot",
    }
)


def _spa_wants_index(path: str) -> bool:
    """Should a miss on `path` fall back to `index.html`?

    Two exclusions, both load-bearing:

    * **`api/...` never falls back.** An unknown API path reaches the mount
      only because no route matched it, and answering that with the SPA's
      HTML and a 200 would turn a typo'd endpoint into a silent success --
      the caller parses HTML as JSON and reports something unrelated.
    * **An asset suffix never falls back** (see `_SPA_ASSET_SUFFIXES`).
    """
    if path == "api" or path.startswith("api/"):
        return False
    last = path.rsplit("/", 1)[-1]
    suffix = last.rsplit(".", 1)[-1].lower() if "." in last else ""
    return suffix not in _SPA_ASSET_SUFFIXES


def _spa_cache_control(path: str) -> Optional[str]:
    """The two-line cache policy a hashed-asset build needs.

    Vite emits `assets/index-<contenthash>.js`, so an asset's URL changes
    whenever its bytes do: those may be cached forever. `index.html` names
    the current hashes and must NOT be, or a browser holding yesterday's
    index asks for an asset the new build deleted and the Console fails to
    boot with a blank page after every deploy. The `no-cache` here means
    "revalidate", not "do not store" -- the 304 is still free.

    `None` for anything else (a stray file at the SPA root), which leaves
    Starlette's own headers untouched rather than asserting a policy for a
    file this build does not know about.
    """
    # `""` and a trailing slash are the DIRECTORY-index requests: with
    # `html=True`, `GET /` reaches `get_response("")` and Starlette serves
    # `index.html` for it, so the root would otherwise miss the policy that
    # exists precisely for the root.
    if (
        path in ("", ".")
        or path.endswith("/")
        or path == "index.html"
        or path.endswith("/index.html")
    ):
        return "no-cache"
    if path == "assets" or path.startswith("assets/"):
        return "public, max-age=31536000, immutable"
    return None


def _maybe_mount_spa(app: FastAPI, settings: ApiSettings) -> None:
    """Mount a built SPA at / if AXRANGE_SPA_DIR points at one (Phase 4).

    Headless by default (empty spa_dir) — the API runs without a UI for the AI
    core / CI.

    **With the history fallback the Console needs** (`docs/design/
    15-console-canvas.md` §7, `docs/WORK_ORDER_console_canvas.md` S2): the URL
    IS the zoom, so a bookmark, a reload or a pasted link lands directly on
    `/imprints/<id>/segments/10.80.20.0-24`, a path that exists only in the
    SPA's router and as no file on disk. `StaticFiles(html=True)` serves
    `index.html` for `/` and for a directory, and 404s everything else — which
    made every deep Console route a 404 on reload while working perfectly when
    navigated to in-app. The fallback is the same `try_files $uri /index.html`
    an nginx deployment would need, kept here so the co-served path and the
    proxied path behave alike.
    """
    if not settings.spa_dir:
        return
    from pathlib import Path

    spa = Path(settings.spa_dir)
    if not spa.is_dir():
        return
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from starlette.responses import Response as StarletteResponse
    from starlette.types import Scope
    from fastapi.staticfiles import StaticFiles

    class _SpaFiles(StaticFiles):
        """`StaticFiles` plus the SPA history fallback and its cache policy
        (see above, and `_spa_cache_control`)."""

        async def get_response(self, path: str, scope: Scope) -> StarletteResponse:
            try:
                response = await super().get_response(path, scope)
            except StarletteHTTPException as exc:
                if exc.status_code != 404 or not _spa_wants_index(path):
                    raise
                # If `index.html` itself is missing, this raises 404 in turn
                # -- an empty/half-built `--spa-dir` reports as missing rather
                # than as a mysterious empty page.
                response = await super().get_response("index.html", scope)
                path = "index.html"
            cache = _spa_cache_control(path)
            if cache and response.status_code < 400:
                response.headers["Cache-Control"] = cache
            return response

    app.mount("/", _SpaFiles(directory=str(spa), html=True), name="spa")
