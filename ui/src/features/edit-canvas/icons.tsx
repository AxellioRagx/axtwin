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

export const Icon = {
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
