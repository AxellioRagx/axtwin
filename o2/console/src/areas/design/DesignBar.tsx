/**
 * The Design area's heading, rendered INSIDE the canvas's own header (one
 * header, not two stacked bars):
 * what this area is for, and a glossary for the words it uses that the
 * run-time screens do not (ruling §2 rule 7 -- "imprint", "cast", "assertion",
 * "ghost", "occupancy", evidence classes and "conduit" stay inside Design,
 * WITH a glossary).
 */
import { useEffect, useRef, useState } from "react";

export const GLOSSARY: ReadonlyArray<{ term: string; meaning: string }> = [
  {
    term: "imprint",
    meaning:
      "A twin's design as learned from a real network capture: its segments, hosts, roles and the traffic observed between them.",
  },
  {
    term: "cast",
    meaning:
      "The twin configuration compiled from an imprint — what a deploy would actually build. The canvas shows the imprint and the cast together.",
  },
  {
    term: "assertion",
    meaning:
      "An operator's correction to the imprint (add a segment, change a role, suppress a host …), saved as a record in the imprint's assertions file. Apply recompiles the cast with it.",
  },
  {
    term: "would deploy",
    meaning:
      "Hosts the compiled cast would stand up if this design were deployed. It is a design-time count, not a running VM.",
  },
  {
    term: "ghost",
    meaning:
      "An entity seen in the capture that the cast leaves out (below the deploy threshold, transient, or suppressed). Drawn dashed.",
  },
  {
    term: "occupancy",
    meaning: "The share of the capture window a host was actually seen active (0–1). Low occupancy hosts are often ghosts.",
  },
  {
    term: "evidence classes",
    meaning:
      "Where a fact comes from, strongest first: asserted (an operator said so), imported, observed (seen in the capture), referenced (named by traffic, not seen), imputed (inferred), compiler (a default). The colour of every element.",
  },
  {
    term: "conduit",
    meaning: "A declared path between two segments that the twin's router will carry traffic over (drawn in the east-west band).",
  },
  {
    term: "pin",
    meaning: "A position you dragged a segment or site to on the map. Layout only; never changes the twin.",
  },
];

export function DesignHeading(): JSX.Element {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") {
        // Handled here so the canvas does not ALSO treat it as "go up a level".
        e.stopImmediatePropagation();
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open]);

  return (
    <span className="design-heading">
      <span className="shell-title" title="A running twin picks a design change up only when it is redeployed.">
        Design<small>edits a twin&apos;s design file (its imprint), never a running twin</small>
      </span>
      <button
        ref={buttonRef}
        type="button"
        className="btn small"
        aria-expanded={open}
        aria-controls="design-glossary"
        data-testid="glossary-toggle"
        onClick={() => setOpen(!open)}
      >
        Glossary
      </button>
      {open && (
        <div id="design-glossary" ref={panelRef} className="popover glossary" role="dialog" aria-label="Design glossary" data-testid="glossary">
          <dl>
            {GLOSSARY.map((g) => (
              <div key={g.term} className="glossary-entry">
                <dt>{g.term}</dt>
                <dd>{g.meaning}</dd>
              </div>
            ))}
          </dl>
          <button type="button" className="btn small" onClick={() => setOpen(false)}>
            Close
          </button>
        </div>
      )}
    </span>
  );
}
