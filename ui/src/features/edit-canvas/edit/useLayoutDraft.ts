/**
 * The layout side-car (`axr-layout/1`: positions and annotations) as a DRAFT:
 * every move, align, placement and annotation change lands on the canvas
 * and counts as unsaved; the document is written only by `save` -- one PUT
 * carrying everything as it stands, the etag the document was read under as
 * the precondition -- never a write per drag. `discard` returns to the layout
 * as last loaded or saved. On a 412 the fresh document's etag is taken so the
 * next `save` replaces the other console's layout with this one, said so.
 *
 * What counts as unsaved (2026-11-20, round 2) is a DIFF, not a tally of gestures: the pins, the
 * annotations and the mode as they stand against the document as last loaded or saved. A pin whose
 * node no longer exists (`isLive` says no -- removed, or undone away) is dormant: it is kept, so a
 * redo brings its node back where it stood, but it is not a change, and it is not "anything on the
 * canvas" (`livePins`). The page's leave guard and the layout bar read this one rule.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError } from "../../../api/client";
import type { Api } from "../../../api/discovery";
import type { LayoutAnnotation, LayoutDoc, LayoutPins, LayoutWrite } from "../../../api/types";
import { CANVAS_PLANE } from "../canvas/plane";
import { NO_PINS, type Pins } from "../layout/pins";
import { fromLayoutList, toLayout, type Annotation } from "./annotations";

export const LAYOUT_SCHEMA = "axr-layout/1";

/**
 * The PUT body of a save: every pin as it stands (the document's own pins
 * when they were authored on THIS plane -- another plane's coordinates are
 * not re-saved as this drawing's -- plus every move since), the annotation
 * list as it stands, the plane stamp, and the etag as the precondition.
 */
export function layoutWrite(current: LayoutDoc | null, id: string, pins: LayoutPins, annotations: readonly LayoutAnnotation[], mode: LayoutMode | null = current?.mode ?? null): LayoutWrite {
  const out: LayoutWrite = { schema: LAYOUT_SCHEMA, imprint: id, pins: { ...pins }, plane: CANVAS_PLANE, etag: current?.etag ?? null };
  if (annotations.length) out.annotations = [...annotations];
  if (mode) out.mode = mode;
  return out;
}

/** The canvas's two layouts (B2): the device tree, or free (every box where it was dropped, edges point to point). */
export type LayoutMode = "tree" | "free";

/** The pins a loaded document contributes: its own when authored on this plane. */
export function pinsOf(doc: LayoutDoc | null): Pins {
  return doc && doc.plane === CANVAS_PLANE ? doc.pins : NO_PINS;
}

/** How many pin keys differ between two pin sets (added, removed or moved), counting only the keys `isLive` keeps. */
export function pinChanges(a: Readonly<Record<string, { x: number; y: number }>>, b: Readonly<Record<string, { x: number; y: number }>>, isLive: (key: string) => boolean = () => true): number {
  let n = 0;
  for (const k of new Set([...Object.keys(a), ...Object.keys(b)])) {
    if (!isLive(k)) continue;
    const p = a[k];
    const q = b[k];
    if (!p || !q || p.x !== q.x || p.y !== q.y) n++;
  }
  return n;
}

/** How many annotations differ between two lists (added, removed or changed), by id. */
function annotationChanges(a: readonly LayoutAnnotation[], b: readonly LayoutAnnotation[]): number {
  const byId = (xs: readonly LayoutAnnotation[]) => new Map(xs.map((x) => [x.id, JSON.stringify(x)]));
  const A = byId(a);
  const B = byId(b);
  let n = 0;
  for (const k of new Set([...A.keys(), ...B.keys()])) if (A.get(k) !== B.get(k)) n++;
  return n;
}

export interface LayoutDraft {
  /** The document as last loaded or saved (its etag is the next save's precondition). */
  doc: LayoutDoc | null;
  pins: Pins;
  annotations: Annotation[];
  /** What differs from the document as last loaded or saved: live pins, annotations, the mode. */
  changes: number;
  /** The pins whose node still exists (`isLive`): what is on the canvas, the dormant ones left out. */
  livePins: Pins;
  saving: boolean;
  error: unknown;
  /** What a failed save means for the next one, when there is something to say. */
  note: string | null;
  savedAt: string | null;
  /** The layout mode chosen on this canvas (saved with the layout), or null when none was chosen. */
  mode: LayoutMode | null;
  /** Choose the layout mode: one unsaved change, written by Save layout. */
  setMode(mode: LayoutMode): void;
  onPin(key: string, at: { x: number; y: number }): void;
  /** Several pins at once (a bus reorder re-ranks its site's lanes): one change. */
  onPinMany(pins: Record<string, { x: number; y: number }>): void;
  setAnnotations(list: Annotation[]): void;
  save(): Promise<boolean>;
  discard(): void;
  /** Drop every position override: the tree lays itself out again (one unsaved change). */
  relayout(): void;
}

