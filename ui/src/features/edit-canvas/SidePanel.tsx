/**
 * The inspector's property panels (`SPEC.md`: "a side panel instead of
 * modals", now the floating inspector's Selection tab): the selected node
 * group, segment, attachment or annotation, or the twin's own properties
 * when nothing is selected. `Checks` is the inspector's other tab: the
 * last on-demand Check and -- on a deployed twin -- every pending change with
 * the blast radius the API classified it with (*live · traffic*, *live · taps*
 * or *re-plan*; `docs/design/topology-layers.md` §4). Every
 * field writes through the editor's `apply`, which refuses only a request
 * the command cannot express (the reason shows on the canvas's strip).
 */
import { useEffect, useState, type ReactNode } from "react";

import type { PendingChange, Persona, ValidationFinding } from "../../api/types";
import { errorText } from "../../areas/shell/parts";
import type { Annotation } from "./edit/annotations";
import { checkBadge, checkStatus, findingTarget, targetKey, type CheckRecord } from "./edit/check";
import {
  conduitImplemented,
  removeConduit,
  addSpan,
  addTap,
  attach,
  clearPersona,
  detach,
  duplicateOnto,
  mergeSegments,
  moveTo,
  readDoc,
  removeLink,
  removeNode,
  removeSegment,
  removeTap,
  renameNode,
  retargetTap,
  renameSegment,
  setAttachmentAddress,
  setLinkField,
  setMemberRole,
  setNodeField,
  setPersona,
  setSegmentField,
  setSensorsOnTaps,
  setSpanSourcesOf,
  setTapSensorsOf,
  tapsWithoutSensor,
  type DocModel,
  type WorkingDoc,
} from "./edit/document";
import type { Files } from "./edit/commands";
import type { TeModel } from "./edit/te";
import { effectiveAssignment } from "./edit/tfc";
import { detachMember, instanceId, mergeGroups, mergeTargets, moveMember, splitGroup } from "./edit/members";
import { carriedBy, duplicateSubtree, subtreeCount, subtreeOf } from "./edit/subtree";
import { isBridgeVm } from "./edit/fabric";
import { hostSwitch, insertSwitch } from "./edit/switches";
import { SPAN_DIRS, selectorOf, spanOn, tapsOn, type SpanSource, type TapBinding, type TapSelector } from "./edit/tap";
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
  /** A `conduits[]` entry, by its list index. */
  | { kind: "conduit"; index: number }
  /** One member of a `×N` group (§5). */
  | { kind: "member"; node: string; index: number }
  /** A device and everything below it (§6). */
  | { kind: "subtree"; root: string }
  | { kind: "annotation"; id: string };

/** The selection with what no longer exists in the document left out (2026-11-20, round 2: an undo
 *  took branch-gw and branch-sw away and the Selection tab still listed them). Nothing left is no
 *  selection -- the Twin summary. An annotation is not the document's: kept. Returns `sel` itself
 *  when nothing changed. Pure. */
export function pruneSelection(sel: Selection, model: { te: Pick<TeModel, "nodes" | "segments" | "links" | "conduits">; tap: { bindings: ReadonlyArray<{ index: number }> } | null }): Selection {
  const te = model.te;
  const node = (n: string) => te.nodes.find((x) => x.name === n);
  switch (sel.kind) {
    case "node":
    case "segment": {
      const names = sel.names.filter((n) => (sel.kind === "node" ? Boolean(node(n)) : te.segments.some((s) => s.name === n)));
      if (names.length === sel.names.length) return sel;
      return names.length ? { ...sel, names } : { kind: "none" };
    }
    case "leg":
      return node(sel.node)?.nets.some((l) => l.segment === sel.segment) ? sel : { kind: "none" };
    case "link":
      return te.links.some((l) => l.index === sel.index) ? sel : { kind: "none" };
    case "conduit":
      return te.conduits.some((c) => c.index === sel.index) ? sel : { kind: "none" };
    case "tap":
      return model.tap?.bindings.some((b) => b.index === sel.index) ? sel : { kind: "none" };
    case "member": {
      const n = node(sel.node);
      return n && sel.index <= n.countValue ? sel : { kind: "none" };
    }
    case "subtree":
      return node(sel.root) ? sel : { kind: "none" };
    default:
      return sel;
  }
}

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
   *  on each of `keys` (the observed hosts the operator named), so a re-cast proposes the split.
   *  Absent on a twin not cast. */
  onPromoteSplit?: ((node: string, keys: string[], name: string) => void) | undefined;
  /** Why the members of this group cannot be keyed (no roster, or no observed members), or null. */
  splitBlock?: ((node: string) => string | null) | undefined;
  /** The group's observed entity keys from the cast roster, sorted (nothing ties one to an instance). */
  memberKeys?: ((node: string) => string[]) | undefined;
  annotations: Annotation[];
  onAnnotation(id: string, patch: Partial<Annotation>): void;
  onRemoveAnnotation(id: string): void;
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
  /** The deploy targets' declared sensors (adapter.conf `EXTERNAL_SENSORS`, `GET /api/targets`),
   *  offered as a tap's sensors beside the ones the tap plan already names. */
  sensors?: string[] | undefined;
  /** The target `sensors` belong to (the deployed twin's, else the default one): the tap inspector names it. Null
   *  when the targets could not be read. */
  sensorTarget?: string | null | undefined;
  /** Convert every bridge VM to a native switch, as one undoable edit (`native-switches.md` §12 S5):
   *  answers the engine's notes and the native-switch rule problems on the result, or rejects with the
   *  reason the fabric cannot convert. Absent where the canvas cannot reach the API. */
  onConvertFabric?: (() => Promise<string | null>) | undefined;
  /** The last conversion's bridges, notes and native-switch rule problems: shown on each converted switch. */
  fabricResult?: FabricResult | null | undefined;
}

