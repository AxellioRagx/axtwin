import type { Requester } from "./client";
import type {
  AssertionsDoc,
  CastRequest,
  CastResponse,
  CastRoster,
  ConfigExitCancel,
  CreateDeployResponse,
  DeployConfig,
  DestroyResult,
  DeployConfigApply,
  DeployConfigReset,
  DeployList,
  DiscoveryPluginList,
  DiscoveryRun,
  DiscoveryRunList,
  DiscoverySource,
  DiscoverySourceList,
  Imprint,
  ImprintList,
  LayoutDoc,
  LayoutWrite,
  PersonaList,
  PlanSummary,
  ProbeResult,
  SourceRunRequest,
  SourceUpdate,
  SourceWrite,
  TapStatus,
  TargetHealth,
  TargetList,
  Topology,
  TopologyExport,
  TwinConfigDetail,
  TwinConfigList,
  TwinConfigWrite,
  TwinDeployRequest,
  TwinList,
  TwinSummary,
  TwinTree,
  ValidateResult,
  Whoami,
} from "./types";

/** The typed calls this console makes. No call carries a credential (A8). */
export interface Api {
  /** Who this caller is under the deployment's write policy, and where to sign in. */
  whoami(signal?: AbortSignal): Promise<Whoami>;
  listPlugins(signal?: AbortSignal): Promise<DiscoveryPluginList>;
  listSources(signal?: AbortSignal): Promise<DiscoverySourceList>;
  getSource(id: string, signal?: AbortSignal): Promise<DiscoverySource>;
  createSource(body: SourceWrite): Promise<DiscoverySource>;
  updateSource(id: string, body: SourceUpdate): Promise<DiscoverySource>;
  deleteSource(id: string): Promise<void>;
  probeSource(id: string): Promise<ProbeResult>;
  startRun(sourceId: string, body: SourceRunRequest): Promise<DiscoveryRun>;
  listRuns(signal?: AbortSignal): Promise<DiscoveryRunList>;
  getRun(id: string, signal?: AbortSignal): Promise<DiscoveryRun>;
  cancelRun(id: string): Promise<DiscoveryRun>;
  /** Forget a finished run's record; the imprint it baked stays. */
  deleteRun(id: string): Promise<void>;
  // Imprints: what discovery produced (the PITs in the IR store).
  listImprints(signal?: AbortSignal): Promise<ImprintList>;
  getImprint(pit: string, signal?: AbortSignal): Promise<Imprint>;
  /** Give an imprint a new name; its id never changes. */
  renameImprint(pit: string, name: string): Promise<Imprint>;
  /** Save-as: a new imprint with the same rows and the operator files copied. */
  copyImprint(pit: string, name: string): Promise<Imprint>;
  /** Remove the imprint's rows and operator files; the twins cast from it stand. */
  deleteImprint(pit: string): Promise<void>;
  getAssertions(pit: string, signal?: AbortSignal): Promise<AssertionsDoc>;
  /** The document as GET returned it, edited; `etag` inside is the precondition. */
  putAssertions(pit: string, doc: Record<string, unknown>): Promise<AssertionsDoc>;
  /** Compile the imprint, with its assertions, into a twin in the catalog. */
  castImprint(pit: string, body: CastRequest): Promise<CastResponse>;
  // Twins: the catalog of deployable twin definitions.
  listTwins(signal?: AbortSignal): Promise<TwinList>;
  getTwin(id: string, signal?: AbortSignal): Promise<TwinSummary>;
  /** The bundle as segment -> node group -> intent and personas. */
  getTwinTree(id: string, signal?: AbortSignal): Promise<TwinTree>;
  /** The compiled bundle's rosters: each node group's observed members, each segment's observed CIDR. */
  getCast(id: string, signal?: AbortSignal): Promise<CastRoster>;
  /** Remove the twin from the catalog; refused under a live deploy. */
  deleteTwin(id: string): Promise<void>;
  // The design canvas (`features/edit-canvas/SPEC.md`): graphs, positions, the palette's sources.
  /** The AUTHORED graph: nodes typed host|router|switch|network, edges host -> network with the address. */
  getScenarioTopology(id: string, signal?: AbortSignal): Promise<Topology>;
  /** The CAST graph with observed evidence; 404 when the twin has no topology export. */
  getTwinTopology(id: string, signal?: AbortSignal): Promise<TopologyExport>;
  /** The positions side-car; `exists: false` with empty pins when none was saved yet. */
  getLayout(id: string, signal?: AbortSignal): Promise<LayoutDoc>;
  /** Replace the side-car; `etag` in the body is the precondition (412 when it moved). */
  putLayout(id: string, doc: LayoutWrite): Promise<LayoutDoc>;
  listTwinConfigs(signal?: AbortSignal): Promise<TwinConfigList>;
  listPersonas(signal?: AbortSignal): Promise<PersonaList>;
  // The edit model (`api/Spec.md` A2, A3): the document, Save / Save-As, and a deploy's working copy.
  /** The twin-config with its `document` (the bundle's text files); 404 when the catalog has no such entry. */
  getTwinConfig(id: string, signal?: AbortSignal): Promise<TwinConfigDetail>;
  /** Save-As: a new twin-config under `body.id`; an existing id is refused, never overwritten. */
  createTwinConfig(body: TwinConfigWrite): Promise<TwinConfigDetail>;
  /** Save (undeployed) or Persist (from a working copy): overwrite the baseline. */
  putTwinConfig(id: string, body: TwinConfigWrite): Promise<TwinConfigDetail>;
  /** The deploys the API knows; the canvas reads which of them stand on a twin. */
  listDeploys(signal?: AbortSignal): Promise<DeployList>;
  /** The working copy with its per-change classification. */
  getDeployConfig(deployId: string, signal?: AbortSignal): Promise<DeployConfig>;
  /** Replace the working copy; refused with the validator's reason when invalid, the stored copy unchanged. */
  putDeployConfig(deployId: string, files: Record<string, string>): Promise<DeployConfig>;
  /** Push the working copy's attribute delta onto the running twin; refused naming the paths when a change is topology. */
  applyDeployConfig(deployId: string): Promise<DeployConfigApply>;
  /** Discard the working copy and revert what Apply pushed (best effort; `tiers` replays named baseline tiers with or without a copy). */
  resetDeployConfig(deployId: string, body?: { tiers?: string[] }): Promise<DeployConfigReset>;
  /** `POST /api/deploys/{id}/tiers/{tier}`: stand one follow-on tier up from the twin's default (detached). */
  startTier(deployId: string, tier: string, body?: { target_node?: string }): Promise<{ deploy_id: string; tier: string; status: string }>;
  /** Stop the Apply or Reset in flight at its next op boundary; 409 when none is. */
  cancelDeployConfigExit(deployId: string): Promise<ConfigExitCancel>;
  /** Validate a document (`{id, files}`) through the Save / Persist path without writing; a FAIL is a 200 with the findings. */
  validateDocument(document: { id: string; files: Record<string, string> }, signal?: AbortSignal): Promise<ValidateResult>;
  // Deploy a twin from the console (2026-11-08): the pure plan, then the deploy pinned to it.
  listTargets(signal?: AbortSignal): Promise<TargetList>;
  /** One status per lab component of a target (`docs/design/targets-and-lab.md` §2.2). */
  getTargetHealth(name: string, signal?: AbortSignal): Promise<TargetHealth>;
  /** The live deploys recorded on one target (`GET /api/deploys?target=`). */
  listTargetDeploys(target: string, signal?: AbortSignal): Promise<DeployList>;
  /** The tap playout standing on a twin (a te deploy id): which sensors its bindings deliver to. */
  getTaps(twinDeployId: string, signal?: AbortSignal): Promise<TapStatus>;
  /** Plan a twin's deploy without running anything; with `target`, against that target, carrying its preflight. */
  planTwin(id: string, body?: { target_node?: string }, signal?: AbortSignal, target?: string): Promise<PlanSummary>;
  /** Start the twin's te deploy (write-gated, lease-guarded, detached); with `target`, on that target, refused
   *  (409, `code: preflight`) while its preflight is not clean. */
  deployTwin(id: string, body: TwinDeployRequest, target?: string): Promise<CreateDeployResponse>;
  /** Destroy: bring the standing deploy down, its tfc / tap / exr children first (`axrange te destroy`). */
  destroyDeploy(deployId: string): Promise<DestroyResult>;
}

