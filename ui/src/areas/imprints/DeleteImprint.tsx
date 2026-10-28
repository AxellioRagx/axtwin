import { useState } from "react";

import { useApi } from "../../api/context";
import { ErrorLine } from "../shell/parts";

/** Remove a bundle from the catalog, behind a confirmation. `compact` draws a bin
 *  for a table row; the full form is the imprint page's button. The API refuses
 *  it under an active run or a live deploy, and the refusal is shown in place. */
export function DeleteImprint({ id, compact = false, onDeleted }: { id: string; compact?: boolean; onDeleted: (id: string) => void }) {
  const api = useApi();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function go() {
    setBusy(true);
    setError(null);
    try {
      await api.deleteImprint(id);
      onDeleted(id);
      setConfirming(false);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className={compact ? "row-actions" : "cancel"}>
      {confirming ? (
        <>
          <button className="danger" type="button" onClick={go} disabled={busy} aria-label={`Confirm delete ${id}`}>
            {busy ? "Deleting…" : compact ? "Confirm delete" : "Confirm: remove the bundle from the catalog"}
          </button>
          <button type="button" onClick={() => setConfirming(false)} disabled={busy}>
            Keep
          </button>
        </>
      ) : compact ? (
        <button className="danger icon" type="button" onClick={() => setConfirming(true)} title="Delete imprint" aria-label={`Delete imprint ${id}`}>
          🗑
        </button>
      ) : (
        <button className="danger" type="button" onClick={() => setConfirming(true)}>
          Delete imprint
        </button>
      )}
      <ErrorLine error={error} prefix="could not delete" />
    </div>
  );
}
