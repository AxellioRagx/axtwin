/**
 * An in-memory `Api`: the shapes the control plane answers, with no fetch.
 * Tests seed it and read back what the console sent.
 */
import { ApiError } from "../../src/api/client";
import type { Api } from "../../src/api/discovery";
import type {
  ConfigExit,
  DiscoveryPlugin,
  DiscoveryRun,
  DiscoverySource,
  AssertionsDoc,
  DeployConfig,
  DeploySummary,
  Target,
  TargetHealth,
  TapStatus,
  Preflight,
  Imprint,
  LayoutDoc,
  PendingChange,
  Persona,
  ProbeResult,
  TwinConfigDetail,
  TwinConfigDocument,
  ValidateResult,
  ValidationFinding,
  TwinSummary,
  TwinTree,
  SourceRunRequest,
  SourceUpdate,
  SourceWrite,
  Topology,
  TopologyExport,
  TwinConfigSummary,
  CastRoster,
  Whoami,
} from "../../src/api/types";
import { mergeSegments, readDoc } from "../../src/features/edit-canvas/edit/document";
import { readTap } from "../../src/features/edit-canvas/edit/tap";
import { readTe, setNodeField } from "../../src/features/edit-canvas/edit/te";
import { validateTe } from "../../src/features/edit-canvas/edit/validate";
import { parseYaml, toPlain, type Plain } from "../../src/features/edit-canvas/edit/yaml";

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
  /** What `GET /api/whoami` answers (A8); the dev default is an open deployment. */
  whoami: Whoami;
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
  /** Authored graphs by scenario/twin id (`GET /api/scenarios/{id}/topology`). */
  topologies: Record<string, Topology>;
  /** Cast graphs with evidence by twin id; a twin without one answers 404 (no export yet). */
  exports: Record<string, TopologyExport>;
  /** Cast rosters (`GET /api/twins/{id}/cast`) by twin id; a twin without one answers 404. */
  casts: Record<string, CastRoster>;
  /** Layout side-cars by twin id, once saved. */
  layouts: Record<string, LayoutDoc>;
  twinConfigs: TwinConfigSummary[];
  personas: Persona[];
  /** The twin-config documents (`api/Spec.md` A3), by id: the baseline each editor opens. */
  documents: Record<string, TwinConfigDocument>;
  /** What `GET /api/deploys` lists. */
  deploys: DeploySummary[];
  /** The images the fake's manifests know; an image outside it is an error finding (default: every image a seeded document names). */
  images: string[] | null;
  /** Each deploy's working copy (A2), by deploy id, once first PUT; absent = the baseline, clean. */
  workingCopies: Record<string, { files: Record<string, string>; applied: DeployConfig["applied"]; updated_at: string }>;
  /** An Apply or Reset the fake reports in flight per deploy (`GET .../config` `exit`), for the progress / Cancel tests. */
  exits: Record<string, ConfigExit>;
  /** The baseline tiers whose replay a Reset reports failed (per deploy). */
  replayFails: Record<string, string[]>;
  /** Catalog folders the listing reports as unreadable. */
  skippedTwins: Array<{ id: string; reason: string }>;
  /** What the imprint listing warns about (a locked store). */
  imprintWarnings: string[];
  /** Every write the console made, in order. */
  writes: Array<{ op: string; id?: string; body?: unknown }>;
  /** What the next create/probe/startRun should do instead of succeeding. */
  refuse: { create?: ApiError; probe?: ApiError; run?: ApiError; cancel?: ApiError; delete?: ApiError; deleteImprint?: ApiError; deleteTwin?: ApiError; rename?: ApiError; copy?: ApiError; cast?: ApiError; assertions?: ApiError; layout?: ApiError; config?: ApiError; apply?: ApiError; twinConfigWrite?: ApiError; plan?: ApiError; deploy?: ApiError; destroy?: ApiError };
  /** How a run progresses: each `getRun` moves it one step along this list. */
  runSteps: Array<Partial<DiscoveryRun>>;
  /** What `GET /api/targets` lists. */
  targets: Target[];
  /** `GET /api/targets/{name}/health` by target name; a target without an entry answers every component unknown. */
  health: Record<string, TargetHealth>;
  /** How many health reads the console made, per target (the 15 s poll tests count them). */
  healthReads: Record<string, number>;
  /** `GET /api/taps?target=<te deploy id>` by te deploy id; absent answers 404. */
  taps: Record<string, TapStatus>;
  /** Per target name: the images the plan's preflight reports bound / total and the unbound ones (`TARGET-IMAGE`).
   *  Absent: every image bound (1 / 1). The `TARGET-SENSOR` rows are computed from the twin's document's tap plan
   *  against the target's lab sensors (else its `sensors`). */
  preflightImages: Record<string, { bound: number; total: number; unbound?: Array<{ image: string; location: string }> }>;
  /** When set, `destroyDeploy` waits on it before answering (a destroy in flight). */
  destroyHold?: Promise<void>;
}

