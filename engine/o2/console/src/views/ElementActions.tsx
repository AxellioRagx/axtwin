/**
 * **Selection drives editing** (`docs/WORK_ORDER_console_canvas.md` §3 S5.1.a,
 * Authority: user; `15` §8's amended table). The selected element -- a level-0
 * tile, a level-1 spine, a segment the edit store adds, the host a chip drills
 * to, or the "add a segment here" a site `+` opens -- is shown in the inspector
 * with **its facts and its actions**, and the S5 `EditPanel`'s context-free
 * forms are gone.
 *
 * **Nothing about the record layer changes.** Every action still builds exactly
 * one `axr-assertions/1` record through `edit/records.ts`'s `buildRecord`, still
 * runs the same `crossRecordConflict` pre-flight, and still hands the result to
 * the same untouched reducer. What moved is HOW a gesture is reached: an
 * "Add a segment" form floating with no subject becomes a `+` on the site the
 * segment will be born in, and "add host" becomes an action on the segment the
 * host will be placed in -- including a segment the file adds, which the map
 * cannot draw yet but S5 could already put hosts and conduits on.
 *
 * **Batches (S5.1.b/c).** Two gestures stage many records at once, through the
 * batch path (`edit/batch.ts`, all or nothing): *Clone this segment…* -- or an
 * Alt-drag of its tile -- asks for a placement and then opens the clone form
 * (`edit/clone.ts` holds its rules), and the site `+` form gains an optional
 * hosts block that generates hosts on the new segment. Neither adds a record
 * kind: every record of a batch still comes out of `buildRecord`.
 */
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";

import type { ConsoleApiError } from "../api/client";
import type { AssertionBlock, AssertionRecord } from "../api/types";
import type { ConsoleGraph, GraphNode, GraphSegment } from "../model/graph";
import type { EditStoreState, GestureTag } from "../edit/store";
import {
  buildRecord,
  canonicalPorts,
  crossRecordConflict,
  PORT_PROTOS,
  RESOURCE_KEYS,
  strictCidr,
  ZONE_TYPES,
  type Gesture,
  type PortEntry,
  type PortProto,
  type ResourceKey,
  type StagedRecord,
  type ZoneType,
} from "../edit/records";
import { buildBatch } from "../edit/batch";
import {
  chooseHosts,
  cloneDefaults,
  planClone,
  sourceConduits,
  type AddressMapping,
  type HostSelection,
} from "../edit/clone";
import { defaultListenFor, rolesWithListenDefaults, ROLES_WITH_DIALED_EMITS } from "../model/roles";
import { TILE_GAP_X, TILE_GAP_Y, TILE_H, TILE_W, type Point } from "../layout/geometry";
import {
  addedSegmentsOf,
  allocateHostAddresses,
  takenAddresses,
  castStandingOf,
  claimMessage,
  claimOn,
  defaultZoneType,
  isRefusal,
  KEEPS_ONLY_WITH_A_HOST,
  nameClaimMessage,
  nameClaimOn,
  nextFreeSlash24,
  observedSegmentInputs,
  segmentClaims,
  segmentLandingNote,
  segmentNameClaims,
  segmentNameFor,
  segmentNamePrefill,
  type AddedSegment,
  type NameClaim,
  type SegmentClaim,
  type ZoneSource,
} from "../edit/allocate";
import { castStatusOf } from "../model/graph";
import { groupIntoSites } from "../layout/sites";
import { formatPercent } from "./mapParts";
import { Field, Row } from "./paneParts";
import type { ExplicitSelection, Selection } from "./selection";

export interface ElementActionsProps {
  /** The graph, or `null` while there is none -- see `graphState`. */
  graph: ConsoleGraph | null;
  /** Which of the three graph-less worlds `graph === null` is (LES-253). */
  graphState: { loading: boolean; error: ConsoleApiError | null; absent: boolean };
  /** What is selected, or `null` -- the network level with nothing picked. */
  selection: Selection | null;
  /**
   * The EXPLICIT in-pane selection, or `null` when the selection is only the
   * element the URL names. Each new one moves keyboard focus into this frame:
   * the gesture that made it lives on the map or in the outline, and without
   * the move a keyboard user would tab through every remaining element of the
   * map before reaching the actions the gesture just opened.
   */
  explicit: ExplicitSelection | null;
  /** Whether `clear` would change anything. It does not when the explicit
   *  selection IS the URL's own subject (a spine clicked on its own level), and
   *  a control that visibly does nothing reads as a broken one. */
  canClear: boolean;
  edits: EditStoreState;
  onStage: (block: AssertionBlock, record: AssertionRecord) => void;
  /** Drop the in-pane selection (the zoom's own subject, if any, comes back). */
  onClear: () => void;
  /** Select a segment in place -- the one a site `+` has just staged (A3). */
  onSelectSegment: (cidr: string) => void;
  /** Stage a BATCH, all or nothing (S5.1.c's generated hosts). */
  onStageBatch?: (records: StagedRecord[], gesture: GestureTag) => void;
  /** A fresh gesture id for a batch -- unique for the store's life. */
  newGestureId?: () => string;
  /** S5.1.b: the clone flow the workspace owns -- absent, cloning is unavailable. */
  clone?: CloneActions;
  /** The added segments that have a position on the network map (a pin, or a
   *  clone's placement this session) -- so an added segment's facts can say
   *  whether the map draws it. */
  placedCidrs?: ReadonlySet<string>;
  /**
   * A HAND-AUTHORED twin (no capture evidence at all): assertion gestures --
   * corrections to what aximprint inferred -- have nothing to correct, so none
   * is offered; the panel says what IS possible instead. `configFile` is the
   * file to edit. Absent / `null` for a bundle with capture evidence.
   */
  handAuthored?: { configFile: string; twinId: string } | null;
}

/**
 * The clone flow (S5.1.b), owned by the workspace because its position must be
 * seen by the map and the inspector alike. The draft is the one for the CURRENT
 * clone selection -- `null` when there is none.
 */
export interface CloneActions {
  /** Open a clone of `source`: selects it and proposes a placement. */
  start: (source: string) => void;
  draft: { proposed: Point; at: Point | null } | null;
  /** Move the proposed placement by a layout delta. */
  nudge: (dx: number, dy: number) => void;
  /** Place the clone at the proposed position. */
  place: () => void;
  /** Choose the placement again. */
  replace: () => void;
  /** The CIDR the clone form holds now (canonical, or `null`) -- the map labels
   *  the placed draft with it and decides by it whether a collapsed group holds
   *  the draft, so the map and the form speak of one network. */
  setCidr: (cidr: string | null) => void;
  /** Stage a planned clone's batch, pin it at `at`, and select it. */
  stage: (records: StagedRecord[], gesture: GestureTag, cidr: string, at: Point) => void;
  /** Where a clone of `cidr` placed at `at` would be HELD -- its group is
   *  collapsed on the map -- or `null` when it would be drawn. */
  heldIn: (cidr: string, at: Point) => { siteKey: string; siteLabel: string } | null;
  /** Lift the per-group budget of a site (`?expand=`). */
  expand: (siteKey: string) => void;
  /** Whether the network map is on screen, so a tap or click can place. */
  mapShown: boolean;
}

/** Why an ADDED segment cannot be cloned, and the path that does exist. A clone
 *  copies an observed segment's placed hosts; a segment this file adds has only
 *  the records staged on it. */
const CLONE_ADDED_UNAVAILABLE =
  "A clone copies an observed segment's placed hosts, and this segment exists " +
  "only as a record in this file. To copy it, add a new segment from its " +
  "site's + and use Add host here on it.";

/** Why cloning is unavailable when the workspace offers no clone flow. */
const CLONE_NO_FLOW =
  "Cloning needs the network map's placement, which this view does not offer.";

/**
 * The prefill's own caveat (review M1), said HERE -- where the default is
 * offered -- and not only in the generated table's header. A `real-service`
 * persona's emits carry no direction, so a port the role DIALS is filed under
 * the role itself and reads exactly like a served one. What is at stake is not
 * cosmetic: a staged `listen` set becomes the recompiled twin config's
 * `nodes[].listen`, `te inventory`'s `hosts[].listen` and the scorecard's rows
 * 4/5, so a sentence calling these "the ports it listens on" would put 3306 on
 * four generated web tiers that serve nothing.
 */
const LISTEN_DIALED_CAVEAT =
  "A real-service persona's emits are filed under its own id, so a port the role " +
  "dials (a web tier's 3306 to the db tier) can appear here; drop what the host " +
  "does not serve.";

type Stage = (g: Gesture) => string | null;
type StageBatch = (gestures: readonly Gesture[], gesture: Omit<GestureTag, "id">) => string | null;