export interface FabricResult {
  bridges: string[];
  notes: string[];
  problems: { rule_id: string; severity: string; message: string }[];
}

/** The sensors a tap field offers: the targets' declared ones, then the plan's own. */
export function knownSensors(model: DocModel, declared: readonly string[] = []): string[] {
  return [...new Set([...declared, ...(model.tap?.bindings ?? []).flatMap((b) => b.sensors)])];
}

function SensorList({ id, sensors }: { id: string; sensors: string[] }) {
  return (
    <datalist id={id}>
      {sensors.map((k) => (
        <option key={k} value={k} />
      ))}
    </datalist>
  );
}

const splitSensors = (v: string) => v.split(",").map((x) => x.trim()).filter(Boolean);

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
      {selection.kind === "conduit" ? <ConduitPanel {...p} index={selection.index} /> : null}
      {selection.kind === "tap" ? <TapPanel {...p} index={selection.index} /> : null}
      {selection.kind === "member" ? <MemberPanel {...p} node={selection.node} index={selection.index} /> : null}
      {selection.kind === "subtree" ? <SubtreePanel {...p} root={selection.root} /> : null}
      {selection.kind === "annotation" ? <AnnotationPanel {...p} id={selection.id} /> : null}
      {selection.kind === "none" ? <TwinPanel {...p} /> : null}
    </aside>
  );
}

/** The inspector's Checks tab: the last Check (on demand, `edit/check.ts`) with its findings --
 *  a click on one selects and zooms to the element it names -- the taps that need a sensor, and
 *  on a deployed twin the pending changes. `onDiscard(path)` puts one file back to the
 *  baseline's text (a pending change is one file's delta), when the page has the baseline and
 *  the canvas is not locked. */
export function Checks(p: Pick<SidePanelProps, "model" | "changes"> & { check: CheckRecord | null; files: Files | null; checking: boolean; onCheck(): void; onFinding?: ((f: ValidationFinding) => void) | undefined; onDiscard?: ((path: string) => void) | undefined; provenance?: { filter: string | null; onFilter(source: string | null): void; fallback?: "discovered" | "authored" } | undefined; taps?: TapSensorProps | undefined }) {
  return (
    <div className="cv-side cv-checks" data-testid="checks">
      <Validation model={p.model} check={p.check} files={p.files} checking={p.checking} onCheck={p.onCheck} onFinding={p.onFinding} />
      {p.taps ? <TapsNeedSensor model={p.model} {...p.taps} /> : null}
      {p.changes ? <Changes changes={p.changes} onDiscard={p.onDiscard} /> : null}
      {p.provenance ? <ProvenanceSummary model={p.model} filter={p.provenance.filter} onFilter={p.provenance.onFilter} fallback={p.provenance.fallback ?? "authored"} /> : null}
    </div>
  );
}

/** The Checks row for taps that name no sensor (a cast's caveat: "every
 *  binding omits sensors"): the count, a filter that leaves only those taps
 *  bright on the canvas, and one sensor set on all of them at once. */
export interface TapSensorProps {
  /** The canvas filter in force (`no-sensor` is this row's own). */
  filter: string | null;
  onFilter(filter: string | null): void;
  sensors: string[];
  locked: boolean;
  apply: SidePanelProps["apply"];
}

export const NO_SENSOR_FILTER = "no-sensor";

