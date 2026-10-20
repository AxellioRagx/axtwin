/**
 * A TRIMMED export + cast, checked in, that the level goldens are produced
 * from (`docs/WORK_ORDER_console_canvas.md` §5). Small enough to read, but it
 * **deliberately mirrors the real CorpAPCN shapes** so a golden built from it
 * proves something about what ships (LES-185):
 *
 *   - the 27-host `generic-endpoint` band in `10.80.72.0/24`, split into
 *     5 live (core) + 22 ghost (4 regular + 18 transient), exactly the real
 *     split -- so the below-threshold band collapses by rung (O6);
 *   - an 18-host live `corp-workstation` band in `10.20.193.0/24`, so the
 *     role-band collapse path is exercised too;
 *   - three infrastructure devices (a domain controller, a resolver, a
 *     collector) among LOCAL nodes, plus external nodes carrying the same
 *     roles that must NOT count as devices (LES-210);
 *   - three site groups -- `10.80` and `10.20` (multi-segment) and a `misc`
 *     group of two singleton /16s;
 *   - conduits including one to the cast's `internet-sink`, which resolves to
 *     no tile and must be reported, not dropped;
 *   - unplaced-local and non-local populations (O7).
 *
 * Hand-authored rather than sliced from the 552 KB real file so a diff to the
 * fixture is a deliberate, readable act -- the goldens regenerate from it via
 * `npm run layout:golden`.
 */
import type {
  TopologyExport,
  TopologyExportEdge,
  TopologyExportNode,
} from "../../src/api/types";
import type { CastInput } from "../../src/model/graph";

const WINDOWS = 10;

function node(part: Partial<TopologyExportNode> & { id: string }): TopologyExportNode {
  return {
    id: part.id,
    address: part.address ?? (part.id.startsWith("address:") ? part.id.slice("address:".length) : null),
    locality: part.locality ?? "local",
    segment: part.segment ?? null,
    segment_evidence_class: part.segment_evidence_class ?? (part.segment ? "observed" : null),
    role: part.role ?? null,
    role_confidence: part.role_confidence ?? (part.role ? 0.7 : null),
    role_evidence_class: part.role_evidence_class ?? (part.role ? "imputed" : null),
    windows_seen: part.windows_seen ?? null,
    listen_ports: part.listen_ports ?? [],
    dial_ports: part.dial_ports ?? [],
  };
}

/** A run of hosts on one segment with a fixed role and windows_seen. */
function band(
  segment: string,
  role: string | null,
  base: number,
  count: number,
  windowsSeen: number,
  listen: string[] = [],
): TopologyExportNode[] {
  const out: TopologyExportNode[] = [];
  for (let i = 0; i < count; i++) {
    const octet = base + i;
    out.push(
      node({
        id: `address:${segment.split("/")[0]!.split(".").slice(0, 3).join(".")}.${octet}`,
        segment,
        role,
        windows_seen: windowsSeen,
        listen_ports: i === 0 ? listen : [],
      }),
    );
  }
  return out;
}

const S_2080 = "10.80.20.0/24";
const S_7280 = "10.80.72.0/24";
const S_192 = "10.20.192.0/24";
const S_193 = "10.20.193.0/24";
const S_110 = "10.1.10.0/24";
const S_168 = "192.168.0.0/24";

