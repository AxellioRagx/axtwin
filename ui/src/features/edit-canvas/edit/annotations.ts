/**
 * Annotations: free text, rectangles, ellipses and lines drawn over the
 * canvas, each a positioned SVG snippet with a lock. They are presentation,
 * never twin content, and the layout side-car is where they belong -- but
 * `axr-layout/1` is a closed key set (`schema, imprint, pins, plane`) whose
 * pins are strictly `{x, y}` under `site:`/`segment:`/`host:` keys, so they
 * have no legal home there yet. Until the schema gains an `annotations`
 * key they are kept in this browser (localStorage, per twin); the page says
 * so. Pure -- the store functions take and return data.
 */
export type AnnotationKind = "text" | "rect" | "ellipse" | "line";

export interface Annotation {
  id: string;
  kind: AnnotationKind;
  x: number;
  y: number;
  w: number;
  h: number;
  text: string;
  locked: boolean;
}

/** The SVG snippet an annotation would be stored as in the side-car. */
export function toSvg(a: Annotation): string {
  switch (a.kind) {
    case "rect":
      return `<rect x="${a.x}" y="${a.y}" width="${a.w}" height="${a.h}" rx="6" fill="none" stroke="currentColor"/>`;
    case "ellipse":
      return `<ellipse cx="${a.x + a.w / 2}" cy="${a.y + a.h / 2}" rx="${a.w / 2}" ry="${a.h / 2}" fill="none" stroke="currentColor"/>`;
    case "line":
      return `<line x1="${a.x}" y1="${a.y}" x2="${a.x + a.w}" y2="${a.y + a.h}" stroke="currentColor"/>`;
    default:
      return `<text x="${a.x}" y="${a.y + 14}">${a.text.replace(/[<&>]/g, (c) => ({ "<": "&lt;", "&": "&amp;", ">": "&gt;" })[c]!)}</text>`;
  }
}

function key(twinId: string): string {
  return `axr-canvas-annotations:${twinId}`;
}

export function loadAnnotations(twinId: string): Annotation[] {
  try {
    const raw = localStorage.getItem(key(twinId));
    if (!raw) return [];
    const list = JSON.parse(raw) as unknown;
    if (!Array.isArray(list)) return [];
    return list.filter((a): a is Annotation => typeof a === "object" && a !== null && typeof (a as Annotation).id === "string");
  } catch {
    return [];
  }
}

export function saveAnnotations(twinId: string, list: readonly Annotation[]): void {
  try {
    localStorage.setItem(key(twinId), JSON.stringify(list));
  } catch {
    // no storage: the annotations live for this page only
  }
}

let seq = 0;
export function newAnnotation(kind: AnnotationKind, at: { x: number; y: number }): Annotation {
  seq++;
  const id = `ann-${Date.now().toString(36)}-${seq}`;
  const size = kind === "text" ? { w: 160, h: 28 } : kind === "line" ? { w: 160, h: 0 } : { w: 200, h: 120 };
  return { id, kind, x: at.x, y: at.y, ...size, text: kind === "text" ? "note" : "", locked: false };
}
