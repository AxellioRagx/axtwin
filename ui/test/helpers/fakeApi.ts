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
  ImprintSummary,
  ProbeResult,
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
  imprints: Record<string, ImprintSummary>;
  /** Catalog folders the listing reports as unreadable. */
  skippedImprints: Array<{ id: string; reason: string }>;
  /** Every write the console made, in order. */
  writes: Array<{ op: string; id?: string; body?: unknown }>;
  /** What the next create/probe/startRun should do instead of succeeding. */
  refuse: { create?: ApiError; probe?: ApiError; run?: ApiError; cancel?: ApiError; delete?: ApiError; deleteImprint?: ApiError; rename?: ApiError };
  /** How a run progresses: each `getRun` moves it one step along this list. */
  runSteps: Array<Partial<DiscoveryRun>>;
}

export function fakeApi(seed: Partial<FakeState> = {}): { api: Api; state: FakeState } {
  const state: FakeState = {
    plugins: [AXQUERY, ZEEK],
    sources: [],
    runs: [],
    imprints: {},
    skippedImprints: [],
    writes: [],
    refuse: {},
    runSteps: [],
    ...seed,
  };
  let nextId = 100;
  const notFound = (what: string) => new ApiError({ kind: "not-found", status: 404, message: `no ${what}`, url: "" });

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
        imprint_id: body.imprint_id,
        stages_done: [],
        stages: ["ingest", "bake", "cast", "install"],
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
    async renamePit(pit, name) {
      state.writes.push({ op: "rename-pit", id: pit, body: { name } });
      if (state.refuse.rename) throw state.refuse.rename;
      const hits = state.runs.filter((r) => r.pit_id === pit || r.pit_name === pit);
      if (hits.length === 0) throw notFound(`PIT '${pit}'`);
      for (const r of hits) r.pit_name = name;
      return { pit_id: String(hits[0]?.pit_id), name };
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
      return {
        imprints: Object.values(state.imprints).map((i) => ({
          id: i.id,
          hosts: i.hosts,
          segments: i.segments,
          as_of: i.as_of,
          mean_confidence: i.mean_confidence,
          has_provenance: true,
          confidence_source: "canonical sidecar (2/3 fields)",
          has_topology_export: true,
          can_apply: true,
          apply_blocked_reason: null,
        })),
        skipped: [...state.skippedImprints],
      };
    },
    async deleteImprint(id) {
      state.writes.push({ op: "delete-imprint", id });
      if (state.refuse.deleteImprint) throw state.refuse.deleteImprint;
      if (!state.imprints[id]) throw notFound(`imprint '${id}'`);
      delete state.imprints[id];
    },
    async getImprint(id) {
      const i = state.imprints[id];
      if (!i) throw notFound(`imprint '${id}'`);
      return i;
    },
  };
  return { api, state };
}
