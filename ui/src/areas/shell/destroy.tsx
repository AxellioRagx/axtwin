import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { ApiError } from "../../api/client";
import { useApi } from "../../api/context";
import type { Api } from "../../api/discovery";
import type { DeploySummary, DestroyProgress, DestroyResult } from "../../api/types";
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
/** After a lost DELETE answer, how many idle polls with the te still standing mean it never ran. */
const LOST_STALLS = 4;

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
  /** The te sweep as the API reports it while the DELETE runs (`GET .../destroy-progress`): the
   *  VM count on a big twin is minutes of the destroy, so the word shows it instead of a bare `te`. */
  sweep?: Pick<DestroyProgress, "step" | "done" | "total"> | null;
};

/** The tier coming down now: the children first (tfc, then tap), the te last. */
export function currentTier(run: DestroyRun): string | null {
  for (const t of [...DESTROY_CHILD_TIERS, "te"]) if (run.tiers.includes(t) && !run.down.includes(t)) return t;
  return null;
}

/** What the te sweep is doing, in words: `42/96 VMs removed`, `clearing hook snippets`, `removing networks`. */
export function sweepWord(sweep: DestroyRun["sweep"]): string | null {
  if (!sweep) return null;
  if (sweep.step === "vms" && sweep.total > 0) return `${sweep.done}/${sweep.total} VMs removed`;
  if (sweep.step === "snippets") return "clearing hook snippets";
  if (sweep.step === "network") return "removing networks";
  return null;
}

/** The bar's progress word, as Apply's: `destroying… te · 12 s`, with the sweep's step once the te
 *  is coming down: `destroying… te · 42/96 VMs removed · 230 s`. */