function TapsNeedSensor(p: TapSensorProps & { model: DocModel }) {
  const [sensor, setSensor] = useState("");
  const missing = tapsWithoutSensor(p.model);
  if (missing.length === 0) return null;
  const on = p.filter === NO_SENSOR_FILTER;
  const set = () => {
    const chosen = splitSensors(sensor);
    if (!chosen.length) return;
    if (p.apply(`sensors on ${missing.length} tap(s)`, (d, m) => setSensorsOnTaps(d, m, tapsWithoutSensor(m), chosen)) === null) {
      setSensor("");
      if (on) p.onFilter(null);
    }
  };
  return (
    <section className="cv-side-section" data-testid="taps-need-sensor">
      <h3>Taps</h3>
      <button type="button" className={`cv-prov-row${on ? " pressed" : ""}`} aria-pressed={on} data-testid="taps-need-sensor-row" onClick={() => p.onFilter(on ? null : NO_SENSOR_FILTER)}>
        <span className="cv-prov-badge cv-prov-unconfirmed" aria-hidden="true">!</span> {missing.length} tap{missing.length === 1 ? "" : "s"} need a sensor
      </button>
      <p className="muted hint">the tap plan names no sensor for these; the tap tier refuses them until one is set</p>
      <div className="row cv-inline">
        <input type="text" aria-label="Sensor for every tap without one" placeholder={p.sensors[0] ? `e.g. ${p.sensors[0]}` : "sensor name"} value={sensor} disabled={p.locked} onChange={(e) => setSensor(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") set(); }} list="cv-sensors-all" />
        <SensorList id="cv-sensors-all" sensors={p.sensors} />
        <button type="button" disabled={p.locked || !splitSensors(sensor).length} data-testid="set-sensor-all" onClick={set}>
          Set on all {missing.length}
        </button>
      </div>
    </section>
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

/** What the Checks tab wears on its strip: the Check's state word (`unchecked`, `valid`,
 *  `invalid (N)`, `changed since check`), the gate's answer at a glance. */
export function checksBadge(p: { check: CheckRecord | null; files: Files | null; checking?: boolean }): string {
  return p.checking ? "checking…" : checkBadge(p.check, p.files);
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

/** The last Check: its state word, the Check button (Ctrl+Enter), and every finding with its rule
 *  and place; a finding that names an element is a button that selects and zooms to it. */
function Validation({ model, check, files, checking, onCheck, onFinding }: { model: DocModel; check: CheckRecord | null; files: Files | null; checking: boolean; onCheck(): void; onFinding?: ((f: ValidationFinding) => void) | undefined }) {
  const status = checkStatus(check, files);
  const word = checking ? "checking…" : checkBadge(check, files);
  const findings = check?.result?.findings ?? [];
  return (
    <section className="cv-side-section" data-testid="validation">
      <h3>Check</h3>
      <div className="row cv-inline">
        <p className={status === "valid" ? "ok" : status === "invalid" ? "error" : "muted"} role="status" data-testid="validation-word" data-status={status}>
          {word}
        </p>
        <button type="button" onClick={onCheck} disabled={checking || !files} data-testid="check-tab-run" title="validate the files on screen (Ctrl+Enter)">
          {checking ? "Checking…" : "Check"}
        </button>
      </div>
      {status === "unchecked" ? <p className="muted hint">nothing validates while you edit: Check judges the files on screen, and Save needs a passing Check on unchanged files</p> : null}
      {status === "changed" ? <p className="muted hint">the files changed since the last Check; its findings below are for the files it judged</p> : null}
      {check?.error ? <p className="error">the API could not validate: {errorText(check.error)}</p> : null}
      {check?.result ? (
        <p className="muted" data-testid="validation-api">
          {check.result.disposition.toLowerCase()} · {check.result.errors} error{check.result.errors === 1 ? "" : "s"}, {check.result.warnings} warning{check.result.warnings === 1 ? "" : "s"}
        </p>
      ) : null}
      <ul className="cv-findings" data-testid="findings">
        {findings.map((f, i) => {
          const target = findingTarget(f, model);
          const body = (
            <>
              <span className="mono">{f.rule_id}</span> <span className={`badge${f.severity === "error" ? " bad" : ""}`}>{f.severity}</span> {f.message}
              {f.location ? <span className="muted"> · {f.location}</span> : null}
            </>
          );
          return (
            <li key={i} data-testid="finding" data-rule={f.rule_id} data-severity={f.severity} data-target={target ? targetKey(target) : undefined}>
              {target && onFinding ? (
                <button type="button" className="cv-link-row cv-finding-row" onClick={() => onFinding(f)} title="select it on the canvas and zoom to it">
                  {body}
                </button>
              ) : (
                body
              )}
            </li>
          );
        })}
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
  // Split and assert, step two: which observed hosts the split instances are (null: not asked yet).
  const [assertKeys, setAssertKeys] = useState<string[] | null>(null);
  const [keyFilter, setKeyFilter] = useState("");
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
  // `keys`: the observed hosts to assert the split on (Split and assert), or none (a plain Split).
  const split = (keys: string[] | null) => {
    const name = splitName.trim();
    const which = [...picking];
    if (p.apply(`split ${which.length} of ${n.name} off as ${name}`, (d, m) => splitGroup(d, m, n.name, which, name)) !== null) return;
    if (keys) p.onPromoteSplit?.(n.name, keys, name);
    setPicked([]);
    setSplitName("");
    setAssertKeys(null);
    setKeyFilter("");
  };
  const splitReady = !ro && picking.length > 0 && picking.length < n.countValue && splitName.trim() !== "";
  const splitBlock = p.onPromoteSplit ? (p.splitBlock?.(n.name) ?? null) : null;
  // A cast's instances are interchangeable: nothing records which observed host an instance
  // stands for (the roster's members are only the group's sorted entity keys). So the operator
  // names the hosts the assertion is about, as many as the instances split off (2026-11-08;
  // it used to take the i-th sorted key for instance i, a guess).
  const keys = p.memberKeys?.(n.name) ?? [];
  const shownKeys = keyFilter.trim() ? keys.filter((k) => k.includes(keyFilter.trim())) : keys;
  const toggleKey = (k: string) => setAssertKeys((s) => (s === null ? [k] : s.includes(k) ? s.filter((x) => x !== k) : [...s, k]));
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
        <button type="button" disabled={!splitReady} data-testid="split" title="the picked members become a new group with this name (the original keeps the rest); a topology change" onClick={() => split(null)}>
          Split
        </button>
        {p.onPromoteSplit ? (
          <button type="button" disabled={!splitReady || splitBlock !== null || assertKeys !== null} data-testid="split-assert" title={splitBlock ?? "split, and record it in the imprint's assertions keyed by the observed hosts you name next, so a re-cast proposes the split instead of the whole group (re-casts the twin)"} onClick={() => setAssertKeys([])}>
            Split and assert…
          </button>
        ) : null}
      </div>
      {assertKeys !== null && p.onPromoteSplit ? (
        <div className="cv-members-bulk" data-testid="split-assert-keys">
          <p className="hint">
            Which observed hosts are the {picking.length} split off? The cast does not record which host an instance stands for; the assertion is about the hosts you pick ({assertKeys.length} of {picking.length}).
          </p>
          {keys.length > 12 ? <input type="text" aria-label="Filter observed hosts" placeholder="filter, e.g. 10.20.193." value={keyFilter} onChange={(e) => setKeyFilter(e.target.value)} /> : null}
          <ul className="cv-key-list">
            {shownKeys.map((k) => (
              <li key={k}>
                <label className="cv-split-pick mono">
                  <input type="checkbox" aria-label={`Observed host ${k}`} checked={assertKeys.includes(k)} onChange={() => toggleKey(k)} disabled={ro || (!assertKeys.includes(k) && assertKeys.length >= picking.length)} /> {k}
                </label>
              </li>
            ))}
          </ul>
          <div className="row cv-inline">
            <button type="button" className="primary" data-testid="split-assert-go" disabled={!splitReady || assertKeys.length !== picking.length} onClick={() => split([...assertKeys].sort())}>
              Split and assert {picking.length} host{picking.length === 1 ? "" : "s"}
            </button>
            <button type="button" onClick={() => { setAssertKeys(null); setKeyFilter(""); }}>
              Cancel
            </button>
          </div>
        </div>
      ) : null}
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
      {(() => {
        // §7.1: a host on a switch of several segments picks its VLAN here (a drop defaulted it).
        const hs = hostSwitch(p.model.te, n.name);
        if (!hs || hs.choices.length < 2) return null;
        return (
          <Field label={`VLAN on ${hs.switch}`} hint="the segment this host's first leg is on, among those its switch carries; re-addressed there">
            <select aria-label="VLAN" data-testid="vlan-picker" value={hs.segment} disabled={ro} onChange={(e) => p.apply(`move ${n.name} to ${e.target.value}`, (d, m) => moveTo(d, m, n.name, e.target.value))}>
              {hs.choices.map((seg) => (
                <option key={seg} value={seg}>
                  {seg}
                </option>
              ))}
            </select>
          </Field>
        );
      })()}
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
      {n.kind === "switch" ? (
        <>
          <h5>Segments (VLANs)</h5>
          {/* §7.1 rule 1: a segment a switch carries has no box of its own; this is where it is selected. */}
          {carriedBy(p.model.te, n.name).length === 0 ? <p className="muted">carries no segment</p> : null}
          <ul className="cv-legs" data-testid="carried-segments">
            {carriedBy(p.model.te, n.name).map((seg) => {
              const sg = p.model.te.segments.find((x) => x.name === seg);
              const hostsOn = p.model.te.nodes.filter((x) => x.kind !== "router" && x.kind !== "switch" && x.nets.some((l) => l.segment === seg)).reduce((k, x) => k + x.countValue, 0);
              return (
                <li key={seg} data-testid="carried-segment" data-segment={seg}>
                  <button type="button" className="cv-link-row" onClick={() => p.onSelect?.({ kind: "segment", names: [seg] })} title="open the segment's panel">
                    <span className="mono">{seg}</span> <span className="muted">· {sg?.cidr ?? "unknown"} · {hostsOn} host{hostsOn === 1 ? "" : "s"}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </>
      ) : null}
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
      {n.kind === "switch" && !n.image ? <SpanPorts {...p} sw={n.name} /> : null}
      {isBridgeVm(n) && p.onConvertFabric ? <ConvertFabric {...p} name={n.name} /> : null}
      {!isBridgeVm(n) && p.fabricResult?.bridges.includes(n.name) ? <FabricNotes result={p.fabricResult} /> : null}
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
  // §7.1: the real switch that carries it, else it is drawn as an implied switch (presentation only).
  const carrier = p.model.te.nodes.find((n) => n.kind === "switch" && carriedBy(p.model.te, n.name).includes(s.name)) ?? null;
  const groups = on.filter((n) => n.kind !== "router" && n.kind !== "switch" && n.nets[0]?.segment === s.name && !(n.countValue === 1 && typeof n.count === "number"));
  return (
    <section className="cv-side-section" data-testid="segment-panel" data-implied={carrier ? undefined : "true"}>
      <h3>
        Segment <span className="mono">{s.name}</span>
      </h3>
      {carrier ? (
        <p className="muted hint" data-testid="segment-carrier">
          carried by switch{" "}
          <button type="button" className="cv-link-row mono" onClick={() => p.onSelect?.({ kind: "node", names: [carrier.name] })}>{carrier.name}</button>
          : its hosts hang off it, the segment a VLAN on their access edges
        </p>
      ) : (
        <div className="cv-implied-note" data-testid="implied-note">
          <p className="muted hint">drawn as an implied switch: no switch in the config carries {s.name}. It is not written to the config.</p>
          <button
            type="button"
            disabled={ro}
            data-testid="insert-switch"
            title={`a kind: switch node (imageless, authored), a trunk from the router to it carrying ${s.name}, and an access link to each single host on it`}
            onClick={() => {
              const made = { name: "" };
              if (p.apply(`insert switch on ${s.name}`, (d, m) => { const out = insertSwitch(d, m, s.name); made.name = out.name; return out.files; }) === null) p.onSelect?.({ kind: "node", names: [made.name] });
            }}
          >
            Insert switch
          </button>
          {groups.length ? <p className="muted hint" data-testid="insert-switch-groups">{groups.map((g) => g.name).join(", ")}: a ×N group cannot be named by a link; it stays on its <span className="mono">net:</span> and draws under the switch</p> : null}
        </div>
      )}
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
  const known = knownSensors(p.model, p.sensors);
  // The tap tier refuses a binding with no sensor: Add tap waits for one (2026-11-09).
  const chosen = splitSensors(sensors);
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
        <SensorList id="cv-sensors" sensors={known} />
        <button type="button" disabled={ro || chosen.length === 0} title={chosen.length ? undefined : "name a sensor first: the tap tier refuses a tap with none"} data-testid="add-tap" onClick={() => { if (p.apply(`tap on ${p.segment}`, (d, m) => addTap(d, m, { scope: "segment", target: p.segment, sensors: chosen, source: "authored" })) === null) setSensors(""); }}>
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
      {b.scope === "span" ? <SpanPorts {...p} sw={b.targets[0] ?? ""} /> : <TapWhere {...p} b={b} />}
      <TapSensors {...p} b={b} ro={ro} />
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

/** A switch's ports for a SPAN session (docs/design/native-switches.md §6): one
 *  row per port its links name (plus any automatic `<host>.<leg>` port already
 *  in the session, or typed in), each with a direction -- off, rx (what the
 *  switch receives on it), tx (what it sends), both -- and, on a trunk, the
 *  VLANs to narrow to. Writes the switch's one `span` binding through the tap
 *  plan: the first direction set adds it (once a sensor is named), the last one
 *  cleared removes it. A `taps` change: live. */
/** The D11 offer on a bridge VM: convert the WHOLE fabric (every bridge VM) to native switches. One
 *  bridge alone cannot convert -- its native neighbours would trunk to the remaining bridge VMs, which
 *  NSW-060 refuses -- so the button converts them all; one undo restores them. */
function ConvertFabric(p: SidePanelProps & { name: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bridges = p.model.te.nodes.filter(isBridgeVm).length;
  const run = () => {
    if (!p.onConvertFabric) return;
    setBusy(true);
    setError(null);
    p.onConvertFabric().then(
      (blocked) => {
        setBusy(false);
        setError(blocked);
      },
      (e: unknown) => {
        setBusy(false);
        setError(errorText(e));
      },
    );
  };
  return (
    <div data-testid="convert-fabric">
      <h4>Native switches <span className="muted">· topology</span></h4>
      <p className="muted hint">
        {p.name} is a bridge VM. Converting replaces all {bridges} bridge VMs with native switches and links, read from the recipe's port tables; the
        tap plans' bindings on the removed segments move to SPAN sessions. One undo restores the VMs; nothing changes until you save.
      </p>
      <button type="button" disabled={p.locked || busy} onClick={run} data-testid="convert-fabric-run">
        {busy ? "Converting…" : `Convert the fabric (${bridges} bridge VMs)`}
      </button>
      {error ? <p className="cv-error" data-testid="convert-fabric-error">{error}</p> : null}
    </div>
  );
}

/** What the engine said about the conversion this switch came from. */
function FabricNotes({ result }: { result: FabricResult }) {
  return (
    <div data-testid="convert-fabric-notes">
      <h4>Converted from a bridge VM <span className="muted">· topology</span></h4>
      <ul className="cv-legs">
        {result.problems.map((x, i) => (
          <li key={`p${i}`} className={x.severity === "error" ? "cv-error" : "muted"}>{x.rule_id}: {x.message}</li>
        ))}
        {result.notes.map((x, i) => (
          <li key={`n${i}`} className="muted">{x}</li>
        ))}
      </ul>
    </div>
  );
}

function SpanPorts(p: SidePanelProps & { sw: string }) {
  const ro = p.locked;
  const binding = spanOn(p.model.tap, p.sw);
  const sources = binding?.sources ?? [];
  const [sensors, setSensors] = useState("");
  const [extra, setExtra] = useState("");
  const known = knownSensors(p.model, p.sensors);
  const chosen = sensors.split(/[,\s]+/).map((x) => x.trim()).filter(Boolean);
  const linkPorts = p.model.te.links.flatMap((l) => {
    const ends = [[l.a, l.b], [l.b, l.a]] as const;
    return ends.filter(([e]) => e.device === p.sw).map(([e, far]) => ({ port: e.port, far: `${far.device}#${far.port}`, mode: l.mode, carried: [...new Set([...(l.native ? [l.native] : []), ...l.vlans])], state: l.state }));
  });
  const rows = [...linkPorts, ...sources.filter((x) => !linkPorts.some((l) => l.port === x.port)).map((x) => ({ port: x.port, far: "automatic port", mode: "access" as const, carried: [] as string[], state: "up" as const }))];
  const write = (next: SpanSource[], label: string) => {
    if (binding) return p.apply(`${label} (span on ${p.sw})`, (d, m) => setSpanSourcesOf(d, m, binding.index, next));
    if (!next.length) return null;
    return p.apply(`span on ${p.sw}`, (d, m) => addSpan(d, m, { switch: p.sw, sources: next, sensors: chosen, source: "authored" }));
  };
  const setDir = (port: string, dir: string) => {
    const rest = sources.filter((x) => x.port !== port);
    const cur = sources.find((x) => x.port === port);
    const next = dir === "off" ? rest : [...rest, { ...(cur ?? { port }), port, dir: dir as SpanSource["dir"] }];
    return write(next.sort((a, b) => a.port.localeCompare(b.port)), `${port} ${dir}`);
  };
  const toggleVlan = (port: string, carried: string[], vlan: string) => {
    const cur = sources.find((x) => x.port === port);
    if (!cur) return;
    const have = new Set(cur.vlans ?? carried);
    if (have.has(vlan)) have.delete(vlan);
    else have.add(vlan);
    const vlans = carried.filter((v) => have.has(v));
    const entry: SpanSource = vlans.length === carried.length || vlans.length === 0 ? { port, dir: cur.dir } : { port, dir: cur.dir, vlans };
    write(sources.map((x) => (x.port === port ? entry : x)), `${port} VLANs`);
  };
  const needSensor = !binding && chosen.length === 0;
  return (
    <div data-testid="span-ports">
      <h4>SPAN <span className="muted">· tap, live</span></h4>
      <p className="muted hint">a native switch's mirror session: set a port's direction to add it (rx = what the switch receives on it)</p>
      {!binding ? (
        <Field label="Sensors" hint="the tap tier refuses a session with no sensor">
          <input type="text" aria-label="SPAN sensors" placeholder={known[0] ? `e.g. ${known[0]}` : "sensor names, comma-separated"} value={sensors} disabled={ro} onChange={(e) => setSensors(e.target.value)} list="cv-sensors" />
        </Field>
      ) : null}
      <table className="cv-span-ports">
        <tbody>
          {rows.map((r) => {
            const cur = sources.find((x) => x.port === r.port);
            return (
              <tr key={r.port} data-testid="span-port" data-port={r.port}>
                <td className="mono">{r.port}</td>
                <td className="muted">{r.far}{r.state !== "up" ? ` · ${r.state}` : ""}</td>
                <td>
                  <select aria-label={`SPAN direction ${r.port}`} value={cur?.dir ?? "off"} disabled={ro || (needSensor && !cur)} onChange={(e) => setDir(r.port, e.target.value)}>
                    <option value="off">off</option>
                    {SPAN_DIRS.map((d) => (
                      <option key={d} value={d}>{d}</option>
                    ))}
                  </select>
                </td>
                <td>
                  {cur && r.mode === "trunk" && r.carried.length > 1
                    ? r.carried.map((v) => (
                        <label key={v} className="cv-inline">
                          <input type="checkbox" aria-label={`SPAN VLAN ${v} on ${r.port}`} checked={(cur.vlans ?? r.carried).includes(v)} disabled={ro} onChange={() => toggleVlan(r.port, r.carried, v)} /> {v}
                        </label>
                      ))
                    : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <span className="row cv-inline">
        <input type="text" aria-label="SPAN automatic port" placeholder="automatic port, e.g. corp-workstation01.0" value={extra} disabled={ro} onChange={(e) => setExtra(e.target.value)} />
        <button type="button" disabled={ro || !extra.trim() || (needSensor) || rows.some((r) => r.port === extra.trim())} onClick={() => { if (setDir(extra.trim(), "both") === null) setExtra(""); }}>
          Add port
        </button>
      </span>
    </div>
  );
}

/** The tap's Sensors (docs/design/targets-and-lab.md §2.3): a multi-select of the target's lab sensors (the
 *  deployed twin's target, else the default one), no free text. A sensor the binding names that the lab does not
 *  have (a stale config, or a twin brought from another target) is shown struck through with a Remap select; each
 *  change is one `taps` edit (one undo step). */
function TapSensors(p: SidePanelProps & { b: TapBinding; ro: boolean }) {
  const { b, ro } = p;
  const offered = p.sensors ?? [];
  const stale = b.sensors.filter((s) => !offered.includes(s));
  const set = (next: string[], label: string) => p.apply(`${label} (tap ${b.index})`, (d, m) => setTapSensorsOf(d, m, b.index, next.filter((s, i, all) => all.indexOf(s) === i)));
  const toggle = (s: string, on: boolean) => set(on ? [...b.sensors, s] : b.sensors.filter((x) => x !== s), `${on ? "add" : "drop"} sensor ${s}`);
  const remap = (from: string, to: string) => set(b.sensors.map((x) => (x === from ? to : x)), `remap sensor ${from} → ${to}`);
  return (
    <fieldset className="cv-sensors" data-testid="tap-sensors" aria-label="Sensors">
      <legend>
        Sensors{" "}
        <span className="muted">{p.sensorTarget ? <>· the lab of <span className="mono">{p.sensorTarget}</span></> : "· the targets could not be read"}</span>
      </legend>
      {offered.length === 0 && p.sensorTarget ? <p className="muted">The lab of {p.sensorTarget} declares no sensor.</p> : null}
      {offered.map((s) => (
        <label key={s} className="cv-sensor-opt">
          <input type="checkbox" checked={b.sensors.includes(s)} disabled={ro} onChange={(e) => toggle(s, e.target.checked)} aria-label={`Sensor ${s}`} /> <span className="mono">{s}</span>
        </label>
      ))}
      {stale.map((s) => (
        <div key={s} className="cv-sensor-stale" data-testid="stale-sensor" data-sensor={s}>
          <s className="mono" title={p.sensorTarget ? `not in the lab of ${p.sensorTarget}` : "not offered by any target read"}>{s}</s>{" "}
          <span className="muted">not in the lab{p.sensorTarget ? ` of ${p.sensorTarget}` : ""}</span>{" "}
          <select aria-label={`Remap ${s}`} value="" disabled={ro || offered.length === 0} onChange={(e) => e.target.value && remap(s, e.target.value)}>
            <option value="">remap to…</option>
            {offered.filter((o) => !b.sensors.includes(o)).map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>{" "}
          <button type="button" className="quiet" disabled={ro} onClick={() => toggle(s, false)} aria-label={`Drop ${s}`}>
            drop
          </button>
        </div>
      ))}
      {b.sensors.length === 0 ? <p className="warn-line">No sensor named: the tap tier refuses this tap.</p> : null}
    </fieldset>
  );
}

/** Where a tap sits, editable (§8, 2026-11-09): the Scope (a segment, the boundary between two
 *  segments, or a port: one device's leg) and its Target, the same choices a drag of the chip
 *  offers. Each change is one move: one `taps` change, one undo step. */
function TapWhere(p: SidePanelProps & { b: TapBinding }) {
  const { b } = p;
  const ro = p.locked;
  const segs = p.model.te.segments.map((x) => x.name);
  const vms = p.model.te.nodes.filter((n) => n.image && n.kind !== "switch" && n.nets.length > 0);
  const cur = selectorOf(b);
  const label = (sel: TapSelector) => (sel.scope === "boundary" ? `boundary ${sel.target.join(" ↔ ")}` : sel.scope === "port" ? `port ${sel.target} (${sel.segment})` : `segment ${sel.target}`);
  const move = (sel: TapSelector) => p.apply(`move tap ${b.index} to ${label(sel)}`, (d, m) => retargetTap(d, m, b.index, sel));
  const known = b.targets.filter((t) => segs.includes(t));
  const firstOther = (not: string) => segs.find((x) => x !== not) ?? not;
  const portOn = (device: string, seg?: string): TapSelector | null => {
    const n = vms.find((x) => x.name === device);
    const leg = n ? (n.nets.find((x) => x.segment === seg) ?? n.nets[0]) : undefined;
    return n && leg ? { scope: "port", target: n.name, segment: leg.segment } : null;
  };
  const toScope = (scope: string) => {
    const seed = known[0] ?? b.segment ?? segs[0] ?? "";
    if (scope === "segment") move({ scope: "segment", target: seed });
    else if (scope === "boundary") move({ scope: "boundary", target: [seed, known[1] && known[1] !== seed ? known[1] : firstOther(seed)] });
    else if (scope === "port") {
      const host = vms.find((n) => n.nets.some((x) => x.segment === seed)) ?? vms[0];
      const sel = host ? portOn(host.name, seed) : null;
      if (sel) move(sel);
    }
  };
  if (!cur) return <p className="muted" data-testid="tap-where">a {b.scope} tap: re-create it to move it (the inspector moves segment, boundary and port taps)</p>;
  return (
    <div data-testid="tap-where">
      <Field label="Scope" hint="a segment, the boundary between two segments, or one device's port (a link's end)">
        <select aria-label="Tap scope" value={cur.scope} disabled={ro} onChange={(e) => toScope(e.target.value)}>
          <option value="segment">segment</option>
          <option value="boundary" disabled={segs.length < 2}>boundary</option>
          <option value="port" disabled={vms.length === 0}>port</option>
        </select>
      </Field>
      {cur.scope === "segment" ? (
        <Field label="Target">
          <select aria-label="Tap segment" value={cur.target} disabled={ro} onChange={(e) => move({ scope: "segment", target: e.target.value })}>
            {segs.map((x) => (
              <option key={x} value={x}>{x}</option>
            ))}
          </select>
        </Field>
      ) : cur.scope === "boundary" ? (
        <Field label="Target" hint="the two segments the boundary joins">
          <span className="row cv-inline">
            {[0, 1].map((i) => (
              <select key={i} aria-label={`Boundary segment ${i + 1}`} value={cur.target[i]} disabled={ro} onChange={(e) => move({ scope: "boundary", target: i === 0 ? [e.target.value, cur.target[1]] : [cur.target[0], e.target.value] })}>
                {segs.map((x) => (
                  <option key={x} value={x} disabled={x === cur.target[1 - i]}>{x}</option>
                ))}
              </select>
            ))}
          </span>
        </Field>
      ) : (
        <Field label="Target" hint="the device whose port is mirrored, and the leg (segment) that port is on">
          <span className="row cv-inline">
            <select aria-label="Tap device" value={cur.target} disabled={ro} onChange={(e) => { const sel = portOn(e.target.value, cur.segment); if (sel) move(sel); }}>
              {vms.map((n) => (
                <option key={n.name} value={n.name}>{n.name}</option>
              ))}
            </select>
            <select aria-label="Tap port segment" value={cur.segment} disabled={ro} onChange={(e) => move({ scope: "port", target: cur.target, segment: e.target.value })}>
              {(vms.find((n) => n.name === cur.target)?.nets ?? []).map((l) => (
                <option key={l.segment} value={l.segment}>{l.segment}</option>
              ))}
            </select>
          </span>
        </Field>
      )}
    </div>
  );
}

/** A `links[]` entry (the device tree, topology-layers §2.3): its two ports,
 *  mode, the segments it carries, its state -- all editable -- and provenance. */
/** A declared conduit (`conduits[]`): its two segments, whether a node implements it (a leg on BOTH --
 *  the engine refuses it otherwise), the fix (a router on one segment gets a leg on the other), Remove,
 *  and its provenance. */
function ConduitPanel(p: SidePanelProps & { index: number }) {
  const c = p.model.te.conduits.find((x) => x.index === p.index);
  const [router, setRouter] = useState("");
  if (!c) return <p className="muted">no conduit at index {p.index}</p>;
  const ro = p.locked;
  const ok = conduitImplemented(p.model.te, c.from, c.to);
  // Routers on exactly one of the two segments, and the leg each would need.
  const fixes = p.model.te.nodes
    .filter((n) => n.kind === "router")
    .flatMap((n) => {
      const onFrom = n.nets.some((l) => l.segment === c.from);
      const onTo = n.nets.some((l) => l.segment === c.to);
      return onFrom !== onTo ? [{ router: n.name, missing: onFrom ? c.to : c.from }] : [];
    });
  const chosen = fixes.find((f) => f.router === router) ?? fixes[0] ?? null;
  return (
    <section className="cv-side-section" data-testid="conduit-panel">
      <h3>
        Conduit <span className="mono">{c.from} ↔ {c.to}</span>
      </h3>
      <p className={ok ? "muted" : "warn-line"} data-testid="conduit-state">
        {ok ? "implemented: a node on both segments, or a chain of routers between them" : "no node has a leg on both segments and no chain of routers joins them: the engine refuses this conduit until one does"}
      </p>
      {!ok ? (
        fixes.length ? (
          <div className="row cv-inline" data-testid="conduit-fix">
            <select aria-label="Router to fix the conduit" value={chosen?.router ?? ""} disabled={ro} onChange={(e) => setRouter(e.target.value)}>
              {fixes.map((f) => (
                <option key={f.router} value={f.router}>
                  {f.router} (+ leg on {f.missing})
                </option>
              ))}
            </select>
            <button type="button" disabled={ro || !chosen} onClick={() => chosen && p.apply(`attach ${chosen.router} to ${chosen.missing}`, (d, m) => attach(d, m, chosen.router, chosen.missing))}>
              Add a router leg to fix
            </button>
          </div>
        ) : (
          <p className="muted hint">no router is on either segment: attach a router to both, or remove the conduit</p>
        )
      ) : null}
      <h4>Provenance</h4>
      <dl className="facts">
        <dt>Source</dt>
        <dd data-testid="conduit-source">{c.source ?? "authored"}{typeof c.confidence === "number" ? ` (${c.confidence.toFixed(2)})` : ""}</dd>
        {c.vantage ? (
          <>
            <dt>Vantage</dt>
            <dd className="mono">{c.vantage}</dd>
          </>
        ) : null}
      </dl>
      <div className="row-actions">
        <button type="button" className="danger" disabled={ro} data-testid="conduit-remove" onClick={() => { if (p.apply(`remove conduit ${c.from} ↔ ${c.to}`, (d, m) => removeConduit(d, m, c.index)) === null) p.onSelect?.({ kind: "none" }); }}>
          Remove
        </button>
      </div>
    </section>
  );
}

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
