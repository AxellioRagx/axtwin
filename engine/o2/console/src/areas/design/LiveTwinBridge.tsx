/**
 * The bridge from a design to where it RUNS (Unified Console, design area):
 * "Running on dev45 as corp-twin-07cfcdb2 — open the live twin", from
 * `/api/range` twins whose `twin_id` is this imprint's id. The Twin page links
 * back the other way ("Design").
 *
 * Tolerant by construction: rendered inside the Design canvas, which a unit
 * test may mount without a run-time client -- then, or when the range cannot
 * be read, it renders nothing (the design is still fully usable; this is a
 * convenience, never a gate).
 */
import { useContext } from "react";
import { Link } from "react-router";

import { useResource } from "../../api/hooks";
import { RuntimeApiContext } from "../../api/runtimeContext";
import { twinPath } from "../../model/appRoutes";

export function LiveTwinBridge({ imprintId }: { imprintId: string }): JSX.Element | null {
  const api = useContext(RuntimeApiContext);
  const range = useResource((signal) => api!.getRange(signal), [api], api !== null);
  const running = (range.data?.targets ?? []).flatMap((t) =>
    t.twins.filter((w) => w.twin_id === imprintId).map((w) => ({ target: t, twin: w })),
  );
  if (running.length === 0) return null;
  return (
    <p className="notice live-bridge" data-testid="live-bridge">
      {running.map(({ target, twin }, i) => (
        <span key={twin.deploy_id}>
          {i > 0 && "; "}
          Running on <strong>{twin.cluster ?? target.name}</strong> as <span className="mono">{twin.deploy_id}</span> —{" "}
          <Link to={twinPath(twin.deploy_id, { target: target.default ? null : target.name })} data-testid="open-live-twin">
            open the live twin
          </Link>
        </span>
      ))}
    </p>
  );
}
