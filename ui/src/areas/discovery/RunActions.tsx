import { useState } from "react";

import { useApi } from "../../api/context";
import type { DiscoveryRun } from "../../api/types";
import { formatIso } from "../../model/sources";
import { ErrorLine } from "../shell/parts";

export function isActive(run: DiscoveryRun): boolean {
  return run.status === "queued" || run.status === "running";
}

/** The one write a run offers, behind a confirmation: cancel while it is queued or
 *  running, delete once it has finished. `compact` draws it as an icon for a table
 *  cell (× to cancel, 🗑 to delete); the full form is the run page's button. */
export function RunAction({
  run,
  compact = false,
  onCancelled,
  onDeleted,
}: {
  run: DiscoveryRun;
  compact?: boolean;
  onCancelled: (r: DiscoveryRun) => void;
  onDeleted: (id: string) => void;
}) {
  const api = useApi();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const active = isActive(run);
  const requested = active && run.cancel_requested_at != null;
  const verb = active ? "cancel" : "delete";
  const label = active ? "Cancel run" : "Delete run";

  async function go() {
    setBusy(true);
    setError(null);
    try {
      if (active) {
        onCancelled(await api.cancelRun(run.id));
      } else {
        await api.deleteRun(run.id);
        onDeleted(run.id);
      }
      setConfirming(false);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  if (requested) {
    return compact ? (
      <span className="muted">cancelling…</span>
    ) : (
      <div className="cancel">
        <span className="muted">Cancel requested {formatIso(run.cancel_requested_at)}; stopping the current stage…</span>
      </div>
    );
  }
  return (
    <div className={compact ? "row-actions" : "cancel"}>
      {confirming ? (
        <>
          <button className="danger" type="button" onClick={go} disabled={busy} aria-label={`Confirm ${verb} ${run.id}`}>
            {busy ? (active ? "Cancelling…" : "Deleting…") : active ? "Confirm cancel" : "Confirm delete"}
          </button>
          <button type="button" onClick={() => setConfirming(false)} disabled={busy}>
            {active ? "Keep running" : "Keep"}
          </button>
        </>
      ) : compact ? (
        <button className="danger icon" type="button" onClick={() => setConfirming(true)} title={label} aria-label={`${label} ${run.id}`}>
          {active ? "×" : "🗑"}
        </button>
      ) : (
        <button className="danger" type="button" onClick={() => setConfirming(true)}>
          {label}
        </button>
      )}
      <ErrorLine error={error} prefix={`could not ${verb}`} />
    </div>
  );
}
