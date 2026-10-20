/**
 * Small SVG parts shared by the map views: a keyboard-accessible clickable
 * group, a role histogram and an evidence-mix ring. Kept in one place so a
 * tile, a chip and a device all render evidence and roles the same way
 * (`15` §6: the same encoding everywhere).
 */
import type { ReactNode } from "react";

import type { EvidenceMixEntry } from "../model/graph";
import { roleBadgeLabel, roleRank, UNROLED } from "../model/roles";
import { BADGE_GAP_X, BADGE_H } from "../layout/geometry";

/** A clickable/focusable `<g>` -- click or Enter/Space activates. SVG has no
 *  button element, so the ARIA role and key handler are explicit. `current`
 *  marks the element the inspector is acting on (`aria-current` plus the
 *  `is-selected` class, `15` §7) -- the same statement in both channels. */
export function Nav(props: {
  onActivate: () => void;
  label: string;
  current?: boolean;
  children: ReactNode;
}): JSX.Element {
  return (
    <g
      role="button"
      tabIndex={0}
      aria-label={props.label}
      aria-current={props.current ? "true" : undefined}
      className={`nav${props.current ? " is-selected" : ""}`}
      onClick={props.onActivate}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          props.onActivate();
        }
      }}
    >
      {props.children}
    </g>
  );
}

/**
 * The post-apply highlight class for a map element (S6, `15` §12 O5), shared
 * by every view so a tile, a chip and a device highlight the same way.
 * `is-changed` marks an element as long as the `?changed=1` filter is on -- the
 * reachability the five-second fade leaves behind; `is-changed-glow` is the
 * transient emphasis on top of it. An element NOT in the change set gets
 * neither, whatever the flags -- so the empty change set is a pure no-op and a
 * view without an apply behind it renders exactly as before.
 */
export function changeClasses(inSet: boolean, filterOn: boolean, glow: boolean): string {
  let cls = "";
  if (inSet && filterOn) cls += " is-changed";
  if (inSet && glow) cls += " is-changed-glow";
  return cls;
}

const BYTE_UNITS = ["B", "kB", "MB", "GB", "TB", "PB"] as const;

/**
 * A byte count as a short human string (`1.8 GB`, `412 kB`, `900 B`).
 *
 * **Decimal units, not binary** — the reference topology report labels the same
 * quantities in GB/TB and an operator reads the two side by side, so a KiB-based
 * figure 7 % away from the report's would read as a discrepancy in the data
 * rather than in the units.
 *
 * Every caller pairs it with "in this window": the export counts capture
 * windows, never seconds, so nothing built from it is a rate (§8).
 */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  let value = bytes;
  let unit = 0;
  while (value >= 1000 && unit < BYTE_UNITS.length - 1) {
    value /= 1000;
    unit += 1;
  }
  // One decimal below 10 of a scaled unit (`1.8 GB`), none above (`412 kB`)
  // and none at all for raw bytes -- `1234.0 B` would be precision theatre.
  return `${value.toFixed(value < 10 && unit > 0 ? 1 : 0)} ${BYTE_UNITS[unit]}`;
}

/** A fraction (0..1) as a whole percent -- `0.6` -> `60%`. The ONE occupancy
 *  formatter: a tile, the inspector and the level-2 table say the same number
 *  the same way. Absent occupancy is the caller's to word, since "no windows"
 *  and "not measured" read differently in different places. */
export function formatPercent(fraction: number): string {
  return `${Math.round(fraction * 100)}%`;
}

/** A role as WORDS, for a title or a label. An export's empty `role: ""` gets
 *  the unroled name rather than rendering as nothing at all (`roleBadgeLabel`
 *  makes the same substitution for the badge glyph). */
export function roleDisplayName(role: string): string {
  return role || UNROLED;
}

/** What the badge row says on hover: how much of the histogram it is showing.
 *  Exported so the tile's `aria-label` can make the same disclosure with the
 *  same numbers rather than a second phrasing that could drift. */
