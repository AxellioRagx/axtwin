/**
 * The change list and the save/apply controls (`docs/design/15-console-canvas.md`
 * §8). It lives in the inspector pane, beneath the selected element's own
 * actions.
 *
 * **S5.1.a moved the GESTURES out of this file** (`docs/WORK_ORDER_console_
 * canvas.md` §3 S5.1.a, Authority: user). S5 presented "Add a segment" / "Add a
 * conduit" as free-standing, context-free forms keyed only on the current zoom
 * level; they are now actions on the **selected element**
 * (`views/ElementActions.tsx`), and "Add a segment" is a `+` on the site the
 * segment will be born in. Nothing about the record layer moved with them:
 * `edit/records.ts`, the reducer, the three states, the change list and the
 * save path are untouched.
 *
 * What is left here is what belongs to the FILE rather than to any one element:
 * the change list across every staged record, the save metadata `14` §3
 * requires, Save, and Apply.
 *
 * **Save is the whole S5 write path.** Save is `PUT .../assertions`; the etag
 * discipline and the stale-etag reload prompt are the store's
 * (`edit/store.ts`) and the inspector's conflict banner. The metadata a save
 * needs (`as_of`, `source`) is parent-owned so a removal-that-re-saves uses the
 * same values. **Apply** (S6) is save + recompile + refresh.
 */
import { setWriteToken, type ConsoleApiError } from "../api/client";
import type { ApplyResponse, AssertionsDoc } from "../api/types";
import { canSave, hasUnsavedEdits, type EditEntry, type EditStoreState } from "../edit/store";
import { isSourceValid, metadataReady } from "../edit/records";
import type { TwinHostChurn } from "../model/changes";
import { ChangeList } from "./ChangeList";
import { Field } from "./paneParts";

export interface EditPanelProps {
  edits: EditStoreState;
  assertions: AssertionsDoc | null;
  saving: boolean;
  /** Remove records together (a segment with what it is the only home of, SF2). */
  onRemove: (entries: readonly EditEntry[]) => void;
  /** Each orphaned entry with its reason (`edit/dependents.ts`). */
  orphans?: ReadonlyMap<string, string>;
  /** The observed segment CIDRs, `null` when not known (`edit/dependents.ts`). */
  observed?: ReadonlySet<string> | null;
  onSave: () => void;
  onReload: () => void;
  /** S6 apply (save + recompile + refresh). `applyResult` carries the fresh
   *  fingerprints and `changed`; `applyError` (a `ConsoleApiError`) carries the
   *  compiler's own stderr in its `detail` on a 502/422. `changedCount` is how
   *  many map elements the last apply's diff touched -- used to say honestly
   *  when a `changed: true` apply moved the twin but nothing on THIS map. */
  onApply: () => void;
  /** Why the server would refuse Apply, known before the click (a twins/
   *  folder, aximprint not configured) -- the button is then disabled and the
   *  reason shown beside it rather than learned from a failure. */
  applyBlockedReason?: string | null;
  applying: boolean;
  applyResult: ApplyResponse | null;
  applyError: ConsoleApiError | null;
  changedCount: number;
  /** MUST-FIX 1: true when the diff behind `changedCount` was actually
   *  COMPUTED. False means the diff endpoint could not answer for this apply
   *  (no history snapshot was written, or the comparison call itself failed)
   *  -- `changedCount` is then meaningless, and `ApplyStatus` must say the
   *  diff is unavailable rather than rendering the same message a genuinely
   *  empty diff gets (LES-240's don't-know-vs-no rule). */
  diffAvailable: boolean;
  /** GAP-087: how many hosts the TWIN gained/lost across the last apply, or
   *  `null` when that side of the diff was not compared. Read ONLY to size a
   *  compiler-change outcome -- the map's own join is observed-keyed and
   *  cannot see a twin-side rename, which is the whole finding. */
  hostChurn?: TwinHostChurn | null;
  /** The save metadata, parent-owned (`14` §3 requires both; the server never
   *  invents `as_of`). Seeded from the file, editable here. */
  source: string;
  onSourceChange: (value: string) => void;
  asOf: string;
  onAsOfChange: (value: string) => void;
  /** S5.1.d: a DRAFT was restored from this browser -- said plainly, with when
   *  it was taken and what was left out (`edit/draft.ts`'s `restoreNotice`).
   *  `null`/absent ⇒ nothing was restored. */
  draftNotice?: string | null;
  /** S5.1.d: the draft could NOT be kept (storage blocked or full). Said once,
   *  and only while there is something to lose, because it changes what the
   *  operator can rely on: the edits are this tab's only copy. */
  draftProblem?: string | null;
  /** Discard the restored draft -- the offer that goes with the notice. */
  onDiscardDraft?: () => void;
}

