import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { useApi } from "../../api/context";
import type { AssertionsDoc, CastResponse, Imprint } from "../../api/types";
import { formatCount } from "../../model/sources";
import { ConfirmDelete } from "../shell/ConfirmDelete";
import { ErrorLine, When } from "../shell/parts";
import { useLoad } from "../shell/useLoad";

/** One imprint: rename it, save it as a new one, edit the operator's assertions about the
 *  network, cast it into a twin, or delete it. */
export function ImprintPage() {
  const api = useApi();
  const { pit = "" } = useParams();
  const navigate = useNavigate();
  const loaded = useLoad((signal) => api.getImprint(pit, signal), [pit]);
  const [imprint, setImprint] = useState<Imprint | null>(null);
  const [castOpen, setCastOpen] = useState(false);
  useEffect(() => {
    if (loaded.data) setImprint(loaded.data);
  }, [loaded.data]);
  const data = imprint;
  return (
    <>
      <div className="row">
        <h1 style={{ flex: 1 }}>Imprint {data ? data.name : pit}</h1>
        {data ? (
          <button className="primary" type="button" aria-expanded={castOpen} onClick={() => setCastOpen((o) => !o)}>
            Cast into a twin
          </button>
        ) : null}
        {data ? (
          <ConfirmDelete label="Delete imprint" target={data.name} confirmText="Confirm: remove the imprint and its assertions" onDelete={() => api.deleteImprint(data.pit_id)} onDeleted={() => navigate("/imprints")} />
        ) : null}
      </div>
      <ErrorLine error={loaded.error} prefix="could not load the imprint" />
      {loaded.loading && !data ? <p className="muted">Loading…</p> : null}
      {data ? (
        <>
          {castOpen ? <CastForm imprint={data} onCast={(r) => setImprint({ ...data, twins: data.twins.some((t) => t.id === r.twin.id) ? data.twins : [...data.twins, { id: r.twin.id }] })} /> : null}
          <div className="panel" data-testid="imprint-facts">
            <dl className="facts">
              <dt>Name</dt>
              <dd>
                <NameEditor imprint={data} onRenamed={(name) => setImprint({ ...data, name })} />
              </dd>
              <dt>Id</dt>
              <dd className="mono">{data.pit_id}</dd>
              {data.derived_from ? (
                <>
                  <dt>Derived from</dt>
                  <dd className="mono">
                    <Link className="navlink" to={`/imprints/${encodeURIComponent(data.derived_from)}`}>{data.derived_from}</Link>
                  </dd>
                </>
              ) : null}
              <dt>As of</dt>
              <dd>
                <When value={data.as_of} />
              </dd>
              <dt>Observed</dt>
              <dd>
                {data.observed_from != null && data.observed_to != null ? (
                  <>
                    <When value={data.observed_from} /> → <When value={data.observed_to} />
                  </>
                ) : (
                  "—"
                )}
              </dd>
              <dt>Source</dt>
              <dd className="mono">{data.source ?? "—"}</dd>
              <dt>Network / vantage</dt>
              <dd>
                {data.network ?? "—"} / {data.vantage ?? "—"}
              </dd>
              <dt>Entities / segments / windows</dt>
              <dd>
                {formatCount(data.entities)} / {formatCount(data.segments)} / {formatCount(data.windows)}
              </dd>
              <dt>Twins cast from it</dt>
              <dd>
                {data.twins.length ? data.twins.map((t, k) => (
                  <span key={t.id}>
                    {k ? ", " : ""}
                    <Link className="navlink" to={`/twins/${encodeURIComponent(t.id)}`}>{t.id}</Link>
                  </span>
                )) : <span className="muted">none yet</span>}
              </dd>
            </dl>
            <SaveAs imprint={data} />
          </div>
          <AssertionsEditor imprint={data} onSaved={() => setImprint({ ...data, has_assertions: true })} />
        </>
      ) : null}
    </>
  );
}

