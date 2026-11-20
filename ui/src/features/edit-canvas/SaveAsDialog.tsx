import { useEffect, useMemo, useState, type FormEvent } from "react";

import { useApi } from "../../api/context";
import { designIdProblem } from "../../areas/canvas/newDesign";
import { useLoad } from "../../areas/shell/useLoad";

/**
 * The first Save of an untitled design: the id it takes in the catalog
 * (the API's id rule, and not one the catalog already holds -- both checked
 * here, before any request) and an optional description.
 */
export function SaveAsDialog({ suggestedId = "", busy, error, onSave, onClose }: { suggestedId?: string; busy: boolean; error: string | null; onSave: (id: string, description: string) => void; onClose: () => void }) {
  const api = useApi();
  // The ids in the catalog: the twin-configs and the twins (a cast twin's folder is an id too).
  const catalog = useLoad(async (signal) => {
    const [configs, twins] = await Promise.all([api.listTwinConfigs(signal).catch(() => ({ twin_configs: [] })), api.listTwins(signal).catch(() => ({ imprints: [], skipped: [] }))]);
    return new Set([...configs.twin_configs.map((c) => c.id), ...twins.imprints.map((t) => t.id)]);
  }, []);
  // A clone suggests `<source>-copy`; the operator may take it or type another.
  const [id, setId] = useState(suggestedId);
  const [description, setDescription] = useState("");
  const taken = useMemo(() => catalog.data ?? new Set<string>(), [catalog.data]);
  const problem = designIdProblem(id.trim(), taken);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [busy, onClose]);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (problem || busy || !catalog.data) return;
    onSave(id.trim(), description);
  };
  return (
    <div className="modal-backdrop" data-testid="saveas-backdrop">
      <form className="modal panel" role="dialog" aria-modal="true" aria-label="Save design" data-testid="saveas-dialog" onSubmit={submit}>
        <h2 style={{ marginTop: 0 }}>Save the design</h2>
        <p className="muted">It is untitled: name it, and it is written to the catalog under that id{suggestedId ? " (the twin it was cloned from is not changed)" : ""}.</p>
        <div className="field">
          <label htmlFor="sa-id">Id</label>
          <input id="sa-id" type="text" value={id} onChange={(e) => setId(e.target.value)} placeholder="my-lab" autoComplete="off" autoFocus />
          <span className="hint">the twin's name in the catalog; letters, digits, '.', '_' and '-'</span>
        </div>
        <div className="field">
          <label htmlFor="sa-description">Description</label>
          <input id="sa-description" type="text" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="what this twin is for (optional)" autoComplete="off" />
        </div>
        {id && problem ? (
          <p className="hint" data-testid="saveas-problem">
            {problem}
          </p>
        ) : null}
        {!catalog.data ? <p className="muted">reading the catalog's ids…</p> : null}
        {error && !busy ? (
          <div className="error" role="alert" data-testid="saveas-error">
            could not save the design: {error}
          </div>
        ) : null}
        <div className="row-actions">
          <button type="submit" className="primary" disabled={Boolean(problem) || busy || !catalog.data}>
            {busy ? "Saving…" : "Save"}
          </button>
          <button type="button" onClick={onClose} disabled={busy}>
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
}
