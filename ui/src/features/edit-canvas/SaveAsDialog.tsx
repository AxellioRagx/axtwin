import { useEffect, useMemo, useState, type FormEvent } from "react";

import { useApi } from "../../api/context";
import { designIdProblem } from "../../areas/canvas/newDesign";
import { useLoad } from "../../areas/shell/useLoad";

/**
 * The first Save of an untitled design: the id it takes in the catalog
 * (the API's id rule, and not one the catalog already holds -- both checked
 * here, before any request) and an optional description.
 *
 * `kind: "draft"` is Save as draft (`api/Spec.md` A3d): the same id rules,
 * for a copy written WITHOUT the validation gate to the drafts area; the twin
 * it came from (`origin`) is never touched.
 */
export function SaveAsDialog({ suggestedId = "", busy, error, onSave, onClose, kind = "design", origin = null }: { suggestedId?: string; busy: boolean; error: string | null; onSave: (id: string, description: string) => void; onClose: () => void; kind?: "design" | "draft"; origin?: string | null }) {
  const api = useApi();
  const draft = kind === "draft";
  // The ids in use: the twin-configs, the twins (a cast twin's folder is an id too) and the drafts (one id space).
  const catalog = useLoad(async (signal) => {
    const [configs, twins, drafts] = await Promise.all([api.listTwinConfigs(signal).catch(() => ({ twin_configs: [] })), api.listTwins(signal).catch(() => ({ imprints: [], skipped: [] })), api.listDrafts(signal).catch(() => ({ drafts: [] }))]);
    return new Set([...configs.twin_configs.map((c) => c.id), ...twins.imprints.map((t) => t.id), ...drafts.drafts.map((d) => d.id)]);
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
    <div className="modal-backdrop" data-testid={draft ? "draft-backdrop" : "saveas-backdrop"}>
      <form className="modal panel" role="dialog" aria-modal="true" aria-label={draft ? "Save as draft" : "Save design"} data-testid={draft ? "draft-dialog" : "saveas-dialog"} onSubmit={submit}>
        <h2 style={{ marginTop: 0 }}>{draft ? "Save as draft" : "Save the design"}</h2>
        {draft ? (
          <p className="muted">
            A draft is a copy kept without the validation gate, under its own id{origin ? <>; <span className="mono">{origin}</span> is not changed</> : ""}. A draft is never deployed{origin ? <>; once it passes Check it can replace <span className="mono">{origin}</span></> : ""}.
          </p>
        ) : (
          <p className="muted">It is untitled: name it, and it is written to the catalog under that id{suggestedId ? " (the twin it was cloned from is not changed)" : ""}.</p>
        )}
        <div className="field">
          <label htmlFor="sa-id">Id</label>
          <input id="sa-id" type="text" value={id} onChange={(e) => setId(e.target.value)} placeholder={draft ? "my-lab-draft" : "my-lab"} autoComplete="off" autoFocus />
          <span className="hint">{draft ? "the draft's own name" : "the twin's name in the catalog"}; letters, digits, '.', '_' and '-'</span>
        </div>
        {draft ? null : (
          <div className="field">
            <label htmlFor="sa-description">Description</label>
            <input id="sa-description" type="text" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="what this twin is for (optional)" autoComplete="off" />
          </div>
        )}
        {id && problem ? (
          <p className="hint" data-testid="saveas-problem">
            {problem}
          </p>
        ) : null}
        {!catalog.data ? <p className="muted">reading the catalog's ids…</p> : null}
        {error && !busy ? (
          <div className="error" role="alert" data-testid="saveas-error">
            could not save the {draft ? "draft" : "design"}: {error}
          </div>
        ) : null}
        <div className="row-actions">
          <button type="submit" className="primary" disabled={Boolean(problem) || busy || !catalog.data}>
            {busy ? "Saving…" : draft ? "Save draft" : "Save"}
          </button>
          <button type="button" onClick={onClose} disabled={busy}>
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
}
