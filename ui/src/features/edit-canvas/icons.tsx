/** The rail's glyphs: 16 px line icons on `currentColor`, no icon font, no
 *  dependency. Each is decorative -- the button's `aria-label` is the name. */
import type { ReactNode } from "react";

function Svg({ children }: { children: ReactNode }) {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {children}
    </svg>
  );
}

/** The role glyphs a tile and a chip draw instead of a text badge: keyed by
 *  the badge label `model/roles.ts` derives (`DC`, `WS`, ...); anything else
 *  gets the generic endpoint glyph, so a role invented tomorrow still draws. */
export function RoleIcon({ label, size = 14 }: { label: string; size?: number }) {
  const k = ROLE_GLYPHS[label] ?? ROLE_GLYPHS["EP"]!;
  return (
    <svg width={size} height={size} viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {k}
    </svg>
  );
}

const ROLE_GLYPHS: Record<string, ReactNode> = {
  WS: <><rect x="1.5" y="2" width="11" height="7.5" rx="1" /><path d="M4.5 12.5h5M7 9.5v3" /></>,
  EP: <><rect x="4" y="1.5" width="6" height="11" rx="1.5" /><path d="M6.5 10.5h1" /></>,
  DC: <><rect x="2" y="2" width="10" height="4" rx="1" /><rect x="2" y="8" width="10" height="4" rx="1" /><path d="M4 4h1M4 10h1" /></>,
  FS: <path d="M1.5 4.5h4l1.5 1.5h5.5v6h-11z" />,
  WEB: <><circle cx="7" cy="7" r="5.5" /><path d="M1.5 7h11M7 1.5c2 2 2 9 0 11M7 1.5c-2 2-2 9 0 11" /></>,
  DB: <><ellipse cx="7" cy="3.5" rx="5" ry="2" /><path d="M2 3.5v7c0 1.1 2.2 2 5 2s5-.9 5-2v-7M2 7c0 1.1 2.2 2 5 2s5-.9 5-2" /></>,
  DNS: <><path d="M2 11l5-8 5 8z" /><path d="M7 6v3" /></>,
  MAIL: <><rect x="1.5" y="3" width="11" height="8" rx="1" /><path d="M1.5 4l5.5 4 5.5-4" /></>,
  MON: <path d="M1.5 8h2.5l1.5-4 2 7 1.5-5 1 2h2.5" />,
  PROXY: <><path d="M2 7h10M9 4l3 3-3 3" /><path d="M5 4L2 7l3 3" /></>,
  LOG: <path d="M3 2h8v10H3zM5 5h4M5 7.5h4M5 10h2" />,
  NET: <><rect x="1.5" y="5" width="11" height="4" rx="1" /><path d="M3.5 7h1M6 7h1" /></>,
  HV: <><rect x="1.5" y="1.5" width="11" height="11" rx="1" /><rect x="4" y="4" width="2.5" height="2.5" /><rect x="7.5" y="4" width="2.5" height="2.5" /><rect x="4" y="7.5" width="2.5" height="2.5" /><rect x="7.5" y="7.5" width="2.5" height="2.5" /></>,
  JUMP: <><path d="M2 11.5V7l5-4.5 5 4.5v4.5" /><path d="M5.5 11.5v-3h3v3" /></>,
  EXT: <circle cx="7" cy="7" r="5.5" strokeDasharray="2 2" />,
  NONE: <><circle cx="7" cy="7" r="5.5" /><path d="M7 4.5v3M7 9.5v.5" /></>,
  GHOST: <><path d="M3 12V6a4 4 0 0 1 8 0v6l-2-1.5-2 1.5-2-1.5z" strokeDasharray="2 2" /></>,
};

