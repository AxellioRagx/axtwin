import { useEffect } from "react";

import { TwinList } from "../twins/TwinList";

/**
 * Open (the canvas bar's Open…, Ctrl+O): the twin catalog -- the Twins page's
 * list, as a searchable picker -- and picking a twin opens it on the canvas.
 */
export function OpenDesignDialog({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-backdrop" data-testid="open-backdrop" onClick={onClose}>
      <div className="modal modal-wide panel" role="dialog" aria-modal="true" aria-label="Open a twin" data-testid="open-dialog" onClick={(e) => e.stopPropagation()}>
        <h2 style={{ marginTop: 0 }}>Open a twin</h2>
        <TwinList mode="pick" onPick={onClose} />
        <div className="row-actions">
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