export function EditPanel(props: EditPanelProps): JSX.Element {
  const { edits, assertions, saving, onRemove, onSave, onReload } = props;
  const { onApply, applying, applyResult, applyError, changedCount, diffAvailable } = props;

  // The Save gate and the remove-that-re-saves gate (App.tsx) share ONE
  // metadata predicate, so a removal never PUTs metadata Save would refuse (S2).
  const sourceOk = isSourceValid(props.source);
  const saveReady = canSave(edits) && metadataReady(props.source, props.asOf);

  return (
    <section className="edit-panel" data-testid="edit-panel" aria-label="Edit">
      <h3>Changes</h3>
      {/* S5.1.d: the draft, said where the edits it restored are listed. A
          live region, because it appears without the operator asking. */}
      {props.draftNotice && (
        <p className="notice" role="status" data-testid="draft-restored">
          {props.draftNotice}{" "}
          {/* Offered whenever there IS a notice -- not only when something was
              restored. A draft kept because the WORLD refused it (fix round D)
              restores nothing, so gating on `hasUnsavedEdits` left the operator
              looking at the notice with no way to clear the key, and the only
              escape was to stage new work, which overwrites it anyway.
              `discardDraft` handles the empty pending set. */}
          {props.onDiscardDraft && (
            <button type="button" className="link" data-testid="draft-discard" onClick={props.onDiscardDraft}>
              {hasUnsavedEdits(edits) ? "discard them" : "discard the draft"}
            </button>
          )}
        </p>
      )}
      {props.draftProblem && (
        <p className="notice is-error" role="alert" data-testid="draft-problem">
          {props.draftProblem}
        </p>
      )}
      {/* S5: a removal of a SAVED record re-saves the file, so it needs
          everything Save needs -- including the metadata; with a source the
          save path refuses, the click used to be a silent no-op.
          CORRECTED (fix round B): the metadata is only needed when the removal
          actually re-saves. A gesture whose records are ALL pending was never
          in the file, so undoing it writes nothing -- gating it on `source`
          disabled a control that had always worked and gave a reason that was
          untrue. The predicate is therefore per-row, and `ChangeList` asks it
          with the records that particular control would remove. */}
      <ChangeList
        edits={edits}
        onRemove={onRemove}
        orphans={props.orphans}
        observed={props.observed}
        removeEnabled={canSave(edits) && !saving}
        removeSavedEnabled={saveReady && !saving}
        savedRemoveBlockedReason={
          saveReady || saving
            ? null
            : !metadataReady(props.source, props.asOf)
              ? "this removal re-saves the file, which needs a valid source and as_of below"
              : "the file moved under this browser — reload it before removing anything"
        }
      />

      <h4>Save</h4>
      <div className="save-meta">
        <Field label="source">
          <input
            data-testid="save-source"
            value={props.source}
            onChange={(e) => props.onSourceChange(e.target.value)}
            placeholder="operator: your-name"
          />
        </Field>
        {!sourceOk && (
          <p className="notice is-error" data-testid="save-source-error">
            source must be <code>operator: &lt;name&gt;</code>
          </p>
        )}
        <Field label="as_of">
          <input
            data-testid="save-asof"
            value={props.asOf}
            onChange={(e) => props.onAsOfChange(e.target.value)}
            placeholder="2026-09-10T00:00:00Z"
          />
        </Field>
        <Field label="write token">
          <input
            type="password"
            data-testid="save-token"
            placeholder="(if the deployment requires one)"
            onChange={(e) => setWriteToken(e.target.value)}
          />
        </Field>
      </div>
      <div className="save-actions">
        <button
          type="button"
          className="save-button"
          data-testid="save-button"
          disabled={!saveReady}
          onClick={onSave}
        >
          {saving ? "Saving…" : assertions?.exists ? "Save changes" : "Create assertions file"}
        </button>
        {/* Apply IS save + recompile (`14` §4): it saves any pending edits FIRST
            (the PUT precedes the POST), then recompiles the bundle and refreshes.
            Shares Save's metadata/conflict gate; refused while one is running. */}
        <button
          type="button"
          className="apply-button"
          data-testid="apply-button"
          disabled={!saveReady || applying || !!props.applyBlockedReason}
          onClick={onApply}
        >
          {applying ? "Applying…" : "Apply (recompile)"}
        </button>
        {hasUnsavedEdits(edits) && (
          <span className="meta" data-testid="unsaved-note">unsaved edits</span>
        )}
        {edits.mustReload && (
          <button
            type="button"
            className="link"
            data-testid="reload-button"
            onClick={onReload}
          >
            Reload the file
          </button>
        )}
      </div>
      {!saveReady && !saving && (
        <p className="meta" data-testid="save-blocked-reason">
          Save needs: {saveNeeds(edits, props.source, props.asOf).join("; ")}.
        </p>
      )}
      {props.applyBlockedReason && (
        <p className="meta" data-testid="apply-blocked-reason">
          {props.applyBlockedReason}
        </p>
      )}
      <ApplyStatus
        result={applyResult}
        error={applyError}
        changedCount={changedCount}
        diffAvailable={diffAvailable}
        hostChurn={props.hostChurn ?? null}
      />
    </section>
  );
}