export const Icon = {
  router: () => (
    <Svg>
      <circle cx="8" cy="8" r="6.5" />
      <path d="M4.5 6.5l2-1.5v3zM11.5 9.5l-2 1.5v-3zM5 9.5h4.5M6.5 6.5h4.5" />
    </Svg>
  ),
  cloud: () => (
    <Svg>
      <path d="M4 12.5a3 3 0 0 1 0-6 4 4 0 0 1 7.7-1.2A3.2 3.2 0 0 1 12 12.5z" />
    </Svg>
  ),
  globe: () => (
    <Svg>
      <circle cx="8" cy="8" r="6.5" />
      <path d="M1.5 8h13M8 1.5c2.5 2.5 2.5 10.5 0 13M8 1.5c-2.5 2.5-2.5 10.5 0 13" />
    </Svg>
  ),
  detailAuto: () => (
    <Svg>
      <rect x="2" y="2" width="12" height="12" rx="2" />
      <path d="M5 11l2.5-6 2.5 6M6 9h3" />
    </Svg>
  ),
  detailFull: () => (
    <Svg>
      <rect x="2" y="2" width="12" height="12" rx="2" />
      <circle cx="5.5" cy="6" r="1.2" fill="currentColor" />
      <circle cx="10.5" cy="6" r="1.2" fill="currentColor" />
      <circle cx="5.5" cy="10" r="1.2" fill="currentColor" />
      <circle cx="10.5" cy="10" r="1.2" fill="currentColor" />
    </Svg>
  ),
  detailCompact: () => (
    <Svg>
      <rect x="2" y="2" width="12" height="12" rx="2" />
      <path d="M5 8h6" />
    </Svg>
  ),
  select: () => (
    <Svg>
      <path d="M3 2l10 6-4.5 1.2L6 14z" />
    </Svg>
  ),
  pan: () => (
    <Svg>
      <path d="M5 8V3.5a1 1 0 0 1 2 0V7M7 7V2.5a1 1 0 0 1 2 0V7M9 7V3.5a1 1 0 0 1 2 0V8M11 8V5.5a1 1 0 0 1 2 0V10c0 2.5-2 4.5-4.5 4.5S4 12.5 4 10V7.5a1 1 0 0 1 1-1" />
    </Svg>
  ),
  zoomIn: () => (
    <Svg>
      <circle cx="7" cy="7" r="4.5" />
      <path d="M10.5 10.5L14 14M7 5v4M5 7h4" />
    </Svg>
  ),
  zoomOut: () => (
    <Svg>
      <circle cx="7" cy="7" r="4.5" />
      <path d="M10.5 10.5L14 14M5 7h4" />
    </Svg>
  ),
  fit: () => (
    <Svg>
      <path d="M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4" />
      <rect x="5.5" y="5.5" width="5" height="5" />
    </Svg>
  ),
  lock: () => (
    <Svg>
      <rect x="3" y="7" width="10" height="7" rx="1" />
      <path d="M5 7V5a3 3 0 0 1 6 0v2" />
    </Svg>
  ),
  unlock: () => (
    <Svg>
      <rect x="3" y="7" width="10" height="7" rx="1" />
      <path d="M5 7V5a3 3 0 0 1 5.8-1" />
    </Svg>
  ),
  light: () => (
    <Svg>
      <path d="M9 1.5L3.5 9H8l-1 5.5L12.5 7H8z" />
    </Svg>
  ),
  find: () => (
    <Svg>
      <circle cx="6.5" cy="6.5" r="4.5" />
      <path d="M10 10l4 4" />
    </Svg>
  ),
  plus: () => (
    <Svg>
      <path d="M8 3v10M3 8h10" />
    </Svg>
  ),
  attach: () => (
    <Svg>
      <circle cx="4" cy="4" r="2" />
      <circle cx="12" cy="12" r="2" />
      <path d="M5.5 5.5l5 5" />
    </Svg>
  ),
  snap: () => (
    <Svg>
      <path d="M3 3h10v10H3zM3 8h10M8 3v10" />
    </Svg>
  ),
  grid: () => (
    <Svg>
      <circle cx="3" cy="3" r="0.8" fill="currentColor" />
      <circle cx="8" cy="3" r="0.8" fill="currentColor" />
      <circle cx="13" cy="3" r="0.8" fill="currentColor" />
      <circle cx="3" cy="8" r="0.8" fill="currentColor" />
      <circle cx="8" cy="8" r="0.8" fill="currentColor" />
      <circle cx="13" cy="8" r="0.8" fill="currentColor" />
      <circle cx="3" cy="13" r="0.8" fill="currentColor" />
      <circle cx="8" cy="13" r="0.8" fill="currentColor" />
      <circle cx="13" cy="13" r="0.8" fill="currentColor" />
    </Svg>
  ),
  alignH: () => (
    <Svg>
      <path d="M2 8h12" />
      <rect x="4" y="4" width="3" height="8" />
      <rect x="9" y="5.5" width="3" height="5" />
    </Svg>
  ),
  alignV: () => (
    <Svg>
      <path d="M8 2v12" />
      <rect x="4" y="4" width="8" height="3" />
      <rect x="5.5" y="9" width="5" height="3" />
    </Svg>
  ),
  annotate: () => (
    <Svg>
      <path d="M3 13l1-3.5L11 2.5l2.5 2.5L6.5 12z" />
      <path d="M9.5 4l2.5 2.5" />
    </Svg>
  ),
  close: () => (
    <Svg>
      <path d="M4 4l8 8M12 4l-8 8" />
    </Svg>
  ),
  labels: () => (
    <Svg>
      <path d="M2 12.5l3-9 3 9M3.2 9.5h3.6M10 6.5h4.5M10 9.5h4.5M10 12.5h3" />
    </Svg>
  ),
  chevronRight: () => (
    <Svg>
      <path d="M6 3l5 5-5 5" />
    </Svg>
  ),
  chevronLeft: () => (
    <Svg>
      <path d="M10 3L5 8l5 5" />
    </Svg>
  ),
};
