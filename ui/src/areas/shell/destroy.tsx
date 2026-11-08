import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { useApi } from "../../api/context";
import type { Api } from "../../api/discovery";
import type { DeploySummary, DestroyResult } from "../../api/types";
import { ErrorLine } from "./parts";

// Destroy (2026-11-08): bring a twin's standing deploy down from the console, the engine's own
// verb (`axrange te destroy`, `DELETE /api/deploys/{id}`), which cascades to the tfc and tap
// children first and releases the tap's sensor binding. One confirm dialog, three places: the
// Twins list, the twin page and the canvas bar. Vocabulary (ui/Spec.md): Deploy stands a twin
// up, Destroy brings it down; Delete is for a twin config in the catalog, never for a deploy.

/** The child tiers a destroy brings down before the te, in the order the engine cascades them. */
export const DESTROY_CHILD_TIERS = ["tfc", "tap"] as const;
/** How often the deploy rows are read while a destroy runs, to tick the tiers off. */
const DESTROY_POLL_MS = 1500;

/** The tiers standing on a te deploy, te first: what a destroy brings down. */
export function standingTiers(rows: readonly DeploySummary[], teDeployId: string): string[] {
  const out = ["te"];
  for (const tier of DESTROY_CHILD_TIERS) {
    if (rows.some((d) => d.scenario_id === `${tier}-${teDeployId}` && d.status !== "destroyed")) out.push(tier);
  }
  return out;
}

export type DestroyRun = {
  deployId: string;
  /** The tiers that were standing, te first. */
  tiers: string[];
  /** The tiers seen down so far. */
  down: string[];
  elapsed: number;
  phase: "running" | "done" | "failed";
  error: unknown;
  result: DestroyResult | null;
};

/** The tier coming down now: the children first (tfc, then tap), the te last. */
export function currentTier(run: DestroyRun): string | null {
  for (const t of [...DESTROY_CHILD_TIERS, "te"]) if (run.tiers.includes(t) && !run.down.includes(t)) return t;
  return null;
}

/** The bar's progress word, as Apply's: `destroying… te · 12 s`. */
export function destroyWord(run: DestroyRun | null): string | null {
  if (!run || run.phase !== "running") return null;
  return `destroying… ${currentTier(run) ?? "te"} · ${Math.round(run.elapsed)} s`;
}

/** The reasons a finished destroy left something standing, in the API's words. */
function leftStanding(r: DestroyResult): string | null {
  const lines = [
    ...r.cascade_errors,
    ...r.actions.filter((a) => a.status === "error" || a.status === "guard-violation").map((a) => `${a.kind} ${a.target}: ${a.status}${a.detail ? ` — ${a.detail}` : ""}`),
  ];
  if (!r.destroyed && !lines.length) lines.push(`deploy ${r.deploy_id} was not marked destroyed (${r.errors} error${r.errors === 1 ? "" : "s"})`);
  return lines.length ? lines.join("\n") : null;
}

/** Run one destroy: the DELETE, a timer and a poll of the deploy rows that ticks tiers off as
 *  they come down. `onDone` runs once, on success. */