export function ElementActions(props: ElementActionsProps): JSX.Element {
  const { graph, graphState, selection, edits, onStage, onClear, onSelectSegment } = props;
  if (props.handAuthored) return <HandAuthoredActions {...props.handAuthored} />;
  const frame = { canClear: props.canClear, focusOn: props.explicit, onClear };
  const { onStageBatch, newGestureId } = props;

  // The one staging pipeline, shared by every form below -- `buildRecord` then
  // the two cross-record refusals `14` §3 names, checked against what is
  // already staged/saved so the UI refuses now rather than at save time.
  const stage: Stage = (g: Gesture): string | null => {
    const result = buildRecord(g);
    if (!result.ok) return result.error;
    const conflict = crossRecordConflict(edits.entries, result.staged);
    if (conflict) return conflict;
    onStage(result.staged.block, result.staged.record);
    return null;
  };

  // The one BATCH pipeline (S5.1.b/c): the whole batch is checked by
  // `buildBatch` before anything is staged, and refused whole naming the first
  // record that would be refused.
  const stageBatch: StageBatch | null =
    onStageBatch && newGestureId
      ? (gestures, gesture) => {
          const result = buildBatch(gestures, edits.entries);
          if (!result.ok) return result.error;
          onStageBatch(result.staged, { ...gesture, id: newGestureId() });
          return null;
        }
      : null;

  if (graph === null) {
    // B4: with no topology export a host route is still a host -- its three
    // actions need nothing but its key, and a 404 on the export is the
    // ordinary "not applicable" case, not a failure.
    if (graphState.absent && selection?.kind === "host") {
      return (
        <Frame {...frame} title={`Host ${selection.key}`} testid="host">
          <p className="meta" data-testid="host-no-export">
            this bundle has no topology export, so there are no observed facts
            to show — the host&apos;s actions work from its key alone.
          </p>
          <HostActions key={selection.key} hostKey={selection.key} stage={stage} />
        </Frame>
      );
    }
    return <ActionsUnavailable {...graphState} />;
  }

  if (selection === null) {
    return (
      <section className="element-actions" data-testid="element-actions" data-actions-frame="" aria-label="Actions">
        <h3>Actions</h3>
        <p className="meta" data-testid="no-selection">
          Nothing is selected. On the network map, a segment tile&apos;s select
          control — the pencil at its top-right corner — selects it in place;
          opening a segment or a host, from the map, the outline or search,
          selects it too. A new segment starts at a site&apos;s +, on its header
          on the map or on its row in the outline.
        </p>
      </section>
    );
  }

  if (selection.kind === "site-add") {
    return (
      <Frame {...frame} title={siteAddTitle(selection.siteKey)} testid="site-add">
        {/* KEYED BY SITE (LES-259): a CIDR typed for one site is never carried
            into the form of another. */}
        <SiteAdd
          key={selection.siteKey ?? "root"}
          graph={graph}
          edits={edits}
          stage={stage}
          stageBatch={stageBatch}
          siteKey={selection.siteKey}
          onStaged={onSelectSegment}
        />
      </Frame>
    );
  }

  if (selection.kind === "clone") {
    return (
      <Frame {...frame} title={`Clone ${selection.source}`} testid="clone">
        <CloneBody
          graph={graph}
          edits={edits}
          source={selection.source}
          clone={props.clone ?? null}
          newGestureId={newGestureId ?? null}
          onClear={onClear}
        />
      </Frame>
    );
  }

  if (selection.kind === "segment") {
    const observed = graph.segmentByCidr.get(selection.cidr) ?? null;
    const peers = conduitPeers(graph, edits, selection.cidr);
    if (observed !== null) {
      return (
        <Frame {...frame} title={`Segment ${selection.cidr}`} testid="segment">
          <SegmentActions
            key={observed.observedCidr}
            graph={graph}
            segment={observed}
            edits={edits}
            peers={peers}
            stage={stage}
            onClone={props.clone ? () => props.clone!.start(observed.observedCidr) : null}
          />
        </Frame>
      );
    }
    const added = addedSegmentsOf(edits.entries, graph.segments.map((s) => s.observedCidr)).find(
      (a) => a.cidr === selection.cidr,
    );
    if (added) {
      return (
        <Frame {...frame} title={`Segment ${selection.cidr}`} testid="segment">
          <AddedSegmentActions
            key={added.cidr}
            added={added}
            edits={edits}
            peers={peers}
            stage={stage}
            placed={props.placedCidrs?.has(added.cidr) ?? false}
            nodes={graph.nodes}
          />
        </Frame>
      );
    }
    return (
      <Frame {...frame} title={`Segment ${selection.cidr}`} testid="segment">
        <p className="notice" data-testid="selection-missing">
          neither this twin&apos;s observed topology nor this file&apos;s adds hold
          a segment <span className="mono">{selection.cidr}</span>, so there are
          no facts to show and no host or peer to act on.
        </p>
      </Frame>
    );
  }

  const node = graph.nodeById.get(selection.key) ?? null;
  return (
    <Frame {...frame} title={`Host ${selection.key}`} testid="host">
      {node === null ? (
        // NOT an error: an assertion may legitimately name a key no entity
        // carries (that is what a `matched: false` suppression is), and the
        // host actions are still the right ones to offer for it.
        <p className="meta" data-testid="selection-unknown-host">
          no entity with this key is in the observed topology — its actions are
          still available, and the compiler will report whether the record
          matched anything.
        </p>
      ) : (
        <HostFacts node={node} />
      )}
      <HostActions key={selection.key} hostKey={selection.key} stage={stage} />
    </Frame>
  );
}

/**
 * The actions slot when there is no graph to select from -- three worlds, and
 * only the third is a fact about the bundle (LES-253). Every segment and site
 * action hangs off an element of the topology, so while it is loading or after
 * its read failed there is nothing to offer, and saying so is different from
 * showing nothing.
 */
