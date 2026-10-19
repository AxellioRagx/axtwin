/**
 * One persona, said as a sentence, with the exact protocol/port list behind
 * a "details" disclosure (fix round 2). Used by the host panel and the
 * persona editor; the picker uses `personaSentence` directly.
 */
import type { PersonaInfo } from "../../api/runtimeTypes";
import { personaSentence } from "../../model/personaWords";

export function PersonaLine({ info, loading }: { info: PersonaInfo | undefined; loading: boolean }): JSX.Element {
  if (!info) {
    return <div className="meta">{loading ? "…" : "What it sends is not published for this persona."}</div>;
  }
  return (
    <div className="persona-line">
      <span data-testid="persona-sentence">{personaSentence(info)}</span>
      {info.summary && (
        <details className="persona-details">
          <summary>details</summary>
          <span className="mono meta" data-testid="persona-literal">
            {info.summary}
          </span>
        </details>
      )}
    </div>
  );
}
