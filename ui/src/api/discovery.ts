import type { Requester } from "./client";
import type {
  AssertionsDoc,
  CastRequest,
  CastResponse,
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
  ProbeResult,
  SourceRunRequest,
  SourceUpdate,
  SourceWrite,
  Topology,
  TopologyExport,
  TwinConfigList,
  TwinList,
  TwinSummary,
  TwinTree,
} from "./types";

/** The typed calls this console makes. Writes carry the token; reads do not. */
export interface Api {
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
}

const enc = encodeURIComponent;

export function makeApi(request: Requester): Api {
  return {
    listPlugins: (signal) => request("GET", "/api/discovery/plugins", { signal }),
    listSources: (signal) => request("GET", "/api/discovery/sources", { signal }),
    getSource: (id, signal) => request("GET", `/api/discovery/sources/${enc(id)}`, { signal }),
    createSource: (body) => request("POST", "/api/discovery/sources", { body, auth: true }),
    updateSource: (id, body) => request("PUT", `/api/discovery/sources/${enc(id)}`, { body, auth: true }),
    deleteSource: (id) => request("DELETE", `/api/discovery/sources/${enc(id)}`, { auth: true }),
    probeSource: (id) => request("POST", `/api/discovery/sources/${enc(id)}/probe`, { auth: true }),
    startRun: (sourceId, body) => request("POST", `/api/discovery/sources/${enc(sourceId)}/runs`, { body, auth: true }),
    listRuns: (signal) => request("GET", "/api/discovery/runs", { signal }),
    getRun: (id, signal) => request("GET", `/api/discovery/runs/${enc(id)}`, { signal }),
    cancelRun: (id) => request("POST", `/api/discovery/runs/${enc(id)}/cancel`, { auth: true }),
    deleteRun: (id) => request("DELETE", `/api/discovery/runs/${enc(id)}`, { auth: true }),
    listImprints: (signal) => request("GET", "/api/imprints", { signal }),
    getImprint: (pit, signal) => request("GET", `/api/imprints/${enc(pit)}`, { signal }),
    renameImprint: (pit, name) => request("PATCH", `/api/imprints/${enc(pit)}`, { body: { name }, auth: true }),
    copyImprint: (pit, name) => request("POST", `/api/imprints/${enc(pit)}/copy`, { body: { name }, auth: true }),
    deleteImprint: (pit) => request("DELETE", `/api/imprints/${enc(pit)}`, { auth: true }),
    getAssertions: (pit, signal) => request("GET", `/api/imprints/${enc(pit)}/assertions`, { signal }),
    putAssertions: (pit, doc) => request("PUT", `/api/imprints/${enc(pit)}/assertions`, { body: doc, auth: true }),
    castImprint: (pit, body) => request("POST", `/api/imprints/${enc(pit)}/cast`, { body, auth: true }),
    listTwins: (signal) => request("GET", "/api/twins", { signal }),
    getTwin: (id, signal) => request("GET", `/api/twins/${enc(id)}`, { signal }),
    getTwinTree: (id, signal) => request("GET", `/api/twins/${enc(id)}/tree`, { signal }),
    deleteTwin: (id) => request("DELETE", `/api/twins/${enc(id)}`, { auth: true }),
    getScenarioTopology: (id, signal) => request("GET", `/api/scenarios/${enc(id)}/topology`, { signal }),
    getTwinTopology: (id, signal) => request("GET", `/api/twins/${enc(id)}/topology`, { signal }),
    getLayout: (id, signal) => request("GET", `/api/twins/${enc(id)}/layout`, { signal }),
    putLayout: (id, doc) => request("PUT", `/api/twins/${enc(id)}/layout`, { body: doc, auth: true }),
    listTwinConfigs: (signal) => request("GET", "/api/twin-configs", { signal }),
    listPersonas: (signal) => request("GET", "/api/personas", { signal }),
  };
}
