/**
 * The RUN-TIME client: Range, Twin, Catalog -- what is deployed, on which
 * cluster, and the guarded writes against it
 * (`docs/reference/ui-consolidation/04-api-contract.md`).
 *
 * A separate interface from the Design client (`client.ts`'s `ConsoleApi`) on
 * purpose: the Design area's tests hand the shell a complete, typed
 * `ConsoleApi` double, and growing that one interface by twenty methods would
 * make every one of them restate endpoints the canvas never calls. Both
 * clients share ONE requester (`makeRequester`), so the error vocabulary, the
 * base URL and the in-memory write token are one thing.
 *
 * `target` is the cluster target's NAME (`?target=`), passed on every read
 * that takes a deploy id -- except `/api/tfc` and `/api/taps`, where
 * `?target=` already means *the twin* (contract §"Cluster targets").
 */
import { makeRequester, type ClientOptions } from "./client";
import type {
  AccessFacts,
  CreateDeployRequest,
  CreateDeployResponse,
  DeployDetail,
  DestroyResponse,
  HealthDoc,
  O2Info,
  PersonaApplyResponse,
  PersonaCatalog,
  PersonaChangeRequest,
  PersonaPlan,
  PlanSource,
  PlanSummary,
  ProfileExport,
  RangeDoc,
  ScenarioList,
  ScenarioTopology,
  TargetList,
  TwinConfigDetail,
  TwinConfigList,
  TwinHostsDoc,
  ValidateResult,
} from "./runtimeTypes";

export interface RuntimeApi {
  getRange(signal?: AbortSignal): Promise<RangeDoc>;
  getTargets(signal?: AbortSignal): Promise<TargetList>;
  getDeploy(id: string, target: string | null, signal?: AbortSignal): Promise<DeployDetail>;
  getHosts(id: string, target: string | null, signal?: AbortSignal): Promise<TwinHostsDoc>;
  getHealth(id: string, target: string | null, host: string | null, signal?: AbortSignal): Promise<HealthDoc>;
  getAccess(id: string, target: string | null, signal?: AbortSignal): Promise<AccessFacts>;
  getPersonas(twin: string | null, signal?: AbortSignal): Promise<PersonaCatalog>;
  planHostPersonas(id: string, host: string, target: string | null, body: PersonaChangeRequest): Promise<PersonaPlan>;
  /** `overrideProtection` = the te deploy id, typed by the operator, for a
   *  protected twin (`?override_protection=`); `null` otherwise. */
  putHostPersonas(
    id: string,
    host: string,
    target: string | null,
    body: PersonaChangeRequest,
    overrideProtection?: string | null,
  ): Promise<PersonaApplyResponse>;
  exportProfile(id: string, target: string | null): Promise<ProfileExport>;
  listTwinConfigs(signal?: AbortSignal): Promise<TwinConfigList>;
  getTwinConfig(id: string, signal?: AbortSignal): Promise<TwinConfigDetail>;
  listScenarios(signal?: AbortSignal): Promise<ScenarioList>;
  getScenarioTopology(id: string, signal?: AbortSignal): Promise<ScenarioTopology>;
  validate(source: PlanSource): Promise<ValidateResult>;
  plan(source: PlanSource): Promise<PlanSummary>;
  createDeploy(body: CreateDeployRequest, target: string | null): Promise<CreateDeployResponse>;
  destroyDeploy(id: string, target: string | null, overrideProtection?: string | null): Promise<DestroyResponse>;
  getO2Info(signal?: AbortSignal): Promise<O2Info>;
}

function enc(id: string): string {
  return encodeURIComponent(id);
}

function withTarget(target: string | null, extra: Record<string, string> = {}): Record<string, string> {
  return target ? { ...extra, target } : extra;
}

export function createRuntimeClient(options: ClientOptions = {}): RuntimeApi {
  const request = makeRequester(options);
  return {
    getRange: (signal) => request<RangeDoc>("GET", "/api/range", { signal }),
    getTargets: (signal) => request<TargetList>("GET", "/api/targets", { signal }),
    getDeploy: (id, target, signal) =>
      request<DeployDetail>("GET", `/api/deploys/${enc(id)}`, { query: withTarget(target), signal }),
    getHosts: (id, target, signal) =>
      request<TwinHostsDoc>("GET", `/api/deploys/${enc(id)}/hosts`, { query: withTarget(target), signal }),
    getHealth: (id, target, host, signal) =>
      request<HealthDoc>("GET", `/api/deploys/${enc(id)}/health`, {
        query: withTarget(target, host ? { host } : {}),
        signal,
      }),
    getAccess: (id, target, signal) =>
      request<AccessFacts>("GET", `/api/deploys/${enc(id)}/access`, { query: withTarget(target), signal }),
    getPersonas: (twin, signal) =>
      request<PersonaCatalog>("GET", "/api/personas", { query: twin ? { twin } : {}, signal }),
    planHostPersonas: (id, host, target, body) =>
      request<PersonaPlan>("POST", `/api/deploys/${enc(id)}/hosts/${enc(host)}/personas/plan`, {
        query: withTarget(target),
        body,
      }),
    putHostPersonas: (id, host, target, body, overrideProtection) =>
      request<PersonaApplyResponse>("PUT", `/api/deploys/${enc(id)}/hosts/${enc(host)}/personas`, {
        query: withTarget(target, overrideProtection ? { override_protection: overrideProtection } : {}),
        body,
        auth: true,
      }),
    exportProfile: (id, target) =>
      request<ProfileExport>("GET", `/api/deploys/${enc(id)}/tfc/profile`, { query: withTarget(target) }),
    listTwinConfigs: (signal) => request<TwinConfigList>("GET", "/api/twin-configs", { signal }),
    getTwinConfig: (id, signal) => request<TwinConfigDetail>("GET", `/api/twin-configs/${enc(id)}`, { signal }),
    listScenarios: (signal) => request<ScenarioList>("GET", "/api/scenarios", { signal }),
    getScenarioTopology: (id, signal) =>
      request<ScenarioTopology>("GET", `/api/scenarios/${enc(id)}/topology`, { signal }),
    validate: (source) => request<ValidateResult>("POST", "/api/validate", { body: source }),
    plan: (source) => request<PlanSummary>("POST", "/api/plan", { body: source }),
    createDeploy: (body, target) =>
      request<CreateDeployResponse>("POST", "/api/deploys", { query: withTarget(target), body, auth: true }),
    // `allow_destroy` rides the QUERY as well as the body: some proxies drop a
    // DELETE's body (the server honours either).
    destroyDeploy: (id, target, overrideProtection) =>
      request<DestroyResponse>("DELETE", `/api/deploys/${enc(id)}`, {
        query: withTarget(target, {
          allow_destroy: "true",
          ...(overrideProtection ? { override_protection: overrideProtection } : {}),
        }),
        body: { allow_destroy: true },
        auth: true,
      }),
    getO2Info: (signal) => request<O2Info>("GET", "/api/o2/info", { signal }),
  };
}