function NameEditor({ imprint, onRenamed }: { imprint: Imprint; onRenamed: (name: string) => void }) {
  const api = useApi();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function save() {
    setBusy(true);
    setError(null);
    try {
      const r = await api.renameImprint(imprint.pit_id, draft.trim());
      onRenamed(r.name);
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
          <input aria-label="Imprint name" value={draft} onChange={(e) => setDraft(e.target.value)} disabled={busy} autoFocus />
          <button className="primary" type="submit" disabled={busy || !draft.trim()}>
            {busy ? "Saving…" : "Save name"}
          </button>
          <button type="button" onClick={() => setEditing(false)} disabled={busy}>
            Cancel
          </button>
        </form>
      ) : (
        <>
          <strong>{imprint.name}</strong>{" "}
          <button
            className="icon"
            type="button"
            title="Rename imprint"
            aria-label="Rename imprint"
            onClick={() => {
              setDraft(imprint.name);
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

function SaveAs({ imprint }: { imprint: Imprint }) {
  const api = useApi();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function copy(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const c = await api.copyImprint(imprint.pit_id, name.trim());
      navigate(`/imprints/${encodeURIComponent(c.pit_id)}`);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="row-actions" onSubmit={copy} aria-label="Save as">
      <input aria-label="Save as name" placeholder="save as a new imprint…" value={name} onChange={(e) => setName(e.target.value)} disabled={busy} />
      <button type="submit" disabled={busy || !name.trim()}>
        {busy ? "Copying…" : "Save as"}
      </button>
      <ErrorLine error={error} prefix="could not copy" />
    </form>
  );
}

/** The record lists the JSON box edits: the three core ones always, `confirm` / `doubt` when
 *  the document holds any (a save used to drop them). */
export function editableText(doc: AssertionsDoc): string {
  const out: Record<string, unknown> = { add: doc.add, override: doc.override, suppress: doc.suppress };
  if (doc.confirm?.length) out.confirm = doc.confirm;
  if (doc.doubt?.length) out.doubt = doc.doubt;
  return JSON.stringify(out, null, 2);
}

/** The record lists of the assertions document, edited as JSON. The etag the GET
 *  carried goes back inside the body, so a save over someone else's is refused. */
function AssertionsEditor({ imprint, onSaved }: { imprint: Imprint; onSaved: () => void }) {
  const api = useApi();
  const loaded = useLoad((signal) => api.getAssertions(imprint.pit_id, signal), [imprint.pit_id]);
  const [doc, setDoc] = useState<AssertionsDoc | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  useEffect(() => {
    if (loaded.data) {
      setDoc(loaded.data);
      setText(editableText(loaded.data));
    }
  }, [loaded.data]);
  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!doc) return;
    setError(null);
    let edited: Record<string, unknown>;
    try {
      edited = JSON.parse(text) as Record<string, unknown>;
    } catch (err) {
      setError(new Error(`not valid JSON: ${String((err as Error).message)}`));
      return;
    }
    setBusy(true);
    try {
      const body: Record<string, unknown> = {
        schema: doc.schema,
        imprint: doc.imprint,
        as_of: doc.as_of ?? new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
        source: doc.source ?? "operator:console",
        add: edited.add ?? [],
        override: edited.override ?? [],
        suppress: edited.suppress ?? [],
      };
      // `confirm` / `doubt` ride along (the text shows them when the document has any); an
      // emptied block is sent as nothing, which is how the server stores an empty one.
      for (const mark of ["confirm", "doubt"] as const) {
        const recs = edited[mark];
        if (Array.isArray(recs) && recs.length) body[mark] = recs;
      }
      if (doc.etag) body.etag = doc.etag;
      const saved = await api.putAssertions(imprint.pit_id, body);
      setDoc(saved);
      setText(editableText(saved));
      setSavedAt(new Date().toISOString());
      onSaved();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="panel" onSubmit={save} aria-label="Assertions">
      <h2 style={{ marginTop: 0 }}>Assertions</h2>
      <p className="muted">
        The operator's claims about the network, applied by the compiler at cast time: <code>add</code> what discovery missed, <code>override</code> what it got wrong, <code>suppress</code> what it should not have seen.
        {doc?.exists ? " Saved on disk." : " Nothing asserted yet."}
      </p>
      <ErrorLine error={loaded.error} prefix="could not load the assertions" />
      <AssertionList doc={doc} />
      <details className="advanced">
        <summary>Advanced: the assertions document as JSON</summary>
        <textarea aria-label="Assertions document" value={text} onChange={(e) => setText(e.target.value)} rows={12} disabled={busy || !doc} spellCheck={false} />
        <div className="row-actions">
          <button className="primary" type="submit" disabled={busy || !doc}>
            {busy ? "Saving…" : "Save assertions"}
          </button>
          {savedAt ? <span className="muted">saved</span> : null}
        </div>
      </details>
      {doc?.warnings?.length ? (
        <ul className="muted">
          {doc.warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      ) : null}
      <ErrorLine error={error} prefix="could not save" />
    </form>
  );
}

const ASSERTION_KINDS = ["add", "override", "suppress", "confirm", "doubt"] as const;

/** One line per assertion with where it came from: the record's own `note` (every add and
 *  suppress carries one) and the document's `source` (`operator:<name>`) and `as_of`. */
function AssertionList({ doc }: { doc: AssertionsDoc | null }) {
  if (!doc) return null;
  const rows = ASSERTION_KINDS.flatMap((group) => (doc[group] ?? []).map((rec) => ({ group, rec })));
  if (rows.length === 0) return <p className="muted">No assertion yet.</p>;
  return (
    <table className="list" aria-label="Assertions list">
      <thead>
        <tr>
          <th>Kind</th>
          <th>About</th>
          <th>What</th>
          <th>Why</th>
          <th>Origin</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(({ group, rec }, i) => (
          <tr key={i} data-testid="assertion-row">
            <td>
              <span className="badge">{group}</span>
            </td>
            <td className="mono">{String(rec.key ?? "—")}</td>
            <td>{assertionWhat(group, rec)}</td>
            <td>{typeof rec.note === "string" && rec.note ? rec.note : <span className="muted">—</span>}</td>
            <td className="muted">
              {doc.source ?? "unknown"}
              {doc.as_of ? (
                <>
                  {" · "}
                  <When value={doc.as_of} />
                </>
              ) : null}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function assertionWhat(group: string, rec: Record<string, unknown>): string {
  if (group === "override") return `${String(rec.field ?? "?")} = ${JSON.stringify(rec.value)}`;
  if (group === "add") {
    const rest = Object.entries(rec)
      .filter(([k]) => k !== "key" && k !== "note")
      .map(([k, v]) => `${k} ${typeof v === "string" ? v : JSON.stringify(v)}`);
    return rest.join(", ") || "—";
  }
  return group === "suppress" ? "left out of the twin" : group === "confirm" ? "pinned as asserted" : "marked unconfirmed";
}

function CastForm({ imprint, onCast }: { imprint: Imprint; onCast: (r: CastResponse) => void }) {
  const api = useApi();
  const [twinId, setTwinId] = useState(imprint.name.replace(/[^A-Za-z0-9._-]+/g, "-"));
  const [sensors, setSensors] = useState("");
  const [replace, setReplace] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<CastResponse | null>(null);
  async function cast(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setResult(null);
    setBusy(true);
    try {
      const r = await api.castImprint(imprint.pit_id, {
        twin_id: twinId.trim(),
        sensors: sensors.split(",").map((s) => s.trim()).filter(Boolean),
        replace,
      });
      setResult(r);
      onCast(r);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="panel" onSubmit={cast} aria-label="Cast">
      <h2 style={{ marginTop: 0 }}>Cast into a twin</h2>
      <div className="fields-2">
        <div className="field">
          <label htmlFor="cast-twin">Twin id</label>
          <input id="cast-twin" type="text" value={twinId} onChange={(e) => setTwinId(e.target.value)} disabled={busy} />
          <span className="hint">The catalog id the twin lands under. An existing id is refused unless you choose to replace it.</span>
        </div>
        <div className="field">
          <label htmlFor="cast-sensors">Sensors</label>
          <input id="cast-sensors" type="text" value={sensors} onChange={(e) => setSensors(e.target.value)} placeholder="XPress-SW-A, …" disabled={busy} />
          <span className="hint">Lab sensor names for the tap plan, comma separated; empty leaves them unfilled.</span>
        </div>
      </div>
      <label className="choice">
        <input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} disabled={busy} /> Replace the twin if that id exists
      </label>
      <div className="row-actions">
        <button className="primary" type="submit" disabled={busy || !twinId.trim()}>
          {busy ? "Casting…" : "Cast"}
        </button>
      </div>
      {result ? (
        <div className="notice" role="status">
          {result.replaced ? "Replaced" : "Cast"} twin <Link className="navlink" to={`/twins/${encodeURIComponent(result.twin.id)}`}>{result.twin.id}</Link>: {formatCount(result.twin.hosts)} hosts, {formatCount(result.twin.segments)} segments
          {result.caveats.length ? `, ${result.caveats.length} caveat(s)` : ""}.
          {result.caveats.length ? (
            <ul className="muted">
              {result.caveats.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
      <ErrorLine error={error} prefix="could not cast" />
    </form>
  );
}
