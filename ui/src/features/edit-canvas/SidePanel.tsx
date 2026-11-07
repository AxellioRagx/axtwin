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
  attach,
  clearPersona,
  detach,
  duplicateOnto,
  mergeSegments,
  removeNode,
  removeSegment,
  renameNode,
  renameSegment,
  setAttachmentAddress,
  setNodeField,
  setPersona,
  setSegmentField,
  type DocModel,
  type WorkingDoc,
} from "./edit/document";
import type { Files } from "./edit/commands";
import { effectiveAssignment } from "./edit/tfc";
import { SWITCH_ROLES } from "./model/roles";

export type Selection =
  | { kind: "none" }
  | { kind: "node"; names: string[] }
  | { kind: "segment"; names: string[] }
  | { kind: "leg"; node: string; segment: string }
  /** A `links[]` entry, by its list index (the device tree, topology-layers §2.3). */
  | { kind: "link"; index: number }
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
}

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
      {selection.kind === "annotation" ? <AnnotationPanel {...p} id={selection.id} /> : null}
      {selection.kind === "none" ? <TwinPanel {...p} /> : null}
    </aside>
  );
}

/** The inspector's Checks tab. `onDiscard(path)` puts one file back to the
 *  baseline's text (a pending change is one file's delta), when the page has
 *  the baseline and the canvas is not locked. */
export function Checks(p: Pick<SidePanelProps, "model" | "serverReason" | "validation" | "validating" | "changes"> & { onDiscard?: ((path: string) => void) | undefined }) {
  return (
    <div className="cv-side cv-checks" data-testid="checks">
      <Validation findings={p.model.findings} serverReason={p.serverReason} validation={p.validation} validating={p.validating} />
      {p.changes ? <Changes changes={p.changes} onDiscard={p.onDiscard} /> : null}
    </div>
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
      {validation?.result && !validating ? (
        <p className="muted" data-testid="validation-api">
          API: {validation.result.disposition.toLowerCase()} · {validation.result.errors} error{validation.result.errors === 1 ? "" : "s"}, {validation.result.warnings} warning{validation.result.warnings === 1 ? "" : "s"}
        </p>
      ) : null}
      <ul className="cv-findings" data-testid="findings">
        {local.map((f, i) => (
          <li key={`l${i}`} data-testid="finding" data-rule={f.rule_id}>
            <span className="mono">{f.rule_id}</span> {f.message}
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

function NodePanel(p: SidePanelProps & { name: string }) {
  const n = p.model.te.nodes.find((x) => x.name === p.name);
  const [dupTarget, setDupTarget] = useState("");
  const [legTarget, setLegTarget] = useState("");
  if (!n) return <p className="muted">node {p.name} is not in the document</p>;
  const ro = p.locked;
  const assignment = effectiveAssignment(p.model.tfc, n.name, n.role);
  const bound = p.model.tfc?.assignments.find((a) => a.by === "node" && a.target === n.name) ?? null;
  const baseRole = p.baseline?.te.nodes.find((x) => x.name === n.name)?.role;
  const roleChanged = p.baseline !== null && baseRole !== undefined && baseRole !== n.role && n.role;
  const otherSegments = p.model.te.segments.filter((s) => !n.nets.some((l) => l.segment === s.name));
  const isSwitch = n.role !== null && SWITCH_ROLES.has(n.role);
  const observed = isSwitch && p.observedSegments ? p.observedSegments(n.name).filter((seg) => p.model.te.segments.some((s) => s.name === seg)) : [];
  return (
    <section className="cv-side-section" data-testid="node-panel">
      <h3>
        {n.kind === "router" ? "Router" : "Node group"} <span className="mono">{n.name}</span>
      </h3>
      <Field label="Name">
        <Text ariaLabel="Node name" value={n.name} disabled={ro} mono onCommit={(v) => { if (p.apply(`rename ${n.name} → ${v}`, (d, m) => renameNode(d, m, n.name, v)) === null) p.onRenamed?.("node", n.name, v); }} />
      </Field>
      <Field label="Role" hint="a role change that keeps the image applies live; with a new image it is a re-plan">
        <Text ariaLabel="Role" value={n.role ?? ""} disabled={ro} onCommit={(v) => p.apply(`role of ${n.name}`, (d) => setNodeField(d, n.name, "role", v.trim() || null))} />
      </Field>
      {p.canPromote && roleChanged ? (
        <PromoteButton testId="promote-role" block={p.promoteBlock({ node: n.name })} disabled={ro} onClick={() => p.onPromoteRole(n.name, n.role!)}>
          Promote to assertion: {n.name} is a {n.role}
        </PromoteButton>
      ) : null}
      <Field label="Image" hint="an image the catalog names, or any ref; the API validates it against the manifests">
        <Text ariaLabel="Image" value={n.image} disabled={ro} mono list="cv-images" onCommit={(v) => { if (v.trim()) p.apply(`image of ${n.name}`, (d) => setNodeField(d, n.name, "image", v.trim())); }} />
        <datalist id="cv-images">
          {p.images.map((i) => (
            <option key={i} value={i} />
          ))}
        </datalist>
      </Field>
      <Field label="Count" hint="instances fanned out at deploy; a number or a ${PARAM:default}">
        <Text ariaLabel="Count" value={String(n.count)} disabled={ro} mono onCommit={(v) => p.apply(`count of ${n.name}`, (d) => setNodeField(d, n.name, "count", /^\d+$/.test(v.trim()) ? Number(v.trim()) : v.trim() || null))} />
      </Field>
      <Field label="Kind">
        <select aria-label="Kind" value={n.kind} disabled={ro} onChange={(e) => p.apply(`kind of ${n.name}`, (d) => setNodeField(d, n.name, "kind", e.target.value === "router" ? "router" : null))}>
          <option value="host">host</option>
          <option value="router">router</option>
        </select>
      </Field>
      <h4>Attachments</h4>
      {isSwitch ? <p className="muted hint" data-testid="switch-note">a switch is a member of its segments, not a gateway to them: its attachments are the config's `nets[]`</p> : null}
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
      <h4>Persona</h4>
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

/** A `links[]` entry (the device tree, topology-layers §2.3): its two ports,
 *  mode, the segments it carries, its state and provenance. */
function LinkPanel(p: SidePanelProps & { index: number }) {
  const link = p.model.te.links[p.index];
  if (!link) return <p className="muted">no link at index {p.index}</p>;
  const segs = [...(link.native ? [link.native] : []), ...link.vlans.filter((v) => v !== link.native)];
  return (
    <section className="cv-side-section" data-testid="link-panel">
      <h3>
        Link <span className="mono">{link.a.device}#{link.a.port} — {link.b.device}#{link.b.port}</span>
      </h3>
      <dl className="facts">
        <dt>Mode</dt>
        <dd>{link.mode}{link.native ? <span className="muted"> · native {link.native}</span> : null}</dd>
        <dt>Segments</dt>
        <dd className="mono">{segs.join(", ") || "none"}</dd>
        <dt>State</dt>
        <dd>{link.state}</dd>
        <dt>Provenance</dt>
        <dd>{link.source ?? "authored"}{typeof link.confidence === "number" ? ` (${link.confidence.toFixed(2)})` : ""}</dd>
      </dl>
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
