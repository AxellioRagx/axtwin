/**
 * The adjacency matrix (`docs/design/15-console-canvas.md` §3/§5): the full
 * edge set as a coloured grid instead of 1437 lines. Roles × roles at level 0,
 * hosts × hosts inside a segment (O3). Rows and columns share the map's fixed
 * order, so the two agree. A cell click opens its edge detail in the inspector.
 */
import { useMemo } from "react";

import type { ConsoleGraph } from "../model/graph";
import { MATRIX_CELL, MATRIX_HEADER } from "../layout/geometry";
import { matrix0, matrixSegment, type MatrixLayout } from "../layout/matrix";
import type { Focus } from "./selection";

export interface MatrixProps {
  graph: ConsoleGraph;
  /** When set, the hosts × hosts matrix of that segment; otherwise roles. */
  cidr?: string;
  onFocus: (focus: Focus) => void;
}

export function MatrixView(props: MatrixProps): JSX.Element {
  const { graph, cidr, onFocus } = props;
  const m: MatrixLayout | null = useMemo(
    () => (cidr ? matrixSegment(graph, cidr) : matrix0(graph)),
    [graph, cidr],
  );

  if (!m) {
    return (
      <div className="map-scroll">
        <p className="notice is-error" data-testid="no-matrix">no segment <code>{cidr}</code></p>
      </div>
    );
  }
  if (m.overBudget) {
    return (
      <div className="map-scroll">
        <p className="notice is-error" data-testid="matrix-over-budget">
          {m.axisCount} axes exceeds the 40×40 budget — the matrix refuses to
          draw rather than collapse. Drill into a segment for its hosts.
        </p>
      </div>
    );
  }

  const maxFlows = m.cells.reduce((max, c) => Math.max(max, c.flows), 0) || 1;
  const cellByKey = new Map(m.cells.map((c) => [`${c.rowIndex}|${c.colIndex}`, c]));

  return (
    <div className="map-scroll">
      <p className="meta">
        {m.kind === "roles" ? "roles × roles" : `hosts × hosts · ${cidr}`} ·{" "}
        {m.axisCount} axes · {m.totalFlows} flows total
      </p>
      <svg
        className="matrix"
        viewBox={`0 0 ${m.width} ${m.height}`}
        width={m.width}
        height={m.height}
        role="img"
        aria-label="adjacency matrix"
        data-testid="matrix-svg"
      >
        {/* Column headers (rotated). */}
        {m.axis.map((label, i) => (
          <text
            key={`col-${label}`}
            className="matrix-axis"
            x={MATRIX_HEADER + i * MATRIX_CELL + MATRIX_CELL / 2}
            y={MATRIX_HEADER - 6}
            textAnchor="start"
            transform={`rotate(-90 ${MATRIX_HEADER + i * MATRIX_CELL + MATRIX_CELL / 2} ${MATRIX_HEADER - 6})`}
          >
            {short(label)}
          </text>
        ))}
        {/* Row headers. */}
        {m.axis.map((label, i) => (
          <text key={`row-${label}`} className="matrix-axis" x={MATRIX_HEADER - 6} y={MATRIX_HEADER + i * MATRIX_CELL + MATRIX_CELL / 2} textAnchor="end" dominantBaseline="central">
            {short(label)}
          </text>
        ))}
        {/* Grid + non-empty cells. */}
        {m.axis.map((_row, ri) =>
          m.axis.map((_col, ci) => {
            const cell = cellByKey.get(`${ri}|${ci}`);
            const x = MATRIX_HEADER + ci * MATRIX_CELL;
            const y = MATRIX_HEADER + ri * MATRIX_CELL;
            if (!cell) {
              return <rect key={`${ri}-${ci}`} className="matrix-empty" x={x} y={y} width={MATRIX_CELL} height={MATRIX_CELL} />;
            }
            return (
              <rect
                key={`${ri}-${ci}`}
                className="matrix-cell"
                data-testid="matrix-cell"
                x={x}
                y={y}
                width={MATRIX_CELL}
                height={MATRIX_CELL}
                style={{ fillOpacity: 0.15 + 0.85 * (cell.flows / maxFlows) }}
                onClick={() => onFocus({ kind: "cell", row: cell.row, col: cell.col })}
                role="button"
                tabIndex={0}
                aria-label={`${cell.row} to ${cell.col}: ${cell.flows} flows`}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onFocus({ kind: "cell", row: cell.row, col: cell.col });
                  }
                }}
              >
                <title>{`${cell.row} → ${cell.col}: ${cell.flows} flows, ${cell.edgeCount} edges (${cell.protos.join(", ")})`}</title>
              </rect>
            );
          }),
        )}
      </svg>
    </div>
  );
}

function short(label: string): string {
  if (label.includes(":")) return label.slice(label.indexOf(":") + 1);
  return label.length > 16 ? `${label.slice(0, 15)}…` : label;
}
