/**
 * The three-pane shell: outline | map | inspector, with the URL as the zoom.
 *
 * All state that matters is in the route (`15` §7) -- level, scope and
 * filters -- so the back button, a bookmark and a pasted link all work
 * without a store to rehydrate. Two things are deliberately NOT in the URL:
 * the edit store (`edit/store.ts`, unsaved edits + the assertions etag), which
 * lives in the per-imprint workspace, and the transient in-pane `focus`
 * (`views/selection.ts`: which matrix cell or O7 drawer is open, or what is
 * selected for editing), reset when the PATHNAME changes -- never on a query
 * flag.
 *
 * S3: the graph (topology export + cast) is fetched once at the shell so the
 * outline tree, the map and the inspector share it; the edit store stays in
 * the workspace so switching imprints discards it (LES-259). Since S5.1.a the
 * workspace renders the outline too: it lists the segments the store adds.
 */
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router";

import { asApiError, useApply, useAssertions, useImprints, useLayout, useProvenance } from "./api/hooks";
import { ConsoleApiError } from "./api/client";
import { useApiClient } from "./api/context";
import type { ApplyResponse, AssertionBlock, AssertionRecord, AssertionsDoc, ImprintListItem, LayoutDoc } from "./api/types";
import {
  batchRefusal,
  canSave,
  documentFrom,
  editReducer,
  entriesFromDoc,
  entriesInState,
  entryId,
  sameRecord,
  hasUnsavedEdits,
  initialEditState,
  suppressedKeysFromPopulation,
  type BatchRecord,
  type EditEntry,
  type GestureTag,
} from "./edit/store";
import {
  clearDraft,
  DRAFT_SCHEMA,
  listDrafts,
  readDraft,
  reconcileDraft,
  restoreNotice,
  writeDraft,
} from "./edit/draft";
import { orphanReasons } from "./edit/dependents";
import { crossRecordConflict, invalidStoredRecord, metadataReady } from "./edit/records";
import { newGestureId } from "./edit/gestures";
import {
  changeSetFromDiff, changeSetSize, EMPTY_CHANGE_SET, twinHostChurn,
  type ChangeSet, type TwinHostChurn,
} from "./model/changes";
import { NO_PINS, type AuthoredPin, type Pins } from "./layout/pins";
import { planeForSave } from "./layout/plane";
import { layout0 } from "./layout/level0";
import { placeAddedTiles, proposedClonePlacement, segmentPinKey } from "./layout/added";
import type { Point } from "./layout/geometry";
import { imprintOf, parentOf, parseRoute, routeFor, zoomToPath } from "./model/zoom";
import type { Filters, Zoom } from "./model/zoom";
import { DesignHeading } from "./areas/design/DesignBar";
import { configFileOf, isHandAuthored } from "./model/declared";
import { EditPanel } from "./views/EditPanel";
import { addedSegmentsOf } from "./edit/allocate";
import { ElementActions, type CloneActions } from "./views/ElementActions";
import { level0OptionsFor, type AddedTileView, type CloneDraftView } from "./views/Level0";
import { Inspector } from "./views/Inspector";
import { MapPane } from "./views/MapPane";
import { Outline } from "./views/Outline";
import {
  resolveSelection,
  sameSelection,
  selectionFromFocus,
  type ExplicitSelection,
  type Focus,
} from "./views/selection";
import { useGraph, type GraphResource } from "./views/useGraph";

const ASSERTIONS_SCHEMA = "axr-assertions/1";

/** The `source` a NEW assertions file is seeded with (Unified Console ruling
 *  §2): a valid value, so Save is not silently disabled on a first edit; it is
 *  shown in the Save form and the operator replaces it with their own name. */
const DEFAULT_SOURCE = "operator: console";

/** The `axr-layout/1` schema the pins doc carries (`15` §4); the API refuses
 *  any other value, so it is stated once here rather than spelled at each save. */
const LAYOUT_SCHEMA = "axr-layout/1";

/** The post-apply highlight window in milliseconds (`15` §12 O5: five seconds,
 *  then the `?changed=1` filter keeps the changed set reachable). */
const HIGHLIGHT_MS = 5000;

/**
 * Whether the outline pane is open (R1). This is the ONE piece of view state
 * that is deliberately NOT in the URL: it is a per-viewer convenience about
 * this browser's window, not a description of what is being looked at, so a
 * pasted link should not carry it (the work order says `localStorage` is
 * acceptable here, and the URL is reserved for zoom + filters).
 *
 * Every access is guarded: `localStorage` throws on access in a hardened
 * browser profile, and the shell must render regardless.
 */
const OUTLINE_KEY = "axrange.console.outline";

function readOutlineOpen(): boolean {
  try {
    return window.localStorage.getItem(OUTLINE_KEY) !== "closed";
  } catch {
    return true; // open is the default, and the safe answer
  }
}

function writeOutlineOpen(open: boolean): void {
  try {
    window.localStorage.setItem(OUTLINE_KEY, open ? "open" : "closed");
  } catch {
    // storage unavailable: the toggle still works for this session
  }
}

