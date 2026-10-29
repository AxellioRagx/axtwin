/**
 * An in-memory `Api`: the shapes the control plane answers, with no fetch.
 * Tests seed it and read back what the console sent.
 */
import { ApiError } from "../../src/api/client";
import type { Api } from "../../src/api/discovery";
import type {
  DiscoveryPlugin,
  DiscoveryRun,
  DiscoverySource,
  AssertionsDoc,
  Imprint,
  ProbeResult,
  TwinSummary,
  TwinTree,
  SourceRunRequest,
  SourceUpdate,
  SourceWrite,
} from "../../src/api/types";

export const AXQUERY: DiscoveryPlugin = {
  name: "axquery",
  source_kind: "http",
  config_schema: {
    type: "object",
    properties: {
      source: { type: "string", description: "an axquery instance, addressed as 'axquery:<base-url>'" },
      ts_from: { type: "number", description: "epoch seconds, inclusive" },
      ts_to: { type: "number", description: "epoch seconds, exclusive" },
      vantage: { type: "string", description: "where the tap sat" },
      network: { type: "string", description: "which network this capture is OF" },
      window: { type: "integer", description: "window seconds" },
      subbuckets: { type: "integer", description: "sub-buckets per window" },
    },
    required: ["source"],
    additionalProperties: false,
  },
};

export const ZEEK: DiscoveryPlugin = {
  name: "zeek",
  source_kind: "path",
  config_schema: { type: "object", properties: { source: { type: "string", description: "a Zeek log directory" } }, required: ["source"] },
};

/** A plugin the console has never heard of: it must still get a form (C1.1). */
export const FOO_HTTP: DiscoveryPlugin = {
  name: "foo-http",
  source_kind: "http",
  config_schema: {
    type: "object",
    properties: {
      source: { type: "string", description: "a foo collector, addressed as 'foo-http:<base-url>'" },
      shard: { type: "integer", description: "which shard to read" },
      ts_from: { type: "number" },
      ts_to: { type: "number" },
    },
    required: ["source"],
    additionalProperties: false,
  },
};

export const SPAN = { first_ts: 1788723036, last_ts: 1791315036 };

export function tunnelSource(over: Partial<DiscoverySource> = {}): DiscoverySource {
  return {
    id: "src-1",
    name: "corp-apcn-reader",
    plugin: "axquery",
    endpoint: "http://127.0.0.1:9090",
    access: {
      transport: "ssh_tunnel",
      auth: "none",
      user: null,
      ssh: { host: "corp-apcn-reader.swlab.axellio.dom", user: "fxadmin", key_path: "/home/op/.ssh/id_ed25519", port: 22 },
    },
    has_password: false,
    has_api_key: false,
    defaults: { days: 10, network: "corp-apcn" },
    last_probe: null,
    created_at: "2026-10-27T15:56:31Z",
    updated_at: "2026-10-27T15:56:31Z",
    ...over,
  };
}

export interface FakeState {
  plugins: DiscoveryPlugin[];
  sources: DiscoverySource[];
  runs: DiscoveryRun[];
  /** The twin catalog, by id. */
  twins: Record<string, TwinSummary>;
  /** Trees by twin id; a twin without one answers an empty tree. */
  trees: Record<string, TwinTree>;
  /** The imprints (PITs) in the IR store. */
  imprints: Imprint[];
  /** Each imprint's assertions document, by pit id, once saved. */
  assertions: Record<string, AssertionsDoc>;
  /** Catalog folders the listing reports as unreadable. */
  skippedTwins: Array<{ id: string; reason: string }>;
  /** What the imprint listing warns about (a locked store). */
  imprintWarnings: string[];
  /** Every write the console made, in order. */
  writes: Array<{ op: string; id?: string; body?: unknown }>;
  /** What the next create/probe/startRun should do instead of succeeding. */
  refuse: { create?: ApiError; probe?: ApiError; run?: ApiError; cancel?: ApiError; delete?: ApiError; deleteImprint?: ApiError; deleteTwin?: ApiError; rename?: ApiError; copy?: ApiError; cast?: ApiError; assertions?: ApiError };
  /** How a run progresses: each `getRun` moves it one step along this list. */
  runSteps: Array<Partial<DiscoveryRun>>;
}