export function badgeRowTitle(shown: number, hidden: number): string {
  const roles = `${shown} role${shown === 1 ? "" : "s"}`;
  return hidden > 0
    ? `${roles} shown of ${shown + hidden} on this segment — +${hidden} more in the inspector`
    : `${roles}, all of them`;
}

/** A fixed, deterministic hue per role (ordering key → hue), so the same role
 *  is the same colour on every tile without a palette lookup table to drift. */
function roleColor(role: string): string {
  const rank = roleRank(role);
  const hue = Math.round((rank * 47) % 360);
  return `hsl(${hue} 42% 55%)`;
}

/**
 * The tile's role badges (R3) -- `DC 2`, `EP 14` -- which replaced the stacked
 * 8 px histogram bar this file used to draw.
 *
 * The bar encoded the same numbers in a channel nobody could read: at 140 units
 * across a 156-unit tile, a three-role mix is three coloured slivers that say
 * *mixed* and nothing more, with the roles themselves only in a hover title.
 * A badge names the role and its count outright. The hue is kept (same
 * `roleColor`, same rank key), so a role still reads as the same colour
 * wherever it appears -- the abbreviation gains a legend it never had.
 *
 * `aria-hidden`, like the evidence ring: the roles reach assistive tech through
 * the tile's own `aria-label`, which is one sentence rather than three
 * interleaved title nodes in document order.
 */
export function RoleBadges(props: {
  badges: ReadonlyArray<{ role: string; count: number }>;
  /** Buckets the tile HOLDS but does not badge (review S2). The badge set is a
   *  projection of the histogram -- and deliberately not even a ranking, since
   *  an infrastructure role is reserved a slot -- so a tile that shows three of
   *  five roles has to say so, or three badges read as the whole answer. */
  hidden: number;
  x: number;
  y: number;
  w: number;
}): JSX.Element {
  const n = props.badges.length;
  if (n === 0) return <g />;
  const width = (props.w - (n - 1) * BADGE_GAP_X) / n;
  return (
    <g className="role-badges" aria-hidden="true">
      <title data-testid="role-badges-title">{badgeRowTitle(n, props.hidden)}</title>
      {props.badges.map((badge, i) => {
        const x = props.x + i * (width + BADGE_GAP_X);
        return (
          <g key={badge.role} className="role-badge" data-testid="role-badge" data-role={badge.role}>
            <rect
              x={x}
              y={props.y}
              width={Math.max(0, width)}
              height={BADGE_H}
              rx={3}
              style={{ fill: roleColor(badge.role) }}
            >
              <title>{`${roleDisplayName(badge.role)}: ${badge.count}`}</title>
            </rect>
            <text
              className="role-badge-text"
              x={x + width / 2}
              y={props.y + BADGE_H / 2}
              dominantBaseline="central"
              textAnchor="middle"
            >
              {`${roleBadgeLabel(badge.role)} ${badge.count}`}
            </text>
          </g>
        );
      })}
    </g>
  );
}

/** A stacked horizontal bar of the evidence mix, coloured by the export's own
 *  `*_evidence_class` via the theme's `.ev-*` variables (never inferred). */
export function EvidenceRing(props: {
  mix: ReadonlyArray<EvidenceMixEntry>;
  x: number;
  y: number;
  w: number;
  h: number;
}): JSX.Element {
  const total = props.mix.reduce((s, m) => s + m.count, 0);
  if (total === 0) return <g />;
  let cursor = props.x;
  return (
    <g className="evidence-ring" aria-hidden="true">
      {props.mix.map((m) => {
        const width = (m.count / total) * props.w;
        const rect = (
          <rect
            key={m.evidenceClass}
            className={m.evidenceClass === "(none)" ? "ev-none" : `ev-${m.evidenceClass}`}
            x={cursor}
            y={props.y}
            width={Math.max(0, width)}
            height={props.h}
            style={{ fill: "var(--ev-color, var(--neutral))" }}
          >
            <title>{`${m.evidenceClass}: ${m.count}`}</title>
          </rect>
        );
        cursor += width;
        return rect;
      })}
    </g>
  );
}
