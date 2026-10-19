/**
 * Level 2 -- the host facts table (`docs/design/15-console-canvas.md` §3/§4):
 * the inspector-as-main-pane. Keys and names, the role with its class /
 * confidence / source, the listen set with protocols, the dial set, edges
 * grouped by peer role and segment, and every field's evidence -- all read
 * from the export's own fields, never inferred.
 *
 * Plain HTML (a table), not SVG: this level is text, and the budget is one
 * facts table (§3).
 */
import { useMemo } from "react";

import type { AssertionsDoc } from "../api/types";
import type { ConsoleGraph, GraphNode } from "../model/graph";
import { EMPTY_CHANGE_SET, isNodeChanged, type ChangeSet } from "../model/changes";
import { evidenceClassName } from "../model/evidence";
import { roleBucket } from "../model/roles";
import type { Filters } from "../model/zoom";
import { changeClasses, formatPercent } from "./mapParts";
import { Row } from "./paneParts";

export interface Level2Props {
  graph: ConsoleGraph;
  /** A declared design: a host has no measured occupancy, ports or edges --
   *  those rows say so instead of showing a fabricated 100% or "none observed". */
  declared?: boolean;
  hostKey: string;
  assertions: AssertionsDoc | null;
  /** The active filters -- read only for `filters.changed`, the `?changed=1`
   *  reachability flag (the other three do not dim a single-host facts view).
   *  Optional so a unit test can render the facts table without a route. */
  filters?: Filters;
  /** S6: if this host's id is in the change set, the facts table is marked
   *  (`is-changed` under `?changed=1`, glow for 5s). Absent ⇒ empty. */
  changeSet?: ChangeSet;
  changeGlow?: boolean;
}

interface PeerGroup {
  role: string;
  segment: string | null;
  flows: number;
  bytes: number;
  peers: number;
  protos: string[];
}

