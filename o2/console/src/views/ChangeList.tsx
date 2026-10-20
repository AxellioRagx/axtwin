/**
 * The change-list panel (`docs/design/15-console-canvas.md` §8): the diff
 * between the current cast and the assertions file, one row per record, each
 * tagged with the kind of deploy it implies for a standing twin.
 *
 * **The three visible states** are the store's (`edit/store.ts`): *pending*
 * (browser only, hatched), *saved* (in the YAML, outlined), *applied* (in the
 * cast, `asserted`). **The deploy tag is derivable from the record's block
 * alone** (`edit/records.ts`'s `deployConsequence`) -- an `add`/`suppress` is
 * an incremental `te deploy --update`, an `override` is a fresh deploy -- which
 * is what lets this panel show the consequence without a key->node-spec map the
 * bundle does not publish (§2).
 *
 * Removing a row deletes the record: a *pending* one is discarded outright, a
 * saved or applied one is discarded and the file re-saved without it (the
 * parent wires which). This is the "remove an assertion in the inspector
 * deletes the record" gesture of §8's table.
 *
 * **S5.1.d: one entry per GESTURE.** A batch -- a clone, a segment generated
 * with hosts -- is ONE row carrying the work order's own shape (`clone of
 * 10.80.20.0/24 → 10.80.31.0/24 · 1 segment · 24 hosts · 3 conduits · pending`)
 * that expands to its records, and *undo* removes the whole batch. A record can
 * still be removed singly, from the expanded list. A gesture of one record, and
 * a record with no gesture at all, stay ONE plain row: most gestures are single
 * records and a header-plus-expander around each of them would make this list
 * harder to read (`edit/gestures.ts`).
 */
import { useState } from "react";

import type { EditEntry, EditState, EditStoreState } from "../edit/store";
import { deployConsequence } from "../edit/records";
import { dependentsOf, removalConsequences } from "../edit/dependents";
import { gestureCounts, groupByGesture, type GestureGroup } from "../edit/gestures";

export interface ChangeListProps {
  edits: EditStoreState;
  /** Remove records TOGETHER -- one entry, or an `add: segment` with the
   *  records it is the only home of (fix round, SF2). The parent decides
   *  discard-only vs discard-then-save by the entries' states; this panel
   *  names them. */
  onRemove: (entries: readonly EditEntry[]) => void;
  /** Each entry whose segment has no home, with the reason
   *  (`edit/dependents.ts`). Absent ⇒ nothing is flagged. */
  orphans?: ReadonlyMap<string, string>;
  /** The observed segment CIDRs -- a home on their own, so a record on one does
   *  not depend on an `add: segment` of it. `null`/absent ⇒ not known. */
  observed?: ReadonlySet<string> | null;
  /** Whether ANY removal may be issued -- a save in flight, or a stale-etag
   *  refusal, stops all of them. */
  removeEnabled: boolean;
  /**
   * Whether a removal that RE-SAVES the file may be issued: it needs everything
   * Save needs, including valid `source`/`as_of` metadata (S5).
   *
   * Separate from `removeEnabled` because a removal of records that are all
   * *pending* writes nothing (fix round B): gating those on the save metadata
   * disabled a control that had always worked, under a reason that was untrue.
   */
  removeSavedEnabled?: boolean;
  /** Why a re-saving removal is disabled, when it is. Shown on the control, so
   *  an operator meets the reason rather than a click that does nothing. */
  savedRemoveBlockedReason?: string | null;
}

/** The ONE wording for the three states in this app -- reused by level 1's
 *  pending band (review S6), so nothing calls a browser-only record
 *  "in this file". */
export const STATE_LABEL: Record<EditState, string> = {
  pending: "pending (browser only)",
  saved: "saved (in the file)",
  applied: "applied (in the cast)",
};

