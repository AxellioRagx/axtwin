/**
 * Destroy a twin (rule 6): state the cascade -- exercise, traffic, taps, then
 * the twin itself -- and require the deploy id typed back before the button
 * arms. The request is synchronous server-side (it returns when the teardown
 * does), so the dialog shows elapsed time while it waits, then the result.
 */
import { useEffect, useRef, useState } from "react";

import { asApiError } from "../../api/hooks";
import { useRuntimeApi } from "../../api/runtimeContext";
import type { DestroyResponse, TwinHostsDoc } from "../../api/runtimeTypes";
import { plural } from "../../model/format";
import { useWriteAccess } from "../shell/writeAccess";
import { ProtectionOverride, typedMatches } from "../shell/ProtectionOverride";

/** The typed confirmation must equal the deploy id exactly (surrounding
 *  whitespace forgiven -- a paste often carries it). */
export function destroyArmed(typed: string, deployId: string): boolean {
  return typedMatches(typed, deployId);
}

/** The cascade, in the order the server tears it down, in plain words. */
export function cascadeLines(doc: Pick<TwinHostsDoc, "tfc" | "tap">, hostCount: number): string[] {
  return [
    "any exercise run against this twin",
    doc.tfc ? `the traffic personas (traffic deploy ${doc.tfc.deploy_id})` : "traffic personas: none recorded",
    doc.tap ? `the taps mirroring it to sensors (tap deploy ${doc.tap.deploy_id})` : "taps: none recorded",
    `the twin itself: ${plural(hostCount, "VM")} and its networks`,
  ];
}

export function DestroyDialog(props: {
  deployId: string;
  target: string | null;
  twinName: string | null;
  hostCount: number;
  doc: Pick<TwinHostsDoc, "tfc" | "tap" | "target" | "protected">;
  /** An override the operator already typed on the page (the locked Destroy
   *  button's step); the destroy's own typed id is still required. */
  override?: string | null;
  onClose: () => void;
  onDestroyed: () => void;
}): JSX.Element {
  const api = useRuntimeApi();
  const access = useWriteAccess();
  const [typed, setTyped] = useState("");
  const [phase, setPhase] = useState<"confirm" | "running" | "done" | "failed">("confirm");
  const [result, setResult] = useState<DestroyResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [now, setNow] = useState(Date.now());
  const inputRef = useRef<HTMLInputElement | null>(null);
  const submitting = useRef(false);
  const [override, setOverride] = useState<string | null>(props.override ?? null);
  const [lockedByServer, setLockedByServer] = useState(false);
  const isProtected = !!props.doc.protected || lockedByServer;

  useEffect(() => inputRef.current?.focus(), []);
  useEffect(() => {
    if (phase !== "running") return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [phase]);

  // A protected twin needs BOTH typed steps: the override, then the destroy.
  const armed = destroyArmed(typed, props.deployId) && phase === "confirm" && (!isProtected || override !== null);
  const cluster = props.doc.target ?? props.target ?? "this cluster";

  const run = async (): Promise<void> => {
    if (submitting.current || !armed) return;
    submitting.current = true;
    setPhase("running");
    setStartedAt(Date.now());
    try {
      const res = await access.runWrite(`Destroying ${props.deployId}`, () =>
        api.destroyDeploy(props.deployId, props.target, isProtected ? override : null),
      );
      setResult(res);
      setPhase("done");
    } catch (cause) {
      const error = asApiError(cause);
      if (error.kind === "locked") {
        // The server knows it is protected even if the page did not: back to
        // the confirm step, with the override step now shown.
        setLockedByServer(true);
        setOverride(null);
        setError("This twin is protected, so the server refused to destroy it. Override protection below to destroy it anyway.");
        setPhase("confirm");
        return;
      }
      setError(error.message);
      setPhase("failed");
    } finally {
      submitting.current = false;
    }
  };

  return (
    <div className="modal-backdrop">
      <div
        className="modal danger"
        role="dialog"
        aria-modal="true"
        aria-labelledby="destroy-title"
        data-testid="destroy-dialog"
        onKeyDown={(e) => {
          if (e.key === "Escape" && phase !== "running") {
            e.stopPropagation();
            props.onClose();
          }
        }}
      >
        <h2 id="destroy-title">
          Destroy {props.twinName ?? "twin"} <span className="mono">{props.deployId}</span> on {cluster}?
        </h2>
        <p>This removes, in order:</p>
        <ol data-testid="destroy-cascade">
          {cascadeLines(props.doc, props.hostCount).map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ol>
        <p>It cannot be undone. Everything running on these VMs is lost.</p>
        {phase === "confirm" && error && (
          <p className="notice is-error" role="alert" data-testid="destroy-locked">
            {error}
          </p>
        )}
        {phase === "confirm" && isProtected && (
          <ProtectionOverride deployId={props.deployId} value={override} onChange={setOverride} what="destroying it" />
        )}
        {phase === "confirm" && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void run();
            }}
          >
            <label className="field">
              <span>
                Type <span className="mono">{props.deployId}</span> to confirm
              </span>
              <input
                ref={inputRef}
                value={typed}
                autoComplete="off"
                spellCheck={false}
                onChange={(e) => setTyped(e.target.value)}
                data-testid="destroy-typed"
              />
            </label>
            <div className="actions">
              <button type="submit" className="btn danger" disabled={!armed} data-testid="destroy-confirm">
                Destroy {props.deployId}
              </button>
              <button type="button" className="btn" onClick={props.onClose}>
                Cancel
              </button>
            </div>
          </form>
        )}
        {phase === "running" && (
          <p className="progress" role="status" data-testid="destroy-progress">
            Destroying… {startedAt ? Math.round((now - startedAt) / 1000) : 0} s. The server answers when the teardown
            finishes; this can take several minutes.
          </p>
        )}
        {phase === "failed" && (
          <div role="alert" className="notice is-error" data-testid="destroy-failed">
            <p>
              <strong>Destroy failed.</strong> {error}
            </p>
            <button type="button" className="btn" onClick={props.onClose}>
              Close
            </button>
          </div>
        )}
        {phase === "done" && result && (
          <div role="status" data-testid="destroy-result" className={`notice${result.destroyed ? "" : " is-error"}`}>
            <p>
              <strong>{result.destroyed ? "Destroyed." : "Not fully destroyed."}</strong>{" "}
              {plural(result.removed, "resource")} removed, {plural(result.errors, "error")}.
            </p>
            {result.cascade_errors.length > 0 && (
              <ul>
                {result.cascade_errors.map((c) => (
                  <li key={c}>Still standing and needs a follow-up destroy: {c}</li>
                ))}
              </ul>
            )}
            {result.actions.filter((a) => a.status === "error" || a.status === "guard-violation").map((a) => (
              <p key={`${a.kind}:${a.target}`} className="meta">
                {a.kind} {a.target}: {a.status}
                {a.detail ? ` — ${a.detail}` : ""}
              </p>
            ))}
            <button type="button" className="btn" onClick={result.destroyed ? props.onDestroyed : props.onClose}>
              {result.destroyed ? "Back to the Range" : "Close"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