export function destroyWord(run: DestroyRun | null): string | null {
  if (!run || run.phase !== "running") return null;
  const tier = currentTier(run) ?? "te";
  const step = tier === "te" ? sweepWord(run.sweep) : null;
  return `destroying… ${tier}${step ? ` · ${step}` : ""} · ${Math.round(run.elapsed)} s`;
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

/** What a destroy the poll saw finish reports when the DELETE's own answer never came (a proxy
 *  timeout on a long sweep): every tier down, nothing counted. */
function polledResult(deployId: string, tiers: readonly string[]): DestroyResult {
  return { deploy_id: deployId, destroyed: true, removed: 0, errors: 0, actions: [], cascade_errors: [], tiers: tiers.map((tier) => ({ tier, deploy_id: null, destroyed: true, removed: 0, errors: 0 })) };
}

/** Run one destroy: the DELETE, a timer and a poll of the deploy rows that ticks tiers off as
 *  they come down. The run ends on the DELETE's answer, or (2026-11-21) on the poll seeing every
 *  tier gone and the API's sweep idle, whichever comes first; `onDone` runs once, on success. */
export function useDestroy(api: Api, onDone: (r: DestroyResult) => void) {
  const [run, setRun] = useState<DestroyRun | null>(null);
  const done = useRef(onDone);
  done.current = onDone;
  // Mounted. Set again in the effect body: StrictMode mounts, unmounts and mounts again in dev, and
  // a cleanup-only effect left this false for good, so every DELETE answer was thrown away
  // (2026-11-21: the dialog sat on "Destroying…" after the destroy had finished).
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  // The run that has ended (by the DELETE or by the poll): a later answer for it changes nothing.
  const token = useRef(0);
  const ended = useRef(0);
  /** The tiers the current run brings down (fixed for the run). */
  const runTiers = useRef<string[]>([]);
  /** The DELETE's answer was lost (no answer, or a gateway timeout): the poll decides, and this is
   *  the error shown if it sees nothing coming down; `stalls` counts its idle, nothing-down reads. */
  const lost = useRef<{ error: unknown; stalls: number } | null>(null);
  const end = useCallback((t: number, next: (prev: DestroyRun) => DestroyRun, result: DestroyResult | null) => {
    if (!live.current || ended.current === t || token.current !== t) return;
    ended.current = t;
    setRun((prev) => (prev ? next(prev) : prev));
    if (result) done.current(result);
  }, []);

  const running = run?.phase === "running";
  const deployId = run?.deployId ?? null;
  useEffect(() => {
    if (!running || !deployId) return;
    const t = token.current;
    const started = Date.now() - (run?.elapsed ?? 0) * 1000;
    const tick = setInterval(() => setRun((r) => (r && r.phase === "running" ? { ...r, elapsed: (Date.now() - started) / 1000 } : r)), 1000);
    const poll = setInterval(() => {
      // The te sweep's own progress (a server without the route, or a destroy another process
      // runs, answers nothing useful: the word stays the bare tier, and the sweep counts as idle).
      const progress = api.getDestroyProgress(deployId).then(
        (p) => {
          const sweep = p.running && p.step && p.step !== "cascade" ? { step: p.step, done: p.done, total: p.total } : null;
          setRun((r) => (!r || r.phase !== "running" || (r.sweep?.step === sweep?.step && r.sweep?.done === sweep?.done && r.sweep?.total === sweep?.total) ? r : { ...r, sweep }));
          return !p.running;
        },
        () => true,
      );
      const rows = api.listDeploys().then(
        (l) => l.deploys,
        () => null,
      );
      void Promise.all([rows, progress]).then(([deploys, idle]) => {
        if (!deploys || !live.current || token.current !== t) return;
        const standing = standingTiers(deploys, deployId);
        // The API drops a destroyed deploy from the list (or, older, keeps it as `destroyed`): no live row is down.
        const teDown = !deploys.some((d) => d.deploy_id === deployId && d.status !== "destroyed");
        const isDown = (x: string) => (x === "te" ? teDown : !standing.includes(x));
        setRun((r) => {
          if (!r || r.phase !== "running") return r;
          const down = r.tiers.filter(isDown);
          return down.length === r.down.length ? r : { ...r, down };
        });
        // Every tier gone and nothing sweeping: the destroy is over, whether or not its DELETE answers.
        const tiers = runTiers.current;
        if (idle && tiers.length && tiers.every(isDown)) end(t, (prev) => ({ ...prev, down: prev.tiers, phase: "done", sweep: null }), polledResult(deployId, tiers));
        else if (lost.current && idle && !teDown && ++lost.current.stalls >= LOST_STALLS) {
          const error = lost.current.error;
          end(t, (prev) => ({ ...prev, phase: "failed", error }), null);
        }
      });
    }, DESTROY_POLL_MS);
    return () => {
      clearInterval(tick);
      clearInterval(poll);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running, deployId, api, end]);

  const start = useCallback(
    (id: string, tiers: string[]) => {
      const t = ++token.current;
      runTiers.current = tiers;
      lost.current = null;
      setRun({ deployId: id, tiers, down: [], elapsed: 0, phase: "running", error: null, result: null });
      api.destroyDeploy(id).then(
        (r) => {
          const problem = leftStanding(r);
          const down = (r.tiers ?? []).filter((x) => x.destroyed).map((x) => x.tier);
          end(t, (prev) => ({ ...prev, down: problem ? prev.tiers.filter((x) => down.includes(x)) : prev.tiers, phase: problem ? "failed" : "done", error: problem ? new Error(problem) : null, result: r }), problem ? null : r);
        },
        (e: unknown) => {
          // The answer lost on the way (a proxy timing out a long sweep): the destroy may well be
          // running or done, so the poll decides instead of a failure shown over a finished destroy.
          if (e instanceof ApiError && (e.kind === "network" || e.status === 504) && token.current === t) {
            lost.current = { error: e, stalls: 0 };
            return;
          }
          end(t, (prev) => ({ ...prev, phase: "failed", error: e }), null);
        },
      );
    },
    [api, end],
  );
  const clear = useCallback(() => {
    token.current++;
    setRun(null);
  }, []);
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
  dirtyKind = "unapplied",
  onPersistFirst,
  onConfirm,
  onClose,
}: {
  twinId: string;
  deploy: Pick<DeploySummary, "deploy_id" | "target">;
  tiers: string[];
  run: DestroyRun | null;
  dirty?: boolean;
  /** What the pending changes are: `unapplied` (in the working copy only) or `applied` (running on the twin, never
   *  persisted to the baseline). Destroy loses both; the words say which. */
  dirtyKind?: "applied" | "unapplied";
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
          <div className="warn" data-testid="destroy-dirty" data-kind={dirtyKind}>
            {dirtyKind === "applied" ? (
              <p style={{ margin: 0 }}>The running twin carries applied changes that were never persisted. Destroying it loses them: the baseline does not have them. Persist first to keep them in the twin.</p>
            ) : (
              <p style={{ margin: 0 }}>Unapplied changes will be discarded.</p>
            )}
            <label>
              <input type="checkbox" checked={discard} onChange={(e) => setDiscard(e.target.checked)} disabled={busy} /> {dirtyKind === "applied" ? "Lose the applied, unpersisted changes" : "Discard the unapplied changes"}
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
