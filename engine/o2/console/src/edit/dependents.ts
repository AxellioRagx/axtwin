/**
 * What the change list must know before a record leaves the file
 * (`docs/WORK_ORDER_console_canvas.md` §3 S5.1.a, fix rounds SF2 and after):
 *
 *  - **dependents** -- the records whose segment's ONLY home is an
 *    `add: segment`: a host whose `segment` is its key, or a conduit with its
 *    key as a half, when that network is neither observed in the export nor
 *    added by another record. The compiler resolves a host against
 *    `known_cidrs` -- the PIT's observed segments plus this file's adds
 *    (aximprint `synth/compiler.py`) -- so a host on an OBSERVED segment is
 *    valid without the add: there the add only asserts a name or a zone type,
 *    and removing it strands nothing. Only where the add is the one home is a
 *    segment removed WITH what is staged on it, after the operator has seen
 *    those records listed and been told truthfully what leaving them without
 *    the segment would do.
 *  - **orphans** -- records whose segment has no home: not observed, not added
 *    by this file. The installed cast's segment roster is NOT a home -- the
 *    compiler never resolves a record against the previous cast, and a
 *    relocated roster's CIDRs are deployed addresses, so counting them only hid
 *    real orphans. Flagged with the reason, never silently kept or dropped.
 *
 * Pure -- no React, no store object: entries are read structurally.
 */

/** An edit-store entry as these rules read it. */
export interface DependentEntryInput {
  id: string;
  block: string;
  record: {
    kind?: string | undefined;
    key?: string | undefined;
    segment?: string | undefined;
  };
}

/** The halves of a conduit key (`a|b`), trimmed; `[]` for anything else. */
function conduitHalves(key: string | undefined): string[] {
  if (!key || !key.includes("|")) return [];
  return key.split("|").map((half) => half.trim());
}

/**
 * The records that lose their segment's only home if the `add: segment`
 * `entry` goes -- its hosts and the conduits that touch it, in store order.
 * `[]` for any other entry, and `[]` when the network has another home --
 * observed in the export, or added by another record.
 *
 * `observed` is `null` when this twin's observed segments are not known (the
 * topology has not loaded, or the bundle has no export): the records are then
 * listed, conservatively, and `removalConsequences` says the home is unknown.
 */
export function dependentsOf<E extends DependentEntryInput>(
  entries: readonly E[],
  entry: E,
  observed: ReadonlySet<string> | null,
): E[] {
  if (entry.block !== "add" || entry.record.kind !== "segment" || !entry.record.key) return [];
  const cidr = entry.record.key.trim();
  if (observed?.has(cidr)) return [];
  const addedElsewhere = entries.some(
    (e) =>
      e.id !== entry.id && e.block === "add" && e.record.kind === "segment" && (e.record.key ?? "").trim() === cidr,
  );
  if (addedElsewhere) return [];
  return entries.filter((e) => {
    if (e.id === entry.id || e.block !== "add") return false;
    if (e.record.kind === "host") return (e.record.segment ?? "").trim() === cidr;
    if (e.record.kind === "conduit") return conduitHalves(e.record.key).includes(cidr);
    return false;
  });
}

/**
 * What leaving the dependents without their segment would do -- the compiler's
 * own outcomes, one sentence per kind: a host whose segment has no home FAILS
 * the apply (the compiler refuses it), while a conduit to a segment the twin
 * does not carry is reported ineffective and dropped. With the observed
 * segments unknown, both are "could", and a sentence says why.
 */
export function removalConsequences(
  dependents: readonly DependentEntryInput[],
  observedKnown: boolean,
): string[] {
  const would = observedKnown ? "would" : "could";
  const hosts = dependents.filter((d) => d.record.kind === "host").length;
  const conduits = dependents.filter((d) => d.record.kind === "conduit").length;
  const out: string[] = [];
  if (hosts > 0) {
    out.push(
      `left without it, ${hosts === 1 ? "the host" : `the ${hosts} hosts`} ${would} fail the apply — the compiler ` +
        "refuses a host whose segment is neither observed nor added by this file",
    );
  }
  if (conduits > 0) {
    out.push(
      `left without it, ${conduits === 1 ? "the conduit" : `the ${conduits} conduits`} ${would} be reported ` +
        "ineffective and dropped — the compiler does not emit a conduit to a segment the twin does not carry",
    );
  }
  if (!observedKnown && out.length > 0) {
    out.push(
      "this twin's observed segments are not known here (the topology has not loaded, or the bundle has no " +
        "export), so whether this segment is observed — which would keep them valid — cannot be told",
    );
  }
  return out;
}

/** Where a segment may exist. `observed` is `null` when this twin's observed
 *  segments are not known -- then nothing can be called an orphan. */
export interface SegmentHomes {
  observed: ReadonlySet<string> | null;
}

/**
 * Each entry whose segment has no home -- not observed, not added by this file
 * -- mapped to the reason.
 *
 * Nothing is flagged while the observed segments are unknown (a topology that
 * has not loaded, a bundle with no export), because "orphaned" would then be a
 * guess presented as a finding (LES-253).
 */
export function orphanReasons(
  entries: readonly DependentEntryInput[],
  homes: SegmentHomes,
): Map<string, string> {
  const out = new Map<string, string>();
  const observed = homes.observed;
  if (observed === null) return out;
  const added = new Set(
    entries
      .filter((e) => e.block === "add" && e.record.kind === "segment" && e.record.key)
      .map((e) => (e.record.key ?? "").trim()),
  );
  const hasHome = (cidr: string): boolean => observed.has(cidr) || added.has(cidr);
  const nowhere = (cidr: string): string => `${cidr} is neither an observed segment nor one this file adds`;
  for (const e of entries) {
    if (e.block !== "add") continue;
    if (e.record.kind === "host") {
      const segment = (e.record.segment ?? "").trim();
      if (segment && !hasHome(segment)) {
        out.set(e.id, `orphaned: ${nowhere(segment)} — the compiler refuses a host on it`);
      }
    } else if (e.record.kind === "conduit") {
      const missing = conduitHalves(e.record.key).filter((half) => !hasHome(half));
      if (missing.length > 0) {
        out.set(
          e.id,
          `orphaned: ${missing.map(nowhere).join("; ")} — the compiler reports the conduit ineffective and drops it`,
        );
      }
    }
  }
  return out;
}