const enc = encodeURIComponent;

export function makeApi(request: Requester): Api {
  return {
    whoami: (signal) => request("GET", "/api/whoami", { signal }),
    listPlugins: (signal) => request("GET", "/api/discovery/plugins", { signal }),
    listSources: (signal) => request("GET", "/api/discovery/sources", { signal }),
    getSource: (id, signal) => request("GET", `/api/discovery/sources/${enc(id)}`, { signal }),
    createSource: (body) => request("POST", "/api/discovery/sources", { body }),
    updateSource: (id, body) => request("PUT", `/api/discovery/sources/${enc(id)}`, { body }),
    deleteSource: (id) => request("DELETE", `/api/discovery/sources/${enc(id)}`, {}),
    probeSource: (id) => request("POST", `/api/discovery/sources/${enc(id)}/probe`, {}),
    startRun: (sourceId, body) => request("POST", `/api/discovery/sources/${enc(sourceId)}/runs`, { body }),
    listRuns: (signal) => request("GET", "/api/discovery/runs", { signal }),
    getRun: (id, signal) => request("GET", `/api/discovery/runs/${enc(id)}`, { signal }),
    cancelRun: (id) => request("POST", `/api/discovery/runs/${enc(id)}/cancel`, {}),
    deleteRun: (id) => request("DELETE", `/api/discovery/runs/${enc(id)}`, {}),
    listImprints: (signal) => request("GET", "/api/imprints", { signal }),
    getImprint: (pit, signal) => request("GET", `/api/imprints/${enc(pit)}`, { signal }),
    renameImprint: (pit, name) => request("PATCH", `/api/imprints/${enc(pit)}`, { body: { name } }),
    copyImprint: (pit, name) => request("POST", `/api/imprints/${enc(pit)}/copy`, { body: { name } }),
    deleteImprint: (pit) => request("DELETE", `/api/imprints/${enc(pit)}`, {}),
    getAssertions: (pit, signal) => request("GET", `/api/imprints/${enc(pit)}/assertions`, { signal }),
    putAssertions: (pit, doc) => request("PUT", `/api/imprints/${enc(pit)}/assertions`, { body: doc }),
    castImprint: (pit, body) => request("POST", `/api/imprints/${enc(pit)}/cast`, { body }),
    listTwins: (signal) => request("GET", "/api/twins", { signal }),
    getTwin: (id, signal) => request("GET", `/api/twins/${enc(id)}`, { signal }),
    getTwinTree: (id, signal) => request("GET", `/api/twins/${enc(id)}/tree`, { signal }),
    getCast: (id, signal) => request("GET", `/api/twins/${enc(id)}/cast`, { signal }),
    deleteTwin: (id) => request("DELETE", `/api/twins/${enc(id)}`, {}),
    getScenarioTopology: (id, signal) => request("GET", `/api/scenarios/${enc(id)}/topology`, { signal }),
    getTwinTopology: (id, signal) => request("GET", `/api/twins/${enc(id)}/topology`, { signal }),
    getLayout: (id, signal) => request("GET", `/api/twins/${enc(id)}/layout`, { signal }),
    putLayout: (id, doc) => request("PUT", `/api/twins/${enc(id)}/layout`, { body: doc }),
    listTwinConfigs: (signal) => request("GET", "/api/twin-configs", { signal }),
    listPersonas: (signal) => request("GET", "/api/personas", { signal }),
    // The server includes the bundle `document` (the canvas's edit mode) only
    // for a caller its write policy accepts; the summary is open.
    getTwinConfig: (id, signal) => request("GET", `/api/twin-configs/${enc(id)}`, { signal }),
    createTwinConfig: (body) => request("POST", "/api/twin-configs", { body }),
    putTwinConfig: (id, body) => request("PUT", `/api/twin-configs/${enc(id)}`, { body }),
    listDeploys: (signal) => request("GET", "/api/deploys", { signal }),
    getDeployConfig: (deployId, signal) => request("GET", `/api/deploys/${enc(deployId)}/config`, { signal }),
    putDeployConfig: (deployId, files) => request("PUT", `/api/deploys/${enc(deployId)}/config`, { body: { files } }),
    applyDeployConfig: (deployId) => request("POST", `/api/deploys/${enc(deployId)}/config/apply`, { body: {} }),
    resetDeployConfig: (deployId, body) => request("POST", `/api/deploys/${enc(deployId)}/config/reset`, { body: body ?? {} }),
    startTier: (deployId, tier, body) => request("POST", `/api/deploys/${enc(deployId)}/tiers/${enc(tier)}`, { body: body ?? {} }),
    cancelDeployConfigExit: (deployId) => request("POST", `/api/deploys/${enc(deployId)}/config/cancel`, { body: {} }),
    validateDocument: (document, signal) => request("POST", "/api/validate", { body: { document }, signal }),
    listTargets: (signal) => request("GET", "/api/targets", { signal }),
    getTargetHealth: (name, signal) => request("GET", `/api/targets/${enc(name)}/health`, { signal }),
    listTargetDeploys: (target, signal) => request("GET", `/api/deploys?target=${enc(target)}`, { signal }),
    getTaps: (twinDeployId, signal) => request("GET", `/api/taps?target=${enc(twinDeployId)}`, { signal }),
    planTwin: (id, body, signal, target) => request("POST", `/api/twins/${enc(id)}/plan${target ? `?target=${enc(target)}` : ""}`, { body: body ?? {}, signal }),
    deployTwin: (id, body, target) => request("POST", `/api/twins/${enc(id)}/deploy${target ? `?target=${enc(target)}` : ""}`, { body }),
    // The confirmation rides the query: some proxies drop a DELETE's body (the route takes either).
    destroyDeploy: (deployId) => request("DELETE", `/api/deploys/${enc(deployId)}?allow_destroy=true`, {}),
  };
}