const nodes: TopologyExportNode[] = [
  // --- 10.80.20.0/24: the multi-role level-1 subject ---------------------
  node({
    id: "hostname:axdc01",
    address: "10.80.20.10",
    segment: S_2080,
    role: "domain-controller",
    windows_seen: 10,
    listen_ports: ["53/udp", "88/tcp", "389/tcp", "445/tcp"],
    dial_ports: ["123/udp"],
  }),
  node({
    id: "address:10.80.20.5",
    segment: S_2080,
    role: "dns-resolver",
    windows_seen: 10,
    listen_ports: ["53/tcp", "53/udp"],
  }),
  ...band(S_2080, "corp-workstation", 30, 3, 9, ["7680/tcp"]),
  ...band(S_2080, "generic-endpoint", 40, 8, 8),
  ...band(S_2080, "generic-endpoint", 60, 3, 2), // ghosts (occ 0.2)
  ...band(S_2080, null, 90, 1, 9), // one unroled-endpoint, live
  ...band(S_2080, null, 95, 1, 2), // one unroled-endpoint, ghost

  // --- 10.80.72.0/24: the LES-185 27-host generic-endpoint band ----------
  ...band(S_7280, "generic-endpoint", 10, 5, 10), // 5 core (live)
  ...band(S_7280, "generic-endpoint", 20, 4, 6), // 4 regular (ghost)
  ...band(S_7280, "generic-endpoint", 30, 18, 2), // 18 transient (ghost)
  node({ id: "address:10.80.72.200", segment: S_7280, role: "web-tier", windows_seen: 10, listen_ports: ["443/tcp"] }),

  // --- 10.20.192.0/24: the collector -------------------------------------
  node({ id: "address:10.20.192.240", segment: S_192, role: "monitoring-collector", windows_seen: 10, listen_ports: ["9090/tcp"] }),
  node({ id: "address:10.20.192.9", segment: S_192, role: "generic-endpoint", windows_seen: 7 }),

  // --- 10.20.193.0/24: the 18-host live corp-workstation band ------------
  ...band(S_193, "corp-workstation", 20, 18, 10, ["7680/tcp"]),

  // --- singletons -> the misc site ---------------------------------------
  node({ id: "address:10.1.10.1", segment: S_110, role: "network-device", windows_seen: 10 }),
  node({ id: "address:10.1.10.50", segment: S_110, role: "generic-endpoint", windows_seen: 3 }),
  node({ id: "address:192.168.0.20", segment: S_168, role: "generic-endpoint", windows_seen: 10 }),

  // --- unplaced local (O7) -----------------------------------------------
  node({ id: "address:0.0.0.0", locality: "local", segment: null, windows_seen: 9, dial_ports: ["67/udp"] }),
  node({ id: "address:10.99.99.99", locality: "local", segment: null, windows_seen: 4 }),

  // --- non-local (O7): incl. an external dns-resolver that is NOT a device
  node({ id: "address:8.8.8.8", locality: "external", role: "dns-resolver", windows_seen: 10, listen_ports: ["53/udp"] }),
  node({ id: "address:1.1.1.1", locality: "external", role: "dns-resolver", windows_seen: 8 }),
  node({ id: "address:203.0.113.9", locality: "external", role: null, windows_seen: 2 }),
];

/** Deterministic, small edge set spanning several role buckets incl. external,
 *  so the matrix has an `external` axis and its cell totals sum to these
 *  flows. Directed (src→dst), one cell each. */
const edges: TopologyExportEdge[] = [
  { src: "address:10.80.20.30", dst: "hostname:axdc01", proto: "tcp", bytes: 1200, flows: 4, evidence_class: "observed" },
  { src: "address:10.80.20.31", dst: "hostname:axdc01", proto: "tcp", bytes: 900, flows: 3, evidence_class: "observed" },
  { src: "hostname:axdc01", dst: "address:10.80.20.5", proto: "udp", bytes: 300, flows: 5, evidence_class: "observed" },
  { src: "address:10.80.20.40", dst: "address:10.80.20.5", proto: "udp", bytes: 200, flows: 6, evidence_class: "observed" },
  { src: "address:10.80.72.10", dst: "address:10.80.20.5", proto: "udp", bytes: 150, flows: 2, evidence_class: "observed" },
  { src: "address:10.80.72.200", dst: "address:8.8.8.8", proto: "udp", bytes: 80, flows: 7, evidence_class: "observed" },
  { src: "address:10.20.193.20", dst: "address:8.8.8.8", proto: "udp", bytes: 60, flows: 9, evidence_class: "observed" },
  { src: "address:8.8.8.8", dst: "address:10.80.72.200", proto: "tcp", bytes: 5000, flows: 1, evidence_class: "observed" },
  { src: "address:10.80.72.10", dst: "address:10.80.72.11", proto: "tcp", bytes: 10, flows: 11, evidence_class: "observed" },
  { src: "address:10.20.192.9", dst: "address:10.20.192.240", proto: "tcp", bytes: 400, flows: 8, evidence_class: "observed" },
];