export function Level2(props: Level2Props): JSX.Element {
  const { graph, hostKey, assertions } = props;
  const changeSet = props.changeSet ?? EMPTY_CHANGE_SET;
  const changeGlow = props.changeGlow ?? false;
  const changeFilterOn = props.filters?.changed ?? false;
  const node = graph.nodeById.get(hostKey) ?? null;

  const peerGroups = useMemo(() => (node ? groupEdges(graph, node.id) : []), [graph, node]);

  if (!node) {
    return (
      <div className="map-scroll">
        <p className="notice is-error" data-testid="no-host">
          no host <code>{hostKey}</code> in this twin
        </p>
      </div>
    );
  }

  const affecting = affectingAssertions(assertions, node);

  return (
    <div
      className={`map-scroll facts-table${changeClasses(isNodeChanged(changeSet, node.id), changeFilterOn, changeGlow)}`}
      data-testid="level2"
    >
      <h3 className="mono">{node.id}</h3>
      <table className="facts2">
        <tbody>
          <Row label="address" value={node.address ?? (props.declared ? "allocated at deploy time" : "—")} />
          <Row label="locality" value={node.locality ?? "—"} />
          <Row label="segment" value={node.segment ?? "(unplaced)"} evidence={node.segmentEvidenceClass} />
          <Row
            label="role"
            value={node.role ?? "(no role discovered)"}
            evidence={node.roleEvidenceClass}
            confidence={node.roleConfidence}
          />
          {props.declared ? (
            <Row label="deploys" value="yes — declared in the twin config (nothing measured)" />
          ) : (
            <>
              <Row
                label="occupancy"
                value={node.occupancy === null ? "— (windows_seen absent)" : `${formatPercent(node.occupancy)} (${node.windowsSeen}/${graph.windows})`}
              />
              <Row label="ghost" value={node.isGhost ? node.ghostReasons.join(", ") : "no"} />
            </>
          )}
        </tbody>
      </table>
      {props.declared && (
        <p className="meta" data-testid="host-declared">
          Declared in the twin config. Its ports and traffic are not part of a declared design — see the running twin
          for what it does.
        </p>
      )}

      <h4>Listen set ({node.listenPorts.length})</h4>
      {node.listenPorts.length === 0 ? (
        <p className="meta">none observed</p>
      ) : (
        <ul className="ports" data-testid="listen-set">
          {node.listenPorts.map((p) => (
            <li key={p} className="mono">{p}</li>
          ))}
        </ul>
      )}

      <h4>Dial set ({node.dialPorts.length})</h4>
      {node.dialPorts.length === 0 ? (
        <p className="meta">none observed</p>
      ) : (
        <ul className="ports">
          {node.dialPorts.map((p) => (
            <li key={p} className="mono">{p}</li>
          ))}
        </ul>
      )}

      <h4>Edges by peer role · segment ({peerGroups.length})</h4>
      {peerGroups.length === 0 ? (
        <p className="meta">no edges touch this host</p>
      ) : (
        <table className="facts2" data-testid="host-edges">
          <thead>
            <tr>
              <th>peer role</th>
              <th>segment</th>
              <th>peers</th>
              <th>flows</th>
              <th>protos</th>
            </tr>
          </thead>
          <tbody>
            {peerGroups.map((g) => (
              <tr key={`${g.role}|${g.segment ?? ""}`}>
                <td>{g.role}</td>
                <td className="mono">{g.segment ?? "—"}</td>
                <td>{g.peers}</td>
                <td>{g.flows}</td>
                <td className="mono">{g.protos.join(", ") || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h4>Assertions affecting this host ({affecting.length})</h4>
      {affecting.length === 0 ? (
        <p className="meta">none</p>
      ) : (
        <ul className="assertion-list" data-testid="host-assertions">
          {affecting.map((a, i) => (
            <li key={`${a.block}-${i}`}>
              <span className={`ev-badge ${evidenceClassName("asserted")}`}>{a.block}</span>{" "}
              <span className="mono">{a.summary}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Edges touching the host, grouped by the OTHER endpoint's role bucket and
 *  segment (`15` §4). Directed edges are counted from either side. */
function groupEdges(graph: ConsoleGraph, id: string): PeerGroup[] {
  const groups = new Map<string, PeerGroup & { peerSet: Set<string> }>();
  for (const e of graph.edges) {
    let peerId: string | null = null;
    if (e.src === id) peerId = e.dst;
    else if (e.dst === id) peerId = e.src;
    if (peerId === null) continue;
    const peer = graph.nodeById.get(peerId);
    const role = roleBucket(peer?.role, peer?.locality);
    const segment = peer?.segment ?? null;
    const key = `${role}|${segment ?? ""}`;
    const g =
      groups.get(key) ??
      { role, segment, flows: 0, bytes: 0, peers: 0, protos: [], peerSet: new Set<string>() };
    g.flows += e.flows;
    g.bytes += e.bytes;
    g.peerSet.add(peerId);
    if (e.proto && !g.protos.includes(e.proto)) g.protos.push(e.proto);
    groups.set(key, g);
  }
  return [...groups.values()]
    .map((g) => ({ role: g.role, segment: g.segment, flows: g.flows, bytes: g.bytes, peers: g.peerSet.size, protos: g.protos.slice().sort() }))
    .sort((a, b) => b.flows - a.flows);
}

interface AffectingAssertion {
  block: string;
  summary: string;
}

/** Assertion records whose key is this host's id or address (S5 fills the
 *  authoring; S3 just displays what the file already carries). */
function affectingAssertions(doc: AssertionsDoc | null, node: GraphNode): AffectingAssertion[] {
  if (!doc) return [];
  const keys = new Set<string>([node.id]);
  if (node.address) keys.add(`address:${node.address}`);
  const out: AffectingAssertion[] = [];
  for (const [block, records] of [
    ["add", doc.add],
    ["override", doc.override],
    ["suppress", doc.suppress],
  ] as const) {
    for (const r of records) {
      if (r.key && keys.has(r.key)) {
        out.push({ block, summary: r.field ? `${r.field} = ${JSON.stringify(r.value)}` : (r.note ?? r.key) });
      }
    }
  }
  return out;
}
