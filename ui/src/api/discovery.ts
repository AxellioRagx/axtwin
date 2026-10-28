import type { Requester } from "./client";
import type {
  DiscoveryPluginList,
  DiscoveryRun,
  DiscoveryRunList,
  DiscoverySource,
  DiscoverySourceList,
  ImprintList,
  ImprintSummary,
  ProbeResult,
  SourceRunRequest,
  SourceUpdate,
  SourceWrite,
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
  /** Forget a finished run's record; the imprint it installed stays. */
  deleteRun(id: string): Promise<void>;
  listImprints(signal?: AbortSignal): Promise<ImprintList>;
  getImprint(id: string, signal?: AbortSignal): Promise<ImprintSummary>;
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
    getImprint: (id, signal) => request("GET", `/api/imprints/${enc(id)}`, { signal }),
  };
}