export const trimmedExport: TopologyExport = {
  imprint_id: "trimmed-fixture",
  pit_id: "pit-trim",
  as_of: 1787823960,
  network: "corpapcn",
  vantage: null,
  windows: WINDOWS,
  schema_version: "1.3",
  segments: [
    { cidr: S_110, name: "seg-10-1-10-0", entities: 2, coverage_source: "inferred_from_traffic" },
    { cidr: S_192, name: "seg-10-20-192-0", entities: 2, coverage_source: "inferred_from_traffic" },
    { cidr: S_193, name: "seg-10-20-193-0", entities: 18, coverage_source: "inferred_from_traffic" },
    { cidr: S_2080, name: "seg-10-80-20-0", entities: 17, coverage_source: "inferred_from_traffic" },
    { cidr: S_7280, name: "seg-10-80-72-0", entities: 28, coverage_source: "inferred_from_traffic" },
    { cidr: S_168, name: "seg-192-168-0-0", entities: 1, coverage_source: "inferred_from_traffic" },
  ],
  nodes,
  edges,
  census: { entities_total: nodes.length, with_role: nodes.filter((n) => n.role).length, placed: nodes.filter((n) => n.segment).length, local: nodes.filter((n) => n.locality === "local").length },
};

export const trimmedCast: CastInput = {
  // Self-consistent with `trimmedExport.imprint_id`: the goldens are a layout
  // shape, which never reads `imprintId`, so this only fixes the canonical id
  // for the model/search tests. The canonical-vs-baked MISMATCH is exercised
  // by its own dedicated fixture in `model.test.ts`.
  imprintId: "trimmed-fixture",
  deployMinOccupancy: 0.8,
  conduits: [
    { from: "seg-10-80-20-0", to: "seg-10-20-193-0" },
    { from: "seg-10-20-192-0", to: "seg-10-20-193-0" },
    { from: "seg-10-80-72-0", to: "seg-10-80-20-0" },
    { from: "seg-10-1-10-0", to: "internet-sink" }, // unresolved on purpose
  ],
  routers: [{ name: "gw", segments: ["seg-10-80-20-0", "seg-10-20-193-0"] }],
  deployedCidrByName: { "seg-10-1-10-0": "10.200.0.0/24", "seg-10-80-20-0": "10.200.10.0/24" },
  zoneTypeByName: { "seg-10-80-20-0": "simple", "seg-10-20-193-0": "vlan" },
};

/** One segment appended for the column-PRESERVING locality golden -- a NEW
 *  segment in the EXISTING `10.80` site that fits the current row grid (a 3rd
 *  tile in a 3-column site is still one row), so only that tile appears and
 *  every other element's coordinates -- tiles, chrome and lanes -- must be
 *  byte-identical (`15` §5.2). */
export function withAddedSegment(): TopologyExport {
  return {
    ...trimmedExport,
    segments: [
      ...trimmedExport.segments,
      { cidr: "10.80.90.0/24", name: "seg-10-80-90-0", entities: 2, coverage_source: "inferred_from_traffic" },
    ],
    nodes: [
      ...trimmedExport.nodes,
      node({ id: "address:10.80.90.5", segment: "10.80.90.0/24", role: "generic-endpoint", windows_seen: 10 }),
      node({ id: "address:10.80.90.6", segment: "10.80.90.0/24", role: "generic-endpoint", windows_seen: 3 }),
    ],
  };
}

/**
 * Two more resolvers in the existing `10.80.20.0/24`, so that site column holds
 * FOUR infrastructure devices and its device row must WRAP (R3).
 *
 * A fixture rather than real content on purpose, and that is worth saying: the
 * busiest column on either shipped bundle holds THREE devices and exactly three
 * fit across -- **zero spare slots**, not one -- so nothing in the tree
 * exercises the wrap, the grown INFRASTRUCTURE band or the half-slot row
 * offset, and one more device in that column would. Proving them against live
 * data is not an option here: the choice is a fixture or no coverage at all.
 */
export function withWrappingDevices(): TopologyExport {
  return {
    ...trimmedExport,
    nodes: [
      ...trimmedExport.nodes,
      node({ id: "address:10.80.20.6", segment: S_2080, role: "dns-resolver", windows_seen: 10 }),
      node({ id: "address:10.80.20.7", segment: S_2080, role: "dns-resolver", windows_seen: 10 }),
    ],
  };
}