export function ChangeList({
  edits,
  onRemove,
  removeEnabled,
  orphans,
  observed,
  removeSavedEnabled,
  savedRemoveBlockedReason,
}: ChangeListProps): JSX.Element {
  const savedOk = removeSavedEnabled ?? removeEnabled;
  /**
   * Whether this control may run, and why not -- asked with the records IT
   * would remove (fix round B). Only a removal that touches a record already in
   * the file re-saves it; a pending-only one is a discard.
   */
  const gate = (targets: readonly EditEntry[]): { enabled: boolean; blocked: string | null } => {
    if (!removeEnabled) return { enabled: false, blocked: null };
    const writes = targets.some((t) => t.state !== "pending");
    if (!writes || savedOk) return { enabled: true, blocked: null };
    return { enabled: false, blocked: savedRemoveBlockedReason ?? null };
  };
  const entries = edits.entries;
  // The one row whose removal is awaiting confirmation -- a segment that is
  // the only home of records staged on it, or a gesture that would strand one.
  const [confirming, setConfirming] = useState<string | null>(null);
  // Which gesture groups are expanded. Collapsed by default: the headline
  // already carries the counts, and a clone of 24 hosts would otherwise bury
  // every other edit.
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set());
  if (entries.length === 0) {
    return <p className="meta" data-testid="change-list-empty">No edits yet.</p>;
  }
  const groups = groupByGesture(entries);
  const rowFor = (entry: EditEntry, inGroup: boolean): JSX.Element => {
    const dependents = dependentsOf(entries, entry, observed ?? null);
    const g = gate([entry, ...dependents]);
    return (
      <ChangeRow
        key={entry.id}
        entry={entry}
        nested={inGroup}
        ineffective={edits.ineffective.includes(entry.record.key ?? "")}
        orphan={orphans?.get(entry.id) ?? null}
        onRemove={() => {
          // A segment that is the ONLY home of records staged on it is not
          // removed alone -- they would be left with no segment. Where the
          // network is observed (or added again), the add only asserts a
          // name or zone type, and goes by itself.
          if (dependents.length > 0) setConfirming(entry.id);
          else onRemove([entry]);
        }}
        removeEnabled={g.enabled}
        blocked={g.blocked}
        confirm={
          confirming === entry.id && dependents.length > 0 ? (
            <RemoveTogether
              title={`Remove ${entry.record.key ?? ""}`}
              lead={
                <>
                  Removing segment <span className="mono">{entry.record.key}</span> also removes the{" "}
                  {dependents.length} record{dependents.length === 1 ? "" : "s"} it is the only home of:
                </>
              }
              dependents={dependents}
              observedKnown={observed !== undefined && observed !== null}
              enabled={g.enabled}
              confirmLabel={`remove all ${dependents.length + 1}`}
              onConfirm={() => {
                setConfirming(null);
                onRemove([entry, ...dependents]);
              }}
              onCancel={() => setConfirming(null)}
            />
          ) : null
        }
      />
    );
  };
  return (
    <ul className="change-list" data-testid="change-list">
      {groups.map((group) => {
        if (group.solo) return rowFor(group.entries[0]!, false);
        const open = expanded.has(group.key);
        // What the WHOLE gesture would strand: every dependent of every record
        // it holds, minus the gesture's own records (a clone's hosts go with
        // its segment because they are part of the same undo, not stranded).
        const own = new Set(group.entries.map((e) => e.id));
        const stranded = group.entries
          .flatMap((e) => dependentsOf(entries, e, observed ?? null))
          .filter((d) => !own.has(d.id));
        const strandedOnce = stranded.filter((d, i) => stranded.findIndex((x) => x.id === d.id) === i);
        const g = gate([...group.entries, ...strandedOnce]);
        return (
          <GestureEntry
            key={group.key}
            group={group}
            open={open}
            onToggle={() =>
              setExpanded((prev) => {
                const next = new Set(prev);
                if (next.has(group.key)) next.delete(group.key);
                else next.add(group.key);
                return next;
              })
            }
            removeEnabled={g.enabled}
            blocked={g.blocked}
            onUndo={() => {
              if (strandedOnce.length > 0) setConfirming(group.key);
              else onRemove(group.entries);
            }}
            confirm={
              confirming === group.key && strandedOnce.length > 0 ? (
                <RemoveTogether
                  title={`Undo ${group.gesture?.label ?? "this gesture"}`}
                  lead={
                    <>
                      Undoing <span className="mono">{group.gesture?.label}</span> also removes the{" "}
                      {strandedOnce.length} record{strandedOnce.length === 1 ? "" : "s"} it would leave with
                      no segment:
                    </>
                  }
                  dependents={strandedOnce}
                  observedKnown={observed !== undefined && observed !== null}
                  enabled={g.enabled}
                  confirmLabel={`remove all ${group.entries.length + strandedOnce.length}`}
                  onConfirm={() => {
                    setConfirming(null);
                    onRemove([...group.entries, ...strandedOnce]);
                  }}
                  onCancel={() => setConfirming(null)}
                />
              ) : null
            }
          >
            {open && <ul className="change-sublist" data-testid="gesture-records">{group.entries.map((e) => rowFor(e, true))}</ul>}
          </GestureEntry>
        );
      })}
    </ul>
  );
}

