import { useState } from "react";

import { useApi } from "../../api/context";
import type { TreeNode, TreePersona, TreeSegment } from "../../api/types";
import { formatCount } from "../../model/sources";
import { ErrorLine } from "../shell/parts";
import { useLoad } from "../shell/useLoad";

/**
 * The twin as a hierarchy: segment -> node group -> what the group is given.
 * Rows are node GROUPS (`count` instances at deploy), the handle twin-tier
 * editing will address; nothing here is per instance.
 */
export function TwinTree({ id }: { id: string }) {
  const api = useApi();
  const { data, error, loading } = useLoad((signal) => api.getTwinTree(id, signal), [id]);
  if (loading && !data) return <p className="muted">Loading the tree…</p>;
  return (
    <div data-testid="twin-tree">
      <ErrorLine error={error} prefix="could not load the tree" />
      {data ? (
        <>
          {data.warnings.length ? (
            <ul className="muted" role="status">
              {data.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          ) : null}
          <p className="muted">
            {formatCount(data.hosts)} hosts across {formatCount(data.segments.length)} segments
            {data.traffic_profile ? <> · personas from <span className="mono">{data.traffic_profile}</span></> : " · no traffic profile in the bundle"}
            {data.tap_plan ? <> · taps from <span className="mono">{data.tap_plan}</span></> : null}
          </p>
          <ul className="tree">
            {data.segments.map((s) => (
              <SegmentRow key={s.name} segment={s} />
            ))}
          </ul>
          {data.routers.length ? (
            <>
              <h3>Routers</h3>
              <ul className="tree">
                {data.routers.map((r) => (
                  <NodeRow key={r.name} node={r} />
                ))}
              </ul>
            </>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

function SegmentRow({ segment }: { segment: TreeSegment }) {
  const [open, setOpen] = useState(false);
  const mix = segment.roles.map((r) => `${r.role ?? "no role"} ×${r.hosts}`).join(", ");
  return (
    <li data-testid="tree-segment">
      <button type="button" className="tree-row" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <span className="tree-caret" aria-hidden="true">{open ? "▾" : "▸"}</span>
        <span className="mono">{segment.cidr}</span>
        <span className="muted">{segment.name}</span>
        <span className="badge">{formatCount(segment.hosts)} hosts</span>
        <span className="muted tree-mix">{mix}</span>
        {segment.taps.map((t, i) => (
          <span key={i} className="badge" title={`tap ${t.mode}`}>tap {t.sensors.join(", ")}</span>
        ))}
        <Occupancy value={segment.observation?.occupancy} />
      </button>
      {open ? (
        segment.nodes.length ? (
          <ul className="tree">
            {segment.nodes.map((n) => (
              <NodeRow key={n.name} node={n} />
            ))}
          </ul>
        ) : (
          <p className="muted tree-empty">No hosts on this segment.</p>
        )
      ) : null}
    </li>
  );
}

function NodeRow({ node }: { node: TreeNode }) {
  const [open, setOpen] = useState(false);
  const listen = node.listen.map((l) => `${l.proto}/${l.port}`).join(" ");
  const conf = node.observation?.role_confidence;
  return (
    <li data-testid="tree-node">
      <button type="button" className="tree-row" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <span className="tree-caret" aria-hidden="true">{open ? "▾" : "▸"}</span>
        <span className="mono">{node.name}</span>
        <span className="badge">{node.role ?? "no role"}</span>
        {node.count > 1 ? <span className="badge">×{node.count}</span> : null}
        {node.kind === "router" ? <span className="badge">router · {node.segments.length} segments</span> : null}
        {listen ? <span className="muted mono">{listen}</span> : null}
        {node.personas.length ? <span className="muted">{node.personas.map((p) => p.id).join(", ")}</span> : <span className="muted">no persona</span>}
        {typeof conf === "number" ? <span className={`badge ${conf < 0.5 ? "low" : ""}`}>role conf {conf.toFixed(2)}</span> : null}
      </button>
      {open ? <NodeDetail node={node} /> : null}
    </li>
  );
}

function NodeDetail({ node }: { node: TreeNode }) {
  const o = node.observation;
  return (
    <div className="tree-detail" data-testid="tree-detail">
      <dl className="facts">
        <dt>Image</dt>
        <dd className="mono">{node.image}</dd>
        <dt>Instances</dt>
        <dd className="mono">{node.instances.join(", ")}</dd>
        <dt>Segments</dt>
        <dd className="mono">{node.segments.join(", ")}</dd>
        {node.requires.length ? (
          <>
            <dt>Requires</dt>
            <dd>{node.requires.join(", ")}</dd>
          </>
        ) : null}
        {node.resources ? (
          <>
            <dt>Resources</dt>
            <dd className="mono">{Object.entries(node.resources).map(([k, v]) => `${k} ${String(v)}`).join(" · ")}</dd>
          </>
        ) : null}
        <dt>Listens</dt>
        <dd className="mono">{node.listen.length ? node.listen.map((l) => `${l.proto}/${l.port}`).join(", ") : <span className="muted">no claim</span>}</dd>
        {node.install.length ? (
          <>
            <dt>Install</dt>
            <dd className="mono">{node.install.join("; ")}</dd>
          </>
        ) : null}
        {node.joins ? (
          <>
            <dt>Joins</dt>
            <dd className="mono">{node.joins}</dd>
          </>
        ) : null}
        <dt>Observed</dt>
        <dd>
          {o ? (
            <>
              {o.windows_seen ?? "?"} of {o.windows_total ?? "?"} windows
              {typeof o.occupancy === "number" ? `, occupancy ${o.occupancy.toFixed(2)}` : ""}
              {typeof o.role_confidence === "number" ? `, role confidence ${o.role_confidence.toFixed(2)}` : ""}
            </>
          ) : (
            <span className="muted">not observed (authored)</span>
          )}
        </dd>
      </dl>
      {node.personas.length ? (
        node.personas.map((p) => <PersonaBlock key={`${p.id}:${p.bound_by}`} persona={p} count={node.count} />)
      ) : (
        <p className="muted">The traffic profile gives this group no persona.</p>
      )}
    </div>
  );
}

function PersonaBlock({ persona, count }: { persona: TreePersona; count: number }) {
  const who = persona.bound_by === "role" ? "by role" : persona.instances === count ? "every instance" : `${persona.instances} of ${count} instances`;
  return (
    <div className="tree-persona" data-testid="tree-persona">
      <p>
        <strong className="mono">{persona.id}</strong> <span className="muted">{who}</span>
        {persona.schedule ? <span className="badge">{persona.schedule}</span> : null}
        {persona.os_family ? <span className="badge">{persona.os_family}</span> : null}
        {persona.drive ? <span className="badge">{persona.drive}</span> : null}
        {persona.missing ? <span className="badge low">no document</span> : null}
      </p>
      {persona.emits.length ? (
        <table className="list">
          <thead>
            <tr>
              <th>Emits</th>
              <th>Ports</th>
              <th>To</th>
              <th>Cadence</th>
              <th>Volume</th>
            </tr>
          </thead>
          <tbody>
            {persona.emits.map((e, i) => (
              <tr key={i}>
                <td className="mono">{e.transport ? `${e.proto}/${e.transport}` : e.proto}{e.optional ? <span className="muted"> optional</span> : null}</td>
                <td className="mono">{e.ports.join(", ") || "*"}</td>
                <td className="mono">{e.peer_role || "?"}{e.peer_scope === "all" ? <span className="muted"> (all)</span> : null}</td>
                <td>{e.cadence}</td>
                <td>{e.volume}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : persona.missing ? null : (
        <p className="muted">Emits nothing.</p>
      )}
    </div>
  );
}

function Occupancy({ value }: { value?: number | null }) {
  if (typeof value !== "number") return null;
  return <span className={`badge ${value < 0.5 ? "low" : ""}`}>occupancy {value.toFixed(2)}</span>;
}