export function fakeApi(seed: Partial<FakeState> = {}): { api: Api; state: FakeState } {
  const state: FakeState = {
    plugins: [AXQUERY, ZEEK],
    sources: [],
    runs: [],
    twins: {},
    trees: {},
    imprints: [],
    assertions: {},
    skippedTwins: [],
    imprintWarnings: [],
    writes: [],
    refuse: {},
    runSteps: [],
    ...seed,
  };
  let nextId = 100;
  const notFound = (what: string) => new ApiError({ kind: "not-found", status: 404, message: `no ${what}`, url: "" });

  const findImprint = (pit: string) => state.imprints.find((x) => x.pit_id === pit) ?? state.imprints.find((x) => x.name === pit);
  const twinsOf = (pit: string) => Object.values(state.twins).filter((t) => t.pit_id === pit).map((t) => ({ id: t.id }));
  const emptyAssertions = (pit: string): AssertionsDoc => ({
    imprint_id: pit, path: `/store/pits/${pit}/${pit}.assertions.yaml`, exists: false, etag: null,
    schema: "axr-assertions/1", imprint: pit, as_of: null, source: null, add: [], override: [], suppress: [],
  });
  const api: Api = {
    async listPlugins() {
      return { plugins: state.plugins };
    },
    async listSources() {
      return { sources: state.sources };
    },
    async getSource(id) {
      const s = state.sources.find((x) => x.id === id);
      if (!s) throw notFound(`discovery source '${id}'`);
      return s;
    },
    async createSource(body: SourceWrite) {
      state.writes.push({ op: "create", body });
      if (state.refuse.create) throw state.refuse.create;
      const src = tunnelSource({
        id: `src-${nextId++}`,
        name: body.name,
        plugin: body.plugin,
        endpoint: body.endpoint,
        access: {
          transport: body.access.transport,
          auth: body.access.auth,
          user: body.access.user ?? null,
          ssh: body.access.ssh ?? null,
        },
        has_password: Boolean(body.password),
        has_api_key: Boolean(body.api_key),
        defaults: body.defaults,
      });
      state.sources.push(src);
      return src;
    },
    async updateSource(id, body: SourceUpdate) {
      state.writes.push({ op: "update", id, body });
      const s = await api.getSource(id);
      Object.assign(s, { name: body.name ?? s.name, endpoint: body.endpoint ?? s.endpoint });
      return s;
    },
    async deleteSource(id) {
      state.writes.push({ op: "delete", id });
      state.sources = state.sources.filter((x) => x.id !== id);
    },
    async probeSource(id) {
      state.writes.push({ op: "probe", id });
      if (state.refuse.probe) throw state.refuse.probe;
      const s = await api.getSource(id);
      const probe: ProbeResult = {
        source_id: id,
        reachable: true,
        error: null,
        ...SPAN,
        vantage: "axellio-hq/corp-apcn-rdr1:12",
        extra: {
          chunks: 69,
          packets: 16893421675,
          bytes: 15494524704945,
          entities: 2558306,
          events: 253402708,
          sites: [{ location: "axellio-hq", systems: [{ name: "corp-apcn-rdr1", chunks: 69, first_ts: SPAN.first_ts, last_ts: SPAN.last_ts, streams: 12 }] }],
        },
        via: "ssh fxadmin@corp-apcn-reader.swlab.axellio.dom:22 -> 127.0.0.1:9090 via 127.0.0.1:37219",
        probed_at: "2026-10-27T16:00:00Z",
      };
      s.last_probe = probe;
      return probe;
    },
    async startRun(sourceId, body: SourceRunRequest) {
      state.writes.push({ op: "run", id: sourceId, body });
      if (state.refuse.run) throw state.refuse.run;
      const s = await api.getSource(sourceId);
      const run: DiscoveryRun = {
        id: `disc-${nextId++}`,
        status: "running",
        plugin: s.plugin,
        name: body.name,
        imprint_id: body.name,
        stages_done: [],
        stages: ["ingest", "bake"],
        pit_id: null,
        error: null,
        created_at: "2026-10-27T16:03:18Z",
        finished_at: null,
        source_id: sourceId,
        started_at: "2026-10-27T16:03:18Z",
        ts_from: null,
        ts_to: null,
        via: null,
        progress_ts: null,
        progress_at: null,
        cancel_requested_at: null,
      };
      state.runs.unshift(run);
      return { ...run };
    },
    async cancelRun(id) {
      state.writes.push({ op: "cancel", id });
      if (state.refuse.cancel) throw state.refuse.cancel;
      const r = state.runs.find((x) => x.id === id);
      if (!r) throw notFound(`discovery run '${id}'`);
      r.cancel_requested_at = "2026-10-28T12:00:00Z";
      // The next poll finds the stage dead.
      state.runSteps.unshift({ status: "cancelled", finished_at: "2026-10-28T12:00:03Z", error: "ingest cancelled by the operator" });
      return { ...r, stages_done: [...r.stages_done] };
    },
    async deleteRun(id) {
      state.writes.push({ op: "delete-run", id });
      if (state.refuse.delete) throw state.refuse.delete;
      if (!state.runs.some((x) => x.id === id)) throw notFound(`discovery run '${id}'`);
      state.runs = state.runs.filter((x) => x.id !== id);
    },
    async listRuns() {
      return { runs: state.runs };
    },
    async getRun(id) {
      const r = state.runs.find((x) => x.id === id);
      if (!r) throw notFound(`discovery run '${id}'`);
      const step = state.runSteps.shift();
      if (step) Object.assign(r, step);
      return { ...r, stages_done: [...r.stages_done] };
    },
    async listImprints() {
      return { pits: state.imprints.map((i) => ({ ...i, twins: twinsOf(i.pit_id) })), stale: state.imprintWarnings.length > 0, warnings: [...state.imprintWarnings] };
    },
    async getImprint(pit) {
      const i = findImprint(pit);
      if (!i) throw notFound(`imprint '${pit}'`);
      return { ...i, twins: twinsOf(i.pit_id) };
    },
    async renameImprint(pit, name) {
      state.writes.push({ op: "rename-imprint", id: pit, body: { name } });
      if (state.refuse.rename) throw state.refuse.rename;
      const i = findImprint(pit);
      if (!i) throw notFound(`imprint '${pit}'`);
      i.name = name;
      for (const r of state.runs) if (r.pit_id === i.pit_id) r.pit_name = name;
      return { ...i, twins: twinsOf(i.pit_id) };
    },
    async copyImprint(pit, name) {
      state.writes.push({ op: "copy-imprint", id: pit, body: { name } });
      if (state.refuse.copy) throw state.refuse.copy;
      const i = findImprint(pit);
      if (!i) throw notFound(`imprint '${pit}'`);
      const copy: Imprint = { ...i, pit_id: `pit-copy-${nextId++}`, name, derived_from: i.pit_id, twins: [] };
      state.imprints.push(copy);
      const src = state.assertions[i.pit_id];
      if (src) state.assertions[copy.pit_id] = { ...src, imprint_id: copy.pit_id, path: src.path.replace(i.pit_id, copy.pit_id) };
      return copy;
    },
    async deleteImprint(pit) {
      state.writes.push({ op: "delete-imprint", id: pit });
      if (state.refuse.deleteImprint) throw state.refuse.deleteImprint;
      const i = findImprint(pit);
      if (!i) throw notFound(`imprint '${pit}'`);
      state.imprints = state.imprints.filter((x) => x.pit_id !== i.pit_id);
    },
    async getAssertions(pit) {
      const i = findImprint(pit);
      if (!i) throw notFound(`imprint '${pit}'`);
      return state.assertions[i.pit_id] ?? emptyAssertions(i.pit_id);
    },
    async putAssertions(pit, doc) {
      state.writes.push({ op: "put-assertions", id: pit, body: doc });
      if (state.refuse.assertions) throw state.refuse.assertions;
      const i = findImprint(pit);
      if (!i) throw notFound(`imprint '${pit}'`);
      const base = emptyAssertions(i.pit_id);
      const d = doc as Partial<AssertionsDoc>;
      const saved: AssertionsDoc = {
        ...base,
        as_of: d.as_of ?? null,
        source: d.source ?? null,
        add: d.add ?? [],
        override: d.override ?? [],
        suppress: d.suppress ?? [],
        exists: true,
        etag: `etag-${nextId++}`,
      };
      state.assertions[i.pit_id] = saved;
      i.has_assertions = true;
      return saved;
    },
    async castImprint(pit, body) {
      state.writes.push({ op: "cast", id: pit, body });
      if (state.refuse.cast) throw state.refuse.cast;
      const i = findImprint(pit);
      if (!i) throw notFound(`imprint '${pit}'`);
      const replaced = Boolean(state.twins[body.twin_id]);
      if (replaced && !body.replace) {
        throw new ApiError({ kind: "conflict", status: 409, message: `twin '${body.twin_id}' already exists in the catalog; pass replace: true to overwrite it, or choose another id`, url: "" });
      }
      const twin: TwinSummary = {
        id: body.twin_id, as_of: "2026-10-06T19:00:00Z", pit_id: i.pit_id,
        hosts: i.entities ?? 0, segments: i.segments ?? 0, services: 0, personas: 0,
        mean_confidence: 0.5, windows: i.windows ?? null, topology_segments: [],
      };
      state.twins[body.twin_id] = twin;
      return { twin, pit_id: i.pit_id, replaced, caveats: ["fake cast"], install_report: "installed" };
    },
    async listTwins() {
      return {
        imprints: Object.values(state.twins).map((t) => ({
          id: t.id,
          hosts: t.hosts,
          segments: t.segments,
          as_of: t.as_of,
          mean_confidence: t.mean_confidence,
          has_provenance: true,
          confidence_source: "canonical sidecar (2/3 fields)",
          has_topology_export: true,
          cast_from: t.pit_id ? { pit_id: t.pit_id, pit_name: findImprint(t.pit_id)?.name ?? null } : null,
          can_apply: true,
          apply_blocked_reason: null,
        })),
        skipped: [...state.skippedTwins],
      };
    },
    async getTwin(id) {
      const t = state.twins[id];
      if (!t) throw notFound(`imprint '${id}'`);
      return t;
    },
    async getTwinTree(id) {
      const t = state.twins[id];
      if (!t) throw notFound(`imprint '${id}'`);
      return state.trees[id] ?? { id, hosts: t.hosts, segments: [], routers: [], traffic_profile: null, tap_plan: null, warnings: [] };
    },
    async deleteTwin(id) {
      state.writes.push({ op: "delete-twin", id });
      if (state.refuse.deleteTwin) throw state.refuse.deleteTwin;
      if (!state.twins[id]) throw notFound(`imprint '${id}'`);
      delete state.twins[id];
    },
  };
  return { api, state };
}