export function useDestroy(api: Api, onDone: (r: DestroyResult) => void) {
  const [run, setRun] = useState<DestroyRun | null>(null);
  const done = useRef(onDone);
  done.current = onDone;
  const live = useRef(true);
  useEffect(() => () => void (live.current = false), []);

  const running = run?.phase === "running";
  const deployId = run?.deployId ?? null;
  useEffect(() => {
    if (!running || !deployId) return;
    const started = Date.now() - (run?.elapsed ?? 0) * 1000;
    const tick = setInterval(() => setRun((r) => (r && r.phase === "running" ? { ...r, elapsed: (Date.now() - started) / 1000 } : r)), 1000);
    const poll = setInterval(() => {
      api.listDeploys().then(
        (l) => {
          const standing = standingTiers(l.deploys, deployId);
          const teDown = l.deploys.some((d) => d.deploy_id === deployId && d.status === "destroyed");
          setRun((r) => {
            if (!r || r.phase !== "running") return r;
            const down = r.tiers.filter((t) => (t === "te" ? teDown : !standing.includes(t)));
            return down.length === r.down.length ? r : { ...r, down };
          });
        },
        () => undefined,
      );
    }, DESTROY_POLL_MS);
    return () => {
      clearInterval(tick);
      clearInterval(poll);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running, deployId, api]);

  const start = useCallback(
    (id: string, tiers: string[]) => {
      setRun({ deployId: id, tiers, down: [], elapsed: 0, phase: "running", error: null, result: null });
      api.destroyDeploy(id).then(
        (r) => {
          if (!live.current) return;
          const problem = leftStanding(r);
          const down = (r.tiers ?? []).filter((t) => t.destroyed).map((t) => t.tier);
          setRun((prev) => (prev ? { ...prev, down: problem ? prev.tiers.filter((t) => down.includes(t)) : prev.tiers, phase: problem ? "failed" : "done", error: problem ? new Error(problem) : null, result: r } : prev));
          if (!problem) done.current(r);
        },
        (e: unknown) => {
          if (!live.current) return;
          setRun((prev) => (prev ? { ...prev, phase: "failed", error: e } : prev));
        },
      );
    },
    [api],
  );
  const clear = useCallback(() => setRun(null), []);
  return { run, start, clear };
}

/** The tiers as pills, ticked off as they come down. */
export function DestroyTiers({ run, tiers }: { run: DestroyRun | null; tiers: string[] }) {
  return (
    <span className="destroy-tiers" data-testid="destroy-tiers">
      {tiers.map((t, k) => {
        const down = run?.down.includes(t) ?? false;
        const now = run?.phase === "running" && currentTier(run) === t;
        return (
          <span key={t}>
            {k ? " · " : ""}
            <span className={`destroy-tier${down ? " down" : ""}${now ? " now" : ""}`} data-testid={`destroy-tier-${t}`} data-down={down || undefined}>
              {down ? "✓ " : ""}
              {t}
            </span>
          </span>
        );
      })}
    </span>
  );
}

/** The one confirm for Destroy. Names the twin, the target, the deploy id and the tiers coming
 *  down, and that the tap's sensor binding is released. A dirty working copy must be
 *  acknowledged with the checkbox, or persisted first. */
export function DestroyDialog({
  twinId,
  deploy,
  tiers,
  run,
  dirty = false,
  onPersistFirst,
  onConfirm,
  onClose,
}: {
  twinId: string;
  deploy: Pick<DeploySummary, "deploy_id" | "target">;
  tiers: string[];
  run: DestroyRun | null;
  dirty?: boolean;
  onPersistFirst?: () => void;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const [discard, setDiscard] = useState(false);
  const busy = run?.phase === "running";
  const tap = tiers.includes("tap");
  useEffect(() => {
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [busy, onClose]);
  // A portal, and no click leaves it: the Twins list's rows navigate on a click.
  return createPortal(
    <div className="modal-backdrop" data-testid="destroy-backdrop" onClick={(e) => e.stopPropagation()}>
      <div className="modal panel" role="dialog" aria-modal="true" aria-label={`Destroy the deploy of ${twinId}`} data-testid="destroy-dialog">
        <h2 style={{ marginTop: 0 }}>Destroy the deploy of {twinId}?</h2>
        <dl className="facts">
          <dt>Twin</dt>
          <dd className="mono">{twinId}</dd>
          <dt>Target</dt>
          <dd>{deploy.target ?? <span className="muted">the server&apos;s default</span>}</dd>
          <dt>Deploy</dt>
          <dd className="mono" data-testid="destroy-deploy-id">{deploy.deploy_id}</dd>
          <dt>Coming down</dt>
          <dd>
            <DestroyTiers run={run} tiers={tiers} />
          </dd>
        </dl>
        <p data-testid="destroy-binding">{tap ? "The tap's sensor binding is released: its gretaps and mirrors to the sensors are removed." : "No tap plan stands, so no sensor binding is held."}</p>
        <p className="muted">Every VM of the twin is removed. The twin config stays in the catalog; Deploy stands it up again.</p>
        {dirty ? (
          <div className="warn" data-testid="destroy-dirty">
            <p style={{ margin: 0 }}>Unapplied changes will be discarded.</p>
            <label>
              <input type="checkbox" checked={discard} onChange={(e) => setDiscard(e.target.checked)} disabled={busy} /> Discard the unapplied changes
            </label>
          </div>
        ) : null}
        {run?.phase === "running" ? (
          <p role="status" data-testid="destroy-progress">
            <span className="cv-spinner" aria-hidden="true" /> {destroyWord(run)}
          </p>
        ) : null}
        <ErrorLine error={run?.phase === "failed" ? run.error : null} prefix="could not destroy" />
        <div className="row-actions">
          <button className="danger" type="button" onClick={onConfirm} disabled={busy || (dirty && !discard)} data-testid="destroy-confirm">
            {busy ? "Destroying…" : `Destroy ${deploy.deploy_id}`}
          </button>
          {dirty && onPersistFirst ? (
            <button type="button" onClick={onPersistFirst} disabled={busy} data-testid="destroy-persist-first">
              Persist first
            </button>
          ) : null}
          <button type="button" onClick={onClose} disabled={busy}>
            Keep
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/** "Destroy…" and its confirm, for a page that is not the canvas (the Twins list, the twin
 *  page). `onDestroyed` runs when everything came down; the dialog then closes. */
export function DestroyControl({ twinId, deploy, rows, onDestroyed, open: openFromOutside = false, onOpenChange }: { twinId: string; deploy: DeploySummary; rows: readonly DeploySummary[]; onDestroyed: () => void; open?: boolean; onOpenChange?: (open: boolean) => void }) {
  const api = useApi();
  const [openSelf, setOpenSelf] = useState(false);
  const open = openSelf || openFromOutside;
  const setOpen = (v: boolean) => {
    setOpenSelf(v);
    onOpenChange?.(v);
  };
  const { run, start, clear } = useDestroy(api, () => {
    setOpen(false);
    clear();
    onDestroyed();
  });
  const tiers = run?.tiers ?? standingTiers(rows, deploy.deploy_id);
  return (
    <>
      <button className="danger" type="button" onClick={() => setOpen(true)} aria-label={`Destroy the deploy of ${twinId}…`} data-testid="destroy-open">
        Destroy…
      </button>
      {open ? (
        <DestroyDialog
          twinId={twinId}
          deploy={deploy}
          tiers={tiers}
          run={run}
          onConfirm={() => start(deploy.deploy_id, tiers)}
          onClose={() => {
            setOpen(false);
            clear();
          }}
        />
      ) : null}
    </>
  );
}