/**
 * One gesture: the headline in the work order's shape, an expander to its
 * records, and an undo that removes the whole batch.
 *
 * The deploy tag is the gesture's own, and it is the WEAKER of the two: a batch
 * that holds any `override` needs a fresh deploy, even if the rest of it could
 * have gone in place -- saying `te deploy --update` for a batch one record of
 * which cannot would be the wrong half of the truth.
 */
function GestureEntry(props: {
  group: GestureGroup;
  open: boolean;
  onToggle: () => void;
  onUndo: () => void;
  removeEnabled: boolean;
  blocked: string | null;
  confirm: JSX.Element | null;
  children: JSX.Element | false;
}): JSX.Element {
  const { group, open, onToggle, onUndo, removeEnabled, confirm } = props;
  const fresh = group.entries.some((e) => e.block === "override");
  const label = group.gesture?.label ?? "gesture";
  const counts = gestureCounts(group.counts);
  return (
    <li
      className={`change-row is-gesture is-${group.state}`}
      data-testid="change-gesture-row"
      data-gesture={group.gesture?.id}
      data-state={group.state}
    >
      <button
        type="button"
        className="link change-expand"
        data-testid="gesture-expand"
        aria-expanded={open}
        onClick={onToggle}
      >
        {open ? "▾" : "▸"}
      </button>{" "}
      <span className="change-gesture" data-testid="change-gesture">
        {label}
      </span>
      <span className="change-summary" data-testid="gesture-counts">
        {counts}
      </span>
      <span className={`change-state is-${group.state}`} data-testid="change-state">
        {STATE_LABEL[group.state]}
      </span>
      <span className={`change-tag ${fresh ? "is-fresh" : "is-update"}`} data-testid="change-tag">
        {deployConsequence(fresh ? "override" : "add")}
      </span>
      <button
        type="button"
        className="link change-remove"
        data-testid="gesture-undo"
        onClick={onUndo}
        disabled={!removeEnabled}
        {...(props.blocked ? { title: props.blocked } : {})}
        /* N3: the confirmed path can remove MORE than the batch, and the label
           said a flat count. It says what it will do and what it will ask. */
        aria-label={`undo ${label} — removes all ${group.entries.length} of its records, and asks about anything it would strand`}
      >
        undo
      </button>
      {props.blocked && !removeEnabled && (
        <span className="meta" data-testid="remove-blocked">
          {props.blocked}
        </span>
      )}
      {confirm}
      {props.children}
    </li>
  );
}

/** The inline confirmation for a removal that takes other records with it --
 *  a segment that is the only home of what is staged on it, or (S5.1.d) a whole
 *  gesture that would strand a record staged outside it. What goes with it is
 *  listed, and what leaving each kind without its segment would do -- the
 *  compiler's own outcomes -- is said, before anything is removed.
 *
 *  ONE component for both, so the two paths cannot disagree about the
 *  consequences they state (LES-025). */