export function ConsoleShell(): JSX.Element {
  const location = useLocation();
  const navigate = useNavigate();
  const { zoom, filters } = useMemo(
    () => parseRoute(`${location.pathname}${location.search}`),
    [location.pathname, location.search],
  );
  const imprintId = zoom ? imprintOf(zoom) : null;

  const catalog = useImprints();
  const graph = useGraph(imprintId);
  const provenance = useProvenance(imprintId);

  // R1: the outline pane collapses to give the map its width back. Seeded from
  // `localStorage` on mount (so the preference survives a reload) and written
  // back on every change.
  const [outlineOpen, setOutlineOpen] = useState(readOutlineOpen);
  useEffect(() => {
    writeOutlineOpen(outlineOpen);
  }, [outlineOpen]);
  // At the catalog the outline IS the navigation and there is no map to give
  // the width to, so a stored `closed` must not strand the operator with
  // "pick an imprint from the outline" and no outline (S5). Forced open there,
  // WITHOUT touching the stored preference -- and the toggle is hidden rather
  // than left as a control that visibly does nothing.
  const outlineForced = zoom?.level === "catalog";
  const outlineShown = outlineOpen || outlineForced;

  // Transient selection, reset when the route's IDENTITY changes -- the
  // pathname, and nothing in the query (`views/selection.ts`). A cell or a
  // drawer opened at one place has no meaning at the next, but no query flag
  // moves the page to another place: S1 found `?summary=` closing an open
  // drawer; S5.1.a found every view flag, every dimming filter and an apply's
  // `?changed=1` dropping an in-place selection AND whatever was typed under
  // it -- the last one on every apply that changed anything.
  const [focus, setFocus] = useState<Focus>(null);
  // Where keyboard focus returns when an explicit selection is cleared: the
  // control that opened it (a tile's select control, a site `+`), or -- when
  // that control is gone -- the map pane. Never `body` (a keyboard user would
  // start again from the top of the document).
  const openerRef = useRef<HTMLElement | SVGElement | null>(null);
  useEffect(() => {
    setFocus(null);
    openerRef.current = null;
  }, [location.pathname]);

  const onFocus = useCallback((next: Focus): void => {
    if (selectionFromFocus(next) !== null) {
      const active = document.activeElement;
      // A selection made FROM the actions frame (the segment a site + has just
      // staged) keeps the opener of the frame itself: the control it came from
      // is about to unmount.
      const inFrame = active?.closest(ACTIONS_FRAME) ?? null;
      if (inFrame === null) {
        // `body` / `documentElement` is where focus sits when NOTHING had it
        // (a pointer on non-focusable chrome) -- not an opener to return to.
        const isDocument = active === document.body || active === document.documentElement;
        openerRef.current =
          !isDocument && (active instanceof HTMLElement || active instanceof SVGElement) ? active : null;
      }
    }
    setFocus(next);
  }, []);

  const clearSelection = useCallback((): void => {
    const opener = openerRef.current;
    openerRef.current = null;
    // Focus moves BEFORE the frame re-renders, so it never falls to `body` in
    // between: to the opener while it is still on the page, else the map pane.
    if (opener && opener.isConnected) opener.focus();
    else focusMapPane();
    setFocus(null);
  }, []);

  // ---- S5.1.d: ONE guard on every way out of an imprint ---------------------
  // GAP-094: pending edits used to vanish on the breadcrumb, an outline link and
  // the browser's own navigation; only Escape was guarded. They now survive as a
  // draft (`edit/draft.ts`), and leaving is still CONFIRMED rather than silent --
  // an operator who meant to stay should not discover the move by the map
  // changing under them.
  //
  // The guard sits on `go`, the ONE function every in-app navigation goes
  // through (the breadcrumb, the outline's imprint list, the map's drills, the
  // Escape handler), so a new link cannot forget it. It fires only when the
  // navigation LEAVES the imprint: moving between levels of the same imprint
  // keeps the store and needs no confirmation.
  //
  // The count is reported UPWARD by the workspace, which owns the store: a ref
  // as well as state, so the callback `go` closes over reads the live value
  // rather than the one from the render that created it.
  const [pendingCount, setPendingCount] = useState(0);
  const pendingRef = useRef(0);
  /**
   * The last count each imprint reported while its workspace was MOUNTED.
   *
   * Separate from `pendingRef` because the workspace's unmount reports 0 -- it
   * has to, or the shell would go on guarding a store that no longer exists --
   * and that 0 arrives BEFORE the route effect below can read the count of the
   * imprint just left. So the unmount zeroes the live guard and leaves this
   * map alone; only a mounted workspace writes it (S9).
   */
  const pendingByImprintRef = useRef<Map<string, number>>(new Map());
  const onPendingCount = useCallback((id: string, n: number): void => {
    pendingRef.current = n;
    setPendingCount(n);
    if (n > 0) pendingByImprintRef.current.set(id, n);
    else pendingByImprintRef.current.delete(id);
  }, []);
  const onWorkspaceGone = useCallback((): void => {
    pendingRef.current = 0;
    setPendingCount(0);
  }, []);
  /**
   * An imprint LEFT with unsaved edits, and how many (review S9).
   *
   * The in-app exits are guarded; the browser's own Back is not, because
   * `history.back()` cannot be intercepted without fighting the history API for
   * a navigation that now loses nothing -- the draft restores the edits on
   * return. So the two paths are made HONEST rather than identical: the guard
   * says the work is kept, and a Back that skipped the guard is met at the
   * destination by a notice naming the imprint, its count and the way back.
   */
  const [leftBehind, setLeftBehind] = useState<{ imprint: string; count: number } | null>(null);
  /** Every draft on this browser, re-read whenever the route leaves an imprint
   *  (review E) -- so the catalog reflects a discard or a save made just now. */
  const [drafts, setDrafts] = useState<ReturnType<typeof listDrafts>>([]);
  useEffect(() => {
    setDrafts(imprintId === null ? listDrafts() : []);
  }, [imprintId, pendingCount]);
  const lastImprintRef = useRef<string | null>(null);
  useEffect(() => {
    const previous = lastImprintRef.current;
    const left = previous === null ? 0 : (pendingByImprintRef.current.get(previous) ?? 0);
    if (previous !== null && previous !== imprintId && left > 0) {
      setLeftBehind({ imprint: previous, count: left });
    } else if (imprintId !== null && imprintId === leftBehind?.imprint) {
      setLeftBehind(null); // back where the edits are
    }
    lastImprintRef.current = imprintId;
  }, [imprintId, leftBehind]);
  const [blockedPath, setBlockedPath] = useState<string | null>(null);
  const navigateNow = useCallback(
    (path: string): void => {
      // `navigate` returns `void | Promise<void>` in react-router 7; the result
      // is intentionally discarded (this is a fire-and-forget navigation).
      void navigate(path);
    },
    [navigate],
  );
  const currentImprint = imprintId;
  const go = useCallback(
    (path: string): void => {
      const parsed = parseRoute(path).zoom;
      const target = parsed === null ? null : imprintOf(parsed);
      const leaving = currentImprint !== null && target !== currentImprint;
      if (leaving && pendingRef.current > 0) {
        setBlockedPath(path);
        return;
      }
      navigateNow(path);
    },
    [currentImprint, navigateNow],
  );
  // A navigation the operator has now confirmed. The path is kept rather than
  // re-derived: "leave anyway" must go exactly where the click was going.
  const leaveAnyway = useCallback((): void => {
    const path = blockedPath;
    setBlockedPath(null);
    if (path !== null) navigateNow(path);
  }, [blockedPath, navigateNow]);
  // A prompt whose reason has gone is DISMISSED, never answered (review S4).
  // The first cut navigated on any route to zero pending -- so undoing the last
  // gesture, or discarding the draft, moved the operator somewhere they had not
  // agreed to go. Leaving is a decision; only *Leave anyway* makes it.
  useEffect(() => {
    if (blockedPath !== null && pendingCount === 0) setBlockedPath(null);
  }, [blockedPath, pendingCount]);

  // The browser's OWN navigation -- a reload, a closed tab, the back button out
  // of the app. `beforeunload` is the only hook for it, and it is deliberately
  // gated on `pendingCount > 0` so it never fires spuriously: a browser that
  // prompts on every reload trains the operator to dismiss it.
  useEffect(() => {
    if (pendingCount === 0) return;
    const onBeforeUnload = (event: BeforeUnloadEvent): void => {
      event.preventDefault();
      // Legacy browsers need the assignment; modern ones show their own text.
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [pendingCount]);

  // Escape (`15` §7) is handled in the WORKSPACE, which owns the edit store:
  // whether "up" may be taken depends on the pending edits it holds (T5). It
  // stays separate from the guard above on LES-324's rule: a keypress is cheap
  // to make by accident, so Escape refuses and says why rather than offering a
  // one-click "leave anyway".

  return (
    <div className={`shell${outlineShown ? "" : " is-outline-collapsed"}`}>
      <header className="shell-head">
        {!outlineForced && (
          <button
            type="button"
            className="rail-toggle"
            data-testid="outline-toggle"
            aria-expanded={outlineShown}
            aria-label={outlineShown ? "Hide the outline" : "Show the outline"}
            onClick={() => setOutlineOpen(!outlineOpen)}
          >
            {outlineShown ? "◀" : "▶"} outline
          </button>
        )}
        <DesignHeading />
        <Breadcrumb zoom={zoom} filters={filters} go={go} />
      </header>
      {/* Review E: at the CATALOG, every draft this browser holds -- the one
          corner the destination notice below cannot reach, because it needs a
          workspace to have reported a count and a cold start has had none. */}
      {imprintId === null && leftBehind === null && drafts.length > 0 && (
        <p className="notice" role="status" data-testid="drafts-here">
          {drafts.length} imprint{drafts.length === 1 ? "" : "s"} on this browser {drafts.length === 1 ? "has" : "have"}{" "}
          unsaved drafts:{" "}
          {drafts.map((d, i) => (
            <span key={d.imprint}>
              {i > 0 && ", "}
              <button
                type="button"
                className="link"
                data-testid="draft-here"
                data-imprint={d.imprint}
                onClick={() => navigateNow(zoomToPath({ level: "network", imprint: d.imprint }))}
              >
                {d.imprint}
              </button>{" "}
              <span className="meta">
                ({d.count} record{d.count === 1 ? "" : "s"}
                {d.savedAt ? `, ${d.savedAt}` : ""})
              </span>
            </span>
          ))}
        </p>
      )}
      {/* S5.1.d/S9: an imprint left with unsaved edits by a navigation the
          guard could not intercept -- the browser's own Back. The work is in
          the draft; this says whose it was and how to get to it. */}
      {leftBehind !== null && blockedPath === null && (
        <p className="notice" role="status" data-testid="left-behind">
          {leftBehind.count} unsaved edit{leftBehind.count === 1 ? "" : "s"} in{" "}
          <span className="mono">{leftBehind.imprint}</span> are kept as a draft on this browser.{" "}
          <button
            type="button"
            className="link"
            data-testid="left-behind-back"
            onClick={() => navigateNow(zoomToPath({ level: "network", imprint: leftBehind.imprint }))}
          >
            Go back to them
          </button>{" "}
          <button type="button" className="link" data-testid="left-behind-dismiss" onClick={() => setLeftBehind(null)}>
            Dismiss
          </button>
        </p>
      )}
      {/* S5.1.d: the one guard, said once, wherever the operator was going. */}
      {blockedPath !== null && (
        <p className="notice leave-guard" role="alert" data-testid="leave-guard">
          {pendingCount} unsaved edit{pendingCount === 1 ? "" : "s"} in this imprint. They are kept as a
          draft on this browser and will be offered back when you return.{" "}
          <button type="button" className="link" data-testid="leave-anyway" onClick={leaveAnyway}>
            Leave anyway
          </button>{" "}
          <button type="button" className="link" data-testid="leave-stay" onClick={() => setBlockedPath(null)}>
            Stay
          </button>
        </p>
      )}
      {/* KEYED BY IMPRINT (LES-259): the workspace owns the edit store, and
          switching imprints must DISCARD it rather than carry one imprint's
          pending edits (and its etag) into another's file. It renders the
          outline too (S5.1.a): the outline lists the segments the store ADDS,
          so it has to sit where the store is. */}
      <ImprintWorkspace
        key={imprintId ?? ""}
        imprintId={imprintId}
        zoom={zoom}
        filters={filters}
        pathname={location.pathname}
        graph={graph}
        provenance={provenance.data}
        provenanceError={provenance.error}
        provenanceLoading={provenance.loading}
        onReloadProvenance={provenance.reload}
        focus={focus}
        onFocus={onFocus}
        onClearSelection={clearSelection}
        go={go}
        outline={
          outlineShown
            ? { catalog: catalog.data, error: catalog.error, loading: catalog.loading }
            : null
        }
        onPendingCount={onPendingCount}
        onWorkspaceGone={onWorkspaceGone}
        imprintEntry={catalog.data?.imprints.find((i) => i.id === imprintId) ?? null}
      />
    </div>
  );
}

function ImprintWorkspace(props: {
  imprintId: string | null;
  zoom: Zoom | null;
  filters: Filters;
  pathname: string;
  graph: GraphResource;
  provenance: Parameters<typeof MapPane>[0]["provenance"];
  provenanceError: Parameters<typeof MapPane>[0]["provenanceError"];
  provenanceLoading: boolean;
  onReloadProvenance: () => void;
  focus: Focus;
  onFocus: (focus: Focus) => void;
  onClearSelection: () => void;
  go: (path: string) => void;
  /** The outline's catalog, or `null` while the outline is collapsed. */
  outline: {
    catalog: ReturnType<typeof useImprints>["data"];
    error: ReturnType<typeof useImprints>["error"];
    loading: boolean;
  } | null;
  /** S5.1.d: how many PENDING edits this imprint holds, reported upward so the
   *  shell's one leave-guard (and `beforeunload`) can read it. The store lives
   *  here; the navigation function lives up there. */
  onPendingCount: (imprintId: string, n: number) => void;
  /** The workspace is going away: zero the live guard, and leave the
   *  per-imprint record alone so the shell can still say what was left. */
  onWorkspaceGone: () => void;
  /** This imprint's catalog entry (its `can_apply`), or `null`. */
  imprintEntry: ImprintListItem | null;
}): JSX.Element {
  const { imprintId, zoom, filters, pathname, graph, focus, onFocus, go } = props;
  const api = useApiClient();
  const assertions = useAssertions(imprintId);
  const apply = useApply(imprintId);

  const [edits, dispatch] = useReducer(editReducer, imprintId ?? "", initialEditState);
  useEffect(() => {
    if (assertions.data) dispatch({ type: "loaded", doc: assertions.data });
  }, [assertions.data]);

  // ---- S5.1.d: the DRAFT (`edit/draft.ts`, closes GAP-094) -------------------
  // Pending edits are kept on this browser, per imprint, so a reload, the
  // breadcrumb, an outline link and the back button no longer discard them.
  //
  // RESTORE runs ONCE per imprint and only after the file has loaded: the
  // reconciliation drops every record the file now holds, and before `loaded`
  // the store holds none of them, so restoring early would re-stage records
  // that are already saved. A ref, not state: this must not re-run on a
  // re-render, and the workspace is keyed by imprint (LES-259) so a fresh
  // instance gets a fresh ref.
  const restoredRef = useRef(false);
  const [draftNotice, setDraftNotice] = useState<string | null>(null);
  // TWO problem slots, not one. A draft that could not be READ (a hardened
  // profile, a document this module did not write) and a draft that cannot be
  // WRITTEN are different facts with different lifetimes -- and one state slot
  // let the write effect's success clear the read's finding a tick later, so
  // "the stored draft was discarded" never reached the operator at all.
  const [draftReadProblem, setDraftReadProblem] = useState<string | null>(null);
  const [draftWriteProblem, setDraftWriteProblem] = useState<string | null>(null);
  const draftProblem = draftWriteProblem ?? draftReadProblem;
  // The recompile check needs the CAST, which is a separate fetch from the
  // assertions (review M3). Restoring on the assertions alone compared the
  // draft's fingerprint against a `null` that only meant "not here yet", so on
  // the ordinary resolution order the warning was skipped -- and the write
  // effect then re-stamped the draft with the new fingerprint, destroying the
  // evidence for every later restore too.
  //
  // So: wait for the cast to have SETTLED, and pass three outcomes, not two --
  // a fingerprint, `null` for a bundle that publishes none, and `"unknown"`
  // when the read failed (LES-273).
  const castSettled = props.graph.cast !== null || props.graph.castError !== null;
  //
  // The bundle's CONTENT fingerprint (R4), never `code_fingerprint`: that is
  // the PIT row's build stamp, which a recompile does not move, so a check
  // keyed on it could never fire (fix round 1 of GAP-087..090). `?? null`
  // covers an older server that publishes no `bundle_fingerprint`.
  const castFingerprint: string | null | "unknown" =
    props.graph.cast !== null
      ? (props.graph.cast.bundle_fingerprint ?? null)
      : props.graph.castError !== null
        ? "unknown"
        : null;
  /**
   * The fingerprint the CURRENT draft was taken under -- the draft's own until
   * a save supersedes it (M3). Never the one observed at restore time: writing
   * that back would make the next restore compare the new build against itself
   * and report nothing, which is how one skipped warning became permanent.
   */
  // `undefined` = a legacy draft whose stamp is unknown; kept so until a save.
  const draftFingerprintRef = useRef<string | null | undefined>(null);
  /** The fingerprint observed RIGHT NOW, for `performSave` to stamp with.
   *  `performSave` is declared above this block, so a ref rather than a
   *  closure over `castFingerprint`. */
  const fingerprintNowRef = useRef<string | null | "unknown">(null);
  fingerprintNowRef.current = castFingerprint;
  /** True while a draft is KEPT but nothing of it was staged (review D): the
   *  write effect must leave the key alone until there is work to replace it
   *  with, or the operator discards. */
  const holdDraftRef = useRef(false);
  useEffect(() => {
    if (restoredRef.current || imprintId === null || !assertions.data || !castSettled) return;
    restoredRef.current = true;
    const { draft, problem } = readDraft(imprintId);
    if (problem !== null) setDraftReadProblem(problem);
    if (draft === null) {
      draftFingerprintRef.current = castFingerprint === "unknown" ? null : castFingerprint;
      return;
    }
    // Whatever the draft was taken under STAYS its fingerprint until a save.
    draftFingerprintRef.current = draft.bundle_fingerprint;
    const held = entriesFromDoc(assertions.data);
    const result = reconcileDraft(
      draft,
      {
        etag: assertions.data.etag,
        fingerprint: castFingerprint,
        heldIds: new Set(held.map((e) => e.id)),
      },
      entryId,
      { heldRecords: held, sameRecord, crossRecordConflict, validRecord: invalidStoredRecord },
    );
    if (result.restore.length > 0) {
      dispatch({
        type: "restoreDraft",
        entries: result.restore.map((r, i) => ({ ...r, gesture: result.gestures[i] })),
      });
    }
    // SAID either way: a draft that restored nothing is still a draft the
    // operator left behind, and silence would read as "there was none".
    setDraftNotice(restoreNotice(draft, result));
    // D: a draft refused by the WORLD is kept -- the operator can resolve the
    // conflict and want their copy back, and the notice offers *discard them*
    // either way. Only a draft that can never come back is dropped here.
    if (result.keepDraft) {
      // ...which means the WRITE effect below must not clear it: with nothing
      // pending it would delegate to `clearDraft` and erase exactly what this
      // branch just decided to keep. The hold lifts the moment there is
      // something pending to write, or the operator discards.
      holdDraftRef.current = result.restore.length === 0;
    } else {
      clearDraft(imprintId);
    }
  }, [imprintId, assertions.data, castSettled, castFingerprint]);

  // WRITE: the draft always mirrors the CURRENT pending set, so it is
  // self-healing -- a save empties it (pending becomes saved), an undo shrinks
  // it, and `writeDraft` clears the key when nothing is pending rather than
  // leaving an empty draft to offer next time. Held back until the restore has
  // run, or the first render after a reload would overwrite the draft with the
  // empty store it starts from.
  const pendingEntries = useMemo(() => edits.entries.filter((e) => e.state === "pending"), [edits.entries]);
  const draftEtag = edits.etag;
  useEffect(() => {
    if (!restoredRef.current || imprintId === null) return;
    if (holdDraftRef.current && pendingEntries.length === 0) return; // D
    holdDraftRef.current = false;
    const outcome = writeDraft({
      schema: DRAFT_SCHEMA,
      imprint: imprintId,
      saved_at: new Date().toISOString(),
      etag: draftEtag,
      // The draft's OWN fingerprint (M3), not the one observed now. An
      // `undefined` (legacy) one is left out of the JSON, so the draft STAYS
      // unknown rather than being silently re-stamped as current.
      bundle_fingerprint: draftFingerprintRef.current,
      entries: pendingEntries.map((e) => ({
        block: e.block,
        record: e.record,
        ...(e.gesture ? { gesture: e.gesture } : {}),
      })),
    });
    // Said ONCE, and only while there is something to lose: an operator with
    // no pending edits does not need to be told the store is unavailable.
    if (!outcome.ok && pendingEntries.length > 0) {
      setDraftWriteProblem(
        `these ${pendingEntries.length} unsaved edit${pendingEntries.length === 1 ? "" : "s"} are NOT being kept as a draft ` +
          `(${outcome.reason}) — they will be lost if you leave or reload. Save them.`,
      );
    } else if (outcome.ok || pendingEntries.length === 0) {
      // S7: with storage blocked, `writeDraft` on an EMPTY pending set
      // delegates to `clearDraft`, which fails too -- so neither branch cleared
      // the old text and "these 18 unsaved edits are NOT being kept" outlived
      // the edits it named. Nothing pending, nothing at risk, nothing to say.
      setDraftWriteProblem(null);
    }
  }, [imprintId, pendingEntries, draftEtag]);

  // `dropPinsOf` is declared further down (it needs `savePins`); the ref keeps
  // the discard path on the SAME function rather than a second copy of the rule.
  const dropPinsOfRef = useRef<(targets: readonly EditEntry[]) => void>(() => {});

  // Reported upward on every change, and reset to 0 when this workspace
  // unmounts -- an imprint switch remounts it (LES-259), and a stale count
  // would leave the shell guarding a store that no longer exists.
  const { onPendingCount, onWorkspaceGone } = props;
  useEffect(() => {
    if (imprintId !== null) onPendingCount(imprintId, pendingEntries.length);
  }, [onPendingCount, imprintId, pendingEntries.length]);
  useEffect(() => {
    return () => onWorkspaceGone();
  }, [onWorkspaceGone]);

  const discardDraft = useCallback(() => {
    if (imprintId === null) return;
    for (const e of pendingEntries) dispatch({ type: "discard", id: e.id });
    // S1: the SAME pin rule the change list's remove uses. Discarding a
    // restored clone without it reached exactly the orphan state iteration 8
    // disclosed -- a pin naming a segment no record adds.
    dropPinsOfRef.current(pendingEntries);
    holdDraftRef.current = false;
    clearDraft(imprintId);
    setDraftNotice(null);
  }, [imprintId, pendingEntries]);

  // The durable `applied` signal, `docs/design/15-console-canvas.md` §8: on a
  // plain load (there is no apply in S5 -- that is S6), the compiler's own
  // `population.suppressed[]` on `GET .../provenance` states which authored
  // suppressions the standing cast honors, with `matched: false` separating an
  // ineffective record from a real suppression. Declared AFTER the `loaded`
  // effect and keyed on `assertions.data` too, so on a simultaneous load it
  // runs against the just-loaded entries rather than an empty store.
  const provenanceData = props.provenance;
  useEffect(() => {
    const pop = provenanceData?.population;
    if (!pop || !Array.isArray(pop.suppressed)) return;
    const { applied, ineffective } = suppressedKeysFromPopulation(pop);
    dispatch({ type: "applied", block: "suppress", keys: applied, ineffective });
  }, [provenanceData, assertions.data]);

  // The save metadata `14` §3 requires on every save (`as_of`, `source`). The
  // server never invents `as_of` -- that would churn the file on a no-op save
  // and break determinism -- so the shell seeds it (the clock lives here, not
  // in the pure reducer) and the operator confirms it once. Seeded from the
  // file when it loads; `source` defaults to the `operator:` prefix so the
  // operator must still name themselves before a save is enabled.
  const [source, setSource] = useState("");
  const [asOf, setAsOf] = useState("");
  useEffect(() => {
    if (!assertions.data) return;
    setSource(assertions.data.source ?? DEFAULT_SOURCE);
    setAsOf(assertions.data.as_of ?? new Date().toISOString());
  }, [assertions.data]);

  // S4 pins live in <imprint>.layout.json, SEPARATE from the assertions/edit
  // store above (pins are layout, never evidence -- `15` §4). `layoutDoc` is
  // the authoritative pins + etag: seeded from the GET, advanced by each PUT's
  // reply, so a save keeps the etag fresh without re-reading (which would flash
  // a pinned tile back to its computed slot for the length of a request).
  const layout = useLayout(imprintId);
  const { save: saveLayout, reload: reloadLayout } = layout;
  const [layoutDoc, setLayoutDoc] = useState<LayoutDoc | null>(null);
  const docRef = useRef<LayoutDoc | null>(null);
  docRef.current = layoutDoc;
  /**
   * What THIS session has written, by key and value (R2/M1, final review S1).
   * It lives here, beside the document it describes, rather than in
   * `usePinDrag`: `Level0` unmounts on a drill to level 1, so progress toward
   * "re-place every one" would otherwise be silently lost on the way back.
   * A ref, so a rapid second drag reads the first one's entry rather than a
   * stale render's copy.
   */
  const authoredRef = useRef<Map<string, { x: number; y: number }>>(new Map());
  /** The last `PUT .../layout` failure, or `null` (review S3). Cleared by the
   *  next write that lands: the map states it beside the pin controls and marks
   *  the tiles whose position is now session-only. */
  const [pinSaveError, setPinSaveError] = useState<string | null>(null);
  useEffect(() => {
    if (layout.data) setLayoutDoc(layout.data);
  }, [layout.data]);

  const savePins = useCallback(
    async (pins: Pins, authored?: AuthoredPin): Promise<void> => {
      if (imprintId === null) return;
      const prev = docRef.current;
      if (authored) authoredRef.current.set(authored.key, authored.at);
      // A reset clears the slate: nothing survives to be re-authored, and
      // leaving entries behind would let a later drag inherit a claim.
      else if (Object.keys(pins).length === 0) authoredRef.current.clear();
      // M1 (LES-289): the stamp advances only when the set was WHOLLY
      // re-authored against the plane on screen -- matched by VALUE, so a pin
      // another writer replaced under us no longer counts as ours. The rule
      // and its reasoning live in `layout/plane.ts` beside the status reader
      // it has to agree with; a second copy here is how the two drift apart.
      const plane = planeForSave(pins, authoredRef.current, prev?.plane);
      // Optimistic: the pinned/reset positions show at once, then reconcile
      // when the PUT returns the authoritative etag.
      setLayoutDoc({
        imprint_id: imprintId,
        path: prev?.path ?? "",
        exists: true,
        etag: prev?.etag ?? null,
        schema: LAYOUT_SCHEMA,
        imprint: imprintId,
        pins: { ...pins },
        plane,
      });
      try {
        // The doc's `imprint` is the CANONICAL URL id the PUT path is keyed on
        // -- the same id `graph.imprintId` carries, never a producer-baked one
        // (LES-263) -- so the API's imprint-vs-path guard always agrees.
        const result = await saveLayout(
          { schema: LAYOUT_SCHEMA, imprint: imprintId, pins, plane },
          prev?.etag ?? null,
        );
        setLayoutDoc(result);
        setPinSaveError(null);
      } catch (err) {
        setLayoutDoc(prev); // roll the optimistic move back
        // SAID, not only rolled back (review S3). A clone's drop position IS
        // this write: the placement survives in session state so the tile does
        // not vanish under the operator, but it is then only in this browser
        // and reloading loses it -- which a silent catch left discoverable
        // solely in an `aria-label`.
        setPinSaveError(
          err instanceof ConsoleApiError
            ? `the position could not be saved (${err.kind}): positions placed since are held in this browser only, and reloading loses them`
            : "the position could not be saved: positions placed since are held in this browser only, and reloading loses them",
        );
        // A stale etag (a concurrent writer) needs a fresh read before the next
        // drag, not a silent retry that would clobber the other write.
        if (err instanceof ConsoleApiError && err.kind === "stale-etag") {
          reloadLayout();
        }
      }
    },
    [imprintId, saveLayout, reloadLayout],
  );

  // How many of the pins CURRENTLY on file are still the ones this session
  // wrote -- the same value-match `planeForSave` applies, so the note's "N of
  // M re-placed" cannot drift from the rule that clears it. Recomputed when
  // `layoutDoc` changes, which is every save (the ref is mutated first).
  const reauthoredCount = useMemo(() => {
    const pins = layoutDoc?.pins ?? {};
    let matched = 0;
    for (const [key, at] of authoredRef.current) {
      const now = pins[key];
      if (now && now.x === at.x && now.y === at.y) matched += 1;
    }
    return matched;
  }, [layoutDoc]);

  // ---- S5.1.b: clone -- placement, then the form, then the batch ------------
  // The draft belongs to ONE clone selection object: a new clone gesture makes a
  // new object, and any other selection makes this draft meaningless, so it is
  // read only while `draft.selection === focus` (never cleared in an effect,
  // which would leave a render holding a stale draft -- LES-259's rule).
  const [cloneDraft, setCloneDraft] = useState<{
    selection: Focus;
    proposed: Point;
    at: Point | null;
    cidr: string | null;
  } | null>(null);
  // Where each clone this session staged was placed -- the tile's position when
  // its pin has not been saved (a `PUT .../layout` that failed rolls the pin
  // back, and the tile must not vanish with it). A pin on file always wins.
  const [placedClones, setPlacedClones] = useState<ReadonlyMap<string, Point>>(() => new Map());
  const activeDraft = cloneDraft !== null && cloneDraft.selection === focus && focus?.kind === "clone" ? cloneDraft : null;
  const graphForLayout = graph.graph;
  const layoutOptions = useMemo(
    () => level0OptionsFor(filters),
    [filters.eastWestAll, filters.tileOrder, filters.expand],
  );
  // The map as drawn (saved pins and the URL's budgets) -- asked where a clone is
  // proposed to go and whether a collapsed group would hold it.
  const drawnLayout = useMemo(
    () => (graphForLayout ? layout0(graphForLayout, layoutDoc?.pins ?? NO_PINS, layoutOptions) : null),
    [graphForLayout, layoutDoc, layoutOptions],
  );
  // Stable: the clone form reports its CIDR from an effect keyed on it.
  const setCloneCidr = useCallback((cidr: string | null) => {
    setCloneDraft((d) => (d && d.cidr !== cidr ? { ...d, cidr } : d));
  }, []);
  const startClone = useCallback(
    (source: string, at: Point | null) => {
      const selection: Focus = { kind: "clone", source };
      const proposed = at ?? (drawnLayout ? proposedClonePlacement(drawnLayout, source) : { x: 0, y: 0 });
      setCloneDraft({ selection, proposed, at, cidr: null });
      onFocus(selection);
    },
    [drawnLayout, onFocus],
  );
  const cloneActions: CloneActions = {
    start: (source) => startClone(source, null),
    draft: activeDraft ? { proposed: activeDraft.proposed, at: activeDraft.at } : null,
    nudge: (dx, dy) =>
      setCloneDraft((d) => (d && d.at === null ? { ...d, proposed: { x: d.proposed.x + dx, y: d.proposed.y + dy } } : d)),
    // A NEW point object per placement: the form takes keyboard focus once per
    // placement, by identity (`views/ElementActions.tsx` `focusedPlacements`).
    place: () => setCloneDraft((d) => (d ? { ...d, at: { x: d.proposed.x, y: d.proposed.y } } : d)),
    replace: () => setCloneDraft((d) => (d ? { ...d, proposed: d.at ?? d.proposed, at: null } : d)),
    setCidr: setCloneCidr,
    stage: (records, gesture, cidr, at) => {
      // S4: the pin, the session placement and the selection hang off the SAME
      // pure decision the reducer makes, asked once here (`edit/store.ts`'s
      // `batchRefusal`). Dispatching and then committing them beside it left a
      // refused batch with a pin, a placement and a selection for records that
      // were never staged -- unreachable while `buildBatch` runs first, which is
      // precisely why an independent second check would have gone unnoticed.
      const refused = batchRefusal(edits.entries, records);
      dispatch({ type: "stageBatch", records, gesture });
      if (refused !== null) return; // the reducer recorded the reason
      setPlacedClones((prev) => new Map(prev).set(cidr, at));
      // The drop position IS an S4 pin on the new segment's key, committed
      // through the same writer a drag uses -- so the plane stamp's rule holds
      // (LES-296: it advances only when the whole set was re-authored, and this
      // gesture authored exactly this one pin).
      const key = segmentPinKey(cidr);
      void savePins({ ...(docRef.current?.pins ?? {}), [key]: at }, { key, at });
      setCloneDraft(null);
      onFocus({ kind: "segment", cidr });
    },
    heldIn: (cidr, at) => {
      if (!drawnLayout || !graphForLayout) return null;
      const observed = graphForLayout.segments.map((s) => s.observedCidr);
      const held = placeAddedTiles(
        drawnLayout,
        [...observed, ...addedSegmentsOf(edits.entries, observed).map((a) => a.cidr)],
        [{ cidr, fallback: at }],
        NO_PINS,
      ).held[0];
      return held ? { siteKey: held.siteKey, siteLabel: held.siteLabel } : null;
    },
    expand: (siteKey) => {
      if (!zoom) return;
      const expand = [...new Set([...filters.expand, siteKey])].sort();
      go(routeFor(zoom, { ...filters, expand }));
    },
    mapShown: zoom?.level === "network",
  };

  // The assertions save path (S5). `PUT .../assertions` with the etag the
  // store holds; the store translates the outcome (`edit/store.ts`). A 412 is
  // the stale-etag case: report + gate a reload, NEVER adopt the server's etag
  // (LES-251). Any other failure is `saveFailed`. There is NO apply here (S6).
  const { save: saveAssertions } = assertions;
  const base = useCallback(
    () => ({
      schema: ASSERTIONS_SCHEMA,
      imprint: imprintId ?? "",
      as_of: asOf,
      source,
    }),
    [imprintId, asOf, source],
  );
  const performSave = useCallback(
    async (
      document: Record<string, unknown>,
      etag: string | null,
      onSuccess?: () => void,
    ): Promise<AssertionsDoc | null> => {
      // Returns the saved document on success (S6's apply chains on the fresh
      // etag it carries) and `null` on any refusal -- the conflict/failed
      // dispatch has already surfaced the reason, so a caller only needs to
      // know whether to proceed.
      dispatch({ type: "saveStarted" });
      try {
        const result = await saveAssertions(document, etag);
        onSuccess?.();
        dispatch({ type: "saveSucceeded", doc: result });
        // A SAVE supersedes the draft's fingerprint (M3): whatever is staged
        // after this was staged against the build on screen now. Advanced HERE,
        // in the one continuation that means "a save happened" -- an effect
        // watching `edits.etag` advanced it on the INITIAL LOAD's own etag too,
        // which made the recompile warning one-shot on exactly the slow-cast
        // path M3 was about.
        if (fingerprintNowRef.current !== "unknown") draftFingerprintRef.current = fingerprintNowRef.current;
        return result;
      } catch (cause) {
        const err = asApiError(cause);
        if (err.kind === "stale-etag") {
          dispatch({ type: "saveConflict", message: err.message, serverEtag: err.etag });
        } else {
          dispatch({ type: "saveFailed", message: err.message });
        }
        return null;
      }
    },
    [saveAssertions],
  );

  const onStageRecord = useCallback(
    (block: AssertionBlock, record: AssertionRecord) =>
      dispatch({ type: "stage", block, record }),
    [],
  );

  // S5.1.b/c: a BATCH is one reducer action, so the store takes all of it or
  // none of it -- never a render holding half a clone.
  //
  // The id must outlive the MOUNT, not merely the workspace: the draft restores
  // each record's whole gesture tag, so a per-mount counter collides with a
  // restored batch (review M2). `edit/gestures.ts` owns the rule.
  const mintGestureId = useCallback((): string => newGestureId(), []);
  const onStageBatch = useCallback(
    (records: readonly BatchRecord[], gesture: GestureTag) => dispatch({ type: "stageBatch", records, gesture }),
    [],
  );

  const onSaveAssertions = useCallback(() => {
    // The same gate the Save button enforces (`metadataReady`), applied at the
    // handler too so a caller cannot PUT metadata the server would refuse.
    if (!canSave(edits) || !metadataReady(source, asOf)) return;
    void performSave(documentFrom(edits, base()), edits.etag);
  }, [edits, base, performSave, source, asOf]);

  /**
   * Drop the PINS of removed added segments (S5.1.d, closing iteration 8's
   * §19.5 residual).
   *
   * A clone's drop position is an S4 pin on `segment:<cidr>`. Removing its
   * records left the pin behind: inert (nothing draws a tile for a segment no
   * record adds) but counted by the pin bar, which then read "1 pinned" over a
   * map with no pinned tile. The pin belongs to the gesture, so an undo of the
   * gesture takes it. Only pins of segments THIS removal takes out of play are
   * dropped -- a pin from an older session that names nothing stays, and the
   * bar's own orphan disclosure keeps naming it, because nothing here can tell
   * whether the operator will re-add that segment.
   *
   * The write goes through the SAME `savePins` a drag uses, so the plane stamp's
   * rule holds (LES-296): removing a pin re-authors nothing.
   */
  const dropPinsOf = useCallback(
    (targets: readonly EditEntry[]) => {
      const gone = targets
        .filter((t) => t.block === "add" && t.record.kind === "segment" && t.record.key)
        .map((t) => segmentPinKey((t.record.key ?? "").trim()));
      if (gone.length === 0) return;
      const pins = docRef.current?.pins ?? {};
      const held = gone.filter((key) => key in pins);
      // The session placement goes either way: it is this browser's memory of
      // where a clone was dropped, and the clone is going.
      setPlacedClones((prev) => {
        const next = new Map(prev);
        let changed = false;
        for (const t of targets) {
          const cidr = (t.record.key ?? "").trim();
          if (t.block === "add" && t.record.kind === "segment" && next.delete(cidr)) changed = true;
        }
        return changed ? next : prev;
      });
      if (held.length === 0) return;
      const remaining = { ...pins };
      for (const key of held) delete remaining[key];
      void savePins(remaining);
    },
    [savePins],
  );
  dropPinsOfRef.current = dropPinsOf;

  // Remove records TOGETHER -- one, or a segment with the records it is the
  // only home of (fix round, SF2).
  // (`dropPinsOfRef` is filled just below, so the draft discard above and the
  // removals here drop pins by one rule.) Pending records only were never in the file,
  // so they are discarded outright; if ANY is saved/applied, the file is
  // re-saved WITHOUT the whole group and the group is discarded only on the
  // server's ack -- a 412 leaves every one of them in place, so a segment and
  // its hosts can never end up half-removed.
  const onRemoveEntries = useCallback(
    (targets: readonly EditEntry[]) => {
      if (targets.length === 0) return;
      const ids = new Set(targets.map((t) => t.id));
      const discardAll = (): void => {
        for (const t of targets) dispatch({ type: "discard", id: t.id });
        dropPinsOf(targets);
      };
      if (targets.every((t) => t.state === "pending")) {
        discardAll();
        return;
      }
      // The re-save shares Save's gate (S2): never PUT metadata Save refuses.
      if (!canSave(edits) || !metadataReady(source, asOf)) return;
      // Build the removal document from the FILE's records (saved + applied)
      // minus the removed ones -- NEVER from pending entries (S1). `documentFrom`
      // collects every entry regardless of state, so including pending here
      // would commit the operator's unsaved, browser-only edits as a
      // side-effect of a remove, breaking the "pending = browser only" boundary
      // (`docs/design/15-console-canvas.md` §8).
      const next = {
        ...edits,
        entries: edits.entries.filter((e) => !ids.has(e.id) && e.state !== "pending"),
      };
      void performSave(documentFrom(next, base()), edits.etag, discardAll);
    },
    [edits, base, performSave, source, asOf, dropPinsOf],
  );

  // SF2: the homes a record's segment may have are the ones the compiler
  // resolves against -- the observed segments and this file's adds -- never
  // the installed cast's roster (relocated, and never consulted). The observed
  // set is known only from a graph: while the topology loads, or on a bundle
  // with no export, it is `null`, and then nothing is called an orphan.
  const observedCidrs = useMemo(
    () => (graph.graph !== null ? new Set(graph.graph.segments.map((seg) => seg.observedCidr)) : null),
    [graph.graph],
  );
  const orphans = useMemo(
    () => orphanReasons(edits.entries, { observed: observedCidrs }),
    [edits.entries, observedCidrs],
  );

  // ---- S6: apply (save + recompile + refresh) ------------------------------
  // `15` §12 O5: highlight what changed for five seconds, then the `?changed=1`
  // filter keeps the changed set reachable. `changeSet` is the last apply's
  // map-highlightable delta (observed segment CIDRs + node ids); it survives
  // intra-imprint navigation because this workspace is keyed by imprint (a
  // switch remounts it fresh). `glow` is the transient window on top.
  const [changeSet, setChangeSet] = useState<ChangeSet>(EMPTY_CHANGE_SET);
  const [glow, setGlow] = useState(false);
  // MUST-FIX 1: whether the diff BEHIND `changeSet` was actually computed.
  // `changeSet` alone cannot carry this -- it is `EMPTY_CHANGE_SET` both when
  // the diff came back genuinely empty AND when it could not be computed at
  // all (no history snapshot written, or the comparison call itself failed)
  // -- so a `changed: true` apply must not let the second case read as the
  // first's "nothing moved" (LES-240's don't-know-vs-no rule, applied here to
  // a derived UI signal rather than a wire field).
  const [diffAvailable, setDiffAvailable] = useState(true);
  // GAP-087: the TWIN side's host churn from the same diff, kept beside
  // `changeSet` rather than inside it -- `changeSet` is observed-keyed
  // (`model/changes.ts`) and a twin id names nothing this map draws, so
  // folding these in would add keys that can never highlight. It exists only
  // to SIZE a compiler-change outcome. `null` = that side was not compared.
  const [hostChurn, setHostChurn] = useState<TwinHostChurn | null>(null);
  useEffect(() => {
    if (!glow) return;
    const t = setTimeout(() => setGlow(false), HIGHLIGHT_MS);
    return () => clearTimeout(t);
  }, [glow]);

  // SHOULD-FIX 2: guards a stale apply's navigation. `go`/`navigate` belongs
  // to the never-unmounted `ConsoleShell` -- only THIS workspace remounts,
  // keyed by imprint (LES-259) -- so an apply that outlives the operator's
  // next navigation must not yank the browser back to where it started.
  // Mirrors `api/hooks.ts`'s `useResource` "abort on unmount" discipline:
  // `liveRef` catches an IMPRINT switch (this instance unmounts; a brand new
  // instance gets its own fresh ref, so this one's frozen value is never read
  // again), and `pathnameRef` catches an in-imprint LEVEL change (this
  // instance survives, so the ref keeps advancing on every render). Checked
  // once, immediately before the one call with an externally visible side
  // effect -- `go(...)` in `onApply` below -- never before the dispatch/
  // setState calls, which stay correct (and harmless if unmounted) regardless
  // of which route ends up showing.
  const liveRef = useRef(true);
  useEffect(() => {
    liveRef.current = true;
    return () => {
      liveRef.current = false;
    };
  }, []);
  const pathnameRef = useRef(pathname);
  pathnameRef.current = pathname;

  const { run: runApply, running: applying } = apply;
  const { reloadCast } = graph;
  const { reload: reloadAssertions } = assertions;
  const { onReloadProvenance } = props;
  const onApply = useCallback(async () => {
    if (imprintId === null) return;
    // Apply shares Save's gate (metadata valid, not mid-conflict) and refuses
    // to start while one is already in flight.
    if (!canSave(edits) || !metadataReady(source, asOf) || applying) return;

    // SHOULD-FIX 2: the pathname THIS apply started from -- compared against
    // the live `pathnameRef` right before the one navigating call at the end.
    const startedFromPathname = pathname;

    // 1. SAVE FIRST (`14` §4: apply IS save + recompile). When there are unsaved
    //    pending edits, persist them via the S5 PUT before the recompile POST --
    //    so the operator's work survives even a failed compile, and the call
    //    order is exactly PUT then POST. The POST then carries the fresh etag the
    //    PUT returned.
    let etagForApply = edits.etag;
    if (hasUnsavedEdits(edits)) {
      const saved = await performSave(documentFrom(edits, base()), edits.etag);
      if (!saved) return; // a conflict/failure already surfaced; do NOT recompile
      etagForApply = saved.etag;
    }

    // 2. RECOMPILE the whole file (apply applies the whole file, `15` §12). The
    //    inline `etag` is dropped so the server's three-way etag check
    //    (`_etag_from`) can never see a stale embedded value disagree with the
    //    fresh header/body one; the guard rides on `etagForApply` alone.
    const doc = documentFrom(edits, base());
    delete (doc as Record<string, unknown>).etag;
    setGlow(false);
    let res: ApplyResponse;
    try {
      res = await runApply({ document: doc, etag: etagForApply });
    } catch {
      return; // `apply.error` holds the ConsoleApiError; the panel shows its stderr
    }
    // Advance the store's etag from the apply's OWN returned file (idempotent
    // with the PUT above) so the next write is not refused as stale.
    dispatch({ type: "saveSucceeded", doc: res.assertions });

    if (!res.changed) {
      // A no-op recompile -- byte-identical bundle, the determinism check
      // (`14` §3). No highlight and no `?changed=1`; the panel reads "no change"
      // straight off `res.changed`, never consulting `diffAvailable` -- reset
      // here anyway so no stale value from a PRIOR apply lingers.
      setChangeSet(EMPTY_CHANGE_SET);
      setDiffAvailable(true);
      // No diff is fetched on this path, so the twin's churn is genuinely
      // unknown here -- reset rather than left holding a PRIOR apply's
      // numbers, which `CompilerChange` would then size itself by.
      setHostChurn(null);
      return;
    }

    // 3. REFRESH + HIGHLIGHT. Re-read the parts a recompile rewrites -- the cast
    //    and the provenance sidecar; the observed topology is immutable, so it
    //    is NOT reloaded (which would blink the map for no new data). Learn WHAT
    //    changed from the diff since the pre-apply fingerprint, glow it for five
    //    seconds, and turn on `?changed=1` so the set stays reachable after.
    reloadCast();
    onReloadProvenance();
    // Refreshes `assertions.data` ITSELF (read directly by `MapPane`/
    // `Inspector`/`EditPanel`'s `assertions` prop) -- NOT redundant with the
    // `saveSucceeded` dispatch above, which only updates the separate edit
    // STORE (`edits`); `assertions.data` has no other way to pick up the
    // freshly-applied file's content (NIT 4).
    reloadAssertions();
    let set = EMPTY_CHANGE_SET;
    // MUST-FIX 1: `diffOk` tracks whether the diff was actually COMPUTED, not
    // just whether it came back empty -- `ApplyStatus` (`views/EditPanel.tsx`)
    // must render a different, honest message when it is false.
    let diffOk = true;
    // GAP-087: read off the SAME diff, never a second fetch. `null` until one
    // arrives, so a failed or absent comparison sizes nothing.
    let churn: TwinHostChurn | null = null;
    if (res.history_written && res.fingerprint_before) {
      try {
        const diff = await api.getDiff(imprintId, res.fingerprint_before);
        set = changeSetFromDiff(diff);
        churn = twinHostChurn(diff);
      } catch {
        // The pre-apply snapshot is gone (e.g. a pruned/unknown fingerprint):
        // changed, but not pinpointable -- NOT the same as "nothing moved".
        diffOk = false;
      }
    } else {
      // No history snapshot was written for this apply -- `res.warnings`
      // carries why (`axrange/api/service.py`'s bookkeeping never fails the
      // apply itself over it). `GET .../diff?since=` has nothing to compare.
      diffOk = false;
    }
    setChangeSet(set);
    setDiffAvailable(diffOk);
    setHostChurn(churn);
    setGlow(true);
    // SHOULD-FIX 2: skip ONLY the navigation if the operator has since moved
    // on -- to another imprint (`liveRef` false, this workspace unmounted) or
    // another level within this one (`pathnameRef` has advanced past
    // `startedFromPathname`). The state above still applies regardless: the
    // highlight/etag are correct wherever the operator ended up.
    if (zoom && liveRef.current && pathnameRef.current === startedFromPathname) {
      go(routeFor(zoom, { ...filters, changed: true }));
    }
  }, [
    imprintId, edits, source, asOf, applying, base, performSave, runApply,
    reloadCast, onReloadProvenance, reloadAssertions, api, zoom, filters, go, pathname,
  ]);

  // ---- Escape (`15` §7) ------------------------------------------------------
  // Up a level -- but first an explicit selection is cleared (when clearing it
  // changes anything: one equal to the URL's own subject costs no press), and
  // a focus inside the actions frame steps out to the map.
  //
  // T5: going up from the network level LEAVES the imprint -- the catalog --
  // and this workspace, with every pending edit it holds, is discarded on the
  // way (LES-259). Escape is never the gesture that discards unsaved work: with
  // pending edits it stays put and says, in a live region, what is holding it.
  // (Other ways out -- the breadcrumb, the outline's imprint list, the browser's
  // back button -- still discard them; there is no draft persistence yet.)
  const [leaveNotice, setLeaveNotice] = useState<string | null>(null);
  useEffect(() => {
    setLeaveNotice(null);
  }, [pathname, edits.entries]);
  const urlSelection = resolveSelection(zoom, null);
  const escapeClears = selectionFromFocus(focus) !== null && !sameSelection(selectionFromFocus(focus), urlSelection);
  const { onClearSelection } = props;
  useEffect(() => {
    function onKey(event: KeyboardEvent): void {
      if (event.key !== "Escape" || !zoom) return;
      if (isEditable(event.target)) return;
      if (escapeClears) {
        event.preventDefault();
        onClearSelection();
        return;
      }
      if (event.target instanceof Element && event.target.closest(ACTIONS_FRAME)) {
        event.preventDefault();
        focusMapPane();
        return;
      }
      const parent = parentOf(zoom);
      if (!parent) return;
      if (parent.level === "catalog" && hasUnsavedEdits(edits)) {
        event.preventDefault();
        const n = entriesInState(edits, "pending").length;
        setLeaveNotice(
          `${n} unsaved edit${n === 1 ? "" : "s"} — save or discard ${n === 1 ? "it" : "them"} ` +
            "before leaving this imprint. Escape does not discard unsaved work.",
        );
        return;
      }
      go(routeFor(parent, filters));
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [zoom, filters, go, escapeClears, onClearSelection, edits]);

  // S5.1.a: WHAT IS SELECTED decides which gestures the inspector offers. An
  // explicit in-pane selection (a tile's select control, a spine, a site `+`,
  // an added segment's outline row) wins; otherwise the element the URL already
  // names is the subject -- drilling in from the map, the outline or search
  // selects as it opens, which is how a segment R6's budget collapsed reaches
  // the same actions with no tile to click.
  const selection = resolveSelection(zoom, focus);
  const explicitSelection = selectionFromFocus(focus);
  const selectedCidr = selection?.kind === "segment" ? selection.cidr : null;
  // A3: the segments the store ADDS that the export does not observe --
  // selectable and actionable before the map can draw them.
  const addedSegments = useMemo(
    () => addedSegmentsOf(edits.entries, (graph.graph?.segments ?? []).map((s) => s.observedCidr)),
    [edits.entries, graph.graph],
  );
  const selectSegment = useCallback(
    (cidr: string) => onFocus({ kind: "segment", cidr } satisfies ExplicitSelection),
    [onFocus],
  );
  // S5.1.b: the added segments as level 0 draws them. Only a PLACED one is
  // drawn -- a pin on file, or a clone's placement this session.
  const savedPinsNow = layoutDoc?.pins ?? NO_PINS;
  const addedTiles = useMemo<AddedTileView[]>(
    () =>
      addedSegments.map((a) => ({
        cidr: a.cidr,
        name: a.name,
        state: a.state,
        cloneOf: a.cloneOf,
        hostCount: edits.entries.filter(
          (e) => e.block === "add" && e.record.kind === "host" && (e.record.segment ?? "").trim() === a.cidr,
        ).length,
        fallback: placedClones.get(a.cidr) ?? null,
      })),
    [addedSegments, edits.entries, placedClones],
  );
  // S5.1.d: the hosts this file adds, for level 1's hatched chips. Every
  // `add: host`, whatever segment it names -- the view picks its own.
  const addedHosts = useMemo(
    () =>
      edits.entries
        .filter((e) => e.block === "add" && e.record.kind === "host")
        .map((e) => ({
          key: e.record.key ?? "",
          role: e.record.role ?? null,
          state: e.state,
          segment: (e.record.segment ?? "").trim(),
        })),
    [edits.entries],
  );
  const placedCidrs = useMemo(
    () => new Set(addedTiles.filter((t) => t.fallback !== null || savedPinsNow[segmentPinKey(t.cidr)]).map((t) => t.cidr)),
    [addedTiles, savedPinsNow],
  );
  // Memoized on the draft itself: the map frames its viewBox off this object,
  // and a fresh one on every workspace render would re-run that layout for
  // nothing.
  const cloneDraftView = useMemo<CloneDraftView | null>(
    () =>
      activeDraft && focus?.kind === "clone"
        ? {
            source: focus.source,
            at: activeDraft.at ?? activeDraft.proposed,
            placed: activeDraft.at !== null,
            cidr: activeDraft.cidr,
          }
        : null,
    [activeDraft, focus],
  );
  const elementActions =
    imprintId && zoom && zoom.level !== "catalog" ? (
      <ElementActions
        graph={graph.graph}
        graphState={{ loading: graph.loading, error: graph.error, absent: graph.topologyAbsent }}
        selection={selection}
        explicit={explicitSelection}
        // `clear` only where it changes something: dropping an explicit
        // selection that IS the URL's own subject would redraw the same frame.
        canClear={
          explicitSelection !== null &&
          !sameSelection(explicitSelection, resolveSelection(zoom, null))
        }
        edits={edits}
        onStage={onStageRecord}
        onClear={props.onClearSelection}
        onSelectSegment={selectSegment}
        onStageBatch={onStageBatch}
        newGestureId={mintGestureId}
        clone={cloneActions}
        placedCidrs={placedCidrs}
        handAuthored={
          graph.cast && isHandAuthored(graph.cast)
            ? { configFile: configFileOf(graph.cast), twinId: graph.cast.twin_config_id }
            : null
        }
      />
    ) : undefined;

  const editPanel =
    imprintId && zoom && zoom.level !== "catalog" ? (
      <EditPanel
        edits={edits}
        assertions={assertions.data}
        saving={assertions.saving}
        onRemove={onRemoveEntries}
        orphans={orphans}
        observed={observedCidrs}
        onSave={onSaveAssertions}
        onReload={assertions.reload}
        onApply={onApply}
        applying={applying}
        applyResult={apply.result}
        applyError={apply.error}
        applyBlockedReason={applyBlockedReason(props.imprintEntry, apply.error)}
        changedCount={changeSetSize(changeSet)}
        diffAvailable={diffAvailable}
        hostChurn={hostChurn}
        source={source}
        onSourceChange={setSource}
        asOf={asOf}
        onAsOfChange={setAsOf}
        draftNotice={draftNotice}
        draftProblem={draftProblem}
        onDiscardDraft={discardDraft}
      />
    ) : undefined;

  return (
    <>
      {props.outline && (
        <Outline
          catalog={props.outline.catalog}
          error={props.outline.error}
          loading={props.outline.loading}
          currentImprint={imprintId}
          graph={graph.graph}
          zoom={zoom}
          go={go}
          // S5.1.a: the outline's site rows carry the same `+` the map's site
          // headers do, and it lists every site and segment -- including the
          // groups R6's per-group budget collapsed on the map, and (A3) the
          // segments this file adds, which the map does not draw yet.
          onAddSegment={(siteKey) => onFocus({ kind: "site-add", siteKey })}
          added={addedSegments}
          onSelectSegment={selectSegment}
          selectedCidr={selectedCidr}
        />
      )}
      <MapPane
        zoom={zoom}
        filters={filters}
        pathname={pathname}
        graph={graph}
        provenance={props.provenance}
        provenanceError={props.provenanceError}
        provenanceLoading={props.provenanceLoading}
        assertions={assertions.data}
        savedPins={layoutDoc?.pins ?? NO_PINS}
        onSavePins={imprintId ? savePins : undefined}
        pinsPlane={layoutDoc?.plane ?? null}
        reauthoredCount={reauthoredCount}
        pinSaveError={pinSaveError}
        changeSet={changeSet}
        changeGlow={glow}
        go={go}
        onFocus={onFocus}
        selectedCidr={selectedCidr}
        addedSegments={addedSegments}
        addedTiles={addedTiles}
        addedHosts={addedHosts}
        cloneDraft={cloneDraftView}
        onPlaceClone={(at) =>
          setCloneDraft((d) => (d && d.selection === focus ? { ...d, proposed: at, at: { x: at.x, y: at.y } } : d))
        }
        onCloneDrop={(source, at) => startClone(source, at)}
      />
      <Inspector
        zoom={zoom}
        filters={filters}
        edits={edits}
        assertions={assertions.data}
        graph={graph.graph}
        declared={graph.declared}
        focus={focus}
        onClearFocus={() => onFocus(null)}
        actions={elementActions}
        editPanel={editPanel}
        status={leaveNotice}
      />
    </>
  );
}

/** The breadcrumb. Its links go through `go` (S5.1.d) rather than navigating
 *  themselves, so the one leave-guard covers them: a `<Link>` that navigates on
 *  its own is exactly the exit GAP-094 was about. They stay real anchors --
 *  middle-click, copy-link and the status bar all keep working. */
function Breadcrumb({ zoom, filters, go }: { zoom: Zoom | null; filters: Filters; go: (path: string) => void }): JSX.Element {
  if (!zoom) return <span className="breadcrumb">unrecognized route</span>;
  const chain: Zoom[] = [];
  for (let step: Zoom | null = zoom; step; step = parentOf(step)) {
    chain.unshift(step);
  }
  return (
    <nav className="breadcrumb" aria-label="Breadcrumb">
      {chain.map((step, i) => {
        const last = i === chain.length - 1;
        const label = crumbLabel(step);
        const to = routeFor(step, filters);
        return (
          <span key={`${step.level}:${label}`}>
            {i > 0 && <span className="sep"> / </span>}
            {last ? (
              <span className="here">{label}</span>
            ) : (
              <Link
                to={to}
                onClick={(event) => {
                  // A modified click is the browser's (new tab, download): leave
                  // it alone -- it opens elsewhere and loses nothing here.
                  if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                  if (event.button !== 0) return;
                  event.preventDefault();
                  go(to);
                }}
              >
                {label}
              </Link>
            )}
          </span>
        );
      })}
    </nav>
  );
}

/** The actions frame, as the Escape handler and the opener bookkeeping find it
 *  -- a dedicated marker attribute, never a test id driving behaviour. */
const ACTIONS_FRAME = "[data-actions-frame]";

/** Put keyboard focus on the map pane -- the fallback when the control that
 *  opened a selection is gone. The pane is focusable for exactly this. */
function focusMapPane(): void {
  const pane = document.querySelector<HTMLElement>(".pane-map");
  pane?.focus();
}

function isEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

function crumbLabel(zoom: Zoom): string {
  switch (zoom.level) {
    case "catalog":
      return "imprints";
    case "network":
      return zoom.imprint;
    case "segment":
      return zoom.cidr;
    case "host":
      return zoom.key;
    case "matrix":
      return zoom.cidr ? `${zoom.cidr} matrix` : "matrix";
  }
}

/**
 * Why Apply would be refused, said BEFORE the click (Unified Console ruling
 * §2) -- or `null` when nothing known stands in the way.
 *
 * The server says so itself: `can_apply` / `apply_blocked_reason` on the
 * imprint's catalog entry (a `twins/` folder, aximprint not configured, …). A
 * server that predates those fields publishes neither and nothing is guessed;
 * a refusal it then returns as 503 is kept beside the button from then on.
 */
export function applyBlockedReason(
  entry: Pick<ImprintListItem, "can_apply" | "apply_blocked_reason"> | null,
  error: ConsoleApiError | null,
): string | null {
  if (entry && entry.can_apply === false) {
    return entry.apply_blocked_reason
      ? `Apply is not available: ${entry.apply_blocked_reason}`
      : "Apply is not available for this imprint (the server gives no reason). Save still writes the assertions file.";
  }
  if (error && error.kind === "unavailable") {
    return `Apply is not available on this server: ${error.message}`;
  }
  return null;
}
