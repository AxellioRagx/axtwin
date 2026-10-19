/**
 * The evidence legend (`docs/design/15-console-canvas.md` §6): the six classes,
 * the two editing states and the ghost outline, one encoding shared by the
 * map, the outline dots and the inspector. Rendered from the theme's `.ev-*`
 * swatches so a hue change is a one-file (`tokens.css`) change.
 */
import { EVIDENCE_CLASSES } from "../model/evidence";

const CLASS_WHY: Readonly<Record<string, string>> = {
  asserted: "an operator said so (this canvas writes these)",
  imported: "an out-of-band import (a directory export)",
  observed: "the vantage saw it on the wire",
  referenced: "a reference channel named it",
  imputed: "derived, never seen",
  compiler: "a compiler choice, not a measurement — never authorable",
};

export function Legend({ declared = false }: { declared?: boolean }): JSX.Element {
  // A DECLARED design (no capture evidence) carries one class, `declared`:
  // the six evidence classes would describe evidence it has not got.
  if (declared) {
    return (
      <ul className="legend" data-testid="legend">
        <li className="ev-none" data-testid="legend-declared">
          <span className="ev-swatch" />
          <span className="name">declared</span>
          <span className="why">from the twin config — nothing here is observed</span>
        </li>
      </ul>
    );
  }
  return (
    <ul className="legend" data-testid="legend">
      {EVIDENCE_CLASSES.map((cls) => (
        <li key={cls} className={`ev-${cls}`}>
          <span className="ev-swatch" />
          <span className="name">{cls}</span>
          <span className="why">{CLASS_WHY[cls]}</span>
        </li>
      ))}
      <li className="ev-pending">
        <span className="ev-swatch" />
        <span className="name">pending</span>
        <span className="why">in the browser only</span>
      </li>
      <li className="ev-saved">
        <span className="ev-swatch" />
        <span className="name">saved</span>
        <span className="why">in the YAML, not yet compiled</span>
      </li>
      <li>
        <span className="ev-swatch is-ghost" />
        <span className="name">ghost</span>
        <span className="why">below the gate, dropped, suppressed or unseen</span>
      </li>
    </ul>
  );
}