/** What stands between the operator and an enabled Save, in words -- so the
 *  button is never disabled without saying why (Unified Console ruling §2). */
export function saveNeeds(edits: EditStoreState, source: string, asOf: string): string[] {
  const out: string[] = [];
  if (edits.mustReload) out.push("reload the file first (it changed on the server)");
  if (!isSourceValid(source)) out.push("a source of the form operator: <your name>");
  if (!metadataReady(source, asOf) && isSourceValid(source)) out.push("an as_of timestamp");
  return out.length > 0 ? out : ["the file to finish loading"];
}

/** The compiler's OWN stderr, verbatim, on a failed recompile (502) or a
 *  refused file (422) -- carried in the error's `detail.stderr` (`14` §4). */
function applyStderr(error: ConsoleApiError | null): string | null {
  if (!error) return null;
  const detail = error.detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const s = (detail as Record<string, unknown>)["stderr"];
    if (typeof s === "string" && s.length > 0) return s;
  }
  return null;
}

/** A short display form of a content fingerprint (S6). */
function shortFingerprint(value: string | null): string {
  return value ? value.slice(0, 12) : "—";
}

/**
 * The apply outcome, `14` §4's three answers kept distinct:
 *   - FAILED  -- the compiler's own stderr, VERBATIM, never a generic banner.
 *   - NO-OP   -- `changed: false`: a byte-identical recompile, the determinism
 *                check; no highlight, stated as "no change" not silence.
 *   - CHANGED -- the fingerprints moved. Two sub-outcomes, kept DISTINCT
 *                (MUST-FIX 1, LES-240's don't-know-vs-no rule): when
 *                `diffAvailable` is true, `changedCount` says whether anything
 *                on THIS (observed) map was touched, honestly reporting the
 *                twin-only case where nothing here moves; when it is false,
 *                the diff itself could not be computed (no history snapshot
 *                was written for this apply, or the comparison call failed)
 *                -- an "I don't know", which must NEVER render as the same
 *                "nothing moved" sentence a genuinely empty diff gets.
 * Error takes precedence over a stale success `result` because `useApply`
 * clears its error at the start of each run, so a present error is THIS run's.
 *
 * **GAP-087 adds a fourth answer, ABOVE the other three and never folded into
 * them.** A recompile re-runs the producer from the bundle's own
 * `cast_invocation`, which pins the cast's flags and not the compiler BUILD,
 * so an upgrade to the sibling repo between cast and apply arrives inside the
 * operator's edit. Measured once: one suppression, 22 hosts added and 23
 * removed, reported here as "1 element changed" -- correct for what this map
 * joins on (observed identity), which is exactly why the population move was
 * invisible. `CompilerChange` renders the server's own verdict and sentence,
 * sized by the TWIN side's churn, and keeps the server's third value
 * (`compiler_changed: null` -- a build stamp that could not be READ) as its
 * own outcome rather than the quiet `false` one (LES-273/LES-333).
 */
function ApplyStatus(props: {
  result: ApplyResponse | null;
  error: ConsoleApiError | null;
  changedCount: number;
  diffAvailable: boolean;
  hostChurn: TwinHostChurn | null;
}): JSX.Element | null {
  const { result, error, changedCount, diffAvailable } = props;
  if (error) {
    const stderr = applyStderr(error);
    return (
      <div className="apply-status is-error" role="alert" data-testid="apply-error">
        <p>the recompile failed ({error.kind}).</p>
        {stderr !== null ? (
          <pre className="apply-stderr" data-testid="apply-stderr">{stderr}</pre>
        ) : (
          <p className="mono">{error.message}</p>
        )}
      </div>
    );
  }
  if (!result) return null;
  if (!result.changed) {
    return (
      <>
        <CompilerChange result={result} hostChurn={props.hostChurn} />
        <p className="apply-status" data-testid="apply-nochange">
          No change — the recompile produced a byte-identical bundle (fingerprint
          unchanged). A deterministic no-op.
        </p>
      </>
    );
  }
  const otherWarnings = result.warnings.filter((w) => w !== result.compiler_note);
  return (
    <div className="apply-status is-ok" data-testid="apply-result">
      <CompilerChange result={result} hostChurn={props.hostChurn} />
      <p>
        Applied — recompiled.{" "}
        <span className="mono">{shortFingerprint(result.fingerprint_before)}</span>
        {" → "}
        <span className="mono">{shortFingerprint(result.fingerprint_after)}</span>.
      </p>
      {diffAvailable ? (
        <p className="meta" data-testid="apply-changed-note">
          {changedCount > 0
            ? `${changedCount} element${changedCount === 1 ? "" : "s"} changed on this map (highlighted for five seconds; the "changes" filter keeps them reachable).`
            : "the twin changed, but no observed element on this map moved — the map is drawn from the observed capture, which an assertion does not alter."}
        </p>
      ) : (
        <p className="notice" data-testid="apply-diff-unavailable">
          the twin changed, but the post-apply diff couldn't be computed — this
          is NOT the same as "nothing moved": the comparison itself is
          unavailable, so nothing here is highlighted.
          {/* `compiler_note` is rendered once, by `CompilerChange`; a server
              that also lists it in `warnings` must not get it said twice. */}
          {otherWarnings.length > 0 && <> {otherWarnings.join(" ")}</>}
        </p>
      )}
    </div>
  );
}

