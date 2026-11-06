/**
 * The editor's state in React: the working files, the command stack and
 * the models read from them. `apply` runs a pure mutation, blocks it when
 * the canvas's own validator finds an error (the reason is returned, the
 * document untouched), and otherwise records it so Ctrl+Z restores the
 * pre-edit text byte-for-byte.
 */
import { useCallback, useMemo, useRef, useState } from "react";

import { EMPTY_STACK, record, redo as redoCmd, sameFiles, undo as undoCmd, type Files, type Stack } from "./commands";
import { readDoc, type DocModel, type WorkingDoc } from "./document";
import { firstReason } from "./validate";

export interface EditorState {
  files: Files;
  /** What the last load / save / sync established as the saved text. */
  saved: Files;
  stack: Stack;
}

export interface Editor {
  doc: WorkingDoc | null;
  model: DocModel | null;
  files: Files | null;
  /** The working text differs from what was last saved or synced. */
  dirty: boolean;
  canUndo: boolean;
  canRedo: boolean;
  lastLabel: string | null;
  /** Run a mutation; returns the reason it was blocked, or null when applied. */
  apply(label: string, fn: (doc: WorkingDoc, model: DocModel) => Files): string | null;
  undo(): void;
  redo(): void;
  /** Undo until the working files equal `files` (a refused sync reverts to what the server holds), or the stack is empty. */
  undoUntil(files: Files): void;
  /** Adopt a document from the server (a load, a reset, a sync from another console). */
  load(doc: { id: string; teFile: string; files: Files }, options?: { keepStack?: boolean }): void;
  /** The files were written: they are now the saved text. */
  markSaved(files?: Files): void;
  /** The command stack as it stands (persisted per working copy by the page). */
  stack: Stack;
  /** Put a stack back under the loaded document (a reload of the same working copy). */
  restoreStack(stack: Stack): void;
}

export function useEditor(): Editor {
  const [ident, setIdent] = useState<{ id: string; teFile: string } | null>(null);
  const [state, setState] = useState<EditorState>({ files: {}, saved: {}, stack: EMPTY_STACK });
  const stateRef = useRef(state);
  stateRef.current = state;

  const doc = useMemo<WorkingDoc | null>(() => (ident ? { id: ident.id, teFile: ident.teFile, files: state.files } : null), [ident, state.files]);
  const model = useMemo(() => (doc ? readDoc(doc) : null), [doc]);

  const apply = useCallback<Editor["apply"]>(
    (label, fn) => {
      if (!ident) return "no document is open";
      const cur = stateRef.current;
      const curDoc: WorkingDoc = { id: ident.id, teFile: ident.teFile, files: cur.files };
      let next: Files;
      try {
        next = fn(curDoc, readDoc(curDoc));
      } catch (e) {
        return e instanceof Error ? e.message : String(e);
      }
      const nextModel = readDoc({ ...curDoc, files: next });
      const reason = firstReason(nextModel.findings);
      if (reason) return reason;
      const stack = record(cur.stack, label, cur.files, next);
      if (stack === cur.stack) return null;
      const s = { ...cur, files: next, stack };
      stateRef.current = s;
      setState(s);
      return null;
    },
    [ident],
  );

  const undo = useCallback(() => {
    const cur = stateRef.current;
    const r = undoCmd(cur.stack, cur.files);
    if (!r) return;
    const s = { ...cur, files: r.files, stack: r.stack };
    stateRef.current = s;
    setState(s);
  }, []);

  const undoUntil = useCallback((files: Files) => {
    let cur = stateRef.current;
    while (!sameFiles(cur.files, files)) {
      const r = undoCmd(cur.stack, cur.files);
      if (!r) break;
      cur = { ...cur, files: r.files, stack: r.stack };
    }
    stateRef.current = cur;
    setState(cur);
  }, []);

  const redo = useCallback(() => {
    const cur = stateRef.current;
    const r = redoCmd(cur.stack, cur.files);
    if (!r) return;
    const s = { ...cur, files: r.files, stack: r.stack };
    stateRef.current = s;
    setState(s);
  }, []);

  const load = useCallback<Editor["load"]>((d, options) => {
    setIdent({ id: d.id, teFile: d.teFile });
    const s = { files: { ...d.files }, saved: { ...d.files }, stack: options?.keepStack ? stateRef.current.stack : EMPTY_STACK };
    stateRef.current = s;
    setState(s);
  }, []);

  const restoreStack = useCallback((stack: Stack) => {
    const cur = stateRef.current;
    const s = { ...cur, stack: { done: [...stack.done], undone: [...stack.undone] } };
    stateRef.current = s;
    setState(s);
  }, []);

  const markSaved = useCallback((files?: Files) => {
    const cur = stateRef.current;
    const s = { ...cur, saved: { ...(files ?? cur.files) } };
    stateRef.current = s;
    setState(s);
  }, []);

  // One stable object per state change, so effects keyed on the editor re-arm only when it moves.
  return useMemo<Editor>(
    () => ({
      doc,
      model,
      files: ident ? state.files : null,
      dirty: ident ? !sameFiles(state.files, state.saved) : false,
      canUndo: state.stack.done.length > 0,
      canRedo: state.stack.undone.length > 0,
      lastLabel: state.stack.done[state.stack.done.length - 1]?.label ?? null,
      apply,
      undo,
      redo,
      undoUntil,
      load,
      markSaved,
      stack: state.stack,
      restoreStack,
    }),
    [doc, model, ident, state, apply, undo, redo, undoUntil, load, markSaved, restoreStack],
  );
}
