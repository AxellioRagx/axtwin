/**
 * The command stack over the working document: every edit is recorded as
 * the files it touched with their text BEFORE and AFTER, so undo restores
 * the before-text byte-for-byte (never a reverse edit) and redo the
 * after-text. The document is immutable data; the stack hands back new
 * documents. Pure -- no React.
 */
export type Files = Record<string, string>;

export interface Command {
  label: string;
  /** Path -> text before (`undefined` = the file did not exist). */
  before: Record<string, string | undefined>;
  after: Record<string, string | undefined>;
}

export interface Stack {
  done: Command[];
  undone: Command[];
}

export const EMPTY_STACK: Stack = { done: [], undone: [] };

/** Record an edit: `next` is the document after `fn`; only paths whose text
 *  changed are kept. A no-op edit records nothing. */
export function record(stack: Stack, label: string, prev: Files, next: Files): Stack {
  const before: Command["before"] = {};
  const after: Command["after"] = {};
  for (const path of new Set([...Object.keys(prev), ...Object.keys(next)])) {
    if (prev[path] !== next[path]) {
      before[path] = prev[path];
      after[path] = next[path];
    }
  }
  if (Object.keys(after).length === 0) return stack;
  return { done: [...stack.done, { label, before, after }], undone: [] };
}

function apply(files: Files, patch: Record<string, string | undefined>): Files {
  const out = { ...files };
  for (const [path, text] of Object.entries(patch)) {
    if (text === undefined) delete out[path];
    else out[path] = text;
  }
  return out;
}

export function undo(stack: Stack, files: Files): { stack: Stack; files: Files; command: Command } | null {
  const cmd = stack.done[stack.done.length - 1];
  if (!cmd) return null;
  return { stack: { done: stack.done.slice(0, -1), undone: [...stack.undone, cmd] }, files: apply(files, cmd.before), command: cmd };
}

export function redo(stack: Stack, files: Files): { stack: Stack; files: Files; command: Command } | null {
  const cmd = stack.undone[stack.undone.length - 1];
  if (!cmd) return null;
  return { stack: { done: [...stack.done, cmd], undone: stack.undone.slice(0, -1) }, files: apply(files, cmd.after), command: cmd };
}

export function sameFiles(a: Files, b: Files): boolean {
  const ka = Object.keys(a);
  const kb = Object.keys(b);
  if (ka.length !== kb.length) return false;
  return ka.every((k) => a[k] === b[k]);
}
