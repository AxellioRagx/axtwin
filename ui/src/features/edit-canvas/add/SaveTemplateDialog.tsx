/**
 * Save as template (`docs/design/canvas-add-panel.md` §5.3): the dialog a marquee selection's menu
 * opens. Name, description and icon; then what becomes a setting (each group's count, the subnet,
 * the name prefix), the clean names proposed for the discovered ones (editable), the ports the
 * outgoing links become, and what is stripped. Save writes it through `POST /api/templates`, which
 * validates it (a refusal shows its findings).
 */
import { useEffect, useState, type FormEvent } from "react";

import type { Proposal, SaveChoices } from "./saveTemplate";

export const TEMPLATE_ICONS = ["custom", "office", "rack", "dmz", "domain", "branch", "tiers", "ot", "edge"];

export function SaveTemplateDialog({ proposal: withUnder, bare, strippedBindings: strippedWith, bareStripped, busy, error, onSave, onClose }: { proposal: Proposal; bare?: Proposal | null; strippedBindings: readonly string[]; bareStripped?: readonly string[]; busy: boolean; error: string | null; onSave(choices: SaveChoices, used: Proposal): void; onClose(): void }) {
  // "Include the segments and hosts under the selected devices" (checked by default): the proposal
  // with them, or the bare selection when unchecked.
  const [under, setUnder] = useState(true);
  const offer = withUnder.under && bare ? withUnder.under : null;
  const proposal = offer && !under ? bare! : withUnder;
  const strippedBindings = offer && !under ? (bareStripped ?? strippedWith) : strippedWith;
  const [label, setLabel] = useState(proposal.label);
  const [description, setDescription] = useState(proposal.description);
  const [icon, setIcon] = useState(proposal.icon);
  const [prefix, setPrefix] = useState(proposal.stem);
  const [names, setNames] = useState<Record<string, string>>(() => Object.fromEntries(proposal.names.map((c) => [c.from, c.to])));
  const [counts, setCounts] = useState<SaveChoices["counts"]>(() => Object.fromEntries(proposal.counts.map((c) => [c.node, { on: c.on, label: c.label }])));
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [busy, onClose]);
  const empty = proposal.set.devices.length + proposal.set.hosts.length === 0;
  const dupNames = new Set(Object.values(names).filter((n, i, all) => all.indexOf(n) !== i));
  const problem = empty ? "nothing in the selection to save: select devices, hosts or a subtree" : !label.trim() ? "give the template a name" : dupNames.size ? `two items would both be named ${[...dupNames][0]}` : null;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (problem || busy) return;
    onSave({ label: label.trim(), description, icon, prefix: prefix.trim() || proposal.stem, names, counts }, proposal);
  };
  const s = proposal.stripped;
  return (
    <div className="modal-backdrop" data-testid="save-template-backdrop">
      <form className="modal panel cv-save-template" role="dialog" aria-modal="true" aria-label="Save as template" data-testid="save-template-dialog" onSubmit={submit}>
        <h2 style={{ marginTop: 0 }}>Save as template</h2>
        <p className="muted">
          {proposal.set.devices.length} device{proposal.set.devices.length === 1 ? "" : "s"} · {proposal.set.segments.length} segment{proposal.set.segments.length === 1 ? "" : "s"} · {proposal.set.hosts.length} host group{proposal.set.hosts.length === 1 ? "" : "s"}. It appears under My templates in the Add panel.
        </p>
        {offer ? (
          <label className="cv-check" data-testid="st-under">
            <input type="checkbox" checked={under} onChange={(e) => setUnder(e.target.checked)} />
            <span>
              Include the segments and hosts under the selected devices ({offer.segments} segment{offer.segments === 1 ? "" : "s"}, {offer.hosts} host group{offer.hosts === 1 ? "" : "s"})
            </span>
          </label>
        ) : null}
        <div className="row">
          <div className="field" style={{ flex: 2 }}>
            <label htmlFor="st-label">Name</label>
            <input id="st-label" type="text" value={label} onChange={(e) => setLabel(e.target.value)} autoFocus autoComplete="off" />
          </div>
          <div className="field" style={{ flex: 1 }}>
            <label htmlFor="st-icon">Icon</label>
            <select id="st-icon" value={icon} onChange={(e) => setIcon(e.target.value)}>
              {TEMPLATE_ICONS.map((i) => <option key={i} value={i}>{i}</option>)}
            </select>
          </div>
        </div>
        <div className="field">
          <label htmlFor="st-description">Description</label>
          <input id="st-description" type="text" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="what this section is (optional)" autoComplete="off" />
        </div>

        <h3>What becomes a setting</h3>
        <div className="field">
          <label htmlFor="st-prefix">Name prefix (default)</label>
          <input id="st-prefix" type="text" value={prefix} onChange={(e) => setPrefix(e.target.value.replace(/[^A-Za-z0-9._-]+/g, "-"))} autoComplete="off" />
          <span className="hint">{proposal.stem !== "tpl" ? `the names' common stem; ` : ""}every name becomes {"{prefix}-…"}</span>
        </div>
        {proposal.set.segments.length ? <p className="muted" data-testid="st-subnet">subnet: auto (the segments' CIDRs are not kept; a drop takes the next free /24s)</p> : null}
        {proposal.counts.length ? (
          <table className="cv-st-table" data-testid="st-counts">
            <tbody>
              {proposal.counts.map((c) => (
                <tr key={c.node}>
                  <td>
                    <input type="checkbox" aria-label={`Count of ${c.node} as a setting`} checked={counts[c.node]?.on ?? c.on} onChange={(e) => setCounts((x) => ({ ...x, [c.node]: { label: x[c.node]?.label ?? c.label, on: e.target.checked } }))} />
                  </td>
                  <td>
                    <input type="text" aria-label={`Setting label for ${c.node}`} value={counts[c.node]?.label ?? c.label} onChange={(e) => setCounts((x) => ({ ...x, [c.node]: { on: x[c.node]?.on ?? c.on, label: e.target.value } }))} />
                  </td>
                  <td className="muted">default {c.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}

        <h3>Names</h3>
        <table className="cv-st-table" data-testid="st-names">
          <tbody>
            {proposal.names.map((c) => (
              <tr key={`${c.kind}:${c.from}`} data-from={c.from}>
                <td className="mono muted">{c.from}</td>
                <td>→</td>
                <td className="mono">{"{prefix}-"}</td>
                <td>
                  <input type="text" aria-label={`New name for ${c.from}`} value={names[c.from] ?? c.to} onChange={(e) => setNames((x) => ({ ...x, [c.from]: e.target.value.replace(/[^A-Za-z0-9._-]+/g, "-") }))} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {proposal.ports.length ? (
          <>
            <h3>Ports</h3>
            <ul data-testid="st-ports">
              {proposal.ports.map((p) => (
                <li key={p.name}>
                  <span className="mono">{p.name}</span> on {names[p.device] ?? p.device} <span className="muted">({p.reason})</span>
                </li>
              ))}
            </ul>
          </>
        ) : null}

        <h3>Stripped</h3>
        <ul className="muted" data-testid="st-stripped">
          <li>{s.addresses} observed address{s.addresses === 1 ? "" : "es"} (CIDRs and pinned IPs)</li>
          <li>{s.provenance} provenance and observation field{s.provenance === 1 ? "" : "s"}</li>
          {s.taps ? <li>{s.taps} tap{s.taps === 1 ? "" : "s"} (bound to sensors outside the selection)</li> : null}
          {s.conduits ? <li>{s.conduits} conduit{s.conduits === 1 ? "" : "s"} leaving the selection</li> : null}
          {strippedBindings.map((b) => <li key={b}>persona binding {b} (not in the persona library)</li>)}
          {proposal.leftOut.map((h) => <li key={h}>{h}: no segment inside the selection, left out</li>)}
        </ul>

        {problem ? <p className="hint" data-testid="st-problem">{problem}</p> : null}
        {error && !busy ? (
          <div className="error" role="alert" data-testid="st-error">
            {error}
          </div>
        ) : null}
        <div className="row">
          <button type="submit" className="primary" disabled={Boolean(problem) || busy} data-testid="st-save">
            {busy ? "Saving…" : "Save template"}
          </button>
          <button type="button" onClick={onClose} disabled={busy}>
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
}

