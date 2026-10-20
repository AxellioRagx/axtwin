/**
 * Small HTML parts shared by the panes -- the level-2 facts table, the
 * inspector's facts and the edit forms -- so a fact, its evidence and a form
 * field render ONE way everywhere (`docs/design/15-console-canvas.md` §2: the
 * same encoding in every pane). The SVG counterparts live in `mapParts.tsx`.
 */
import type { ReactNode } from "react";

import { evidenceClassName } from "../model/evidence";

/**
 * One fact as a table row: its label, its value, and -- when the fact has one
 * -- the export's own evidence class and confidence, in a cell of their own.
 * `evidence` ABSENT means the fact carries no evidence field at all; `null`
 * means it does and the export left it empty ("no evidence").
 */
export function Row(props: {
  label: string;
  value: string;
  evidence?: string | null;
  confidence?: number | null;
}): JSX.Element {
  return (
    <tr>
      <th>{props.label}</th>
      <td>{props.value}</td>
      <td>
        {props.evidence !== undefined && (
          <span className={`ev-badge ${evidenceClassName(props.evidence)}`}>
            {props.evidence ?? "no evidence"}
          </span>
        )}
        {props.confidence !== undefined && props.confidence !== null && (
          <span className="meta"> conf {props.confidence.toFixed(2)}</span>
        )}
      </td>
    </tr>
  );
}

/** One fact as a definition-list pair -- the inspector's frame facts. */
export function Fact({ name, value }: { name: string; value: string }): JSX.Element {
  return (
    <>
      <dt className="label">{name}</dt>
      <dd className="mono">{value}</dd>
    </>
  );
}

/** A labelled form control. */
export function Field({ label, children }: { label: string; children: ReactNode }): JSX.Element {
  return (
    <label className="edit-field">
      <span className="edit-label">{label}</span>
      {children}
    </label>
  );
}