function RemoveTogether(props: {
  title: string;
  lead: JSX.Element;
  dependents: readonly EditEntry[];
  observedKnown: boolean;
  enabled: boolean;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
}): JSX.Element {
  const { dependents, enabled } = props;
  return (
    <div className="change-confirm" role="group" aria-label={props.title} data-testid="remove-confirm">
      <p className="notice">{props.lead}</p>
      <ul className="change-confirm-list" data-testid="remove-confirm-list">
        {dependents.map((d) => (
          <li key={d.id} className="mono">
            {summarize(d)} · {d.state}
          </li>
        ))}
      </ul>
      {removalConsequences(dependents, props.observedKnown).map((line) => (
        <p key={line} className="meta" data-testid="remove-confirm-consequence">
          {line}
        </p>
      ))}
      <button type="button" className="link" data-testid="remove-confirm-all" disabled={!enabled} onClick={props.onConfirm}>
        {props.confirmLabel}
      </button>{" "}
      <button type="button" className="link" data-testid="remove-confirm-cancel" onClick={props.onCancel}>
        keep them
      </button>
    </div>
  );
}

function ChangeRow(props: {
  entry: EditEntry;
  /** Rendered INSIDE an expanded gesture: the gesture label is on the header
   *  above, so repeating it on every record would be noise. */
  nested: boolean;
  ineffective: boolean;
  orphan: string | null;
  onRemove: () => void;
  removeEnabled: boolean;
  blocked: string | null;
  confirm: JSX.Element | null;
}): JSX.Element {
  const { entry, ineffective, orphan, onRemove, removeEnabled, confirm } = props;
  const tag = deployConsequence(entry.block);
  return (
    <li
      className={`change-row is-${entry.state}${props.nested ? " is-nested" : ""}`}
      data-testid="change-row"
      data-block={entry.block}
      data-state={entry.state}
    >
      <span className="change-summary mono">{summarize(entry)}</span>
      {/* The gesture a SOLO row came from -- a batch of one. Inside an expanded
          gesture the header above already names it. */}
      {entry.gesture && !props.nested && (
        <span className="change-gesture" data-testid="change-gesture" data-gesture={entry.gesture.id}>
          {entry.gesture.label}
        </span>
      )}
      <span className={`change-state is-${entry.state}`} data-testid="change-state">
        {STATE_LABEL[entry.state]}
      </span>
      {/* The tag is a property of the block, and the same for every row of a
          block -- an `add`/`suppress` updates a standing twin in place, an
          `override` needs a fresh deploy. */}
      <span
        className={`change-tag ${entry.block === "override" ? "is-fresh" : "is-update"}`}
        data-testid="change-tag"
      >
        {tag}
      </span>
      {ineffective && (
        <span className="change-ineffective" data-testid="change-ineffective">
          matched nothing in the cast
        </span>
      )}
      {orphan && (
        <span className="change-orphan notice is-error" data-testid="change-orphan">
          {orphan}
        </span>
      )}
      <button
        type="button"
        className="link change-remove"
        data-testid="change-remove"
        onClick={onRemove}
        disabled={!removeEnabled}
        {...(props.blocked ? { title: props.blocked } : {})}
        aria-label={`remove ${entry.id}`}
      >
        remove
      </button>
      {props.blocked && !removeEnabled && (
        <span className="meta" data-testid="remove-blocked">
          {props.blocked}
        </span>
      )}
      {confirm}
    </li>
  );
}

/** A one-line human summary of a record, for the row. Not a contract: the
 *  file is the contract, and the inspector shows the full record. */
function summarize(entry: EditEntry): string {
  const r = entry.record;
  const key = r.key ?? "?";
  if (entry.block === "add") {
    if (r.kind === "conduit") return `+ conduit ${key}`;
    if (r.kind === "segment") return `+ segment ${key} (${r.name ?? "?"})`;
    return `+ host ${key}${r.role ? ` as ${r.role}` : ""}`;
  }
  if (entry.block === "override") {
    return `${key}: ${r.field} = ${JSON.stringify(r.value)}`;
  }
  return `suppress ${key}`;
}
