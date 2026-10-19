/**
 * The host table -- a first-class view (ruling §2 rule 5): every host, with a
 * filter and sortable columns, sharing selection and the URL with the map.
 *
 * "Recorded" and "live" are separate columns with their own headings. Live is
 * empty ("not checked") until the operator runs the live check, then carries
 * the check's age in its heading.
 */
import { useEffect, useMemo, useState } from "react";

import { filterHosts, sortHosts, type DeployedTwin } from "../../model/deployed";
import type { SortKey, TwinQuery } from "../../model/appRoutes";
import { ago, plural, powerTone, statusTone, statusWord } from "../../model/format";
import { shortPersona } from "./TwinMap";

const COLUMNS: ReadonlyArray<{ key: SortKey; label: string; title?: string }> = [
  { key: "host", label: "Host" },
  { key: "segment", label: "Segment" },
  { key: "ip", label: "Address" },
  { key: "role", label: "Role" },
  { key: "os", label: "OS" },
  { key: "personas", label: "Personas" },
  { key: "recorded", label: "Status (recorded)", title: "What axRange recorded when it deployed and applied (state store)" },
  { key: "live", label: "Power (live)", title: "Read from the cluster when you run the live check" },
];

export function HostTable(props: {
  model: DeployedTwin;
  query: TwinQuery;
  onQuery: (patch: Partial<TwinQuery>, opts?: { replace?: boolean }) => void;
  selectedHost: string | null;
  onHost: (host: string) => void;
  live: { power: ReadonlyMap<string, string>; checkedAt: number } | null;
  now: number;
}): JSX.Element {
  const { model, query, live } = props;
  // The filter box is local while typing and written to the URL (replace, so
  // Back is not a keystroke history).
  const [text, setText] = useState(query.q);
  useEffect(() => setText(query.q), [query.q]);

  const rows = useMemo(() => {
    const filtered = filterHosts(model.hosts, {
      segment: query.segment,
      q: query.q,
      personas: query.personas,
      persona: query.persona,
    });
    return sortHosts(filtered, query.sort, query.dir, (id) => live?.power.get(id) ?? null);
  }, [model.hosts, query.segment, query.q, query.personas, query.persona, query.sort, query.dir, live]);

  const sortBy = (key: SortKey): void => {
    props.onQuery({ sort: key, dir: query.sort === key && query.dir === "asc" ? "desc" : "asc" });
  };

  return (
    <section className="host-table-section" aria-labelledby="hosts-title">
      <div className="section-head">
        <h2 id="hosts-title">Hosts</h2>
        <label className="filter">
          <span className="sr-only">Filter hosts</span>
          <input
            type="search"
            placeholder="Filter: name, address, role, persona…"
            value={text}
            data-testid="host-filter"
            onChange={(e) => {
              setText(e.target.value);
              props.onQuery({ q: e.target.value }, { replace: true });
            }}
          />
        </label>
        <label className="filter-select">
          <span className="sr-only">Hosts with or without personas</span>
          <select
            value={query.personas}
            data-testid="persona-presence"
            onChange={(e) => props.onQuery({ personas: e.target.value as TwinQuery["personas"] })}
          >
            <option value="any">all hosts</option>
            <option value="with">with personas</option>
            <option value="without">without personas</option>
          </select>
        </label>
        <label className="filter-select">
          <span className="sr-only">Hosts running a persona</span>
          <select
            value={query.persona ?? ""}
            data-testid="persona-filter"
            onChange={(e) => props.onQuery({ persona: e.target.value || null })}
          >
            <option value="">any persona</option>
            {model.personaMix.map((p) => (
              <option key={p.persona} value={p.persona}>
                {p.persona} ({p.count})
              </option>
            ))}
            {query.persona && !model.personaMix.some((p) => p.persona === query.persona) && (
              <option value={query.persona}>{query.persona} (none on this twin)</option>
            )}
          </select>
        </label>
        {query.segment && (
          <span className="chip-filter">
            segment <span className="mono">{query.segment}</span>{" "}
            <button type="button" className="link" onClick={() => props.onQuery({ segment: null })} aria-label="Show hosts of every segment">
              ×
            </button>
          </span>
        )}
        <span className="meta" data-testid="host-count" aria-live="polite">
          {rows.length === model.hosts.length ? plural(rows.length, "host") : `${rows.length} of ${model.hosts.length} hosts`}
        </span>
      </div>
      <div className="table-scroll">
        <table className="hosts" data-testid="host-table">
          <thead>
            <tr>
              {COLUMNS.map((c) => (
                <th key={c.key} scope="col" aria-sort={query.sort === c.key ? (query.dir === "asc" ? "ascending" : "descending") : "none"}>
                  <button type="button" className="th-sort" title={c.title} onClick={() => sortBy(c.key)}>
                    {c.label}
                    {c.key === "live" && live ? ` · ${ago(live.checkedAt, props.now)}` : ""}
                    {query.sort === c.key ? (query.dir === "asc" ? " ▲" : " ▼") : ""}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((h) => {
              const power = live ? (live.power.get(h.id) ?? "unknown") : null;
              const selected = props.selectedHost === h.id;
              return (
                <tr
                  key={h.id}
                  className={selected ? "is-selected" : undefined}
                  aria-selected={selected}
                  data-host={h.id}
                  onClick={() => props.onHost(h.id)}
                >
                  <td>
                    <button
                      type="button"
                      className="link mono"
                      onClick={(e) => {
                        e.stopPropagation();
                        props.onHost(h.id);
                      }}
                    >
                      {h.id}
                    </button>
                    {h.isRouter && <span className="tag">router</span>}
                  </td>
                  <td className="mono">
                    {h.primarySegment ?? <span className="meta">none</span>}
                    {h.segments.length > 1 && <span className="meta"> +{h.segments.length - 1}</span>}
                  </td>
                  <td className="mono">{h.ips[0] ?? <span className="meta">none recorded</span>}</td>
                  <td>
                    {h.raw.role ?? <span className="meta">none</span>}
                    {h.raw.role_origin && h.raw.role_origin !== "baseline" && (
                      <span className="tag" title={`role set by ${h.raw.role_origin}; the twin's own is ${h.raw.role_baseline ?? "none"}`}>
                        changed
                      </span>
                    )}
                  </td>
                  <td>{h.raw.os ?? <span className="meta">unknown</span>}</td>
                  <td>
                    {h.personaIds.length === 0 ? (
                      <span className="meta">none</span>
                    ) : (
                      h.personaIds.map((p) => (
                        <span key={p} className="persona-tag" title={p}>
                          {shortPersona(p)}
                        </span>
                      ))
                    )}
                  </td>
                  <td>
                    <span className={`status tone-${statusTone(h.recorded)}`}>{statusWord(h.recorded)}</span>
                    {h.raw.last_error && <span className="bad" title={h.raw.last_error.error_message ?? ""}> · error</span>}
                  </td>
                  <td>
                    {power === null ? (
                      <span className="meta">not checked</span>
                    ) : (
                      <span className={`status tone-${powerTone(power)}`}>{power}</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {rows.length === 0 && (
          <p className="empty">
            No host matches{query.q ? ` “${query.q}”` : ""}
            {query.segment ? ` on segment ${query.segment}` : ""}.
          </p>
        )}
      </div>
    </section>
  );
}