export function fakeApi(seed: Partial<FakeState> = {}): { api: Api; state: FakeState } {
  const state: FakeState = {
    whoami: { user: "anonymous", can_write: true, mode: "none", signin_url: "" },
    plugins: [AXQUERY, ZEEK],
    sources: [],
    runs: [],
    twins: {},
    trees: {},
    imprints: [],
    assertions: {},
    topologies: {},
    exports: {},
    casts: {},
    layouts: {},
    twinConfigs: [],
    personas: [],
    documents: {},
    deploys: [],
    exits: {},
    replayFails: {},
    images: null,
    workingCopies: {},
    skippedTwins: [],
    imprintWarnings: [],
    writes: [],
    refuse: {},
    runSteps: [],
    targets: [{ node: "dev45", name: "dev45", default: true, nodes: ["dev45"], platform: "proxmox", error: null, sensors: ["XPress-SW-A"] }],
    health: {},
    healthReads: {},
    taps: {},
    preflightImages: {},
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
  // The plan's preflight against one target, as the API computes it: the tap plan's sensors against the target's
  // lab (else its sensor list), the images as seeded per target.
  const preflightOf = (twinId: string, targetName: string): Preflight => {
    const t = state.targets.find((x) => (x.name ?? x.node) === targetName);
    if (!t) throw notFound(`target '${targetName}'`);
    const sensors = [...new Set([...(t.lab?.components ?? []).filter((c) => c.role === "sensor").map((c) => c.id), ...(t.sensors ?? [])])].sort();
    const findings: ValidationFinding[] = [];
    const doc = state.documents[twinId];
    const tapText = doc?.files["tap/default.yaml"];
    if (tapText) {
      for (const b of readTap(tapText).bindings) {
        b.sensors.forEach((s, j) => {
          if (!sensors.includes(s)) findings.push({ rule_id: "TARGET-SENSOR", severity: "error", message: `sensor ${s} not in lab of target ${targetName}`, location: `tap/default.yaml: tap-plan.bindings[${b.index}].sensors[${j}]` });
        });
      }
    }
    const img = state.preflightImages[targetName] ?? { bound: 1, total: 1 };
    for (const u of img.unbound ?? []) findings.push({ rule_id: "TARGET-IMAGE", severity: "error", message: `image ${u.image} has no binding for platform ${t.platform ?? "proxmox"}`, location: u.location });
    return { target: targetName, platform: t.platform ?? "proxmox", clean: findings.length === 0, images_bound: img.bound, images_total: img.total, sensors, findings };
  };
  const api: Api = {
    async whoami() {
      return state.whoami;
    },
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
        ...(d.confirm?.length ? { confirm: d.confirm } : {}),
        ...(d.doubt?.length ? { doubt: d.doubt } : {}),
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
      state.twins[body.twin_id] = { ...state.twins[body.twin_id], ...twin };
      // A re-cast compiles the assertions into the twin's baseline: the fake
      // applies the two the canvas promotes (a host's role, a segment merge).
      const docu = state.documents[body.twin_id];
      const asserted = state.assertions[i.pit_id];
      if (docu && asserted) {
        let files = { ...docu.files };
        const te = () => readTe(files[docu.te_file] ?? "");
        // The real compiler joins an assertion on the IR's entity keys, never on a node
        // name: the roster says which group a key belongs to and which observed network a
        // segment was. Without a roster the fake resolves nothing, as the compiler would.
        const roster = state.casts[body.twin_id];
        const groupOf = (key: string) => roster?.node_roster.find((n) => n.members.includes(key))?.name;
        const segmentOf = (observed: string) => {
          const name = roster?.segment_roster.find((x) => x.observed_cidr === observed)?.name;
          return name ? te().segments.find((x) => x.name === name) : undefined;
        };
        for (const o of asserted.override) {
          const node = groupOf(String(o["key"] ?? ""));
          if (o["field"] === "role" && node && te().nodes.some((n) => n.name === node)) files[docu.te_file] = setNodeField(files[docu.te_file]!, node, "role", String(o["value"]));
        }
        for (const sup of asserted.suppress) {
          const from = segmentOf(String(sup["key"] ?? ""));
          const into = asserted.override.find((o) => o["field"] === "segment");
          const to = into ? segmentOf(String(into["value"])) : undefined;
          if (from && to) files = mergeSegments({ id: body.twin_id, teFile: docu.te_file, files }, readDoc({ id: body.twin_id, teFile: docu.te_file, files }), from.name, to.name);
        }
        state.documents[body.twin_id] = { ...docu, files };
        for (const [deployId, wc] of Object.entries(state.workingCopies)) {
          const d = state.deploys.find((x) => x.deploy_id === deployId);
          if (d?.scenario_id === body.twin_id && wc) delete state.workingCopies[deployId];
        }
      }
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
    async getCast(id) {
      const c = state.casts[id];
      if (!c) throw notFound(`cast bundle '${id}'`);
      return c;
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
    async getScenarioTopology(id) {
      const t = state.topologies[id];
      if (!t) throw notFound(`scenario '${id}'`);
      return t;
    },
    async getTwinTopology(id) {
      const e = state.exports[id];
      // The API's own rule: 404 doubles as "unknown id" and "known id, no export yet".
      if (!e) throw notFound(`topology export for '${id}'`);
      return e;
    },
    async getLayout(id) {
      if (!state.twins[id]) throw notFound(`imprint '${id}'`);
      return state.layouts[id] ?? { imprint_id: id, path: `/store/twins/${id}/generated/layout.json`, exists: false, etag: null, schema: "axr-layout/1", imprint: id, pins: {}, plane: null, annotations: [] };
    },
    async putLayout(id, doc) {
      state.writes.push({ op: "put-layout", id, body: doc });
      if (state.refuse.layout) throw state.refuse.layout;
      if (!state.twins[id]) throw notFound(`imprint '${id}'`);
      const current = state.layouts[id];
      if (current && current.etag !== doc.etag) {
        throw new ApiError({ kind: "http", status: 412, message: `layout for '${id}' changed since it was read; reload and retry`, detail: { etag: current.etag }, url: "" });
      }
      if (doc.imprint !== id) throw new ApiError({ kind: "bad-request", status: 400, message: `request body: document declares imprint '${doc.imprint}' but is being written for '${id}'`, url: "" });
      const annotations = checkAnnotations(doc.annotations ?? [], `layout for '${id}'`);
      const saved: LayoutDoc = { imprint_id: id, path: `/store/twins/${id}/generated/layout.json`, exists: true, etag: `layout-etag-${nextId++}`, schema: doc.schema, imprint: doc.imprint, pins: { ...doc.pins }, plane: doc.plane, annotations };
      state.layouts[id] = saved;
      return saved;
    },
    async listTwinConfigs() {
      return { twin_configs: state.twinConfigs };
    },
    async listPersonas() {
      return { twin: null, personas: state.personas, warnings: [] };
    },
    async getTwinConfig(id) {
      const d = state.documents[id];
      if (!d) throw notFound(`twin-config '${id}'`);
      return detailOf(d);
    },
    async createTwinConfig(body) {
      state.writes.push({ op: "create-twin-config", id: body.id, body });
      if (state.refuse.twinConfigWrite) throw state.refuse.twinConfigWrite;
      const id = body.id ?? "";
      if (!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(id)) throw new ApiError({ kind: "bad-request", status: 400, message: `'id' ${JSON.stringify(id)} is not a usable twin-config id`, url: "" });
      if (state.documents[id] || state.twins[id]) throw new ApiError({ kind: "conflict", status: 409, message: `twin-config '${id}' already exists; Save-As never overwrites`, url: "" });
      const teFile = teFileOf(body.files);
      validateDocument(id, teFile, body.files);
      const d: TwinConfigDocument = { id, te_file: teFile, files: { ...body.files }, skipped: [] };
      state.documents[id] = d;
      const te = readTe(body.files[teFile]!);
      state.twinConfigs.push({ id, description: te.description, cluster: null, parameterized: Object.keys(te.params).length > 0, param_names: Object.keys(te.params), error: null });
      state.twins[id] = { id, as_of: "2026-10-30T00:00:00Z", pit_id: null, hosts: te.nodes.reduce((a, n) => a + n.countValue, 0), segments: te.segments.length, services: 0, personas: 0, mean_confidence: 0, topology_segments: [] };
      return detailOf(d);
    },
    async putTwinConfig(id, body) {
      state.writes.push({ op: "put-twin-config", id, body });
      if (state.refuse.twinConfigWrite) throw state.refuse.twinConfigWrite;
      if (body.id !== undefined && body.id !== id) throw new ApiError({ kind: "bad-request", status: 400, message: `body id '${body.id}' does not match the path id '${id}'`, url: "" });
      const existing = state.documents[id];
      if (!existing) throw notFound(`twin-config '${id}'`);
      validateDocument(id, existing.te_file, body.files);
      existing.files = { ...body.files };
      // After a Persist the deploy's working copy equals the baseline: clean.
      for (const d of state.deploys) if (d.scenario_id === id) delete state.workingCopies[d.deploy_id];
      return detailOf(existing);
    },
    async listDeploys() {
      return { deploys: [...state.deploys], warnings: [] };
    },
    async getDeployConfig(deployId) {
      return configOf(deployId);
    },
    async putDeployConfig(deployId, files) {
      state.writes.push({ op: "put-deploy-config", id: deployId, body: { files } });
      if (state.refuse.config) throw state.refuse.config;
      const { d, doc } = deployOf(deployId);
      validateDocument(d.scenario_id, doc.te_file, files);
      const prev = state.workingCopies[deployId];
      state.workingCopies[deployId] = { files: { ...files }, applied: prev?.applied ?? null, updated_at: stamp() };
      return configOf(deployId);
    },
    async applyDeployConfig(deployId) {
      state.writes.push({ op: "apply-deploy-config", id: deployId });
      if (state.refuse.apply) throw state.refuse.apply;
      const cfg = configOf(deployId);
      const blocked = cfg.changes.filter((c) => c.kind === "topology");
      if (blocked.length) {
        throw new ApiError({ kind: "conflict", status: 409, message: `requires-replan: ${blocked.map((c) => c.path).join(", ")} changed the topology; the twin must be re-planned`, detail: { code: "requires-replan", paths: blocked.flatMap((c) => c.paths) }, url: "" });
      }
      const tiers = [...new Set(cfg.changes.flatMap((c) => c.via))];
      const roles = Object.assign({}, ...cfg.changes.map((c) => c.roles)) as Record<string, string>;
      const wc = state.workingCopies[deployId];
      if (wc) {
        // As the API records it: the apply touches the copy, so `applied.at` equals `updated_at` until the next PUT.
        wc.updated_at = stamp();
        wc.applied = { at: wc.updated_at, tiers: Object.fromEntries(tiers.map((t) => [t, "done"])), roles };
      }
      return { deploy_id: deployId, twin_id: cfg.twin_id, applied: tiers, roles, warnings: [], config: configOf(deployId) };
    },
    async listTargets() {
      return { targets: [...state.targets] };
    },
    async getTargetHealth(name) {
      state.healthReads[name] = (state.healthReads[name] ?? 0) + 1;
      const t = state.targets.find((x) => (x.name ?? x.node) === name);
      if (!t) throw notFound(`target '${name}'`);
      return state.health[name] ?? { target: name, components: (t.lab?.components ?? []).map((c) => ({ id: c.id, role: c.role, status: "unknown" })) };
    },
    async listTargetDeploys(target) {
      return { deploys: state.deploys.filter((d) => (d.target ?? "dev45") === target), warnings: [] };
    },
    async getTaps(twinDeployId) {
      const t = state.taps[twinDeployId];
      if (!t) throw notFound(`tap playout on '${twinDeployId}'`);
      return t;
    },
    async planTwin(id, body, _signal, target) {
      state.writes.push({ op: "plan", id, body, ...(target ? { target } : {}) });
      if (state.refuse.plan) throw state.refuse.plan;
      const t = state.twins[id];
      if (!t) throw notFound(`twin '${id}'`);
      return { plan_id: `${id}-plan${nextId++}`, scenario_id: id, scenario_version: 1, ir_hash: "h", ops: t.hosts * 8, barriers: 4, edges: 10, tree: "", config_warnings: ["deploy gate: 1 node spec(s) removed"], vm_count: t.hosts, params: {}, preflight: target ? preflightOf(id, target) : null };
    },
    async deployTwin(id, body, target) {
      state.writes.push({ op: "deploy", id, body, ...(target ? { target } : {}) });
      if (state.refuse.deploy) throw state.refuse.deploy;
      if (target && !preflightOf(id, target).clean) throw new ApiError({ kind: "conflict", status: 409, message: `the preflight for target '${target}' is not clean`, detail: { code: "preflight" }, url: "" });
      const deploy_id = `dep-${nextId++}`;
      const now = Date.now() / 1000;
      const follow_on = Object.fromEntries((body.follow_on ?? []).map((t) => [t, { status: "pending", reason: null, deploy_id: null, at: now }]));
      state.deploys.push({ deploy_id, scenario_id: id, status: "running", started_at: now, updated_at: now, tier: "te", plan_id: body.expect_plan_id ?? null, target: "dev45", follow_on });
      return { deploy_id, status: "running", detached: true, plan_id: body.expect_plan_id ?? null, config_warnings: [] };
    },
    async startTier(deployId, tier) {
      state.writes.push({ op: "start-tier", id: deployId, body: { tier } });
      const now = Date.now() / 1000;
      // As the API records it: queued now; a test moves it on by editing the te row.
      state.deploys = state.deploys.map((x) => (x.deploy_id === deployId ? { ...x, follow_on: { ...(x.follow_on ?? {}), [tier]: { status: "pending", reason: null, deploy_id: null, at: now } } } : x));
      return { deploy_id: deployId, tier, status: "pending" };
    },
    async destroyDeploy(deployId) {
      state.writes.push({ op: "destroy", id: deployId });
      if (state.destroyHold) await state.destroyHold;
      if (state.refuse.destroy) throw state.refuse.destroy;
      const te = state.deploys.find((x) => x.deploy_id === deployId);
      if (!te) throw notFound(`deploy ${deployId}`);
      const tiers = [];
      const gone = new Set<string>([deployId]);
      for (const tier of ["exr", "tfc", "tap"]) {
        const child = state.deploys.find((x) => x.scenario_id === `${tier}-${deployId}` && x.status !== "destroyed");
        if (child) gone.add(child.deploy_id);
        tiers.push({ tier, deploy_id: child?.deploy_id ?? null, destroyed: Boolean(child), removed: child ? 1 : 0, errors: 0, released: child && tier === "tap" ? ["gretap gre-corp"] : [] });
      }
      // New rows, never a mutation: the seeds share their row objects between tests.
      state.deploys = state.deploys.map((x) => (gone.has(x.deploy_id) ? { ...x, status: "destroyed" } : x));
      delete state.workingCopies[deployId];
      tiers.push({ tier: "te", deploy_id: deployId, destroyed: true, removed: 3, errors: 0, released: [] });
      return { deploy_id: deployId, destroyed: true, removed: 3, errors: 0, actions: [], cascade_errors: [], tiers };
    },
    async validateDocument(document) {
      state.writes.push({ op: "validate", id: document.id, body: document });
      return validateResult(document.id, teFileOf(document.files), document.files);
    },
    async resetDeployConfig(deployId, body) {
      state.writes.push({ op: "reset-deploy-config", id: deployId, body });
      const cfg = configOf(deployId);
      const wc = state.workingCopies[deployId];
      delete state.workingCopies[deployId];
      const recorded = wc?.applied ? Object.keys(wc.applied.tiers) : [];
      const tiers = [...new Set([...recorded, ...(body?.tiers ?? [])])];
      const failed = tiers.filter((t) => (state.replayFails[deployId] ?? []).includes(t));
      return {
        deploy_id: deployId, twin_id: cfg.twin_id, discarded: Boolean(wc), reverted: tiers.filter((t) => !failed.includes(t)), replay_failed: failed,
        warnings: failed.map((t) => `${t}: the baseline replay failed (persona cannot play); the copy is discarded but the twin still runs what it applied to ${t}`),
        config: configOf(deployId),
      };
    },
    async cancelDeployConfigExit(deployId) {
      state.writes.push({ op: "cancel-deploy-config-exit", id: deployId });
      const ex = state.exits[deployId];
      if (!ex) throw new ApiError({ kind: "conflict", status: 409, message: `no Apply or Reset is in flight on deploy ${deployId}; nothing to cancel`, detail: { code: "nothing-in-flight" }, url: "" });
      ex.cancel_requested = true;
      return { deploy_id: deployId, kind: ex.kind, tier: ex.tier, cancel_requested: true };
    },
  };

  function teFileOf(files: Record<string, string>): string {
    const manifest = files["twin.yaml"];
    if (manifest) {
      const p = toPlain(parseYaml(manifest).root) as { twin?: { tiers?: { te?: { default?: string } } } } | null;
      const te = p?.twin?.tiers?.te?.default;
      if (typeof te === "string" && files[te] !== undefined) return te;
    }
    return "te.yaml";
  }

  /** The fake validator: the canvas's structural rules plus an image the
   *  (fake) manifests do not know, as `POST /api/validate {document}` answers
   *  them -- a FAIL is a result with the error findings, never a throw. */
  function validateResult(id: string, teFile: string, files: Record<string, string>): ValidateResult {
    const text = files[teFile];
    const findings: ValidationFinding[] = [];
    if (text === undefined) findings.push({ rule_id: "DOC-001", severity: "error", message: `document for '${id}' has no twin config file`, location: teFile });
    else {
      const te = readTe(text);
      if (te.id !== id) findings.push({ rule_id: "DOC-002", severity: "error", message: `document declares twin_config.id '${te.id}' but is validated as '${id}'`, location: "twin_config.id" });
      findings.push(...validateTe(te));
      const known = new Set(state.images ?? Object.values(state.documents).flatMap((d) => readTe(d.files[d.te_file] ?? "").nodes.map((n) => n.image)));
      for (const n of te.nodes) if (n.image && !known.has(n.image)) findings.push({ rule_id: "IMG-010", severity: "error", message: `node '${n.name}': image '${n.image}' has no manifest under the images root`, location: `twin_config.nodes[${n.name}].image` });
    }
    const errors = findings.filter((f) => f.severity === "error").length;
    return { disposition: errors ? "FAIL" : "PASS", exit_code: errors ? 1 : 0, passes_run: [1, 2, 3], errors, warnings: 0, findings };
  }

  /** The server's refusal shape for an invalid document (A2.3 / A3): the validator's findings, nothing written. */
  function validateDocument(id: string, teFile: string, files: Record<string, string>): void {
    const r = validateResult(id, teFile, files);
    const bad = r.findings.filter((f) => f.severity === "error");
    const shape = bad.find((f) => f.rule_id.startsWith("DOC-"));
    if (shape) throw new ApiError({ kind: "bad-request", status: 400, message: shape.message, url: "" });
    if (bad.length) {
      throw new ApiError({ kind: "unprocessable", status: 422, message: `twin-config '${id}' fails validation (${bad.length} error(s)): ${bad[0]!.rule_id} ${bad[0]!.message}`, detail: { code: "invalid", findings: bad }, url: "" });
    }
  }

  function detailOf(d: TwinConfigDocument): TwinConfigDetail {
    const te = readTe(d.files[d.te_file] ?? "");
    return {
      id: d.id, description: te.description, cluster: null, params: Object.entries(te.params).map(([name, def]) => ({ name, default: def })),
      segments: te.segments.map((s) => ({ name: s.name, cidr: s.cidr, zone_type: s.zoneType })),
      nodes: te.nodes.map((n) => ({ name: n.name, kind: n.kind, image: n.image, count: n.count, role: n.role })),
      document: { ...d, files: { ...d.files } }, warnings: [],
    };
  }

  function deployOf(deployId: string): { d: DeploySummary; doc: TwinConfigDocument } {
    const d = state.deploys.find((x) => x.deploy_id === deployId);
    if (!d) throw notFound(`deploy '${deployId}'`);
    const doc = state.documents[d.scenario_id];
    if (!doc) throw notFound(`twin-config '${d.scenario_id}' behind deploy '${deployId}'`);
    return { d, doc };
  }

  /** The fake's clock: every write a second later than the last, as ISO text that sorts. */
  function stamp(): string {
    return new Date(Date.UTC(2026, 9, 30, 0, 0, nextId++)).toISOString().replace(/\.\d{3}Z$/, "Z");
  }

  function configOf(deployId: string): DeployConfig {
    const { d, doc } = deployOf(deployId);
    const wc = state.workingCopies[deployId];
    const files = wc ? wc.files : doc.files;
    const changes = wc ? classify(doc.files, files, doc.te_file) : [];
    return {
      deploy_id: deployId, twin_id: d.scenario_id, dirty: changes.length > 0, has_working_copy: Boolean(wc),
      document: { id: doc.id, te_file: doc.te_file, files: { ...files }, skipped: [] },
      changes, apply_allowed: changes.every((c) => c.kind !== "topology"), applied: wc?.applied ?? null, updated_at: wc?.updated_at ?? null, exit: state.exits[deployId] ?? null, warnings: [],
    };
  }

  return { api, state };
}

const ANNOTATION_KINDS = ["text", "rect", "ellipse", "line"];
const SVG_REFUSED: Array<[RegExp, string]> = [
  [/<\s*script/i, "a <script> element"],
  [/<\s*foreignObject/i, "a <foreignObject> element"],
  [/\bon[a-z]+\s*=/i, "an on*= event-handler attribute"],
  [/\b(?:xlink:)?href\s*=/i, "an href / xlink:href attribute"],
];

/** `console_files._canonical_annotations` as the fake applies it on a layout PUT: a 400 naming the item and the rule. */
export function checkAnnotations(raw: unknown, where: string): LayoutDoc["annotations"] {
  const bad = (msg: string) => new ApiError({ kind: "bad-request", status: 400, message: `${where}: ${msg}`, url: "" });
  if (!Array.isArray(raw)) throw bad("`annotations` must be a list");
  if (raw.length > 200) throw bad(`${raw.length} annotations; at most 200`);
  const seen = new Set<string>();
  return raw.map((item: Record<string, unknown>, i) => {
    const label = `annotations[${i}]`;
    const id = item["id"];
    if (typeof id !== "string" || !/^[A-Za-z0-9._:-]{1,64}$/.test(id)) throw bad(`${label} \`id\` must be 1-64 characters from [A-Za-z0-9._:-], got ${JSON.stringify(id)}`);
    if (seen.has(id)) throw bad(`${label} \`id\` '${id}' is used twice`);
    seen.add(id);
    if (!ANNOTATION_KINDS.includes(String(item["kind"]))) throw bad(`${label} \`kind\` ${JSON.stringify(item["kind"])} is not one of ${JSON.stringify(ANNOTATION_KINDS)}`);
    for (const axis of ["x", "y"]) if (typeof item[axis] !== "number") throw bad(`${label} \`${axis}\` must be a number`);
    for (const size of ["w", "h"]) if (item[size] !== undefined && item[size] !== null && (typeof item[size] !== "number" || (item[size] as number) < 0)) throw bad(`${label} \`${size}\` must be a non-negative number`);
    const svg = item["svg"];
    if (typeof svg !== "string" || !svg.trim()) throw bad(`${label} \`svg\` must be a non-empty string`);
    if (new TextEncoder().encode(svg).length > 8192) throw bad(`${label} \`svg\` is over 8192 bytes`);
    for (const [re, what] of SVG_REFUSED) if (re.test(svg)) throw bad(`${label} \`svg\` carries ${what}, which a layout annotation may not (the file is served back to browsers)`);
    return { id, kind: String(item["kind"]), x: item["x"] as number, y: item["y"] as number, w: (item["w"] as number | undefined) ?? null, h: (item["h"] as number | undefined) ?? null, svg, locked: Boolean(item["locked"]), hidden: Boolean(item["hidden"]) };
  });
}

/** `api/Spec.md`'s apply classification, by the tier file a change touches
 *  (`engine/src/axrange/api/edits.py`): tfc / personas / tap are attribute,
 *  te.yaml is topology unless only a node's `role` changed with its image
 *  unchanged, exr and prose are inert. */
export function classify(baseline: Record<string, string>, working: Record<string, string>, teFile: string): PendingChange[] {
  const out: PendingChange[] = [];
  for (const path of [...new Set([...Object.keys(baseline), ...Object.keys(working)])].sort()) {
    const before = baseline[path];
    const after = working[path];
    if (before === after) continue;
    const op = before === undefined ? "added" : after === undefined ? "removed" : "modified";
    const top = path.split("/")[0]!;
    if (path === teFile) {
      if (op !== "modified") out.push({ path, op, kind: "topology", via: [], paths: [path], reason: `${path} ${op}: the twin must be re-planned`, roles: {} });
      else out.push(classifyTe(path, before!, after!));
    } else if (path === "twin.yaml") {
      out.push({ path, op, kind: "traffic", via: ["tfc", "tap"], paths: [path], reason: "tier wiring changed: the named tiers' playouts re-apply their defaults", roles: {} });
    } else if (top === "tfc" || top === "personas") {
      out.push({ path, op, kind: "traffic", via: ["tfc"], paths: [path], reason: top === "tfc" ? "traffic profile: the tfc playout re-applies" : "twin-local persona: the tfc playout re-applies", roles: {} });
    } else if (top === "tap") {
      out.push({ path, op, kind: "taps", via: ["tap"], paths: [path], reason: "tap plan: the tap playout re-applies", roles: {} });
    } else if (top === "exr" || /\.(md|txt)$/i.test(path)) {
      out.push({ path, op, kind: "inert", via: [], paths: [path], reason: "nothing on the running twin reads this file; Persist carries it", roles: {} });
    } else {
      out.push({ path, op, kind: "topology", via: [], paths: [path], reason: "not a tier file Apply knows how to re-apply", roles: {} });
    }
  }
  return out;
}

function classifyTe(path: string, before: string, after: string): PendingChange {
  const a = readTe(before);
  const b = readTe(after);
  const paths: string[] = [];
  const roles: Record<string, string> = {};
  const plainA = toPlain(parseYaml(before).root) as Plain;
  const plainB = toPlain(parseYaml(after).root) as Plain;
  const strip = (p: Plain): Plain => {
    if (p && typeof p === "object" && !Array.isArray(p)) {
      const o = { ...(p as Record<string, Plain>) };
      const tc = o["twin_config"];
      if (tc && typeof tc === "object" && !Array.isArray(tc)) {
        const t = { ...(tc as Record<string, Plain>) };
        delete t["nodes"];
        o["twin_config"] = t;
      }
      return o;
    }
    return p;
  };
  if (JSON.stringify(strip(plainA)) !== JSON.stringify(strip(plainB))) paths.push("twin_config.segments");
  const byA = new Map(a.nodes.map((n) => [n.name, n]));
  const byB = new Map(b.nodes.map((n) => [n.name, n]));
  let topology = paths.length > 0;
  for (const name of new Set([...byA.keys(), ...byB.keys()])) {
    const x = byA.get(name);
    const y = byB.get(name);
    if (!x || !y) {
      paths.push(`twin_config.nodes[${name}]`);
      topology = true;
      continue;
    }
    if (x.role !== y.role) {
      paths.push(`twin_config.nodes[${name}].role`);
      roles[name] = y.role ?? "";
    }
    if (x.image !== y.image || JSON.stringify(x.nets) !== JSON.stringify(y.nets) || x.count !== y.count || x.kind !== y.kind || JSON.stringify(x.requires) !== JSON.stringify(y.requires)) {
      paths.push(`twin_config.nodes[${name}]`);
      topology = true;
    }
  }
  if (topology) return { path, op: "modified", kind: "topology", via: [], paths, reason: "segments, nodes, attachments, counts or images changed: the twin must be re-planned", roles: {} };
  return { path, op: "modified", kind: "traffic", via: ["tfc"], paths, reason: "role re-assignment with the image unchanged: the tfc playout re-asserts it", roles };
}