function ActionsUnavailable(props: {
  loading: boolean;
  error: ConsoleApiError | null;
  absent: boolean;
}): JSX.Element {
  const { loading, error, absent } = props;
  // Its own test id, NOT `element-actions`: that one means "the actions of a
  // selection over a loaded graph", and a placeholder answering to it would
  // satisfy a wait for the real frame before the graph has arrived.
  return (
    <section className="element-actions" data-testid="actions-unavailable" data-actions-frame="" aria-label="Actions">
      <h3>Actions</h3>
      {absent ? (
        <p className="meta" data-testid="actions-absent">
          no actions here: this bundle has no topology export (the API answers
          404, &ldquo;not applicable yet&rdquo;), so there is no segment or site to
          select. A host&apos;s route still offers that host&apos;s actions.
        </p>
      ) : loading ? (
        <p className="meta" data-testid="actions-loading">
          loading the topology — the selected element&apos;s actions appear once
          it has loaded.
        </p>
      ) : error ? (
        <p className="notice is-error" data-testid="actions-failed">
          no actions: the topology read FAILED ({error.kind}), and every action
          belongs to an element of it. The map pane shows the reason.
        </p>
      ) : (
        <p className="meta" data-testid="actions-none">
          no topology has been read yet, so there is nothing to select.
        </p>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// frames and facts
// ---------------------------------------------------------------------------
function siteAddTitle(siteKey: string | null): string {
  if (siteKey === null) return "Add a segment";
  const label = siteKey.startsWith("site:") ? siteKey.slice("site:".length) : siteKey;
  return `Add a segment in ${label}`;
}

/**
 * The select gestures whose frame has already taken keyboard focus, by object
 * identity. A gesture makes a NEW selection object, so selecting the same tile
 * again after tabbing away still moves focus -- but this frame UNMOUNTS whenever
 * the graph goes away and mounts again with the SAME object, and a mount-time
 * effect alone would then pull focus off whatever the operator is on for a
 * selection made minutes earlier. The set outlives the mount for that reason; a
 * `WeakSet`, so it holds nothing a cleared selection would otherwise let go of.
 */
const focusedGestures = new WeakSet<ExplicitSelection>();

function Frame(props: {
  title: string;
  testid: string;
  canClear: boolean;
  /** The explicit selection this frame shows, or `null`; a new one takes
   *  keyboard focus (see `ElementActionsProps.explicit`). */
  focusOn: ExplicitSelection | null;
  onClear: () => void;
  children: ReactNode;
}): JSX.Element {
  const { focusOn } = props;
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  // Keyed on the selection OBJECT, so a re-render (a keystroke in a form below)
  // never steals focus; `focusedGestures` makes it once per gesture, not once
  // per mount.
  useEffect(() => {
    if (focusOn === null || focusedGestures.has(focusOn)) return;
    focusedGestures.add(focusOn);
    headingRef.current?.focus();
  }, [focusOn]);
  return (
    <section
      className="element-actions"
      data-testid="element-actions"
      // The runtime marker the shell's Escape and opener rules look for -- a
      // test id is for tests, and must not drive behaviour.
      data-actions-frame=""
      data-selection={props.testid}
      aria-label="Actions"
    >
      <div className="selection-head">
        <h3 ref={headingRef} tabIndex={-1} className="selection-title mono" data-testid="selection-title">
          {props.title}
        </h3>
        {props.canClear && (
          <button type="button" className="link" data-testid="selection-clear" onClick={props.onClear}>
            clear
          </button>
        )}
      </div>
      {props.children}
    </section>
  );
}

/** An observed segment's facts -- the export's, and (A1) where it stands
 *  against the cast: whether the cast declares it -- under its own name, or a
 *  rename this file records (SF1) -- and as what network, with which zone
 *  type. "Declared", never "deployed": a segment the deploy gate removes is
 *  declared in the cast all the same. */
function SegmentFacts(props: {
  graph: ConsoleGraph;
  segment: GraphSegment;
  edits: EditStoreState;
}): JSX.Element {
  const { graph, segment, edits } = props;
  const occ = segment.occupancy === null ? "—" : formatPercent(segment.occupancy);
  const standing = castStandingOf(
    graph.castSegments,
    { name: segment.name, observedCidr: segment.observedCidr, status: castStatusOf(graph, segment.name) },
    edits.entries,
  );
  const record = standing.record;
  const cast = standing.castSegment;
  let castFact: string;
  switch (standing.status) {
    case "carried":
      castFact = "yes";
      break;
    case "renamed":
      castFact = `yes, as ${cast?.name ?? "?"} — renamed by a ${record?.state ?? "?"} add in this file`;
      break;
    case "absent":
      // MF2: never "restores". The compiler keeps an OBSERVED segment only when
      // a host is placed on it; an add alone sets its name and zone type.
      castFact =
        "not under this name — renamed or pruned (the cast joins by name)" +
        (record ? `; a ${record.state} add in this file names it, and ` : "; ") +
        KEEPS_ONLY_WITH_A_HOST.replace(": select it and use Add host here", ": use Add host here");
      break;
    case "unpublished":
      castFact = "not published by this server";
      break;
  }
  // Three different statements, never collapsed into "not declared": the cast
  // declares none, the cast does not declare this segment under this name, or
  // the server did not say.
  const fromCast = (value: string | null | undefined, none: string): string => {
    if (standing.status === "absent") return "— (not in the cast under this name)";
    if (standing.status === "unpublished") return "not published by this server";
    return value ?? none;
  };
  return (
    <table className="facts2" data-testid="segment-facts">
      <tbody>
        <Row label="name" value={segment.name} />
        <Row label="hosts" value={`${segment.deployedCount} would deploy · ${segment.ghostCount} ghost · ${occ}`} />
        <Row
          label="roles"
          value={
            segment.roleHistogram.length === 0
              ? "—"
              : segment.roleHistogram.map((r) => `${r.role} ${r.count}`).join(", ")
          }
        />
        <Row label="declared in the cast" value={castFact} />
        <Row label="declared as" value={fromCast(cast?.cidr, "not declared")} />
        <Row label="zone" value={fromCast(cast?.zone_type, "not declared in the config")} />
      </tbody>
    </table>
  );
}

/** A host's facts -- the level-2 table's own rows (`paneParts.tsx` `Row`), so
 *  the evidence badge and the role confidence read the same in both places. */
function HostFacts({ node }: { node: GraphNode }): JSX.Element {
  return (
    <table className="facts2" data-testid="host-facts">
      <tbody>
        <Row label="address" value={node.address ?? "—"} />
        <Row label="segment" value={node.segment ?? "(unplaced)"} evidence={node.segmentEvidenceClass} />
        <Row
          label="role"
          value={node.role ?? "(no role discovered)"}
          evidence={node.roleEvidenceClass}
          confidence={node.roleConfidence}
        />
        <Row label="occupancy" value={node.occupancy === null ? "—" : formatPercent(node.occupancy)} />
        <Row label="ghost" value={node.isGhost ? node.ghostReasons.join(", ") : "no"} />
      </tbody>
    </table>
  );
}

// ---------------------------------------------------------------------------
// the action list
// ---------------------------------------------------------------------------
/**
 * One action. An accordion rather than four open forms: the complaint S5.1.a
 * answers is that the inspector was a wall of forms with no subject, and four
 * open forms per selection is still a wall.
 *
 * `unavailable` is a REASON, never a bare disabled state: an action that cannot
 * be used and does not say why is indistinguishable from one that is broken.
 * The control stays in the tab order (`aria-disabled`, not `disabled`), and the
 * reason is its accessible description as well as visible text beside it, so a
 * keyboard user reaches it and a screen reader hears why at the control.
 */
function Action(props: {
  id: string;
  label: string;
  openId: string | null;
  setOpen: (id: string | null) => void;
  unavailable?: string | null;
  /** An action that STARTS a flow elsewhere instead of opening a form below it
   *  (S5.1.b's clone, which selects the clone and asks for a placement). It is
   *  then not an accordion entry, so it carries no `aria-expanded`. */
  onActivate?: () => void;
  children?: ReactNode;
}): JSX.Element {
  const whyId = useId();
  const isOpen = props.openId === props.id;
  const blocked = props.unavailable ?? null;
  const starts = props.onActivate !== undefined;
  return (
    <div className="action" data-testid={`action-${props.id}`}>
      <button
        type="button"
        className={`action-button${isOpen ? " is-open" : ""}`}
        data-testid={`action-open-${props.id}`}
        aria-expanded={starts ? undefined : isOpen}
        aria-disabled={blocked !== null ? "true" : undefined}
        aria-describedby={blocked !== null ? whyId : undefined}
        onClick={() => {
          if (blocked !== null) return;
          if (props.onActivate) {
            props.onActivate();
            return;
          }
          props.setOpen(isOpen ? null : props.id);
        }}
      >
        {props.label}
      </button>
      {blocked !== null && (
        <p id={whyId} className="meta action-why" data-testid={`action-why-${props.id}`}>
          {blocked}
        </p>
      )}
      {isOpen && blocked === null && props.children}
    </div>
  );
}

/** A segment a conduit may join to: an observed segment, or one the file adds. */
interface ConduitPeer {
  cidr: string;
  label: string;
}

/** Every segment but `cidr` a conduit from it may reach: the observed ones in
 *  CIDR order, then the ones the edit store adds (A3), labelled as such. */
function conduitPeers(graph: ConsoleGraph, edits: EditStoreState, cidr: string): ConduitPeer[] {
  const observed = graph.segments
    .filter((s) => s.observedCidr !== cidr)
    .map((s) => ({ cidr: s.observedCidr, label: s.observedCidr }));
  const added = addedSegmentsOf(edits.entries, graph.segments.map((s) => s.observedCidr))
    .filter((a) => a.cidr !== cidr)
    .map((a) => ({ cidr: a.cidr, label: `${a.cidr} (${a.state} add)` }));
  return [...observed, ...added];
}

const NO_PEER = "a conduit joins two DIFFERENT segments, and this twin has only this one";

function SegmentActions(props: {
  graph: ConsoleGraph;
  segment: GraphSegment;
  edits: EditStoreState;
  peers: readonly ConduitPeer[];
  stage: Stage;
  /** Start a clone of this segment, or `null` when the view offers no clone flow. */
  onClone: (() => void) | null;
}): JSX.Element {
  const { graph, segment, edits, peers, stage, onClone } = props;
  const [open, setOpen] = useState<string | null>(null);
  return (
    <>
      <SegmentFacts graph={graph} segment={segment} edits={edits} />
      <div className="action-list" data-testid="segment-actions">
        <Action id="add-host" label="Add host here" openId={open} setOpen={setOpen}>
          <AddHostForm stage={stage} segment={segment.observedCidr} edits={edits} nodes={graph.nodes} />
        </Action>
        {/* S5.1.b: a clone is a FLOW, not a form under the button -- it selects
            the clone and asks where on the map it goes. */}
        <Action
          id="clone"
          label="Clone this segment…"
          openId={open}
          setOpen={setOpen}
          unavailable={onClone === null ? CLONE_NO_FLOW : null}
          {...(onClone !== null ? { onActivate: onClone } : {})}
        />
        <Action
          id="add-conduit"
          label="Add conduit to…"
          openId={open}
          setOpen={setOpen}
          unavailable={peers.length === 0 ? NO_PEER : null}
        >
          <AddConduitForm stage={stage} from={segment.observedCidr} peers={peers} edits={edits} />
        </Action>
        <Action
          id="suppress-hosts"
          label="Suppress hosts…"
          openId={open}
          setOpen={setOpen}
          unavailable={
            segment.hosts.length === 0
              ? "this segment holds no placed host to suppress (suppress takes host keys only)"
              : null
          }
        >
          <SuppressHostsForm stage={stage} hosts={segment.hosts} />
        </Action>
      </div>
    </>
  );
}

/**
 * A segment the edit store ADDS (A3): actionable before the map can draw it,
 * which S5's free-standing forms already allowed -- *Add host here* and *Add
 * conduit to…* on a segment that exists only as a record. Its facts are the
 * record's, and what the store has staged on it.
 */
function AddedSegmentActions(props: {
  added: AddedSegment;
  edits: EditStoreState;
  peers: readonly ConduitPeer[];
  stage: Stage;
  /** Whether the network map draws it AT A PLACEMENT of its own -- a pin, or a
   *  clone's drop. Since S5.1.d an unplaced one is drawn too, appended to its
   *  site column, so this decides which sentence is true, not whether it is
   *  drawn at all. */
  placed: boolean;
  /** The observed nodes, for *Add host here*'s next-free-address prefill. */
  nodes: readonly GraphNode[];
}): JSX.Element {
  const { added, edits, peers, stage, placed } = props;
  const [open, setOpen] = useState<string | null>(null);
  const stagedHosts = edits.entries.filter(
    (e) => e.block === "add" && e.record.kind === "host" && e.record.segment === added.cidr,
  ).length;
  return (
    <>
      <table className="facts2" data-testid="added-segment-facts">
        <tbody>
          <Row label="name" value={added.name || "—"} />
          <Row label="record" value={`${added.state} add`} />
          {added.cloneOf !== null && <Row label="cloned from" value={added.cloneOf} />}
          <Row label="zone" value={added.zoneType ?? "not set in the record"} />
          <Row label="staged hosts" value={String(stagedHosts)} />
        </tbody>
      </table>
      {/* Restated TWICE (LES-299). S5.1.b: a PLACED added segment -- a clone's
          drop position is pinned -- IS drawn. S5.1.d: one with NO placement is
          drawn too, appended to its own site column, so the second sentence no
          longer says it is undrawn; what differs now is only whether the
          position is the operator's or the map's. */}
      <p className="meta" data-testid="added-segment-note">
        {placed
          ? `drawn on the network map at its placement, ${added.state === "pending" ? "hatched while it is pending" : "outlined"} — held, and said so, while its group is collapsed there.`
          : `drawn on the network map appended to its site's column, ${added.state === "pending" ? "hatched while it is pending" : "outlined"} — drag it to give it a placement of its own. It is held, and said so, while its group is collapsed there.`}
      </p>
      <div className="action-list" data-testid="segment-actions">
        <Action id="add-host" label="Add host here" openId={open} setOpen={setOpen}>
          <AddHostForm stage={stage} segment={added.cidr} edits={edits} nodes={props.nodes} />
        </Action>
        <Action id="clone" label="Clone this segment…" openId={open} setOpen={setOpen} unavailable={CLONE_ADDED_UNAVAILABLE} />
        <Action
          id="add-conduit"
          label="Add conduit to…"
          openId={open}
          setOpen={setOpen}
          unavailable={peers.length === 0 ? NO_PEER : null}
        >
          <AddConduitForm stage={stage} from={added.cidr} peers={peers} edits={edits} />
        </Action>
        <Action
          id="suppress-hosts"
          label="Suppress hosts…"
          openId={open}
          setOpen={setOpen}
          unavailable={
            "a segment this file adds has no observed host to suppress — to take " +
            "back a host staged here, remove its record from the change list"
          }
        />
      </div>
    </>
  );
}

function HostActions(props: { hostKey: string; stage: Stage }): JSX.Element {
  const { hostKey, stage } = props;
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div className="action-list" data-testid="host-actions">
      <Action id="change-role" label="Change role" openId={open} setOpen={setOpen}>
        <ChangeRoleForm stage={stage} hostKey={hostKey} />
      </Action>
      {/* ONE action, two records (`15` §8's table keeps image and resources
          separate): the operator thinks "how is this machine sized", and the
          two forms stage their own `override` record each. */}
      <Action id="override" label="Override image / resources" openId={open} setOpen={setOpen}>
        <OverrideImageForm stage={stage} hostKey={hostKey} />
        <OverrideResourcesForm stage={stage} hostKey={hostKey} />
      </Action>
      <Action id="suppress" label="Suppress" openId={open} setOpen={setOpen}>
        <SuppressForm stage={stage} hostKey={hostKey} />
      </Action>
    </div>
  );
}

// ---------------------------------------------------------------------------
// the site `+` -- add a segment, born inside a site
// ---------------------------------------------------------------------------
function SiteAdd(props: {
  graph: ConsoleGraph;
  edits: EditStoreState;
  stage: Stage;
  /** The batch pipeline, for the optional hosts block (S5.1.c); `null` when the
   *  view offers none, and then the block is not shown. */
  stageBatch: StageBatch | null;
  siteKey: string | null;
  onStaged: (cidr: string) => void;
}): JSX.Element {
  const { graph, edits, stage, stageBatch, siteKey, onStaged } = props;
  // The prefix is the SITE's own (`Site.prefix`), read from the grouping the
  // map draws -- never a copy stored when the `+` was pressed.
  const site = siteKey === null ? null : (groupIntoSites(graph.segments).find((s) => s.key === siteKey) ?? null);
  const prefix = site?.prefix ?? null;
  const observed = observedSegmentInputs(graph.segments, graph.castSegments, edits.entries);
  const claims = segmentClaims(observed, edits.entries);
  const nameClaims = segmentNameClaims(graph.castSegments, observed, edits.entries);
  const stagedCidrs = edits.entries
    .filter((e) => e.block === "add" && e.record.kind === "segment" && e.record.key)
    .map((e) => e.record.key ?? "");
  // "Free" is free of EVERY claim -- observed segments the cast does not
  // declare included -- so two `+` clicks in a row cannot suggest one twice.
  const suggested = prefix === null ? null : nextFreeSlash24(prefix, claims.map((c) => c.cidr));
  // ZT: the zone type the SITE's cast segments mostly declare, else the whole
  // cast's, else simple (the twin config's own default) -- and the form says
  // which.
  const siteCidrs = new Set((site?.segments ?? []).map((m) => m.observedCidr));
  const zoneDefault = defaultZoneType(
    observed.filter((o) => siteCidrs.has(o.observedCidr)).map((o) => o.standing.castSegment?.zone_type ?? null),
    (graph.castSegments ?? []).map((r) => r.zone_type),
  );
  return (
    <>
      {siteKey !== null && site === null ? (
        <p className="notice" data-testid="site-add-gone">
          this site is no longer in the topology, so there is no prefix to
          allocate in — type the CIDR.
        </p>
      ) : prefix === null ? (
        <p className="meta" data-testid="site-add-noprefix">
          {siteKey === null
            ? "No site is named here, so there is no prefix to allocate in. A segment added from the outline root would sit in the shared misc group, unless an observed segment shares its two-octet prefix — type the CIDR, and the form says where it would group."
            : "This group holds single segments from different /16s, so it has no one prefix to allocate in — type the CIDR, and the form says where it would group."}
        </p>
      ) : suggested === null ? (
        <p className="notice" data-testid="site-add-full">
          no /24 in <span className="mono">{prefix}</span> is free of this
          twin&apos;s observed segments and the adds in this file — type a CIDR in
          another prefix.
        </p>
      ) : (
        <p className="meta" data-testid="site-add-suggestion">
          the next free /24 in <span className="mono">{prefix}</span> is{" "}
          <span className="mono">{suggested}</span>.
        </p>
      )}
      <AddSegmentForm
        stage={stage}
        stageBatch={stageBatch}
        taken={takenAddresses(graph.nodes, edits.entries)}
        suggestion={suggested ?? ""}
        claims={claims}
        nameClaims={nameClaims}
        observedCidrs={graph.segments.map((s) => s.observedCidr)}
        stagedCidrs={stagedCidrs}
        zoneDefault={zoneDefault}
        siteLabel={site && !site.isMisc ? site.label : null}
        onStaged={onStaged}
      />
    </>
  );
}

// ---------------------------------------------------------------------------
// the gesture forms (moved from `views/EditPanel.tsx`, testids unchanged)
// ---------------------------------------------------------------------------
/**
 * The add-segment form under a site `+`.
 *
 * **The CIDR field FOLLOWS the suggestion until the operator types in it.** A
 * `useState(suggestion)` reads its argument once, on mount -- so a changed
 * suggestion never reached an open form. A typed value is held separately,
 * `null` until the field is touched: untouched, the field shows whatever the
 * suggestion is NOW (after a removal, a reload); touched, it shows what was
 * typed, and no later change to the suggestion overwrites it. The name follows
 * the CIDR the same way.
 *
 * **One canonical value decides everything shown about the CIDR** (B1): the
 * live claim note, the name prefill and its collision check, the landing note
 * and the Stage refusal all read `strictCidr(cidr)` -- so a half-typed or
 * host-bits-set value gets no note at all rather than a note about a network
 * the Stage check would never consider.
 *
 * A successful stage SELECTS the new segment (A3), so *Add host here* and *Add
 * conduit to…* on it are one click away.
 */
function AddSegmentForm(props: {
  stage: Stage;
  /** The batch pipeline for the hosts block (S5.1.c), or `null`: no block. */
  stageBatch: StageBatch | null;
  /** Every host address already in play (`takenAddresses`) -- generated hosts
   *  are numbered past them. */
  taken: ReadonlySet<string>;
  /** The prefill: the next free `/24`, or `""` when there is none to offer. */
  suggestion: string;
  claims: readonly SegmentClaim[];
  nameClaims: readonly NameClaim[];
  observedCidrs: readonly string[];
  stagedCidrs: readonly string[];
  /** The zone type the form starts at, and where that came from (ZT). */
  zoneDefault: { zone: ZoneType; source: ZoneSource };
  /** The site the form was opened in, for the zone note -- `null` for misc/root. */
  siteLabel: string | null;
  /** Select a segment in place: the one just staged, or an observed one a
   *  note points at. */
  onStaged: (cidr: string) => void;
}): JSX.Element {
  const { stage, stageBatch, taken, suggestion, claims, nameClaims, observedCidrs, stagedCidrs, zoneDefault, siteLabel, onStaged } = props;
  const [typedCidr, setTypedCidr] = useState<string | null>(null);
  const [typedName, setTypedName] = useState<string | null>(null);
  const cidr = typedCidr ?? suggestion;
  const canonical = strictCidr(cidr);
  const hit = canonical === null ? null : claimOn(canonical, claims);
  const name =
    typedName ?? (canonical === null ? segmentNameFor(cidr) : segmentNamePrefill(canonical, nameClaims));
  const nameHit = canonical === null ? null : nameClaimOn(name, canonical, nameClaims);
  const landing = canonical === null ? null : segmentLandingNote(canonical, observedCidrs, stagedCidrs);
  const [typedZone, setTypedZone] = useState<ZoneType | null>(null);
  const zoneType = typedZone ?? zoneDefault.zone;
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  // S5.1.c: the optional hosts block. `hosts` is what was typed ("" = none);
  // the listen set FOLLOWS the role's persona default until it is edited
  // (LES-322: `null` until touched), and the addresses are allocated from `.10`
  // on the canonical CIDR -- the same value every other note here reads.
  const [hosts, setHosts] = useState("");
  const [role, setRole] = useState("");
  const [typedListen, setTypedListen] = useState<PortEntry[] | null>(null);
  const hostCount = /^\d+$/.test(hosts.trim()) ? Number(hosts.trim()) : hosts.trim() === "" ? 0 : NaN;
  const listenDefault = defaultListenFor(role);
  const listen = typedListen ?? listenDefault.entries;
  const allocation =
    canonical !== null && Number.isInteger(hostCount) && hostCount > 0
      ? allocateHostAddresses(canonical, hostCount, taken)
      : null;
  return (
    <GestureForm
      title="Add a segment"
      testid="form-add-segment"
      note={note}
      error={error}
      onSubmit={() => {
        // The CONTENT refusals the writer cannot make (`edit/allocate.ts`),
        // decided on the same canonical value the notes above were written
        // from. A value that is not a CIDR at all gets the writer mirror's own
        // reason from `stage`.
        if (canonical !== null && hit !== null && isRefusal(hit)) {
          setError(claimMessage(canonical, hit));
          return;
        }
        if (canonical !== null && nameHit !== null) {
          setError(nameClaimMessage(name, nameHit));
          return;
        }
        let err: string | null;
        if (stageBatch !== null && hostCount !== 0) {
          // S5.1.c: the segment AND its hosts, as ONE batch -- all or nothing.
          if (!Number.isInteger(hostCount) || hostCount < 0) {
            setError(`a host count is a whole number, got ${JSON.stringify(hosts.trim())}`);
            return;
          }
          if (canonical === null) {
            setError(stage({ kind: "add-segment", cidr, name, zoneType, note }));
            return;
          }
          if (allocation === null || !allocation.ok) {
            setError(allocation?.error ?? "no host addresses could be allocated");
            return;
          }
          const ports = canonicalPorts(listen);
          const gestures: Gesture[] = [
            { kind: "add-segment", cidr, name, zoneType, note },
            ...allocation.addresses.map(
              (address): Gesture => ({
                kind: "add-host",
                key: `address:${address}`,
                segment: canonical,
                ...(role.trim() ? { role: role.trim() } : {}),
                ...(ports.length > 0 ? { listen: ports } : {}),
                note,
              }),
            ),
          ];
          err = stageBatch(gestures, {
            label: `add segment ${canonical} with ${hostCount} host${hostCount === 1 ? "" : "s"}`,
            kind: "generate",
          });
        } else {
          err = stage({ kind: "add-segment", cidr, name, zoneType, note });
        }
        setError(err);
        if (!err && canonical !== null) {
          setTypedCidr(null);
          setTypedName(null);
          setNote("");
          setHosts("");
          setRole("");
          setTypedListen(null);
          onStaged(canonical);
        }
      }}
    >
      <Field label="CIDR">
        <input
          data-testid="seg-cidr"
          value={cidr}
          onChange={(e) => setTypedCidr(e.target.value)}
          placeholder="10.80.31.0/24"
        />
      </Field>
      {canonical !== null && hit !== null && (
        <p className={isRefusal(hit) ? "notice" : "meta"} data-testid="seg-claim">
          {claimMessage(canonical, hit)}
          {isRefusal(hit) ? ". Stage refuses it." : "."}
        </p>
      )}
      {/* Both notes that say "select it" offer the selection they name -- as a
          control beside the sentence, so the sentence stays the one the Stage
          refusal repeats. */}
      {canonical !== null && hit !== null && hit.exact && (hit.claim.source === "cast" || hit.claim.source === "absent") && (
        <button
          type="button"
          className="link"
          data-testid="seg-claim-select"
          onClick={() => onStaged(hit.claim.cidr.trim())}
        >
          select {hit.claim.cidr.trim()}
        </button>
      )}
      {landing && (
        <p className="meta" data-testid="seg-landing">
          {landing}
        </p>
      )}
      <Field label="name">
        <input
          data-testid="seg-name"
          value={name}
          onChange={(e) => setTypedName(e.target.value)}
          placeholder="seg-10-80-31-0"
        />
      </Field>
      {canonical !== null && nameHit !== null && (
        <p className="notice" data-testid="seg-name-claim">
          {nameClaimMessage(name, nameHit)}. Stage refuses it.
        </p>
      )}
      <Field label="zone type">
        <select
          data-testid="seg-zone"
          value={zoneType}
          onChange={(e) => setTypedZone(e.target.value as ZoneType)}
        >
          {ZONE_TYPES.map((z) => (
            <option key={z} value={z}>{z}</option>
          ))}
        </select>
      </Field>
      {typedZone === null && (
        <p className="meta" data-testid="seg-zone-default">
          {zoneDefault.source === "site"
            ? `starts at ${zoneDefault.zone} — the zone type most of ${siteLabel ?? "this group"}'s cast segments declare`
            : zoneDefault.source === "twin"
              ? `starts at ${zoneDefault.zone} — the zone type most of the cast's segments declare`
              : `starts at ${zoneDefault.zone}, the twin config's own default — no zone type is published for this twin's segments`}
        </p>
      )}
      {stageBatch !== null && (
        <fieldset className="hosts-block" data-testid="seg-hosts-block">
          <legend className="edit-label">hosts (optional)</legend>
          <Field label="how many">
            <input
              data-testid="seg-host-count"
              inputMode="numeric"
              value={hosts}
              onChange={(e) => setHosts(e.target.value)}
              placeholder="0"
            />
          </Field>
          {hostCount !== 0 && (
            <>
              <Field label="role">
                <input
                  data-testid="seg-host-role"
                  value={role}
                  list="axr-role-suggestions"
                  onChange={(e) => setRole(e.target.value)}
                  placeholder="domain-controller"
                />
              </Field>
              <datalist id="axr-role-suggestions">
                {rolesWithListenDefaults().map((r) => (
                  <option key={r} value={r} />
                ))}
              </datalist>
              <PortListEditor
                label="listen"
                ports={listen}
                onChange={(ports) => setTypedListen(canonicalPorts(ports))}
                testid="seg-host-listen"
              />
              <p className="meta" data-testid="seg-host-listen-source">
                {typedListen !== null
                  ? "as edited — a changed role no longer replaces it"
                  : !role.trim()
                    ? "no role given, so the listen set starts empty"
                    : listenDefault.servedBy === "persona"
                      ? `prefilled from the persona library: the ports a ${role.trim()} is reached on there — what its own persona serves, and what other personas dial it on (the load-generator personas are left out). ${LISTEN_DIALED_CAVEAT}`
                      : `no persona in the library serves ${role.trim()}, so the listen set starts empty`}
              </p>
              {/* M1: named where the default is OFFERED, not only in the
                  table's header -- and the role typed is named first when it is
                  one of them, because that is the one about to be written. */}
              {typedListen === null && listenDefault.servedBy === "persona" && (
                <p
                  className={listenDefault.dialedPorts ? "notice" : "meta"}
                  data-testid="seg-host-listen-caveat"
                >
                  {listenDefault.dialedPorts
                    ? `${role.trim()} is one of them: check this set before staging.`
                    : `The roles this is known to affect: ${ROLES_WITH_DIALED_EMITS.join(", ")}.`}
                </p>
              )}
              {Number.isNaN(hostCount) ? (
                <p className="notice" data-testid="seg-host-addresses">
                  a host count is a whole number. Stage refuses it.
                </p>
              ) : allocation === null ? null : allocation.ok ? (
                <p className="meta" data-testid="seg-host-addresses">
                  addresses: {allocation.addresses.join(", ")} — from .10 upward, skipping every address the export
                  or this file already holds; the image and sizing are left to the compiler.
                </p>
              ) : (
                <p className="notice" data-testid="seg-host-addresses">
                  {allocation.error}. Stage refuses it.
                </p>
              )}
            </>
          )}
        </fieldset>
      )}
      <NoteField note={note} onChange={setNote} />
    </GestureForm>
  );
}

function AddConduitForm(props: {
  stage: Stage;
  from: string;
  peers: readonly ConduitPeer[];
  edits: EditStoreState;
}): JSX.Element {
  const { stage, from, peers, edits } = props;
  const [chosen, setChosen] = useState<string | null>(null);
  // The EFFECTIVE peer is derived from the peers on offer now: a choice whose
  // segment has since gone (its add removed) falls back to the first peer
  // rather than staging a conduit to a segment no longer offered.
  const b = chosen !== null && peers.some((p) => p.cidr === chosen) ? chosen : (peers[0]?.cidr ?? "");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const replaces = existingAdd(edits, conduitKeyOf(from, b));
  return (
    <GestureForm
      title={`Add a conduit from ${from}`}
      testid="form-add-conduit"
      note={note}
      error={error}
      onSubmit={() => {
        const err = stage({ kind: "add-conduit", cidrA: from, cidrB: b, note });
        setError(err);
        if (!err) setNote("");
      }}
    >
      {/* Half A is the SELECTION, not a field -- that is the whole point of a
          selection-driven gesture. Shown so the record is readable before it
          is staged; the canonical half-ordering is `buildRecord`'s. */}
      <Field label="from (selected)">
        <output className="mono" data-testid="conduit-a">{from}</output>
      </Field>
      <Field label="to">
        <select data-testid="conduit-b" value={b} onChange={(e) => setChosen(e.target.value)}>
          {peers.map((p) => (
            <option key={p.cidr} value={p.cidr}>
              {p.label}
            </option>
          ))}
        </select>
      </Field>
      {replaces && <ReplacesNote entry={replaces} />}
      <NoteField note={note} onChange={setNote} />
    </GestureForm>
  );
}

/**
 * *Add host here* (S5.1.e): the segment is the one selected, and the key is
 * prefilled with the **next free address** on it.
 *
 * The allocation is `allocateHostAddresses`, the same function the generated
 * hosts of S5.1.c use, so "free" means exactly one thing: not an address the
 * export carries, and not a key any record in this file already holds -- saved,
 * pending or suppressed (`edit/allocate.ts`'s `takenAddresses`).
 *
 * `null`-until-typed (LES-322): untouched, the field FOLLOWS the allocation in
 * both directions, so staging one host moves the suggestion to the next address
 * and removing it moves it back; typed, nothing overwrites it.
 */
function AddHostForm(props: {
  stage: Stage;
  segment: string;
  edits: EditStoreState;
  /** The observed nodes, for what is already taken. Absent ⇒ no suggestion (a
   *  view with no graph cannot tell a free address from a taken one). */
  nodes?: readonly GraphNode[];
}): JSX.Element {
  const { stage, segment, edits } = props;
  const [typedKey, setTypedKey] = useState<string | null>(null);
  const [role, setRole] = useState("");
  const [listen, setListen] = useState<PortEntry[]>([]);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const nodes = props.nodes;
  const suggestion = useMemo(() => {
    if (!nodes) return null;
    const allocation = allocateHostAddresses(segment, 1, takenAddresses(nodes, edits.entries));
    return allocation.ok ? `address:${allocation.addresses[0]}` : null;
  }, [nodes, segment, edits.entries]);
  const key = typedKey ?? suggestion ?? "";
  return (
    <GestureForm
      title={`Add a host on ${segment}`}
      testid="form-add-host"
      note={note}
      error={error}
      onSubmit={() => {
        const g: Gesture = {
          kind: "add-host",
          key,
          segment,
          note,
          ...(role.trim() ? { role } : {}),
          ...(listen.length ? { listen } : {}),
        };
        const err = stage(g);
        setError(err);
        if (!err) {
          // Back to FOLLOWING the suggestion, which has just moved on to the
          // next free address -- not to the address just staged.
          setTypedKey(null);
          setRole("");
          setListen([]);
          setNote("");
        }
      }}
    >
      <Field label="key">
        <input data-testid="host-key" value={key} onChange={(e) => setTypedKey(e.target.value)} placeholder="address:10.80.20.5" />
      </Field>
      {typedKey === null && (
        <p className="meta" data-testid="host-key-default">
          {suggestion === null
            ? `no free address in ${segment} — type a key`
            : `the next free address in ${segment}, from .10 upward, skipping every address the export or this file already holds`}
        </p>
      )}
      <Field label="role">
        <input data-testid="host-role" value={role} onChange={(e) => setRole(e.target.value)} placeholder="domain-controller" />
      </Field>
      <PortListEditor label="listen" ports={listen} onChange={setListen} testid="host-listen" />
      {existingAdd(edits, key.trim()) && <ReplacesNote entry={existingAdd(edits, key.trim())!} />}
      <NoteField note={note} onChange={setNote} />
    </GestureForm>
  );
}

/** The `add` record the store already holds under `key`, or `null`. */
function existingAdd(edits: EditStoreState, key: string): EditStoreState["entries"][number] | null {
  if (!key) return null;
  return edits.entries.find((e) => e.block === "add" && (e.record.key ?? "") === key) ?? null;
}

/** A conduit's key as `buildRecord` writes it: the two halves sorted, `|`-joined. */
function conduitKeyOf(a: string, b: string): string {
  const [left, right] = a < b ? [a, b] : [b, a];
  return `${left}|${right}`;
}

/**
 * Re-staging an add under a key the store already holds REPLACES that record
 * -- the store keys an add by its key, so the change list keeps one row. Said
 * before Stage is pressed, never done silently (fix round, nit).
 */
function ReplacesNote({ entry }: { entry: EditStoreState["entries"][number] }): JSX.Element {
  return (
    <p className="notice" data-testid="replaces-note">
      a {entry.state} add for <span className="mono">{entry.record.key}</span> is
      already in this file — Stage replaces it, and the change list keeps one row.
    </p>
  );
}

/**
 * *Suppress hosts…* on a segment -- one record per host, not a batch.
 *
 * `suppress` takes HOST keys only (the server refuses a CIDR by name), so
 * "suppress this segment" is not a thing the file can say; what a segment
 * offers is its own hosts, each staged as the same single `suppress` record the
 * host-level gesture produces. One gesture, one record -- S5.1.b is where a
 * gesture gains the right to stage a batch.
 */
function SuppressHostsForm(props: { stage: Stage; hosts: readonly GraphNode[] }): JSX.Element {
  const { stage, hosts } = props;
  const [key, setKey] = useState(hosts[0]?.id ?? "");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  return (
    <GestureForm
      title="Suppress a host on this segment"
      testid="form-suppress-hosts"
      note={note}
      error={error}
      onSubmit={() => {
        const err = stage({ kind: "suppress-host", key, note });
        setError(err);
        if (!err) setNote("");
      }}
    >
      <Field label="host">
        <select data-testid="suppress-host-key" value={key} onChange={(e) => setKey(e.target.value)}>
          {hosts.map((h) => (
            <option key={h.id} value={h.id}>
              {h.id}
              {h.role ? ` · ${h.role}` : ""}
            </option>
          ))}
        </select>
      </Field>
      <p className="meta">
        one record per host — stage, pick the next, stage again.
      </p>
      <NoteField note={note} onChange={setNote} />
    </GestureForm>
  );
}

function ChangeRoleForm({ stage, hostKey }: { stage: Stage; hostKey: string }): JSX.Element {
  const [role, setRole] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  return (
    <GestureForm
      title="Change role"
      testid="form-change-role"
      note={note}
      error={error}
      onSubmit={() => {
        const err = stage({ kind: "change-role", key: hostKey, role, note });
        setError(err);
        if (!err) {
          setRole("");
          setNote("");
        }
      }}
    >
      <Field label="role">
        <input data-testid="role-value" value={role} onChange={(e) => setRole(e.target.value)} placeholder="domain-controller" />
      </Field>
      <NoteField note={note} onChange={setNote} />
    </GestureForm>
  );
}

function OverrideImageForm({ stage, hostKey }: { stage: Stage; hostKey: string }): JSX.Element {
  const [image, setImage] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  return (
    <GestureForm
      title="Override image"
      testid="form-override-image"
      note={note}
      error={error}
      onSubmit={() => {
        const err = stage({ kind: "override-image", key: hostKey, image, note });
        setError(err);
        if (!err) {
          setImage("");
          setNote("");
        }
      }}
    >
      <Field label="image">
        <input data-testid="image-value" value={image} onChange={(e) => setImage(e.target.value)} placeholder="debian-12" />
      </Field>
      <NoteField note={note} onChange={setNote} />
    </GestureForm>
  );
}

function OverrideResourcesForm({ stage, hostKey }: { stage: Stage; hostKey: string }): JSX.Element {
  const [values, setValues] = useState<Record<ResourceKey, string>>({ cpu: "", ram: "", disk: "" });
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const set = (k: ResourceKey, v: string): void => setValues((prev) => ({ ...prev, [k]: v }));
  return (
    <GestureForm
      title="Override sizing"
      testid="form-override-resources"
      note={note}
      error={error}
      onSubmit={() => {
        const resources: Record<string, string | number> = {};
        for (const k of RESOURCE_KEYS) {
          const raw = values[k].trim();
          if (!raw) continue;
          resources[k] = /^\d+$/.test(raw) ? Number(raw) : raw;
        }
        const err = stage({ kind: "override-resources", key: hostKey, resources, note });
        setError(err);
        if (!err) {
          setValues({ cpu: "", ram: "", disk: "" });
          setNote("");
        }
      }}
    >
      {RESOURCE_KEYS.map((k) => (
        <Field key={k} label={k}>
          <input data-testid={`res-${k}`} value={values[k]} onChange={(e) => set(k, e.target.value)} />
        </Field>
      ))}
      <NoteField note={note} onChange={setNote} />
    </GestureForm>
  );
}

function SuppressForm({ stage, hostKey }: { stage: Stage; hostKey: string }): JSX.Element {
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  return (
    <GestureForm
      title="Suppress this host"
      testid="form-suppress"
      note={note}
      error={error}
      onSubmit={() => {
        const err = stage({ kind: "suppress-host", key: hostKey, note });
        setError(err);
        if (!err) setNote("");
      }}
    >
      <NoteField note={note} onChange={setNote} />
    </GestureForm>
  );
}

// ---------------------------------------------------------------------------
// S5.1.b -- clone a segment: placement, then the form
// ---------------------------------------------------------------------------
/** One nudge of a proposed placement: a whole tile slot, so the arrow controls
 *  step through positions a tile could occupy rather than through pixels. */
const NUDGE_X = TILE_W + TILE_GAP_X;
const NUDGE_Y = TILE_H + TILE_GAP_Y;

/**
 * The placements whose form has already taken keyboard focus, by object
 * identity -- `focusedGestures`' rule (LES-324) for the second half of the clone
 * gesture: placing it swaps the placement controls for the form, the control
 * that had focus unmounts, and focus must land in the form ONCE, not on every
 * re-render or remount of the same placement.
 */
const focusedPlacements = new WeakSet<Point>();

function CloneBody(props: {
  graph: ConsoleGraph;
  edits: EditStoreState;
  source: string;
  clone: CloneActions | null;
  newGestureId: (() => string) | null;
  onClear: () => void;
}): JSX.Element {
  const { graph, edits, source, clone, newGestureId, onClear } = props;
  const segment = graph.segmentByCidr.get(source) ?? null;
  if (segment === null) {
    return (
      <p className="notice" data-testid="clone-no-source">
        <span className="mono">{source}</span> is not an observed segment of this
        twin, so there are no placed hosts to clone.
      </p>
    );
  }
  if (clone === null || clone.draft === null || newGestureId === null) {
    return (
      <p className="notice" data-testid="clone-no-flow">
        {CLONE_NO_FLOW}
      </p>
    );
  }
  // The placement step sits ABOVE the form, and the form STAYS MOUNTED through
  // it (review S1). "Choose again" used to swap the form out, and React threw
  // away every field with it -- a typed CIDR, a typed note and a ticked "mirror
  // conduits" all reset to defaults after re-placing, under a control that
  // reads as position-only. Conditional SIBLINGS, so the form keeps its slot in
  // the children array and therefore its state across the swap.
  //
  // KEYED BY SOURCE (LES-259, second review): keeping a form mounted means
  // giving it an identity, or the two rules collide. `CloneBody` re-renders in
  // place for a NEW clone gesture that did not pass through another selection
  // -- an Alt-drag of another tile while a clone form is open -- and without
  // the key that gesture inherits the previous one's CIDR, note and mirror
  // choice under a retitled frame, staging `cloned from <new source>: <old
  // note>` on a CIDR chosen for the old one. The key is the whole difference
  // between "the same gesture, re-placed" and "a different gesture".
  //
  // The two keys are PREFIXED because these are siblings of one children array:
  // a bare `source` on both is the same key twice, which React reconciles as a
  // collision (and warns about) rather than as two independent identities.
  return (
    <>
      {clone.draft.at === null && (
        <ClonePlacement key={`placement:${source}`} source={source} clone={clone} proposed={clone.draft.proposed} onCancel={onClear} />
      )}
      <CloneForm
        key={`form:${source}`}
        graph={graph}
        edits={edits}
        segment={segment}
        clone={clone}
        at={clone.draft.at}
        newGestureId={newGestureId}
      />
    </>
  );
}

/**
 * The placement step -- completable by keyboard alone and by touch
 * (`docs/WORK_ORDER_console_canvas.md` §3 S5.1.b). Every control is a native
 * button, so Enter and Space work without a key handler; a tap or click on the
 * network map places the clone where it lands, and these controls do the same
 * without a pointer: move the proposed position a tile slot at a time, then
 * place it there.
 */
function ClonePlacement(props: {
  source: string;
  clone: CloneActions;
  proposed: Point;
  onCancel: () => void;
}): JSX.Element {
  const { source, clone, proposed, onCancel } = props;
  const nudges: Array<{ id: string; label: string; glyph: string; dx: number; dy: number }> = [
    { id: "left", label: "Move the proposed position left one tile", glyph: "←", dx: -NUDGE_X, dy: 0 },
    { id: "up", label: "Move the proposed position up one tile", glyph: "↑", dx: 0, dy: -NUDGE_Y },
    { id: "down", label: "Move the proposed position down one tile", glyph: "↓", dx: 0, dy: NUDGE_Y },
    { id: "right", label: "Move the proposed position right one tile", glyph: "→", dx: NUDGE_X, dy: 0 },
  ];
  return (
    <div className="clone-placement" data-testid="clone-placement">
      <p className="meta" data-testid="clone-placement-help">
        {clone.mapShown
          ? `Choose where the clone of ${source} goes on the network map: click or tap the map, or move the dashed proposed tile with these controls and place it there. Escape cancels.`
          : `The network map is not open here, so place the clone of ${source} by position: it is proposed beside its group's last tile, and these controls move it. Escape cancels.`}
      </p>
      <p className="mono" data-testid="clone-placement-at">
        proposed at x {Math.round(proposed.x)}, y {Math.round(proposed.y)}
      </p>
      <div className="clone-nudges" role="group" aria-label="Move the proposed position">
        {nudges.map((n) => (
          <button
            key={n.id}
            type="button"
            className="zoom-chip"
            data-testid={`clone-nudge-${n.id}`}
            aria-label={n.label}
            onClick={() => clone.nudge(n.dx, n.dy)}
          >
            {n.glyph}
          </button>
        ))}
      </div>
      <div className="save-actions">
        <button type="button" className="stage-button" data-testid="clone-place" onClick={clone.place}>
          Place here
        </button>
        <button type="button" className="link" data-testid="clone-cancel" onClick={onCancel}>
          cancel
        </button>
      </div>
    </div>
  );
}

/**
 * The clone form (the work order's table). Every field starts at `cloneDefaults`
 * and follows it until typed (LES-322); the live preview runs BOTH pure passes
 * Stage runs -- `planClone` and then `buildBatch` (review S5) -- so what the
 * form says it will stage is exactly what Stage stages, and every refusal either
 * can raise is said while the operator is still choosing, in the words Stage
 * would refuse with. A batch check skipped here let "stages 1 segment · 17 hosts
 * · 2 conduits" sit above a Stage button that then refused the whole gesture.
 *
 * `at` is `null` while the placement is being chosen (review S1): the form stays
 * mounted and keeps every field, and only Stage is held back.
 */
function CloneForm(props: {
  graph: ConsoleGraph;
  edits: EditStoreState;
  segment: GraphSegment;
  clone: CloneActions;
  at: Point | null;
  newGestureId: () => string;
}): JSX.Element {
  const { graph, edits, segment, clone, at, newGestureId } = props;
  const source = segment.observedCidr;
  const defaults = useMemo(() => cloneDefaults(graph, edits.entries, source), [graph, edits.entries, source]);
  const [typedCidr, setTypedCidr] = useState<string | null>(null);
  const [typedName, setTypedName] = useState<string | null>(null);
  const [typedZone, setTypedZone] = useState<ZoneType | null>(null);
  const [hostMode, setHostMode] = useState<HostSelection["mode"]>("all");
  const [roles, setRoles] = useState<string[]>([]);
  const [firstN, setFirstN] = useState("");
  const [mapping, setMapping] = useState<AddressMapping>("same-host-part");
  const [mirror, setMirror] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  const cidr = typedCidr ?? defaults.cidr ?? "";
  const canonical = strictCidr(cidr);
  const observed = observedSegmentInputs(graph.segments, graph.castSegments, edits.entries);
  const nameClaims = segmentNameClaims(graph.castSegments, observed, edits.entries);
  const name = typedName ?? (canonical === null ? segmentNameFor(cidr) : segmentNamePrefill(canonical, nameClaims));
  const zoneType = typedZone ?? defaults.zoneType;
  const selection: HostSelection =
    hostMode === "roles"
      ? { mode: "roles", roles }
      : hostMode === "first"
        ? {
          mode: "first",
          count: /^\d+$/.test(firstN.trim()) ? Number(firstN.trim()) : NaN,
          // What was typed, so a refusal quotes it rather than `NaN` (N3).
          typed: firstN,
        }
        : { mode: "all" };
  const options = { cidr, name, zoneType, hosts: selection, mapping, mirrorConduits: mirror };
  // The preview runs the planner with a stand-in note, so a form with no note
  // yet still shows what it WOULD stage; Stage runs it with the real one.
  const preview = planClone(graph, edits.entries, source, { ...options, note: note.trim() || "…" });
  // S5: the SECOND pure pass Stage runs. A plan can be whole and still be
  // refused by the batch path (a key this file already holds, a cross-record
  // conflict), and a preview that stopped at `planClone` promised a count the
  // Stage button then refused.
  const batchPreview = preview.ok ? buildBatch(preview.plan.gestures, edits.entries) : null;
  const previewRefusal = !preview.ok ? preview.error : batchPreview && !batchPreview.ok ? batchPreview.error : null;
  const choice = chooseHosts(segment, selection);
  const conduits = sourceConduits(graph, edits.entries, source);
  const notMirrored = preview.ok ? preview.plan.notMirrored : conduits.notMirrored;
  const held = canonical === null || at === null ? null : clone.heldIn(canonical, at);
  const { setCidr } = clone;
  useEffect(() => {
    setCidr(canonical);
  }, [canonical, setCidr]);

  // Focus moves into the form ONCE per placement (see `focusedPlacements`).
  const titleRef = useRef<HTMLParagraphElement | null>(null);
  useEffect(() => {
    if (at === null || focusedPlacements.has(at)) return;
    focusedPlacements.add(at);
    titleRef.current?.focus();
  }, [at]);

  const counts =
    preview.ok && previewRefusal === null
      ? `1 segment · ${preview.plan.hosts.length} host${preview.plan.hosts.length === 1 ? "" : "s"} · ` +
        `${preview.plan.conduits.length} conduit${preview.plan.conduits.length === 1 ? "" : "s"}`
      : null;

  return (
    <GestureForm
      title={`Clone ${source}`}
      testid="form-clone"
      note={note}
      error={error}
      stageLabel="Stage the clone"
      blocked={at === null ? "choose where the clone goes first — the controls above place it" : null}
      onSubmit={() => {
        if (at === null) return;
        const result = planClone(graph, edits.entries, source, { ...options, note });
        if (!result.ok) {
          setError(result.error);
          return;
        }
        const batch = buildBatch(result.plan.gestures, edits.entries);
        if (!batch.ok) {
          setError(batch.error);
          return;
        }
        setError(null);
        clone.stage(
          batch.staged,
          { id: newGestureId(), label: result.plan.label, kind: "clone", source },
          result.plan.cidr,
          at,
        );
      }}
    >
      {at !== null && (
        <p ref={titleRef} tabIndex={-1} className="meta" data-testid="clone-placed">
          placed at x {Math.round(at.x)}, y {Math.round(at.y)} on the network map{" "}
          <button type="button" className="link" data-testid="clone-replace" onClick={clone.replace}>
            choose again
          </button>{" "}
          <span className="meta">— what is filled in below is kept</span>
        </p>
      )}
      {held && (
        <p className="notice" data-testid="clone-held">
          its group {held.siteLabel} is collapsed on the map, so the clone is held there, not drawn —{" "}
          <button type="button" className="link" data-testid="clone-held-expand" onClick={() => clone.expand(held.siteKey)}>
            open {held.siteLabel}
          </button>
        </p>
      )}
      <Field label="CIDR">
        <input data-testid="clone-cidr" value={cidr} onChange={(e) => setTypedCidr(e.target.value)} placeholder="10.80.31.0/24" />
      </Field>
      {typedCidr === null && (
        <p className="meta" data-testid="clone-cidr-default">
          {defaults.cidr === null
            ? `no /24 in ${defaults.prefix ?? "the source's prefix"} is free — type one`
            : defaults.prefixSource === "site"
              ? `the next free /24 in ${defaults.prefix}, the source's site`
              : `the next free /24 in ${defaults.prefix}, the source's own /16 (its group has no single prefix)`}
        </p>
      )}
      <Field label="name">
        <input data-testid="clone-name" value={name} onChange={(e) => setTypedName(e.target.value)} />
      </Field>
      <Field label="zone type">
        <select data-testid="clone-zone" value={zoneType} onChange={(e) => setTypedZone(e.target.value as ZoneType)}>
          {ZONE_TYPES.map((z) => (
            <option key={z} value={z}>{z}</option>
          ))}
        </select>
      </Field>
      {typedZone === null && (
        <p className="meta" data-testid="clone-zone-default">
          {defaults.zoneSource === "source"
            ? `starts at ${defaults.zoneType} — the zone type ${source} declares in the cast`
            : `${source} declares no zone type in the cast, so it starts at ${defaults.zoneType} — ${
                defaults.zoneSource === "site"
                  ? "the zone type most of its site's cast segments declare"
                  : defaults.zoneSource === "twin"
                    ? "the zone type most of the cast's segments declare"
                    : "the twin config's own default"
              }`}
        </p>
      )}
      <fieldset className="clone-hosts" data-testid="clone-hosts">
        <legend className="edit-label">hosts to bring</legend>
        {(["all", "roles", "first"] as const).map((mode) => (
          <label key={mode} className="clone-choice">
            <input
              type="radio"
              name="clone-host-mode"
              data-testid={`clone-hosts-${mode}`}
              checked={hostMode === mode}
              onChange={() => setHostMode(mode)}
            />{" "}
            {mode === "all" ? "all placed hosts" : mode === "roles" ? "by role" : "first N by address"}
          </label>
        ))}
        {hostMode === "roles" && (
          <div className="clone-roles" data-testid="clone-roles">
            {segment.roleHistogram.map((r) => (
              <label key={r.role} className="clone-choice">
                <input
                  type="checkbox"
                  data-testid="clone-role"
                  value={r.role}
                  checked={roles.includes(r.role)}
                  onChange={(e) =>
                    setRoles((prev) => (e.target.checked ? [...prev, r.role] : prev.filter((x) => x !== r.role)))
                  }
                />{" "}
                {r.role} <span className="meta">{r.count}</span>
              </label>
            ))}
          </div>
        )}
        {hostMode === "first" && (
          <Field label="N">
            <input data-testid="clone-first-n" inputMode="numeric" value={firstN} onChange={(e) => setFirstN(e.target.value)} />
          </Field>
        )}
      </fieldset>
      {choice.notCloned.length > 0 && (
        <div className="notice" data-testid="clone-not-cloned">
          not cloned: {choice.notCloned.length} host{choice.notCloned.length === 1 ? "" : "s"}
          <ul className="change-confirm-list">
            {choice.notCloned.map((n) => (
              <li key={n.key}>
                <span className="mono">{n.key}</span> — {n.reason}
              </li>
            ))}
          </ul>
        </div>
      )}
      <fieldset className="clone-mapping" data-testid="clone-mapping">
        <legend className="edit-label">address mapping</legend>
        <label className="clone-choice">
          <input
            type="radio"
            name="clone-mapping"
            data-testid="clone-mapping-same"
            checked={mapping === "same-host-part"}
            onChange={() => setMapping("same-host-part")}
          />{" "}
          same host part
        </label>
        <label className="clone-choice">
          <input
            type="radio"
            name="clone-mapping"
            data-testid="clone-mapping-renumber"
            checked={mapping === "renumber"}
            onChange={() => setMapping("renumber")}
          />{" "}
          renumber from .10
        </label>
      </fieldset>
      <label className="clone-choice">
        <input type="checkbox" data-testid="clone-mirror" checked={mirror} onChange={(e) => setMirror(e.target.checked)} />{" "}
        mirror conduits ({conduits.peers.length})
      </label>
      {mirror && !conduits.rosterPublished && (
        <p className="meta" data-testid="clone-roster-unpublished">
          this server does not publish the cast&apos;s conduit roster, so only the conduits this file adds can be
          mirrored.
        </p>
      )}
      {/* The PLAN's list, not `sourceConduits`' own: the plan drops one more
          peer the roster cannot know about -- the clone's own CIDR (N4) -- and
          two lists of "not mirrored" would disagree by exactly that one. Falls
          back to the roster's while the plan is refused, so a refusal never
          also blanks this. */}
      {mirror && notMirrored.length > 0 && (
        <div className="notice" data-testid="clone-not-mirrored">
          not mirrored: {notMirrored.length}
          <ul className="change-confirm-list">
            {notMirrored.map((n) => (
              <li key={n.peer}>
                <span className="mono">{n.peer}</span> — {n.reason}
              </li>
            ))}
          </ul>
        </div>
      )}
      <p className="meta" data-testid="clone-copies">
        each host brings its role, listen and dial sets, and an image or sizing only where an assertion in this file
        gives the source host one. Every record carries the note as “cloned from {source}: …”, and nothing observed
        travels as evidence.
      </p>
      {previewRefusal === null ? (
        <>
          <p className="meta" data-testid="clone-summary">
            stages {counts}
          </p>
          {preview.ok &&
            preview.plan.warnings.map((w) => (
              <p key={w} className="meta" data-testid="clone-warning">
                {w}.
              </p>
            ))}
        </>
      ) : (
        // Said while choosing, in the words Stage refuses with -- from EITHER
        // pure pass (S5). (The preview runs with a stand-in note, so a missing
        // note is never the reason here: the Stage button itself waits for one.)
        <div className="notice" data-testid="clone-refusal">
          {previewRefusal}. Stage refuses it.
          {!preview.ok && preview.offerRenumber && mapping !== "renumber" && (
            <>
              {" "}
              <button type="button" className="link" data-testid="clone-offer-renumber" onClick={() => setMapping("renumber")}>
                renumber from .10
              </button>
            </>
          )}
        </div>
      )}
      <NoteField note={note} onChange={setNote} />
    </GestureForm>
  );
}

// ---------------------------------------------------------------------------
// small building blocks (unchanged from S5)
// ---------------------------------------------------------------------------
function GestureForm(props: {
  title: string;
  testid: string;
  note: string;
  error: string | null;
  onSubmit: () => void;
  /** The submit control's label -- `Stage` unless a gesture says more. */
  stageLabel?: string;
  /** Why staging is held back for a reason that is NOT the note -- said, and
   *  the control disabled. `null`/absent: only the note gate applies. */
  blocked?: string | null;
  children: ReactNode;
}): JSX.Element {
  const blocked = props.blocked ?? null;
  return (
    <form
      className="gesture-form"
      data-testid={props.testid}
      onSubmit={(e) => {
        e.preventDefault();
        if (blocked !== null) return;
        props.onSubmit();
      }}
    >
      <div className="gesture-title">{props.title}</div>
      {props.children}
      {props.error && (
        <p className="notice is-error" role="alert" data-testid="gesture-error">
          {props.error}
        </p>
      )}
      {blocked !== null && (
        <p className="meta" data-testid="stage-blocked">
          {blocked}
        </p>
      )}
      {/* The note gate: a record cannot be staged without a note (`14` §3). */}
      <button
        type="submit"
        className="stage-button"
        data-testid="stage-button"
        disabled={!props.note.trim() || blocked !== null}
      >
        {props.stageLabel ?? "Stage"}
      </button>
    </form>
  );
}

function NoteField({ note, onChange }: { note: string; onChange: (v: string) => void }): JSX.Element {
  return (
    <Field label="note (required)">
      <input data-testid="gesture-note" value={note} onChange={(e) => onChange(e.target.value)} placeholder="why this assertion" />
    </Field>
  );
}

function PortListEditor(props: {
  label: string;
  ports: PortEntry[];
  onChange: (ports: PortEntry[]) => void;
  testid: string;
}): JSX.Element {
  const [port, setPort] = useState("");
  const [proto, setProto] = useState<PortProto>("tcp");
  const add = (): void => {
    const n = Number(port);
    if (!Number.isInteger(n) || n < 1 || n > 65535) return;
    props.onChange([...props.ports, { port: n, proto }]);
    setPort("");
  };
  return (
    <Field label={props.label}>
      <div className="port-editor" data-testid={props.testid}>
        <ul className="port-list">
          {props.ports.map((p, i) => (
            <li key={`${p.port}/${p.proto}`} className="mono">
              {p.port}/{p.proto}{" "}
              <button
                type="button"
                className="link"
                aria-label={`remove ${p.port}/${p.proto}`}
                onClick={() => props.onChange(props.ports.filter((_, j) => j !== i))}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
        <input data-testid={`${props.testid}-port`} value={port} onChange={(e) => setPort(e.target.value)} placeholder="port" />
        <select data-testid={`${props.testid}-proto`} value={proto} onChange={(e) => setProto(e.target.value as PortProto)}>
          {PORT_PROTOS.map((p) => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
        <button type="button" className="link" data-testid={`${props.testid}-add`} onClick={add}>
          + port
        </button>
      </div>
    </Field>
  );
}

/** The Actions panel of a hand-authored twin: what an operator CAN do, in
 *  words, instead of gestures that would correct capture evidence it has not
 *  got. */
function HandAuthoredActions({ configFile, twinId }: { configFile: string; twinId: string }): JSX.Element {
  return (
    <section className="element-actions" data-testid="actions-hand-authored" data-actions-frame="" aria-label="Actions">
      <h3>Actions</h3>
      <p>
        This twin is hand-authored, so there is no capture evidence here to correct. To change it, edit{" "}
        <code>{configFile}</code> and redeploy with <code>axrange te deploy --config {twinId} --update</code>.
      </p>
      <p className="meta">
        To change one running host&apos;s personas without a redeploy, open the live twin (if it is running) and use
        &ldquo;Change personas…&rdquo;. Dragging segments on this map still arranges the layout.
      </p>
    </section>
  );
}

