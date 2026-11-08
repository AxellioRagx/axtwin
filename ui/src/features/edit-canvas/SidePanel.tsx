/**
 * The inspector's property panels (`SPEC.md`: "a side panel instead of
 * modals", now the floating inspector's Selection tab): the selected node
 * group, segment, attachment or annotation, or the twin's own properties
 * when nothing is selected. `Checks` is the inspector's other tab: the
 * validation state and -- on a deployed twin -- every pending change with
 * the blast radius the API classified it with (*live · traffic*, *live · taps*
 * or *re-plan*; `docs/design/topology-layers.md` §4). Every
 * field writes through the editor's `apply`, which blocks an invalid edit
 * with its reason (shown on the canvas's strip, not here).
 */
import { useEffect, useState, type ReactNode } from "react";

import type { PendingChange, Persona, ValidateResult, ValidationFinding } from "../../api/types";
import { errorText } from "../../areas/shell/parts";
import type { Annotation } from "./edit/annotations";
import {
  addTap,
  attach,
  clearPersona,
  detach,
  duplicateOnto,
  mergeSegments,
  readDoc,
  removeLink,
  removeNode,
  removeSegment,
  removeTap,
  renameNode,
  renameSegment,
  setAttachmentAddress,
  setLinkField,
  setMemberRole,
  setNodeField,
  setPersona,
  setSegmentField,
  setTapSensorsOf,
  type DocModel,
  type WorkingDoc,
} from "./edit/document";
import type { Files } from "./edit/commands";
import { effectiveAssignment } from "./edit/tfc";
import { detachMember, instanceId, mergeGroups, mergeTargets, moveMember, splitGroup } from "./edit/members";
import { duplicateSubtree, subtreeCount, subtreeOf } from "./edit/subtree";
import { tapsOn } from "./edit/tap";
import { SWITCH_ROLES } from "./model/roles";

export type Selection =
  | { kind: "none" }
  | { kind: "node"; names: string[] }
  | { kind: "segment"; names: string[] }
  | { kind: "leg"; node: string; segment: string }
  /** A `links[]` entry, by its list index (the device tree, topology-layers §2.3). */
  | { kind: "link"; index: number }
  /** A tap binding, by its index in the tap plan (§8). */
  | { kind: "tap"; index: number }
  /** One member of a `×N` group (§5). */
  | { kind: "member"; node: string; index: number }
  /** A device and everything below it (§6). */
  | { kind: "subtree"; root: string }
  | { kind: "annotation"; id: string };

export interface SidePanelProps {
  twinId: string;
  model: DocModel;
  /** The baseline's model, for what a promote would assert; null when unknown. */
  baseline: DocModel | null;
  selection: Selection;
  locked: boolean;
  images: string[];
  personas: Persona[];
  /** The twin was cast from an imprint: a correction can be promoted to an assertion. */
  canPromote: boolean;
  /** Why this node or merge cannot be promoted (no observed members or network in the cast), or null. */
  promoteBlock(what: { node: string } | { from: string; to: string }): string | null;
  apply(label: string, fn: (doc: WorkingDoc, model: DocModel) => Files): string | null;
  onPromoteRole(node: string, role: string): void;
  onPromoteMerge(from: string, to: string): void;
  /** Record a member split in the imprint's assertions (topology-layers §5): an override `field: group`
   *  on each picked member's entity key, so a re-cast proposes the split. Absent on a twin not cast. */
  onPromoteSplit?: ((node: string, indices: number[], name: string) => void) | undefined;
  /** Why the members of this group cannot be keyed (no roster, or a roster that does not match the count), or null. */
  splitBlock?: ((node: string) => string | null) | undefined;
  annotations: Annotation[];
  onAnnotation(id: string, patch: Partial<Annotation>): void;
  onRemoveAnnotation(id: string): void;
  /** The API's refusal of the last write, verbatim, or null. */
  serverReason: string | null;
  /** The API's latest document validate for the files on screen, or null while none has answered. */
  validation: { result: ValidateResult | null; error: unknown } | null;
  /** A validate is in flight or owed for the files on screen. */
  validating: boolean;
  /** On a deployed twin: the API's classification of every pending change. */
  changes: PendingChange[] | null;
  /** The reason the last edit was blocked, shown until the next edit. */
  blocked: string | null;
  /** A node group or segment was renamed: the selection is keyed by name, re-key it. */
  onRenamed?: ((kind: "node" | "segment", from: string, to: string) => void) | undefined;
  /** Rule 3 (track 4): the segments a switch-class host was OBSERVED on and is not attached to
   *  (`canvas/evidence.ts` `observedSegments`), evidence the inspector offers to attach. */
  observedSegments?: ((node: string) => string[]) | undefined;
  /** The Provenance block's actions through the assertions seam (topology-layers §3.2, §3.3):
   *  confirm / doubt a node, a segment or a link; assert a host's device kind; assert an
   *  authored link or tap. Absent on a twin that was not cast. */
  onProvenance?: ((action: ProvenanceAction) => void) | undefined;
  /** Why a provenance action is not possible for this subject (nothing in the cast roster keys it), or null. */
  provenanceBlock?: ((subject: ProvenanceSubject) => string | null) | undefined;
  /** Select something from a panel (a link row, a tap row). */
  onSelect?: ((sel: Selection) => void) | undefined;
}

export type ProvenanceSubject = { node: string } | { segment: string } | { link: number } | { tap: number };
export type ProvenanceAction =
  | { kind: "confirm" | "doubt"; subject: ProvenanceSubject }
  | { kind: "assert-device"; node: string; deviceKind: "switch" | "router" | "host" }
  | { kind: "assert-link"; index: number }
  | { kind: "assert-tap"; index: number };

const ZONE_TYPES = ["", "vlan", "simple", "dmz", "mgmt", "ot"];

export function SidePanel(p: SidePanelProps) {
  const { selection } = p;
  return (
    <aside className="cv-side" aria-label="Properties" data-testid="side-panel">
      {selection.kind === "node" && selection.names.length === 1 ? <NodePanel {...p} name={selection.names[0]!} /> : null}
      {selection.kind === "node" && selection.names.length > 1 ? <NodesPanel {...p} names={selection.names} /> : null}
      {selection.kind === "segment" && selection.names.length === 1 ? <SegmentPanel {...p} name={selection.names[0]!} /> : null}
      {selection.kind === "segment" && selection.names.length > 1 ? <SegmentsPanel {...p} names={selection.names} /> : null}
      {selection.kind === "leg" ? <LegPanel {...p} node={selection.node} segment={selection.segment} /> : null}
      {selection.kind === "link" ? <LinkPanel {...p} index={selection.index} /> : null}
      {selection.kind === "tap" ? <TapPanel {...p} index={selection.index} /> : null}
      {selection.kind === "member" ? <MemberPanel {...p} node={selection.node} index={selection.index} /> : null}
      {selection.kind === "subtree" ? <SubtreePanel {...p} root={selection.root} /> : null}
      {selection.kind === "annotation" ? <AnnotationPanel {...p} id={selection.id} /> : null}
      {selection.kind === "none" ? <TwinPanel {...p} /> : null}
    </aside>
  );
}

/** The inspector's Checks tab. `onDiscard(path)` puts one file back to the
 *  baseline's text (a pending change is one file's delta), when the page has
 *  the baseline and the canvas is not locked. */
export function Checks(p: Pick<SidePanelProps, "model" | "serverReason" | "validation" | "validating" | "changes"> & { onDiscard?: ((path: string) => void) | undefined; provenance?: { filter: string | null; onFilter(source: string | null): void; fallback?: "discovered" | "authored" } | undefined }) {
  return (
    <div className="cv-side cv-checks" data-testid="checks">
      <Validation findings={p.model.findings} serverReason={p.serverReason} validation={p.validation} validating={p.validating} />
      {p.changes ? <Changes changes={p.changes} onDiscard={p.onDiscard} /> : null}
      {p.provenance ? <ProvenanceSummary model={p.model} filter={p.provenance.filter} onFilter={p.provenance.onFilter} fallback={p.provenance.fallback ?? "authored"} /> : null}
    </div>
  );
}

