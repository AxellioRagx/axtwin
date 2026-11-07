/**
 * The layout side-car (`axr-layout/1`: positions and annotations) as a DRAFT:
 * every move, align, placement and annotation change lands on the canvas
 * and counts as unsaved; the document is written only by `save` -- one PUT
 * carrying everything as it stands, the etag the document was read under as
 * the precondition -- never a write per drag. `discard` returns to the layout
 * as last loaded or saved. On a 412 the fresh document's etag is taken so the
 * next `save` replaces the other console's layout with this one, said so.
 */
import { useCallback, useEffect, useRef, useState } from "react";

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
export function layoutWrite(current: LayoutDoc | null, id: string, pins: LayoutPins, annotations: readonly LayoutAnnotation[]): LayoutWrite {
  const out: LayoutWrite = { schema: LAYOUT_SCHEMA, imprint: id, pins: { ...pins }, plane: CANVAS_PLANE, etag: current?.etag ?? null };
  if (annotations.length) out.annotations = [...annotations];
  return out;
}

/** The pins a loaded document contributes: its own when authored on this plane. */
export function pinsOf(doc: LayoutDoc | null): Pins {
  return doc && doc.plane === CANVAS_PLANE ? doc.pins : NO_PINS;
}

export interface LayoutDraft {
  /** The document as last loaded or saved (its etag is the next save's precondition). */
  doc: LayoutDoc | null;
  pins: Pins;
  annotations: Annotation[];
  /** Moves and annotation changes since the last load or save. */
  changes: number;
  saving: boolean;
  error: unknown;
  /** What a failed save means for the next one, when there is something to say. */
  note: string | null;
  savedAt: string | null;
  onPin(key: string, at: { x: number; y: number }): void;
  /** Several pins at once (a bus reorder re-ranks its site's lanes): one change. */
  onPinMany(pins: Record<string, { x: number; y: number }>): void;
  setAnnotations(list: Annotation[]): void;
  save(): Promise<boolean>;
  discard(): void;
  /** Drop every position override: the tree lays itself out again (one unsaved change). */
  relayout(): void;
}

export function useLayoutDraft(api: Pick<Api, "putLayout" | "getLayout">, id: string, loaded: LayoutDoc | null): LayoutDraft {
  const [doc, setDoc] = useState<LayoutDoc | null>(null);
  const [pins, setPins] = useState<Pins>(NO_PINS);
  const [annotations, setAnnotationsState] = useState<Annotation[]>([]);
  const [changes, setChanges] = useState(0);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [note, setNote] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);

  // A (re)load replaces the draft.
  useEffect(() => {
    if (!loaded) return;
    setDoc(loaded);
    setPins(pinsOf(loaded));
    setAnnotationsState(fromLayoutList(loaded.annotations));
    setChanges(0);
    setNote(null);
  }, [loaded]);

  const docRef = useRef<LayoutDoc | null>(null);
  docRef.current = doc;
  const pinsRef = useRef<Pins>(NO_PINS);
  pinsRef.current = pins;
  const annotationsRef = useRef<Annotation[]>([]);
  annotationsRef.current = annotations;

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
      const saved = await api.putLayout(id, layoutWrite(docRef.current, id, pinsRef.current, annotationsRef.current.map(toLayout)));
      setDoc(saved);
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
    setPins(pinsOf(current));
    setAnnotationsState(fromLayoutList(current?.annotations ?? []));
    setChanges(0);
    setError(null);
    setNote(null);
  }, []);

  // Leaving the page with unsaved moves asks first.
  useEffect(() => {
    if (changes === 0) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [changes]);

  return { doc, pins, annotations, changes, saving, error, note, savedAt, onPin, onPinMany, setAnnotations, save, discard, relayout };
}