/** FIVE devices in one column -- three rows, where alternating rows no longer
 *  guarantees a clear drop (rows 1 and 3 share a parity). R3 measures that
 *  limit rather than claiming it away. */
export function withThreeDeviceRows(): TopologyExport {
  return {
    ...trimmedExport,
    nodes: [
      ...trimmedExport.nodes,
      node({ id: "address:10.80.20.6", segment: S_2080, role: "dns-resolver", windows_seen: 10 }),
      node({ id: "address:10.80.20.7", segment: S_2080, role: "dns-resolver", windows_seen: 10 }),
      node({ id: "address:10.80.20.8", segment: S_2080, role: "dns-resolver", windows_seen: 10 }),
    ],
  };
}

/**
 * EVERY pair of the six segments carrying local↔local traffic (R4) -- fifteen
 * observed pairs, in descending volume, so the top-K budget, `hiddenPairs` and
 * the "N of M" disclosure have real data to work on.
 *
 * A fixture rather than real content, and for the usual reason (LES-298's
 * family): the shipped fixture has exactly ONE observed pair, so the branch
 * that budgets, hides and discloses is written, type-checked and never run.
 * The real pit bundle exercises it at 40 pairs (`layout.real.test.ts`); this is
 * the small, readable case a golden-style assertion can name.
 *
 * Built from hosts the fixture already places, one per segment, so no node is
 * added and nothing else about the drawing moves. Bytes descend by pair index,
 * which makes the drawn set exactly "the first K of `trafficPairs`".
 */
export function withEastWestPairs(): TopologyExport {
  const host: Record<string, string> = {
    [S_110]: "address:10.1.10.1",
    [S_192]: "address:10.20.192.9",
    [S_193]: "address:10.20.193.20",
    [S_2080]: "address:10.80.20.40",
    [S_7280]: "address:10.80.72.11",
    [S_168]: "address:192.168.0.20",
  };
  const segments = [S_110, S_192, S_193, S_2080, S_7280, S_168];
  const extra: TopologyExportEdge[] = [];
  let rank = 0;
  for (let i = 0; i < segments.length; i++) {
    for (let j = i + 1; j < segments.length; j++) {
      rank += 1;
      extra.push({
        src: host[segments[i]!]!,
        dst: host[segments[j]!]!,
        proto: rank % 2 === 0 ? "udp" : "tcp",
        // 15 distinct, strictly descending magnitudes -- so "the busiest K"
        // is unambiguous and a tie-break never decides what is drawn.
        bytes: (16 - rank) * 1_000_000,
        flows: rank,
        evidence_class: "observed",
      });
    }
  }
  return { ...trimmedExport, edges: [...trimmedExport.edges, ...extra] };
}

/**
 * One infrastructure device whose segment this map draws NO tile for -- the
 * export placed it in `10.250.0.0/24`, which is not in `segments[]`.
 *
 * Also fixture-only: every device on both real bundles is placed in a drawn
 * segment, so the centred-and-dashed path has no live case. It is the honest
 * shape of "the export knows more than the map draws", and the alternative to a
 * fixture is shipping that branch untested.
 */
export function withUnplacedDevice(): TopologyExport {
  return {
    ...trimmedExport,
    nodes: [
      ...trimmedExport.nodes,
      node({
        id: "address:10.250.0.9",
        segment: "10.250.0.0/24",
        role: "dns-resolver",
        windows_seen: 10,
      }),
    ],
  };
}

/**
 * NINE segments in the existing `10.80` site, so that group is over R6's
 * `SITE_BUDGET` of six and collapses: six tiles plus a `+3 more` tile.
 *
 * Host counts descend with the index (`10.80.81.0/24` holds 4, `10.80.89.0/24`
 * holds 1 and the last two hold none), which makes the two ORDERS visibly
 * different -- ranked by hosts the new segments sort between the fixture's own
 * two, by CIDR they sort after them -- and gives the budget an unambiguous
 * boundary to cut at. The `10.20` and `misc` groups are untouched, so the same
 * drawing carries an over-budget group AND two under-budget ones, which is what
 * makes "a group under budget claims no residual" assertable on one layout.
 */
