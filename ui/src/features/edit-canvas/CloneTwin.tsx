import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";

import { useApi } from "../../api/context";
import { UNTITLED_ID } from "../../areas/canvas/newDesign";
import { ErrorLine } from "../../areas/shell/parts";
import { clearUntitled, writeUntitled, writeUntitledLayout } from "./CanvasPage";
import { toLayout, fromLayoutList } from "./edit/annotations";
import { renamedFiles } from "./edit/document";
import { pinsOf } from "./edit/useLayoutDraft";

/**
 * `/canvas/new?from=<id>` (Open's Clone): the twin's saved twin-config --
 * the catalog's baseline, not a deploy's working copy -- becomes the untitled
 * design, its ids renamed to the placeholder, its layout pins and annotations
 * carried along; then the page moves to `/canvas/new`, the untitled canvas,
 * which reads it from the browser. Nothing is written to the server: the
 * clone reaches the catalog on its first Save, a Save-As under a new id,
 * and the source twin is never changed.
 */
export function CloneTwin({ from }: { from: string }) {
  const api = useApi();
  const navigate = useNavigate();
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    const ctl = new AbortController();
    (async () => {
      const detail = await api.getTwinConfig(from, ctl.signal);
      if (!detail.document) throw new Error(`the API serves twin-config '${from}' without its document (it serves one only to a caller that may write); sign in to clone it`);
      const doc = detail.document;
      const layout = await api.getLayout(from, ctl.signal).catch(() => null);
      if (ctl.signal.aborted) return;
      clearUntitled();
      writeUntitled(renamedFiles(doc.files, doc.te_file, UNTITLED_ID), { done: [], undone: [] }, { teFile: doc.te_file, source: from });
      writeUntitledLayout(pinsOf(layout), fromLayoutList(layout?.annotations ?? []).map(toLayout));
      navigate("/canvas/new", { replace: true });
    })().catch((e: unknown) => {
      if (!ctl.signal.aborted) setError(e);
    });
    return () => ctl.abort();
  }, [api, from, navigate]);
  return (
    <div className="panel" style={{ margin: 24 }} data-testid="clone-twin">
      {error ? (
        <>
          <ErrorLine error={error} prefix={`could not clone ${from}`} />
          <p>
            <Link to="/">Start a blank design</Link>
          </p>
        </>
      ) : (
        <p className="muted">Cloning {from}…</p>
      )}
    </div>
  );
}
