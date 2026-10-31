/**
 * Annotations: free text, rectangles, ellipses and lines drawn over the
 * canvas, each a positioned SVG snippet with a lock and a hidden flag. They
 * are presentation, never twin content, and live in the layout side-car
 * (`axr-layout/1`'s optional `annotations` list, `api/Spec.md`), written
 * ETag-guarded through the same chained queue as the pins. The server
 * refuses a snippet by name when it carries script, a foreignObject, an
 * `on*=` attribute or an href; what this module emits never does, because
 * every operator-typed character that could spell one is escaped. A set an
 * earlier build kept in this browser (localStorage) is migrated once.
 * Pure -- no React.
 */
import type { LayoutAnnotation } from "../../../api/types";

export type AnnotationKind = "text" | "rect" | "ellipse" | "line";
export const ANNOTATION_KINDS: readonly AnnotationKind[] = ["text", "rect", "ellipse", "line"];

export interface Annotation {
  id: string;
  kind: AnnotationKind;
  x: number;
  y: number;
  w: number;
  h: number;
  text: string;
  locked: boolean;
  hidden: boolean;
}

/** Text content as SVG character data: nothing in it can open a tag, an
 *  attribute or an entity the sanitiser would refuse (`=` included, so
 *  `onion = 3` never reads as `on*=`). */
export function escapeText(text: string): string {
  return text.replace(/[<>&"'=]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&#39;", "=": "&#61;" })[c]!);
}

export function unescapeText(text: string): string {
  return text.replace(/&(lt|gt|amp|quot|#39|#61);/g, (_m, e: string) => ({ lt: "<", gt: ">", amp: "&", quot: '"', "#39": "'", "#61": "=" })[e]!);
}

const num = (v: number) => String(Math.round(v * 100) / 100);

/** The SVG snippet an annotation is stored as in the side-car. */
export function toSvg(a: Annotation): string {
  switch (a.kind) {
    case "rect":
      return `<rect x="${num(a.x)}" y="${num(a.y)}" width="${num(a.w)}" height="${num(a.h)}" rx="6" fill="none" stroke="currentColor"/>`;
    case "ellipse":
      return `<ellipse cx="${num(a.x + a.w / 2)}" cy="${num(a.y + a.h / 2)}" rx="${num(a.w / 2)}" ry="${num(a.h / 2)}" fill="none" stroke="currentColor"/>`;
    case "line":
      return `<line x1="${num(a.x)}" y1="${num(a.y)}" x2="${num(a.x + a.w)}" y2="${num(a.y + a.h)}" stroke="currentColor"/>`;
    default:
      return `<text x="${num(a.x)}" y="${num(a.y + 14)}">${escapeText(a.text)}</text>`;
  }
}

export function toLayout(a: Annotation): LayoutAnnotation {
  return { id: a.id, kind: a.kind, x: a.x, y: a.y, w: a.w, h: a.h, svg: toSvg(a), locked: a.locked, hidden: a.hidden };
}

/** Back from the side-car; a text's content is read out of its `<text>` element. */
export function fromLayout(l: LayoutAnnotation): Annotation | null {
  if (!ANNOTATION_KINDS.includes(l.kind as AnnotationKind)) return null;
  const kind = l.kind as AnnotationKind;
  const m = /<text\b[^>]*>([\s\S]*?)<\/text>/.exec(l.svg);
  return { id: l.id, kind, x: l.x, y: l.y, w: l.w ?? (kind === "text" ? 160 : 0), h: l.h ?? 0, text: kind === "text" ? unescapeText(m?.[1] ?? "") : "", locked: l.locked, hidden: l.hidden };
}

export function fromLayoutList(list: readonly LayoutAnnotation[] | undefined): Annotation[] {
  return (list ?? []).map(fromLayout).filter((a): a is Annotation => a !== null);
}

function legacyKey(twinId: string): string {
  return `axr-canvas-annotations:${twinId}`;
}

/** The set an earlier build kept in this browser, to migrate into the side-car once. */
export function readLegacyAnnotations(twinId: string): Annotation[] {
  try {
    const raw = localStorage.getItem(legacyKey(twinId));
    if (!raw) return [];
    const list = JSON.parse(raw) as unknown;
    if (!Array.isArray(list)) return [];
    return list
      .filter((a): a is Annotation => typeof a === "object" && a !== null && typeof (a as Annotation).id === "string" && ANNOTATION_KINDS.includes((a as Annotation).kind))
      .map((a) => ({ ...a, hidden: Boolean(a.hidden), locked: Boolean(a.locked), text: typeof a.text === "string" ? a.text : "" }));
  } catch {
    return [];
  }
}

export function clearLegacyAnnotations(twinId: string): void {
  try {
    localStorage.removeItem(legacyKey(twinId));
  } catch {
    // no storage: nothing to clear
  }
}

let seq = 0;
export function newAnnotation(kind: AnnotationKind, at: { x: number; y: number }): Annotation {
  seq++;
  const id = `ann-${Date.now().toString(36)}-${seq}`;
  const size = kind === "text" ? { w: 160, h: 28 } : kind === "line" ? { w: 160, h: 0 } : { w: 200, h: 120 };
  return { id, kind, x: at.x, y: at.y, ...size, text: kind === "text" ? "note" : "", locked: false, hidden: false };
}