/** The counts per source over the document's segments, nodes and links
 *  (topology-layers §3.3); a row filters the canvas to that state. */
export function provenanceCounts(model: DocModel, fallback: "discovered" | "authored" = "authored"): Record<"discovered" | "asserted" | "authored" | "unconfirmed", number> {
  const counts = { discovered: 0, asserted: 0, authored: 0, unconfirmed: 0 };
  const bump = (s: string | null) => {
    const k = (s ?? fallback) as keyof typeof counts;
    if (k in counts) counts[k] += 1;
  };
  for (const s of model.te.segments) bump(s.source);
  for (const n of model.te.nodes) bump(n.source);
  for (const l of model.te.links) bump(l.source);
  return counts;
}

function ProvenanceSummary({ model, filter, onFilter, fallback }: { model: DocModel; filter: string | null; onFilter(source: string | null): void; fallback: "discovered" | "authored" }) {
  const counts = provenanceCounts(model, fallback);
  return (
    <section className="cv-side-section" data-testid="provenance-summary">
      <h3>Provenance</h3>
      <p className="muted hint">where the twin's segments, nodes and links came from; a row filters the canvas (the overlay shows the marks)</p>
      <ul className="cv-prov-rows">
        {(["discovered", "asserted", "authored", "unconfirmed"] as const).map((k) => (
          <li key={k}>
            <button type="button" className={`cv-prov-row${filter === k ? " pressed" : ""}`} aria-pressed={filter === k} data-testid={`prov-row-${k}`} onClick={() => onFilter(filter === k ? null : k)}>
              <span className={`cv-prov-badge cv-prov-${k}`} aria-hidden="true">{k === "discovered" ? "D" : k === "asserted" ? "A" : k === "authored" ? "+" : "?"}</span> {k} <span className="badge">{counts[k]}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** The validation state word: the canvas's structural findings, the API's
 *  refusal and the API's document validate, folded into one of three. */
export function validationState(p: Pick<SidePanelProps, "serverReason" | "validation" | "validating"> & { findings: ValidationFinding[] }): "invalid" | "validating" | "valid" {
  const local = p.findings.filter((f) => f.severity === "error");
  const apiErrors = (p.validation?.result?.findings ?? []).filter((f) => f.severity === "error");
  const apiFailed = p.validation !== null && (p.validation.error !== null || apiErrors.length > 0);
  return local.length || p.serverReason || apiFailed ? "invalid" : p.validating ? "validating" : "valid";
}

/** What the Checks tab wears on its strip: the validation state word (the
 *  gate's answer at a glance, without opening the tab). */
export function checksBadge(p: Pick<SidePanelProps, "model" | "serverReason" | "validation" | "validating">): string {
  return validationState({ findings: p.model.findings, serverReason: p.serverReason, validation: p.validation, validating: p.validating });
}

// ---------------------------------------------------------------------------
// Pieces
// ---------------------------------------------------------------------------
function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <label className="cv-field">
      <span className="cv-field-label">{label}</span>
      {children}
      {hint ? <span className="hint">{hint}</span> : null}
    </label>
  );
}

/** A text field that commits on blur or Enter, so a half-typed value is not an edit. */
function Text({ value, onCommit, disabled, ariaLabel, placeholder, mono, list }: { value: string; onCommit(v: string): void; disabled: boolean; ariaLabel: string; placeholder?: string; mono?: boolean; list?: string }) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  return (
    <input
      type="text"
      className={mono ? "mono" : undefined}
      aria-label={ariaLabel}
      list={list}
      value={v}
      placeholder={placeholder}
      disabled={disabled}
      onChange={(e) => setV(e.target.value)}
      onBlur={() => {
        if (v !== value) onCommit(v);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          if (v !== value) onCommit(v);
        }
      }}
    />
  );
}

/** The validation state: the canvas's structural findings (instant) and the
 *  API's document validate (the gate), each finding with its rule and place. */
function Validation({ findings, serverReason, validation, validating }: { findings: ValidationFinding[]; serverReason: string | null; validation: SidePanelProps["validation"]; validating: boolean }) {
  const local = findings.filter((f) => f.severity === "error");
  const api = validation?.result?.findings ?? [];
  const state = validationState({ findings, serverReason, validation, validating });
  return (
    <section className="cv-side-section" data-testid="validation">
      <h3>Validation</h3>
      <p className={state === "valid" ? "ok" : state === "invalid" ? "error" : "muted"} role="status" data-testid="validation-word">
        {state}
      </p>
      {validation?.error ? <p className="error">the API could not validate: {errorText(validation.error)}</p> : null}
      {local.length ? (
        <p className="muted" data-testid="validation-local">
          canvas: {local.length} error{local.length === 1 ? "" : "s"} the API's pass does not cover (the twin IR's own rules)
        </p>
      ) : null}
      {validation?.result && !validating ? (
        <p className="muted" data-testid="validation-api">
          API: {validation.result.disposition.toLowerCase()} · {validation.result.errors} error{validation.result.errors === 1 ? "" : "s"}, {validation.result.warnings} warning{validation.result.warnings === 1 ? "" : "s"}
        </p>
      ) : null}
      <ul className="cv-findings" data-testid="findings">
        {local.map((f, i) => (
          <li key={`l${i}`} data-testid="finding" data-rule={f.rule_id}>
            <span className="mono">{f.rule_id}</span> <span className="badge bad">error</span> {f.message}
            {f.location ? <span className="muted"> · {f.location}</span> : null}
          </li>
        ))}
        {api.filter((f) => !local.some((l) => l.rule_id === f.rule_id && l.message === f.message)).map((f, i) => (
          <li key={`a${i}`} data-testid="finding" data-rule={f.rule_id} data-severity={f.severity}>
            <span className="mono">{f.rule_id}</span> <span className="badge">{f.severity}</span> {f.message}
            {f.location ? <span className="muted"> · {f.location}</span> : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

function Changes({ changes, onDiscard }: { changes: PendingChange[]; onDiscard?: ((path: string) => void) | undefined }) {
  return (
    <section className="cv-side-section" data-testid="pending-changes">
      <h3>Pending changes</h3>
      {changes.length === 0 ? <p className="muted">none: the working copy equals the baseline</p> : null}
      <ul className="cv-changes">
        {changes.map((c) => (
          <li key={c.path} data-testid="pending-change" data-kind={c.kind}>
            <span className={`badge ${c.kind === "traffic" || c.kind === "taps" ? "ok" : c.kind === "topology" ? "bad" : ""}`}>{c.kind === "traffic" ? "live · traffic" : c.kind === "taps" ? "live · taps" : c.kind === "topology" ? "re-plan" : "inert"}</span> <span className="mono">{c.path}</span> <span className="muted">{c.op}</span>
            {onDiscard ? (
              <button type="button" className="cv-discard" onClick={() => onDiscard(c.path)} title={`put ${c.path} back to the baseline's text (an undoable edit)`} data-testid="discard-change">
                Discard
              </button>
            ) : null}
            <div className="muted cv-change-reason">
              {c.reason}
              {c.via.length ? ` · via ${c.via.join(", ")}` : ""}
              {Object.keys(c.roles).length ? ` · roles: ${Object.entries(c.roles).map(([n, r]) => `${n} → ${r}`).join(", ")}` : ""}
            </div>
            {c.paths.length ? <div className="mono muted cv-change-paths">{c.paths.slice(0, 6).join(", ")}{c.paths.length > 6 ? ` … +${c.paths.length - 6}` : ""}</div> : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

function TwinPanel(p: SidePanelProps) {
  const te = p.model.te;
  return (
    <section className="cv-side-section">
      <h3>Twin</h3>
      <dl className="facts">
        <dt>Id</dt>
        <dd className="mono">{te.id ?? <span className="muted">none</span>}</dd>
        <dt>Description</dt>
        <dd>{te.description ? te.description.split("\n")[0] : <span className="muted">none</span>}</dd>
        <dt>Params</dt>
        <dd className="mono">{Object.keys(te.params).length ? Object.entries(te.params).map(([k, v]) => `${k}=${String(v)}`).join(", ") : "none"}</dd>
        <dt>Segments</dt>
        <dd>{te.segments.length}</dd>
        <dt>Node groups</dt>
        <dd>
          {te.nodes.length} ({te.nodes.reduce((a, n) => a + n.countValue, 0)} instances)
        </dd>
        <dt>Traffic profile</dt>
        <dd className="mono">{p.model.tfcPath ?? <span className="muted">none yet</span>}</dd>
      </dl>
      <p className="muted">Select a node, a segment or an attachment to edit it. The twin config carries no presentation: positions and annotations live beside it.</p>
    </section>
  );
}

/** The Provenance block (topology-layers §3.1, §3.3): where the subject came
 *  from and how firmly, and the actions through the seam on a cast twin. */
function Provenance(p: SidePanelProps & { subject: ProvenanceSubject; source: string | null; confidence: number | null; extra?: ReactNode }) {
  const block = p.provenanceBlock?.(p.subject) ?? (p.onProvenance ? null : "this twin was not cast from an imprint; there is nothing to confirm or doubt");
  const act = (kind: "confirm" | "doubt") => p.onProvenance?.({ kind, subject: p.subject });
  return (
    <>
      <h4>Provenance</h4>
      <dl className="facts" data-testid="provenance">
        <dt>Source</dt>
        <dd data-testid="provenance-source">{p.source ?? "authored"}{typeof p.confidence === "number" ? ` · confidence ${p.confidence.toFixed(2)}` : ""}</dd>
      </dl>
      <div className="row cv-inline">
        <button type="button" disabled={p.locked || block !== null} title={block ?? "pin what discovery found as asserted: it survives a re-cast"} onClick={() => act("confirm")} data-testid="prov-confirm">
          Confirm
        </button>
        <button type="button" disabled={p.locked || block !== null} title={block ?? "mark this as unconfirmed (weak evidence); re-derived by the next cast unless pinned"} onClick={() => act("doubt")} data-testid="prov-doubt">
          Mark unconfirmed
        </button>
        {p.extra}
      </div>
      {block ? <p className="muted hint">{block}</p> : null}
    </>
  );
}

/** The ×N group's members (topology-layers §5): one row per instance, the
 *  role override (te) and the persona (tfc, by instance id) editable. */
function Members(p: SidePanelProps & { name: string }) {
  const n = p.model.te.nodes.find((x) => x.name === p.name);
  const [picked, setPicked] = useState<number[]>([]);
  const [anchor, setAnchor] = useState<number | null>(null);
  const [splitName, setSplitName] = useState("");
  const [bulkRole, setBulkRole] = useState("");
  const [bulkPersona, setBulkPersona] = useState("");
  if (!n || n.countValue <= 1) return null;
  const ro = p.locked;
  const rows = Array.from({ length: n.countValue }, (_, i) => i + 1);
  const instance = (i: number) => instanceId(n.name, i);
  // Shift-click picks the range from the last row picked (multi-select rows, §5).
  const pick = (i: number, range: boolean) => {
    setPicked((s) => {
      if (range && anchor !== null) {
        const [lo, hi] = anchor < i ? [anchor, i] : [i, anchor];
        const span = rows.filter((r) => r >= lo && r <= hi);
        return [...new Set([...s, ...span])].sort((a, b) => a - b);
      }
      return s.includes(i) ? s.filter((x) => x !== i) : [...s, i].sort((a, b) => a - b);
    });
    setAnchor(i);
  };
  const picking = picked.filter((i) => i <= n.countValue);
  const allPicked = picking.length === n.countValue;
  const setRoles = (role: string | null) => p.apply(`role of ${picking.length} members of ${n.name}`, (d) => picking.reduce((files, i) => setMemberRole({ ...d, files }, n.name, i, role), d.files));
  const setPersonas = (persona: string) =>
    p.apply(`persona of ${picking.length} members of ${n.name}`, (d) =>
      picking.reduce((files, i) => {
        const doc = { ...d, files };
        const mo = readDoc(doc);
        return persona ? setPersona(doc, mo, "node", instance(i), { persona }) : clearPersona(doc, mo, "node", instance(i));
      }, d.files),
    );
  const split = (assert: boolean) => {
    const name = splitName.trim();
    const which = [...picking];
    if (p.apply(`split ${which.length} of ${n.name} off as ${name}`, (d, m) => splitGroup(d, m, n.name, which, name)) !== null) return;
    if (assert) p.onPromoteSplit?.(n.name, which, name);
    setPicked([]);
    setSplitName("");
  };
  const splitReady = !ro && picking.length > 0 && picking.length < n.countValue && splitName.trim() !== "";
  const splitBlock = p.onPromoteSplit ? (p.splitBlock?.(n.name) ?? null) : null;
  return (
    <>
      <h4>Members</h4>
      <table className="cv-members" data-testid="members">
        <thead>
          <tr>
            <th>
              <label className="cv-split-pick" title="pick every member (Shift-click a row picks a range)">
                <input type="checkbox" aria-label="Pick every member" checked={allPicked} disabled={ro} onChange={() => setPicked(allPicked ? [] : rows)} /> pick
              </label>
            </th>
            <th>instance</th>
            <th>role</th>
            <th>persona</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((i) => {
            const m = n.members.find((x) => x.index === i) ?? null;
            const bound = p.model.tfc?.assignments.find((a) => a.by === "node" && a.target === instance(i)) ?? null;
            const inherited = effectiveAssignment(p.model.tfc, n.name, m?.role ?? n.role);
            return (
              <tr key={i} data-testid="member-row" data-index={i} data-overridden={m ? "true" : undefined} data-picked={picking.includes(i) ? "true" : undefined}>
                <td>
                  <label className="cv-split-pick">
                    <input type="checkbox" aria-label={`Pick ${instance(i)}`} checked={picking.includes(i)} disabled={ro} onChange={() => undefined} onClick={(e) => pick(i, e.shiftKey)} /> {i}
                  </label>
                </td>
                <td>
                  <button type="button" className="cv-link-row mono" onClick={() => p.onSelect?.({ kind: "member", node: n.name, index: i })} title="open the member's panel">
                    {instance(i)}
                  </button>
                </td>
                <td>
                  <Text ariaLabel={`Role of ${instance(i)}`} value={m?.role ?? ""} placeholder={n.role ?? "inherits"} disabled={ro} onCommit={(v) => p.apply(`role of ${instance(i)}`, (d) => setMemberRole(d, n.name, i, v.trim() || null))} />
                </td>
                <td>
                  <select aria-label={`Persona of ${instance(i)}`} value={bound?.persona ?? ""} disabled={ro} onChange={(e) => { const v = e.target.value; if (!v) p.apply(`unbind persona of ${instance(i)}`, (d, mo) => clearPersona(d, mo, "node", instance(i))); else p.apply(`persona of ${instance(i)}`, (d, mo) => setPersona(d, mo, "node", instance(i), { persona: v })); }}>
                    <option value="">{inherited ? `(${inherited.persona})` : "none"}</option>
                    {p.personas.map((ps) => (
                      <option key={ps.id} value={ps.id}>{ps.id}</option>
                    ))}
                    {bound && !p.personas.some((ps) => ps.id === bound.persona) ? <option value={bound.persona}>{bound.persona}</option> : null}
                  </select>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="muted hint">a member's role override is a traffic change while the group's image stands; its persona is the traffic profile's binding of that instance</p>
      {picking.length > 1 ? (
        <div className="cv-members-bulk" data-testid="members-bulk">
          <p className="hint">{picking.length} picked: change them at once (one undo step)</p>
          <div className="row cv-inline">
            <input type="text" aria-label="Role for picked members" placeholder={n.role ? `role (empty: inherit ${n.role})` : "role"} value={bulkRole} disabled={ro} onChange={(e) => setBulkRole(e.target.value)} />
            <button type="button" disabled={ro} data-testid="bulk-role" onClick={() => { if (setRoles(bulkRole.trim() || null) === null) setBulkRole(""); }}>
              Set role
            </button>
          </div>
          <div className="row cv-inline">
            <select aria-label="Persona for picked members" value={bulkPersona} disabled={ro} onChange={(e) => setBulkPersona(e.target.value)}>
              <option value="">unbind (inherit)</option>
              {p.personas.map((ps) => (
                <option key={ps.id} value={ps.id}>{ps.id}</option>
              ))}
            </select>
            <button type="button" disabled={ro} data-testid="bulk-persona" onClick={() => setPersonas(bulkPersona)}>
              Set persona
            </button>
          </div>
        </div>
      ) : null}
      <div className="row cv-inline" data-testid="split-form">
        <input type="text" aria-label="New group name" placeholder={`split ${picking.length || "…"} off as…`} value={splitName} disabled={ro} onChange={(e) => setSplitName(e.target.value)} />
        <button type="button" disabled={!splitReady} data-testid="split" title="the picked members become a new group with this name (the original keeps the rest); a topology change" onClick={() => split(false)}>
          Split
        </button>
        {p.onPromoteSplit ? (
          <button type="button" disabled={!splitReady || splitBlock !== null} data-testid="split-assert" title={splitBlock ?? "split, and record it in the imprint's assertions keyed by the members' entity keys, so a re-cast proposes the split instead of the whole group (re-casts the twin)"} onClick={() => split(true)}>
            Split and assert
          </button>
        ) : null}
      </div>
      {p.onPromoteSplit && splitBlock !== null ? (
        <p className="hint" data-testid="split-assert-block">
          {splitBlock}
        </p>
      ) : null}
    </>
  );
}

/** A device's subtree (§6): what it holds, Duplicate parked or onto a device
 *  (Ctrl+D on the canvas parks the copy), Remove the lot. */
function SubtreePanel(p: SidePanelProps & { root: string }) {
  const [target, setTarget] = useState("");
  if (!p.model.te.nodes.some((n) => n.name === p.root)) return <p className="muted">node {p.root} is not in the document</p>;
  const t = subtreeOf(p.model, p.root);
  const ro = p.locked;
  const devices = p.model.te.nodes.filter((n) => (n.kind === "switch" || n.kind === "router") && !t.devices.includes(n.name));
  const count = subtreeCount(p.model, t);
  const dup = (attachTo: string | null) => {
    const r = { root: "" };
    if (p.apply(`duplicate ${p.root}'s subtree${attachTo ? ` onto ${attachTo}` : ""}`, (d, m) => { const out = duplicateSubtree(d, m, p.root, attachTo); r.root = out.root; return out.files; }) === null) p.onSelect?.({ kind: "subtree", root: r.root });
  };
  return (
    <section className="cv-side-section" data-testid="subtree-panel">
      <h3>
        Subtree of <span className="mono">{p.root}</span>
      </h3>
      <p data-testid="subtree-count">{count}</p>
      <dl className="facts">
        <dt>Devices</dt>
        <dd className="mono">{t.devices.join(", ") || "none"}</dd>
        <dt>Segments</dt>
        <dd className="mono">{t.segments.join(", ") || "none"}</dd>
        <dt>Hosts</dt>
        <dd className="mono">{t.hosts.join(", ") || "none"}</dd>
        <dt>Uplink</dt>
        <dd className="mono">{t.uplink ? `${t.uplink.a.device}#${t.uplink.a.port} — ${t.uplink.b.device}#${t.uplink.b.port}` : "none (parked)"}</dd>
      </dl>
      <h4>Duplicate <span className="muted">· topology</span></h4>
      <p className="muted hint">names follow -2, -3…; every copied segment takes the next free /24 in its prefix and its hosts addresses in it; roles, personas, counts and member overrides copy; taps never do</p>
      <div className="row cv-inline">
        <select aria-label="Duplicate subtree onto device" value={target} disabled={ro} onChange={(e) => setTarget(e.target.value)}>
          <option value="">parked (attach later)</option>
          {devices.map((d) => (
            <option key={d.name} value={d.name}>{d.name}</option>
          ))}
        </select>
        <button type="button" disabled={ro} data-testid="duplicate-subtree" onClick={() => dup(target || null)}>
          Duplicate
        </button>
      </div>
      <h4>Actions</h4>
      <button type="button" className="danger" disabled={ro} data-testid="remove-subtree" title="remove the devices, hosts, links and segments of this subtree" onClick={() => { if (p.apply(`remove ${p.root}'s subtree`, (d, m) => { let files = d.files; let model = m; for (const h of [...t.hosts, ...t.devices]) { files = removeNode({ ...d, files }, model, h); model = readDoc({ ...d, files }); } for (const s of t.segments) { files = removeSegment({ ...d, files }, model, s); model = readDoc({ ...d, files }); } return files; }) === null) p.onSelect?.({ kind: "none" }); }}>
        Remove subtree
      </button>
    </section>
  );
}

/** One member of a `×N` group (§5): its instance id, its role override, its
 *  own traffic binding, Detach as a `×1` group, Move to another group. */
function MemberPanel(p: SidePanelProps & { node: string; index: number }) {
  const n = p.model.te.nodes.find((x) => x.name === p.node);
  const [into, setInto] = useState("");
  if (!n) return <p className="muted">node {p.node} is not in the document</p>;
  if (p.index < 1 || p.index > n.countValue) return <p className="muted">{p.node} has no member {p.index}</p>;
  const ro = p.locked;
  const id = instanceId(n.name, p.index);
  const m = n.members.find((x) => x.index === p.index) ?? null;
  const bound = p.model.tfc?.assignments.find((a) => a.by === "node" && a.target === id) ?? null;
  const inherited = effectiveAssignment(p.model.tfc, n.name, m?.role ?? n.role);
  const targets = mergeTargets(p.model, n.name);
  return (
    <section className="cv-side-section" data-testid="member-panel">
      <h3>
        Member <span className="mono">{id}</span> <span className="muted">of {n.name} ×{n.countValue}</span>
      </h3>
      <h4>Traffic <span className="muted">· tfc, live</span></h4>
      <Field label="Role" hint={n.role ? `inherits ${n.role} unless overridden (a te members[] entry: a traffic change)` : "a members[] role override"}>
        <Text ariaLabel="Member role" value={m?.role ?? ""} placeholder={n.role ?? "inherits"} disabled={ro} onCommit={(v) => p.apply(`role of ${id}`, (d) => setMemberRole(d, n.name, p.index, v.trim() || null))} />
      </Field>
      <Field label={bound ? "Bound to this instance" : inherited ? `Inherited: ${inherited.persona}` : "Not bound"}>
        <select aria-label="Member persona" value={bound?.persona ?? ""} disabled={ro} onChange={(e) => { const v = e.target.value; if (!v) p.apply(`unbind persona of ${id}`, (d, mo) => clearPersona(d, mo, "node", id)); else p.apply(`persona of ${id}`, (d, mo) => setPersona(d, mo, "node", id, { persona: v })); }}>
          <option value="">{inherited && !bound ? `(group's: ${inherited.persona})` : "none"}</option>
          {p.personas.map((ps) => (
            <option key={ps.id} value={ps.id}>{ps.id}</option>
          ))}
          {bound && !p.personas.some((ps) => ps.id === bound.persona) ? <option value={bound.persona}>{bound.persona}</option> : null}
        </select>
      </Field>
      {bound ? (
        <>
          <Field label="Intensity">
            <Text ariaLabel="Member intensity" value={bound.intensity === null ? "" : String(bound.intensity)} placeholder="profile default" disabled={ro} mono onCommit={(v) => p.apply(`intensity of ${id}`, (d, mo) => setPersona(d, mo, "node", id, { intensity: v.trim() === "" ? null : Number(v) }))} />
          </Field>
          <Field label="Schedule">
            <Text ariaLabel="Member schedule" value={bound.schedule ?? ""} placeholder="24x7, business-hours, …" disabled={ro} onCommit={(v) => p.apply(`schedule of ${id}`, (d, mo) => setPersona(d, mo, "node", id, { schedule: v.trim() || null }))} />
          </Field>
        </>
      ) : null}
      <h4>Actions <span className="muted">· topology</span></h4>
      <div className="row cv-inline">
        <button type="button" disabled={ro || n.countValue < 2} data-testid="detach-member" title="this member becomes its own ×1 group (a re-plan)" onClick={() => { const r = { name: "" }; if (p.apply(`detach ${id} as its own group`, (d, mo) => { const out = detachMember(d, mo, n.name, p.index); r.name = out.newName; return out.files; }) === null) p.onSelect?.({ kind: "node", names: [r.name] }); }}>
          Detach as its own group
        </button>
      </div>
      {targets.length ? (
        <div className="row cv-inline">
          <select aria-label="Move member to group" value={into} disabled={ro} onChange={(e) => setInto(e.target.value)}>
            <option value="">move to…</option>
            {targets.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
          <button type="button" disabled={ro || !into} data-testid="move-member" title="this member joins that group as its last instance, keeping its role and persona (a re-plan)" onClick={() => { if (p.apply(`move ${id} to ${into}`, (d, mo) => moveMember(d, mo, n.name, p.index, into)) === null) p.onSelect?.({ kind: "node", names: [into] }); }}>
            Move
          </button>
        </div>
      ) : (
        <p className="muted hint">no other group shares this one's image and segments; a member moves only between such groups</p>
      )}
      <button type="button" className="cv-link-row" onClick={() => p.onSelect?.({ kind: "node", names: [n.name] })}>
        ← the group {n.name}
      </button>
    </section>
  );
}

function NodePanel(p: SidePanelProps & { name: string }) {
  const n = p.model.te.nodes.find((x) => x.name === p.name);
  const [dupTarget, setDupTarget] = useState("");
  const [legTarget, setLegTarget] = useState("");
  const [mergeTarget, setMergeTarget] = useState("");
  if (!n) return <p className="muted">node {p.name} is not in the document</p>;
  const mergeInto = mergeTargets(p.model, n.name);
  const ro = p.locked;
  const assignment = effectiveAssignment(p.model.tfc, n.name, n.role);
  const bound = p.model.tfc?.assignments.find((a) => a.by === "node" && a.target === n.name) ?? null;
  const baseRole = p.baseline?.te.nodes.find((x) => x.name === n.name)?.role;
  const roleChanged = p.baseline !== null && baseRole !== undefined && baseRole !== n.role && n.role;
  const otherSegments = p.model.te.segments.filter((s) => !n.nets.some((l) => l.segment === s.name));
  const isSwitch = n.kind === "switch" || (n.role !== null && SWITCH_ROLES.has(n.role));
  const observed = isSwitch && p.observedSegments ? p.observedSegments(n.name).filter((seg) => p.model.te.segments.some((s) => s.name === seg)) : [];
  const links = p.model.te.links.filter((l) => l.a.device === n.name || l.b.device === n.name);
  const tapsHere = (p.model.tap?.bindings ?? []).filter((b) => b.scope === "port" && b.targets.includes(n.name));
  const title = n.kind === "router" ? "Router" : n.kind === "switch" ? "Switch" : "Node group";
  return (
    <section className="cv-side-section" data-testid="node-panel" data-tier-blocks="identity,traffic,taps,members,provenance">
      <h3>
        {title} <span className="mono">{n.name}</span>
      </h3>
      <h4>Identity &amp; placement <span className="muted">· topology</span></h4>
      <Field label="Name">
        <Text ariaLabel="Node name" value={n.name} disabled={ro} mono onCommit={(v) => { if (p.apply(`rename ${n.name} → ${v}`, (d, m) => renameNode(d, m, n.name, v)) === null) p.onRenamed?.("node", n.name, v); }} />
      </Field>
      <Field label="Kind" hint="a switch is a device wired by links; without an image it deploys as nothing but its segments' vnets">
        <select aria-label="Kind" value={n.kind} disabled={ro} onChange={(e) => p.apply(`kind of ${n.name}`, (d) => setNodeField(d, n.name, "kind", e.target.value === "host" ? null : e.target.value))}>
          <option value="host">host</option>
          <option value="router">router</option>
          <option value="switch">switch</option>
        </select>
      </Field>
      <Field label="Role" hint="a role change that keeps the image applies live; with a new image it is a re-plan">
        <Text ariaLabel="Role" value={n.role ?? ""} disabled={ro} onCommit={(v) => p.apply(`role of ${n.name}`, (d) => setNodeField(d, n.name, "role", v.trim() || null))} />
      </Field>
      {p.canPromote && roleChanged ? (
        <PromoteButton testId="promote-role" block={p.promoteBlock({ node: n.name })} disabled={ro} onClick={() => p.onPromoteRole(n.name, n.role!)}>
          Promote to assertion: {n.name} is a {n.role}
        </PromoteButton>
      ) : null}
      <Field label="Image" hint={n.kind === "switch" ? "empty: the switch deploys as its segments' vnets only; with an image, a VM with one L2 port per carried segment" : "an image the catalog names, or any ref; the API validates it against the manifests"}>
        <Text ariaLabel="Image" value={n.image} placeholder={n.kind === "switch" ? "none (vnets only)" : undefined} disabled={ro} mono list="cv-images" onCommit={(v) => p.apply(`image of ${n.name}`, (d) => setNodeField(d, n.name, "image", v.trim() || null))} />
        <datalist id="cv-images">
          {p.images.map((i) => (
            <option key={i} value={i} />
          ))}
        </datalist>
      </Field>
      <Field label="Count" hint="instances fanned out at deploy; a number or a ${PARAM:default}">
        <Text ariaLabel="Count" value={String(n.count)} disabled={ro} mono onCommit={(v) => p.apply(`count of ${n.name}`, (d) => setNodeField(d, n.name, "count", /^\d+$/.test(v.trim()) ? Number(v.trim()) : v.trim() || null))} />
      </Field>
      <h5>Attachments</h5>
      {isSwitch ? <p className="muted hint" data-testid="switch-note">a switch's attachments are its own ports (address-less unless a management address is given); the segments its links carry are below</p> : null}
      {n.nets.length === 0 ? <p className="muted">on no segment</p> : null}
      <ul className="cv-legs" data-testid="attachments">
        {n.nets.map((l) => (
          <li key={l.segment} data-testid="attachment" data-segment={l.segment}>
            <span className="mono">{l.segment}</span>{" "}
            <Text ariaLabel={`Address on ${l.segment}`} value={l.ip ?? ""} placeholder="allocated at deploy" disabled={ro} mono onCommit={(v) => p.apply(`address of ${n.name} on ${l.segment}`, (d, m) => setAttachmentAddress(d, m, n.name, l.segment, v.trim() || null))} />
            <button type="button" disabled={ro} onClick={() => p.apply(`detach ${n.name} from ${l.segment}`, (d, m) => detach(d, m, n.name, l.segment))}>
              Detach
            </button>
          </li>
        ))}
      </ul>
      {otherSegments.length ? (
        <div className="row cv-inline">
          <select aria-label="Attach to segment" value={legTarget} disabled={ro} onChange={(e) => setLegTarget(e.target.value)}>
            <option value="">add a leg on…</option>
            {otherSegments.map((s) => (
              <option key={s.name} value={s.name}>
                {s.name} ({s.cidr})
              </option>
            ))}
          </select>
          <button
            type="button"
            disabled={ro || !legTarget}
            onClick={() => {
              if (p.apply(`attach ${n.name} to ${legTarget}`, (d, m) => attach(d, m, n.name, legTarget)) === null) setLegTarget("");
            }}
          >
            Attach
          </button>
        </div>
      ) : null}
      {observed.length ? (
        <div className="cv-observed" data-testid="observed-segments">
          <p className="muted">
            observed on {observed.length} segment{observed.length === 1 ? "" : "s"} it is not attached to: <span className="mono">{observed.join(", ")}</span>
          </p>
          <button type="button" disabled={ro} onClick={() => p.apply(`attach ${n.name} to ${observed.join(", ")} (observed)`, (d, m) => observed.reduce((files, seg) => attach({ ...d, files }, { ...m, te: { ...m.te, nodes: m.te.nodes.map((x) => (x.name === n.name ? { ...x, nets: [...x.nets, ...observed.slice(0, observed.indexOf(seg)).map((s2) => ({ segment: s2, ip: null }))] } : x)) } }, n.name, seg), d.files))} title="write these segments into the node's nets[] (a topology change: a re-plan)" data-testid="attach-observed">
            Attach…
          </button>
        </div>
      ) : null}
      <h5>Links and ports</h5>
      {links.length === 0 ? <p className="muted">no link names this device{n.kind === "switch" ? ": wire it with the Attach tool from another device, or add a link in the Link panel" : ""}</p> : null}
      <ul className="cv-legs" data-testid="device-links">
        {links.map((l) => {
          const mine = l.a.device === n.name ? l.a : l.b;
          const other = l.a.device === n.name ? l.b : l.a;
          const segs = [...(l.native ? [l.native] : []), ...l.vlans.filter((v) => v !== l.native)];
          return (
            <li key={l.index} data-testid="device-link" data-index={l.index}>
              <button type="button" className="cv-link-row" onClick={() => p.onSelect?.({ kind: "link", index: l.index })} title="open the link's row">
                <span className="mono">{mine.port}</span> — <span className="mono">{other.device}#{other.port}</span> <span className="muted">· {l.mode}{segs.length ? ` · ${segs.join(", ")}` : ""}{l.state !== "up" ? ` · ${l.state}` : ""}</span>
              </button>
            </li>
          );
        })}
      </ul>
      <h4>Traffic <span className="muted">· tfc, live</span></h4>
      <Field label={bound ? "Bound to this group" : assignment ? `Inherited from role ${assignment.target}` : "Not bound"} hint="written to the traffic profile; applies live on a deployed twin">
        <select
          aria-label="Persona"
          value={bound?.persona ?? ""}
          disabled={ro}
          onChange={(e) => {
            const v = e.target.value;
            if (!v) p.apply(`unbind persona of ${n.name}`, (d, m) => clearPersona(d, m, "node", n.name));
            else p.apply(`persona of ${n.name}`, (d, m) => setPersona(d, m, "node", n.name, { persona: v }));
          }}
        >
          <option value="">{assignment && !bound ? `(role's: ${assignment.persona})` : "none"}</option>
          {p.personas.map((ps) => (
            <option key={ps.id} value={ps.id}>
              {ps.id}
            </option>
          ))}
          {bound && !p.personas.some((ps) => ps.id === bound.persona) ? <option value={bound.persona}>{bound.persona}</option> : null}
        </select>
      </Field>
      {bound ? (
        <>
          <Field label="Intensity">
            <Text ariaLabel="Intensity" value={bound.intensity === null ? "" : String(bound.intensity)} placeholder="profile default" disabled={ro} mono onCommit={(v) => p.apply(`intensity of ${n.name}`, (d, m) => setPersona(d, m, "node", n.name, { intensity: v.trim() === "" ? null : Number(v) }))} />
          </Field>
          <Field label="Schedule">
            <Text ariaLabel="Schedule" value={bound.schedule ?? ""} placeholder="24x7, business-hours, …" disabled={ro} onCommit={(v) => p.apply(`schedule of ${n.name}`, (d, m) => setPersona(d, m, "node", n.name, { schedule: v.trim() || null }))} />
          </Field>
        </>
      ) : null}
      {tapsHere.length || n.kind !== "host" ? (
        <>
          <h4>Taps <span className="muted">· tap, live</span></h4>
          {tapsHere.length === 0 ? <p className="muted">no port tap on this device; a segment's panel adds a tap on the segment</p> : null}
          <ul className="cv-legs" data-testid="node-taps">
            {tapsHere.map((b) => (
              <li key={b.index} data-testid="tap-row" data-index={b.index}>
                <button type="button" className="cv-link-row" onClick={() => p.onSelect?.({ kind: "tap", index: b.index })}>
                  port {b.segment ?? "?"} → {b.sensors.join(", ") || "no sensor"}
                </button>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      <Members {...p} name={n.name} />
      <Provenance
        {...p}
        subject={{ node: n.name }}
        source={n.source}
        confidence={n.confidence}
        extra={
          p.onProvenance && n.kind !== "router" ? (
            <button type="button" disabled={ro || (p.provenanceBlock?.({ node: n.name }) ?? null) !== null} title={n.kind === "switch" ? "record in the imprint's assertions that this host is a switch" : "record in the imprint's assertions that this host is a switch, and make it one"} onClick={() => p.onProvenance?.({ kind: "assert-device", node: n.name, deviceKind: "switch" })} data-testid="prov-assert-switch">
              Assert: a switch
            </button>
          ) : null
        }
      />
      <h4>Actions</h4>
      <div className="row cv-inline">
        <select aria-label="Duplicate onto segment" value={dupTarget} disabled={ro} onChange={(e) => setDupTarget(e.target.value)}>
          <option value="">duplicate onto…</option>
          {p.model.te.segments.map((s) => (
            <option key={s.name} value={s.name}>
              {s.name} ({s.cidr})
            </option>
          ))}
        </select>
        <button type="button" disabled={ro || !dupTarget} onClick={() => p.apply(`duplicate ${n.name} onto ${dupTarget}`, (d, m) => duplicateOnto(d, m, [n.name], dupTarget).files)}>
          Duplicate
        </button>
      </div>
      {mergeInto.length ? (
        <div className="row cv-inline">
          <select aria-label="Merge into group" value={mergeTarget} disabled={ro} onChange={(e) => setMergeTarget(e.target.value)}>
            <option value="">merge into…</option>
            {mergeInto.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
          <button type="button" disabled={ro || !mergeTarget} data-testid="merge" title="every instance of this group joins that one; a differing role becomes a member override, bindings follow; a re-plan" onClick={() => { if (p.apply(`merge ${n.name} into ${mergeTarget}`, (d, m) => mergeGroups(d, m, n.name, mergeTarget)) === null) p.onSelect?.({ kind: "node", names: [mergeTarget] }); }}>
            Merge
          </button>
        </div>
      ) : null}
      <button type="button" className="danger" disabled={ro} onClick={() => p.apply(`remove ${n.name}`, (d, m) => removeNode(d, m, n.name))}>
        Remove node group
      </button>
    </section>
  );
}

function NodesPanel(p: SidePanelProps & { names: string[] }) {
  const [target, setTarget] = useState("");
  return (
    <section className="cv-side-section" data-testid="nodes-panel">
      <h3>{p.names.length} node groups</h3>
      <p className="mono">{p.names.join(", ")}</p>
      <div className="row cv-inline">
        <select aria-label="Duplicate selection onto segment" value={target} disabled={p.locked} onChange={(e) => setTarget(e.target.value)}>
          <option value="">duplicate the section onto…</option>
          {p.model.te.segments.map((s) => (
            <option key={s.name} value={s.name}>
              {s.name} ({s.cidr})
            </option>
          ))}
        </select>
        <button type="button" disabled={p.locked || !target} onClick={() => p.apply(`duplicate ${p.names.length} groups onto ${target}`, (d, m) => duplicateOnto(d, m, p.names, target).files)}>
          Duplicate
        </button>
      </div>
      <button type="button" className="danger" disabled={p.locked} onClick={() => p.apply(`remove ${p.names.length} groups`, (d, m) => p.names.reduce((files, name) => removeNode({ ...d, files }, m, name), d.files))}>
        Remove all
      </button>
    </section>
  );
}

function SegmentPanel(p: SidePanelProps & { name: string }) {
  const s = p.model.te.segments.find((x) => x.name === p.name);
  if (!s) return <p className="muted">segment {p.name} is not in the document</p>;
  const ro = p.locked;
  const on = p.model.te.nodes.filter((n) => n.nets.some((l) => l.segment === s.name));
  return (
    <section className="cv-side-section" data-testid="segment-panel">
      <h3>
        Segment <span className="mono">{s.name}</span>
      </h3>
      <Field label="Name">
        <Text ariaLabel="Segment name" value={s.name} disabled={ro} mono onCommit={(v) => { if (p.apply(`rename segment ${s.name} → ${v}`, (d) => renameSegment(d, s.name, v)) === null) p.onRenamed?.("segment", s.name, v); }} />
      </Field>
      <Field label="CIDR" hint="the deployed network, as its network address">
        <Text ariaLabel="CIDR" value={s.cidr} disabled={ro} mono onCommit={(v) => p.apply(`cidr of ${s.name}`, (d) => setSegmentField(d, s.name, "cidr", v.trim()))} />
      </Field>
      <Field label="Zone type">
        <select aria-label="Zone type" value={s.zoneType ?? ""} disabled={ro} onChange={(e) => p.apply(`zone of ${s.name}`, (d) => setSegmentField(d, s.name, "zone_type", e.target.value || null))}>
          {ZONE_TYPES.map((z) => (
            <option key={z} value={z}>
              {z || "(none)"}
            </option>
          ))}
          {s.zoneType && !ZONE_TYPES.includes(s.zoneType) ? <option value={s.zoneType}>{s.zoneType}</option> : null}
        </select>
      </Field>
      <h4>On this segment</h4>
      {on.length === 0 ? <p className="muted">nothing attached</p> : <p className="mono">{on.map((n) => `${n.name}${n.countValue > 1 ? ` ×${n.countValue}` : ""}`).join(", ")}</p>}
      <SegmentTaps {...p} segment={s.name} />
      <Provenance {...p} subject={{ segment: s.name }} source={s.source} confidence={s.confidence} />
      <h4>Actions</h4>
      <button type="button" className="danger" disabled={ro} onClick={() => p.apply(`remove segment ${s.name}`, (d, m) => removeSegment(d, m, s.name))}>
        Remove segment{on.length ? ` (detaches ${on.length})` : ""}
      </button>
    </section>
  );
}

function SegmentsPanel(p: SidePanelProps & { names: string[] }) {
  const [a, b] = p.names;
  const [from, setFrom] = useState(b ?? "");
  if (!a || !b) return null;
  const to = from === a ? b : a;
  return (
    <section className="cv-side-section" data-testid="segments-panel">
      <h3>{p.names.length} segments</h3>
      <p className="mono">{p.names.join(", ")}</p>
      {p.names.length === 2 ? (
        <>
          <Field label="Merge" hint="every leg on the merged segment moves to the other; the twin config changes (a re-plan)">
            <select aria-label="Segment to merge away" value={from} onChange={(e) => setFrom(e.target.value)} disabled={p.locked}>
              <option value={a}>{a} into {b}</option>
              <option value={b}>{b} into {a}</option>
            </select>
          </Field>
          <button type="button" disabled={p.locked} onClick={() => p.apply(`merge ${from} into ${to}`, (d, m) => mergeSegments(d, m, from, to))} data-testid="merge-segments">
            Merge {from} into {to}
          </button>
          {p.canPromote ? (
            <PromoteButton testId="promote-merge" block={p.promoteBlock({ from, to })} disabled={p.locked} onClick={() => p.onPromoteMerge(from, to)}>
              Promote to assertion: {from} and {to} are one segment
            </PromoteButton>
          ) : null}
        </>
      ) : null}
    </section>
  );
}

function LegPanel(p: SidePanelProps & { node: string; segment: string }) {
  const n = p.model.te.nodes.find((x) => x.name === p.node);
  const leg = n?.nets.find((l) => l.segment === p.segment);
  if (!n || !leg) return <p className="muted">no attachment of {p.node} on {p.segment}</p>;
  const seg = p.model.te.segments.find((s) => s.name === leg.segment);
  return (
    <section className="cv-side-section" data-testid="leg-panel">
      <h3>
        Attachment <span className="mono">{n.name} → {leg.segment}</span>
      </h3>
      <dl className="facts">
        <dt>Segment</dt>
        <dd className="mono">{seg ? `${seg.name} (${seg.cidr})` : leg.segment}</dd>
      </dl>
      <Field label="Address" hint="empty: the planner allocates one at deploy">
        <Text ariaLabel="Attachment address" value={leg.ip ?? ""} placeholder="allocated at deploy" disabled={p.locked} mono onCommit={(v) => p.apply(`address of ${n.name} on ${leg.segment}`, (d, m) => setAttachmentAddress(d, m, n.name, leg.segment, v.trim() || null))} />
      </Field>
      <button type="button" className="danger" disabled={p.locked} onClick={() => p.apply(`detach ${n.name} from ${leg.segment}`, (d, m) => detach(d, m, n.name, leg.segment))}>
        Detach
      </button>
    </section>
  );
}

/** The taps on a segment (§8): one row per binding that names it, the
 *  sensors editable, Remove, and Add tap -- a `taps` change, live. */
function SegmentTaps(p: SidePanelProps & { segment: string }) {
  const [sensors, setSensors] = useState("");
  const ro = p.locked;
  const taps = tapsOn(p.model.tap, p.segment);
  const known = [...new Set((p.model.tap?.bindings ?? []).flatMap((b) => b.sensors))];
  return (
    <>
      <h4>Taps <span className="muted">· tap, live</span></h4>
      {taps.length === 0 ? <p className="muted">no tap on this segment</p> : null}
      <ul className="cv-legs" data-testid="segment-taps">
        {taps.map((b) => (
          <li key={b.index} data-testid="tap-row" data-index={b.index} data-scope={b.scope}>
            <button type="button" className="cv-link-row" onClick={() => p.onSelect?.({ kind: "tap", index: b.index })} title="open the tap's row">
              {b.scope}{b.scope === "boundary" ? ` ${b.targets.join(" ↔ ")}` : ""} → {b.sensors.join(", ") || "no sensor"}
            </button>
            <button type="button" disabled={ro} onClick={() => p.apply(`remove tap ${b.index} on ${p.segment}`, (d, m) => removeTap(d, m, b.index))}>
              Remove
            </button>
          </li>
        ))}
      </ul>
      <div className="row cv-inline">
        <input type="text" aria-label="Tap sensors" placeholder={known[0] ? `sensors, e.g. ${known[0]}` : "sensor names, comma-separated"} value={sensors} disabled={ro} onChange={(e) => setSensors(e.target.value)} list="cv-sensors" />
        <datalist id="cv-sensors">
          {known.map((k) => (
            <option key={k} value={k} />
          ))}
        </datalist>
        <button type="button" disabled={ro} data-testid="add-tap" onClick={() => { if (p.apply(`tap on ${p.segment}`, (d, m) => addTap(d, m, { scope: "segment", target: p.segment, sensors: sensors.split(",").map((x) => x.trim()).filter(Boolean), source: "authored" })) === null) setSensors(""); }}>
          Add tap
        </button>
      </div>
    </>
  );
}

/** A tap binding (§8): where it sits, its sensors (editable), Remove, provenance. */
function TapPanel(p: SidePanelProps & { index: number }) {
  const b = p.model.tap?.bindings[p.index];
  if (!b) return <p className="muted">no tap at index {p.index}</p>;
  const ro = p.locked;
  return (
    <section className="cv-side-section" data-testid="tap-panel">
      <h3>
        Tap <span className="mono">{b.scope} {b.targets.join(" ↔ ")}{b.segment ? ` (${b.segment})` : ""}</span>
      </h3>
      <h4>Taps <span className="muted">· tap, live</span></h4>
      <Field label="Sensors" hint="comma-separated sensor names the tap delivers to (the tap plan's sensors[])">
        <Text ariaLabel="Sensors" value={b.sensors.join(", ")} disabled={ro} mono onCommit={(v) => p.apply(`sensors of tap ${b.index}`, (d, m) => setTapSensorsOf(d, m, b.index, v.split(",").map((x) => x.trim()).filter(Boolean)))} />
      </Field>
      <dl className="facts">
        <dt>Mode</dt>
        <dd>{b.mode ?? "exclusive"}</dd>
      </dl>
      <Provenance {...p} subject={{ tap: b.index }} source={b.source} confidence={null} extra={p.onProvenance && !b.source ? <button type="button" disabled={ro || (p.provenanceBlock?.({ tap: b.index }) ?? null) !== null} onClick={() => p.onProvenance?.({ kind: "assert-tap", index: b.index })} data-testid="prov-assert-tap">Assert this tap</button> : null} />
      <h4>Actions</h4>
      <button type="button" className="danger" disabled={ro} onClick={() => p.apply(`remove tap ${b.index}`, (d, m) => removeTap(d, m, b.index))}>
        Remove tap
      </button>
    </section>
  );
}

/** A `links[]` entry (the device tree, topology-layers §2.3): its two ports,
 *  mode, the segments it carries, its state -- all editable -- and provenance. */
function LinkPanel(p: SidePanelProps & { index: number }) {
  const link = p.model.te.links[p.index];
  if (!link) return <p className="muted">no link at index {p.index}</p>;
  const ro = p.locked;
  const segs = p.model.te.segments.map((s) => s.name);
  const toggle = (seg: string) => {
    const next = link.vlans.includes(seg) ? link.vlans.filter((v) => v !== seg) : [...link.vlans, seg];
    p.apply(`vlans of link ${link.index}`, (d) => setLinkField(d, link.index, "vlans", next));
  };
  return (
    <section className="cv-side-section" data-testid="link-panel">
      <h3>
        Link <span className="mono">{link.a.device}#{link.a.port} — {link.b.device}#{link.b.port}</span>
      </h3>
      <h4>Identity &amp; placement <span className="muted">· topology</span></h4>
      <Field label={`Port on ${link.a.device}`}>
        <Text ariaLabel={`Port on ${link.a.device}`} value={link.a.port} disabled={ro} mono onCommit={(v) => { if (v.trim()) p.apply(`port of link ${link.index}`, (d) => setLinkField(d, link.index, "a.port", v.trim())); }} />
      </Field>
      <Field label={`Port on ${link.b.device}`}>
        <Text ariaLabel={`Port on ${link.b.device}`} value={link.b.port} disabled={ro} mono onCommit={(v) => { if (v.trim()) p.apply(`port of link ${link.index}`, (d) => setLinkField(d, link.index, "b.port", v.trim())); }} />
      </Field>
      <Field label="Mode" hint="a trunk carries its VLANs tagged and the native untagged; an access link carries one segment">
        <select aria-label="Link mode" value={link.mode} disabled={ro} onChange={(e) => p.apply(`mode of link ${link.index}`, (d) => setLinkField(d, link.index, "mode", e.target.value))}>
          <option value="trunk">trunk</option>
          <option value="access">access</option>
        </select>
      </Field>
      {link.mode === "trunk" ? (
        <Field label="Native">
          <select aria-label="Native VLAN" value={link.native ?? ""} disabled={ro} onChange={(e) => p.apply(`native of link ${link.index}`, (d) => setLinkField(d, link.index, "native", e.target.value || null))}>
            <option value="">(none)</option>
            {segs.map((sg) => (
              <option key={sg} value={sg}>{sg}</option>
            ))}
          </select>
        </Field>
      ) : null}
      <Field label="Segments carried" hint="the segments the link's `vlans[]` names">
        <div className="cv-checks-list" data-testid="link-vlans">
          {segs.map((sg) => (
            <label key={sg} className="cv-check">
              <input type="checkbox" checked={link.vlans.includes(sg)} disabled={ro} onChange={() => toggle(sg)} /> <span className="mono">{sg}</span>
            </label>
          ))}
        </div>
      </Field>
      <Field label="State" hint="down or blocked deploys nothing and draws dashed">
        <select aria-label="Link state" value={link.state} disabled={ro} onChange={(e) => p.apply(`state of link ${link.index}`, (d) => setLinkField(d, link.index, "state", e.target.value === "up" ? null : e.target.value))}>
          <option value="up">up</option>
          <option value="down">down</option>
          <option value="blocked">blocked</option>
        </select>
      </Field>
      <Provenance {...p} subject={{ link: link.index }} source={link.source} confidence={link.confidence} extra={p.onProvenance && link.source !== "asserted" ? <button type="button" disabled={ro || (p.provenanceBlock?.({ link: link.index }) ?? null) !== null} onClick={() => p.onProvenance?.({ kind: "assert-link", index: link.index })} data-testid="prov-assert-link">Assert this link</button> : null} />
      <h4>Actions</h4>
      <button type="button" className="danger" disabled={ro} onClick={() => { p.apply(`remove link ${link.index}`, (d) => removeLink(d, link.index)); p.onSelect?.({ kind: "none" }); }}>
        Remove link
      </button>
    </section>
  );
}

function AnnotationPanel(p: SidePanelProps & { id: string }) {
  const a = p.annotations.find((x) => x.id === p.id);
  if (!a) return null;
  return (
    <section className="cv-side-section" data-testid="annotation-panel">
      <h3>Annotation ({a.kind})</h3>
      {a.kind === "text" ? (
        <Field label="Text">
          <textarea aria-label="Annotation text" value={a.text} rows={3} onChange={(e) => p.onAnnotation(a.id, { text: e.target.value })} disabled={a.locked} />
        </Field>
      ) : null}
      <label className="cv-check">
        <input type="checkbox" checked={a.locked} onChange={(e) => p.onAnnotation(a.id, { locked: e.target.checked })} /> locked
      </label>
      <label className="cv-check">
        <input type="checkbox" checked={a.hidden} onChange={(e) => p.onAnnotation(a.id, { hidden: e.target.checked })} /> hidden
      </label>
      <button type="button" className="danger" onClick={() => p.onRemoveAnnotation(a.id)}>
        Remove annotation
      </button>
      <p className="muted">Stored as a positioned SVG snippet in the layout side-car, beside the positions.</p>
    </section>
  );
}

/** A promote action: disabled, with its reason beside it, when the cast cannot key the assertion. */
function PromoteButton(p: { testId: string; block: string | null; disabled: boolean; onClick(): void; children: ReactNode }) {
  return (
    <>
      <button type="button" onClick={p.onClick} disabled={p.disabled || p.block !== null} title={p.block ?? undefined} data-testid={p.testId}>
        {p.children}
      </button>
      {p.block !== null ? (
        <p className="hint" data-testid={`${p.testId}-block`}>
          {p.block}
        </p>
      ) : null}
    </>
  );
}
