import { useState } from "react";

import { Link } from "react-router";

import { useApi } from "../../api/context";
import type { DiscoveryRun } from "../../api/types";
import { ErrorLine } from "../shell/parts";

/** The imprint a run baked: its name (a link to it), with the id beside it once they
 *  differ, and a rename behind an edit button. The id is the identity and never changes. */
export function PitName({ run, onRenamed }: { run: DiscoveryRun; onRenamed: (name: string) => void }) {
  const api = useApi();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  if (!run.pit_id) return <span className="muted">—</span>;
  const name = run.pit_name ?? run.pit_id;
  async function save() {
    if (!run.pit_id) return;
    setBusy(true);
    setError(null);
    try {
      const pit = await api.renameImprint(run.pit_id, draft.trim());
      onRenamed(pit.name);
      setEditing(false);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <span className="pit">
      {editing ? (
        <form
          className="row-actions"
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <input aria-label="PIT name" value={draft} onChange={(e) => setDraft(e.target.value)} disabled={busy} autoFocus />
          <button className="primary" type="submit" disabled={busy || !draft.trim()}>
            {busy ? "Saving…" : "Save name"}
          </button>
          <button type="button" onClick={() => setEditing(false)} disabled={busy}>
            Cancel
          </button>
        </form>
      ) : (
        <>
          <Link to={`/imprints/${encodeURIComponent(run.pit_id)}`}>
            <strong>{name}</strong>
          </Link>
          {name !== run.pit_id ? (
            <>
              {" "}
              <code className="muted">{run.pit_id}</code>
            </>
          ) : null}{" "}
          <button
            className="icon"
            type="button"
            title="Rename PIT"
            aria-label="Rename PIT"
            onClick={() => {
              setDraft(name);
              setEditing(true);
            }}
          >
            ✎
          </button>
        </>
      )}
      <ErrorLine error={error} prefix="could not rename" />
    </span>
  );
}