export function useLayoutDraft(api: Pick<Api, "putLayout" | "getLayout">, id: string, loaded: LayoutDoc | null, isLive: ((key: string) => boolean) | null = null): LayoutDraft {
  const [doc, setDoc] = useState<LayoutDoc | null>(null);
  // What the draft is measured against: the document as last loaded, saved or discarded to (a 412
  // takes the other console's etag into `doc`, not its pins into the base).
  const [base, setBase] = useState<LayoutDoc | null>(null);
  const [pins, setPins] = useState<Pins>(NO_PINS);
  const [annotations, setAnnotationsState] = useState<Annotation[]>([]);
  // Bumped by every gesture: only to re-render; what is unsaved is the diff below.
  const [, setChanges] = useState(0);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [note, setNote] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [mode, setModeState] = useState<LayoutMode | null>(null);

  // A (re)load replaces the draft.
  useEffect(() => {
    if (!loaded) return;
    setDoc(loaded);
    setBase(loaded);
    setPins(pinsOf(loaded));
    setAnnotationsState(fromLayoutList(loaded.annotations));
    setModeState(loaded.mode ?? null);
    setChanges(0);
    setNote(null);
  }, [loaded]);

  const docRef = useRef<LayoutDoc | null>(null);
  docRef.current = doc;
  const pinsRef = useRef<Pins>(NO_PINS);
  pinsRef.current = pins;
  const annotationsRef = useRef<Annotation[]>([]);
  annotationsRef.current = annotations;
  const modeRef = useRef<LayoutMode | null>(null);
  modeRef.current = mode;
  const setMode = useCallback((m: LayoutMode) => {
    setModeState(m);
    setChanges((n) => n + 1);
  }, []);

  const onPin = useCallback((key: string, at: { x: number; y: number }) => {
    setPins((p) => ({ ...p, [key]: at }));
    setChanges((n) => n + 1);
  }, []);
  const onPinMany = useCallback((many: Record<string, { x: number; y: number }>) => {
    setPins((p) => ({ ...p, ...many }));
    setChanges((n) => n + 1);
  }, []);
  const setAnnotations = useCallback((list: Annotation[]) => {
    setAnnotationsState(list);
    setChanges((n) => n + 1);
  }, []);

  const save = useCallback(async (): Promise<boolean> => {
    setSaving(true);
    setError(null);
    setNote(null);
    try {
      const saved = await api.putLayout(id, layoutWrite(docRef.current, id, pinsRef.current, annotationsRef.current.map(toLayout), modeRef.current));
      setDoc(saved);
      setBase(saved);
      setChanges(0);
      setSavedAt(new Date().toISOString());
      return true;
    } catch (e) {
      setError(e);
      if (e instanceof ApiError && e.status === 412) {
        const fresh = await api.getLayout(id).catch(() => null);
        if (fresh) {
          setDoc(fresh);
          setNote("another console saved this layout meanwhile; Save layout again to replace it with yours");
        }
      }
      return false;
    } finally {
      setSaving(false);
    }
  }, [api, id]);

  /** Re-layout: drop every position override so the tree's own layout (dagre's) shows; one unsaved change, written by Save layout. */
  const relayout = useCallback(() => {
    setPins(NO_PINS);
    setChanges((n) => n + 1);
  }, []);

  const discard = useCallback(() => {
    const current = docRef.current;
    setBase(current);
    setPins(pinsOf(current));
    setAnnotationsState(fromLayoutList(current?.annotations ?? []));
    setModeState(current?.mode ?? null);
    setChanges(0);
    setError(null);
    setNote(null);
  }, []);

  const changes = useMemo(() => {
    const live = isLive ?? (() => true);
    return pinChanges(pinsOf(base), pins, live) + annotationChanges(fromLayoutList(base?.annotations ?? []).map(toLayout), annotations.map(toLayout)) + ((base?.mode ?? null) !== mode ? 1 : 0);
  }, [base, pins, annotations, mode, isLive]);
  const livePins = useMemo<Pins>(() => (isLive ? Object.fromEntries(Object.entries(pins).filter(([k]) => isLive(k))) : pins), [pins, isLive]);

  // Leaving the page with unsaved moves asks first.
  useEffect(() => {
    if (changes === 0) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [changes]);

  return { doc, pins, livePins, annotations, changes, saving, error, note, savedAt, mode, setMode, onPin, onPinMany, setAnnotations, save, discard, relayout };
}
