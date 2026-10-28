import { useState } from "react";

import { ErrorLine } from "./parts";

/** A destructive write behind a confirmation. `compact` draws a bin for a table row; the
 *  full form is a page's button. A refusal from the API is shown in place. */
export function ConfirmDelete({
  label,
  target,
  compact = false,
  confirmText,
  onDelete,
  onDeleted,
}: {
  /** "Delete imprint", "Delete twin": the button's text and the start of its accessible name. */
  label: string;
  /** The id, appended to the accessible name so rows stay distinguishable. */
  target: string;
  compact?: boolean;
  confirmText?: string;
  onDelete: () => Promise<void>;
  onDeleted: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function go() {
    setBusy(true);
    setError(null);
    try {
      await onDelete();
      onDeleted();
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
          <button className="danger" type="button" onClick={go} disabled={busy} aria-label={`Confirm delete ${target}`}>
            {busy ? "Deleting…" : compact ? "Confirm delete" : (confirmText ?? "Confirm delete")}
          </button>
          <button type="button" onClick={() => setConfirming(false)} disabled={busy}>
            Keep
          </button>
        </>
      ) : compact ? (
        <button className="danger icon" type="button" onClick={() => setConfirming(true)} title={label} aria-label={`${label} ${target}`}>
          🗑
        </button>
      ) : (
        <button className="danger" type="button" onClick={() => setConfirming(true)}>
          {label}
        </button>
      )}
      <ErrorLine error={error} prefix="could not delete" />
    </div>
  );
}
