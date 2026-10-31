import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router";

import { ApiError } from "../../api/client";
import { useApi } from "../../api/context";
import { ErrorLine } from "../shell/parts";
import { useLoad } from "../shell/useLoad";
import { DEFAULT_CIDR, DEFAULT_SEGMENT, blankDesignFiles, blankDesignProblem, copiedDesignFiles } from "./newDesign";

const BLANK = "";

/** `/canvas/new`: a new twin-config, blank or copied from one in the
 *  catalog, written through A3 and opened on the canvas. */
export function NewDesignPage() {
  const api = useApi();
  const navigate = useNavigate();
  const catalog = useLoad((signal) => api.listTwinConfigs(signal).catch(() => ({ twin_configs: [] })), []);
  const [id, setId] = useState("");
  const [description, setDescription] = useState("");
  const [from, setFrom] = useState(BLANK);
  const [segment, setSegment] = useState(DEFAULT_SEGMENT);
  const [cidr, setCidr] = useState(DEFAULT_CIDR);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const sources = (catalog.data?.twin_configs ?? []).filter((c) => !c.error);
  const problem = from === BLANK ? blankDesignProblem({ id, description, segment, cidr }) : blankDesignProblem({ id, description, segment: DEFAULT_SEGMENT, cidr: DEFAULT_CIDR });

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (problem || busy) return;
    setBusy(true);
    setError(null);
    try {
      let files: Record<string, string>;
      if (from === BLANK) files = blankDesignFiles({ id, description, segment, cidr });
      else {
        const source = await api.getTwinConfig(from);
        if (!source.document) throw new ApiError({ kind: "unauthorized", status: 401, message: `twin-config '${from}' served no document; the write token is needed to read one`, url: "" });
        files = copiedDesignFiles(source.document, id);
      }
      await api.createTwinConfig({ id, files });
      navigate(`/canvas/${encodeURIComponent(id)}`);
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  };

  return (
    <>
      <h1>New design</h1>
      <p className="muted">A new twin-config in the catalog, opened on the canvas. Writing it needs the write token.</p>
      <form onSubmit={(e) => void submit(e)} className="panel" aria-label="New design">
        <div className="fields-2">
          <div className="field">
            <label htmlFor="nd-id">Id</label>
            <input id="nd-id" type="text" value={id} onChange={(e) => setId(e.target.value)} placeholder="my-lab" autoComplete="off" autoFocus />
            <span className="hint">the twin's name in the catalog; letters, digits, '.', '_' and '-'</span>
          </div>
          <div className="field">
            <label htmlFor="nd-from">Start from</label>
            <select id="nd-from" value={from} onChange={(e) => setFrom(e.target.value)}>
              <option value={BLANK}>Blank: one empty segment</option>
              {sources.map((c) => (
                <option key={c.id} value={c.id}>
                  Copy of {c.id}
                </option>
              ))}
            </select>
            <span className="hint">a copy takes every file of the source under the new id</span>
          </div>
        </div>
        <div className="field">
          <label htmlFor="nd-description">Description</label>
          <input id="nd-description" type="text" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="what this twin is for" autoComplete="off" />
        </div>
        {from === BLANK ? (
          <div className="fields-2">
            <div className="field">
              <label htmlFor="nd-segment">First segment</label>
              <input id="nd-segment" type="text" value={segment} onChange={(e) => setSegment(e.target.value)} autoComplete="off" />
            </div>
            <div className="field">
              <label htmlFor="nd-cidr">Its network</label>
              <input id="nd-cidr" type="text" value={cidr} onChange={(e) => setCidr(e.target.value)} autoComplete="off" />
              <span className="hint">an IPv4 network in CIDR form; more segments are added on the canvas</span>
            </div>
          </div>
        ) : null}
        {id && problem ? <p className="hint" data-testid="new-design-problem">{problem}</p> : null}
        <ErrorLine error={error} prefix="could not create the design" />
        <div className="row">
          <button type="submit" className="primary" disabled={Boolean(problem) || busy}>
            {busy ? "Creating…" : "Create and open"}
          </button>
        </div>
      </form>
    </>
  );
}