export function withOverBudgetSite(): TopologyExport {
  const segments = [...trimmedExport.segments];
  const nodes = [...trimmedExport.nodes];
  for (let i = 0; i < 7; i++) {
    const third = 81 + i;
    const cidr = `10.80.${third}.0/24`;
    // 4, 3, 2, 1, 1, 1, 5 placed hosts. Two properties are deliberate:
    //
    //  - the THREE-WAY tie at the budget's own boundary, so the CIDR tie-break
    //    decides a real case rather than a hypothetical one (`10.80.84.0/24` is
    //    drawn; `85` and `86`, which hold exactly as many hosts, are not) -- and
    //    the two collapsed ones still HOLD a host, which is what lets a test put
    //    east-west traffic behind a `+N more` tile;
    //  - the LAST segment is the group's third busiest, so the two orders show
    //    different SETS rather than the same six in a different order. A fixture
    //    where they coincide passes with either implementation and certifies
    //    nothing (LES-312's rule, applied to the ranking).
    const hosts = i === 6 ? 5 : i < 4 ? 4 - i : 1;
    segments.push({
      cidr,
      name: `seg-10-80-${third}-0`,
      entities: hosts,
      coverage_source: "inferred_from_traffic",
    });
    for (let h = 0; h < hosts; h++) {
      nodes.push(
        node({
          id: `address:10.80.${third}.${10 + h}`,
          segment: cidr,
          role: "generic-endpoint",
          windows_seen: 10,
        }),
      );
    }
  }
  return { ...trimmedExport, segments, nodes };
}

/**
 * `withOverBudgetSite()` plus east-west traffic that REACHES the collapsed set:
 * two links into different hidden segments of one group (which must merge onto
 * that group's single anchor) and one BETWEEN two hidden segments (which no
 * curve can carry).
 *
 * Separate from `withOverBudgetSite` so the collapsed goldens' own arc band
 * stays the fixture's three conduits, and shared from here so the layout test
 * and the view test describe ONE drawing (LES-025).
 */
export function withCollapsedTraffic(): TopologyExport {
  const base = withOverBudgetSite();
  const extra: TopologyExportEdge[] = [
    // 10.20.193.0/24 (drawn) → 10.80.85.0/24 (collapsed)
    { src: "address:10.20.193.20", dst: "address:10.80.85.10", proto: "tcp", bytes: 900_000, flows: 9, evidence_class: "observed" },
    // 10.20.193.0/24 (drawn) → 10.80.86.0/24 (collapsed) -- the SAME two anchors
    { src: "address:10.20.193.21", dst: "address:10.80.86.10", proto: "tcp", bytes: 100_000, flows: 3, evidence_class: "observed" },
    // 10.80.85.0/24 ↔ 10.80.86.0/24: BOTH collapsed, in one group
    { src: "address:10.80.85.10", dst: "address:10.80.86.10", proto: "udp", bytes: 50_000, flows: 2, evidence_class: "observed" },
  ];
  return { ...base, edges: [...base.edges, ...extra] };
}

/** Two segments in a BRAND-NEW `/16`, forming a new site COLUMN -- the
 *  column-COUNT-changing edit that is NOT local (`15` §5.2, corrected): the
 *  new `10.99` column slots in after `10.80`, so the misc column, the unplaced
 *  tile, the total width and the width-centred chrome all move. Its golden
 *  (`level0-added-column`) is the reviewed baseline S4's pins commit against;
 *  nothing here should be read as a claim that the rest stayed put. */
export function withColumnAddingSegment(): TopologyExport {
  return {
    ...trimmedExport,
    segments: [
      ...trimmedExport.segments,
      { cidr: "10.99.1.0/24", name: "seg-10-99-1-0", entities: 1, coverage_source: "inferred_from_traffic" },
      { cidr: "10.99.2.0/24", name: "seg-10-99-2-0", entities: 1, coverage_source: "inferred_from_traffic" },
    ],
    nodes: [
      ...trimmedExport.nodes,
      node({ id: "address:10.99.1.5", segment: "10.99.1.0/24", role: "generic-endpoint", windows_seen: 10 }),
      node({ id: "address:10.99.2.5", segment: "10.99.2.0/24", role: "web-tier", windows_seen: 10 }),
    ],
  };
}