/**
 * GAP-087, the fourth apply outcome: **the compiler changed under this apply**.
 *
 * Rendered ABOVE the changed/no-change sentence and never inside it, because
 * it answers a different question -- not "did the bundle move" but "was this
 * apply's move yours". Three server verdicts, three renders:
 *
 *   - `false`  -- the ordinary outcome. Nothing at all: the operator's
 *                 attention is spent only where there is something to spend
 *                 it on.
 *   - `true`   -- the population move may not be the edit's. Sized by the
 *                 TWIN side's host churn, which is the count this map cannot
 *                 show (it joins on observed identity, so a twin-side rename
 *                 is invisible to it BY DESIGN -- the reason "1 element
 *                 changed" was an honest answer to a misleading question).
 *   - `null`   -- a build stamp could not be READ. Its OWN sentence, carrying
 *                 the server's reason, never the quiet `false` render: "I
 *                 could not tell" is not "no" (LES-273/LES-333).
 */
function CompilerChange(props: {
  result: ApplyResponse;
  hostChurn: TwinHostChurn | null;
}): JSX.Element | null {
  const { result, hostChurn } = props;
  // `undefined` is an OLDER server that predates the verdict -- it said
  // nothing, so neither does this. Only an explicit `true` asserts a move;
  // falling through to the changed render on a missing field would print a
  // build change nobody measured ("— → —").
  if (result.compiler_changed === undefined || result.compiler_changed === false) {
    return null;
  }

  if (result.compiler_changed === null) {
    // ONE sentence, not two: the server's note already says "could not be
    // determined" with its reason (and, when the bundle moved, the
    // population caveat), so it is rendered as-is rather than stacked under
    // a near-identical Console lead. The Console's own sentence is only the
    // fallback for a server that sent the verdict without a note -- and it
    // drops the "population difference" clause when nothing moved at all
    // (`changed: false`), where that question is moot.
    return (
      <p className="notice" data-testid="apply-compiler-unknown">
        {result.compiler_note
          ? sentence(result.compiler_note)
          : result.changed
            ? "Whether the compiler changed between this bundle's cast and this apply could not be determined, so a population difference below may not be yours."
            : "Whether the compiler changed between this bundle's cast and this apply could not be determined."}
      </p>
    );
  }

  // The churn is `null` when the twin side was not compared -- said as
  // unknown, never as zero.
  const churn =
    hostChurn === null
      ? "how much of the twin's population moved with it is unknown here (the twin side of the post-apply diff was not compared)"
      : `${hostChurn.added} host${hostChurn.added === 1 ? "" : "s"} added and ${hostChurn.removed} removed in the twin`;
  return (
    <p className="notice" data-testid="apply-compiler-changed">
      The compiler changed between this bundle's cast and this apply (
      {/* Shown in FULL, unlike the content fingerprints above: this is the
          value an operator compares against the bundle's own
          `cast_invocation.code_fingerprint` to decide whether to re-cast, and
          a truncation would make that comparison a guess. */}
      <span className="mono">{result.cast_code_fingerprint_before ?? "—"}</span>
      {" → "}
      <span className="mono">{result.cast_code_fingerprint_after ?? "—"}</span>
      ): {churn} — some or all of it unrelated to your edit. Re-cast
      deliberately if that is not what you wanted.
    </p>
  );
}

/** A server sentence (lower-case lead, no full stop) rendered as prose. */
function sentence(text: string): string {
  const t = text.trim();
  if (!t) return t;
  const lead = t.charAt(0).toUpperCase() + t.slice(1);
  return /[.!?]$/.test(lead) ? lead : `${lead}.`;
}
