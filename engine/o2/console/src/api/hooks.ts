/**
 * Typed hooks over the eight Console routes plus the catalog listing.
 *
 * Deliberately small -- there is no query cache and no state library. Every
 * hook is `load on mount / on id change`, with an explicit `reload()` the
 * apply path calls, because the Console's refresh story is "an apply happened,
 * re-read the bundle" and a cache would only have to be invalidated at exactly
 * that moment anyway.
 *
 * **A failed request is a typed `ConsoleApiError`, not a string** (see
 * `client.ts`): the shell can distinguish "reload and retry" (412) from "wait,
 * an apply holds the lock" (409) without parsing prose.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { useApiClient } from "./context";
import { ConsoleApiError } from "./client";
import type {
  ApplyRequest,
  ApplyResponse,
  AssertionsDoc,
  CastSummary,
  ImprintDiff,
  ImprintList,
  ImprintProvenance,
  LayoutDoc,
  TopologyExport,
} from "./types";

export interface Resource<T> {
  data: T | null;
  error: ConsoleApiError | null;
  loading: boolean;
  reload: () => void;
}

/** What a read resolved to, and the SUBJECT (the deps, with `enabled`) it was
 *  read for. */
interface Slot<T> {
  subject: readonly unknown[];
  data: T | null;
  error: ConsoleApiError | null;
}

function sameSubject(a: readonly unknown[], b: readonly unknown[]): boolean {
  return a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
}

/**
 * **A resource answers only for the subject it was read for.** Its data, its
 * error and its loading flag are returned for the CURRENT deps and no other:
 * on the render after the deps change -- imprint A to imprint B, or the catalog
 * (a disabled read) to B -- the previous subject's value is already held back,
 * before any effect has run.
 *
 * Clearing in the effect alone was not enough (S5.1.a fix round, MF1): the
 * first render after a switch still returned A's topology and cast, so anything
 * DERIVED from them -- a graph built under B's id, a hold that kept it -- mixed
 * two subjects, and the canvas drew A's tiles under B's heading and staged A's
 * segments into B's file. A value keyed to its subject cannot be mistaken for
 * another's (LES-259: pin the isolation at the unit you own).
 */
export function useResource<T>(
  load: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
  enabled = true,
): Resource<T> {
  const [slot, setSlot] = useState<Slot<T> | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [nonce, setNonce] = useState(0);
  const loadRef = useRef(load);
  loadRef.current = load;
  const subject = [...deps, enabled];

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    const loadedFor = subject;
    const controller = new AbortController();
    setLoading(true);
    // Drop what was held -- another subject's, or this one's before a reload
    // -- before fetching. Without this a pane keeps rendering imprint A's
    // numbers under imprint B's heading for the length of the request.
    setSlot({ subject: loadedFor, data: null, error: null });
    loadRef
      .current(controller.signal)
      .then((value) => {
        if (controller.signal.aborted) return;
        setSlot({ subject: loadedFor, data: value, error: null });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        setSlot({ subject: loadedFor, data: null, error: asApiError(cause) });
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
    // The dep list is the caller's, spread deliberately: `load` is held in a
    // ref so an inline closure cannot re-fire the effect, and the deps are
    // the values that genuinely identify the request. (No eslint runs in
    // this package, so no suppression comment is carried for one.)
  }, [...deps, enabled, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  const current = slot !== null && sameSubject(slot.subject, subject);
  return {
    data: current ? slot.data : null,
    error: current ? slot.error : null,
    // A subject whose read has not started yet IS loading -- whatever the
    // previous subject's flag still says.
    loading: current ? loading : enabled,
    reload,
  };
}

export function asApiError(cause: unknown): ConsoleApiError {
  if (cause instanceof ConsoleApiError) return cause;
  return new ConsoleApiError({
    kind: "network",
    status: 0,
    message: cause instanceof Error ? cause.message : String(cause),
    url: "",
  });
}

// ---- reads ---------------------------------------------------------------
export function useImprints(): Resource<ImprintList> {
  const api = useApiClient();
  return useResource((signal) => api.listImprints(signal), [api]);
}

export function useTopology(imprintId: string | null): Resource<TopologyExport> {
  const api = useApiClient();
  return useResource(
    (signal) => api.getTopology(imprintId ?? "", signal),
    [api, imprintId],
    imprintId !== null,
  );
}

export function useCast(imprintId: string | null): Resource<CastSummary> {
  const api = useApiClient();
  return useResource(
    (signal) => api.getCast(imprintId ?? "", signal),
    [api, imprintId],
    imprintId !== null,
  );
}

export function useProvenance(
  imprintId: string | null,
): Resource<ImprintProvenance> {
  const api = useApiClient();
  return useResource(
    (signal) => api.getProvenance(imprintId ?? "", signal),
    [api, imprintId],
    imprintId !== null,
  );
}

/**
 * `since` is a fingerprint a PREVIOUS apply produced on this host; anything
 * else is a 404 naming the known set (`15` §10). The hook stays disabled
 * until there is one, so the SPA never asks the question it cannot ask.
 */
export function useDiff(
  imprintId: string | null,
  since: string | null,
): Resource<ImprintDiff> {
  const api = useApiClient();
  return useResource(
    (signal) => api.getDiff(imprintId ?? "", since ?? "", signal),
    [api, imprintId, since],
    imprintId !== null && !!since,
  );
}

// ---- read + write --------------------------------------------------------
export interface DocResource<T> extends Resource<T> {
  save: (document: Record<string, unknown>, etag: string | null) => Promise<T>;
  saving: boolean;
  saveError: ConsoleApiError | null;
}

function useDocResource<T>(
  read: Resource<T>,
  write: (document: Record<string, unknown>, etag: string | null) => Promise<T>,
): DocResource<T> {
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<ConsoleApiError | null>(null);
  const save = useCallback(
    async (document: Record<string, unknown>, etag: string | null) => {
      setSaving(true);
      setSaveError(null);
      try {
        return await write(document, etag);
      } catch (cause) {
        const error = asApiError(cause);
        setSaveError(error);
        throw error;
      } finally {
        setSaving(false);
      }
    },
    [write],
  );
  return { ...read, save, saving, saveError };
}

export function useAssertions(
  imprintId: string | null,
): DocResource<AssertionsDoc> {
  const api = useApiClient();
  const read = useResource(
    (signal) => api.getAssertions(imprintId ?? "", signal),
    [api, imprintId],
    imprintId !== null,
  );
  const write = useCallback(
    (document: Record<string, unknown>, etag: string | null) =>
      api.putAssertions(imprintId ?? "", document, etag),
    [api, imprintId],
  );
  return useDocResource(read, write);
}

export function useLayout(imprintId: string | null): DocResource<LayoutDoc> {
  const api = useApiClient();
  const read = useResource(
    (signal) => api.getLayout(imprintId ?? "", signal),
    [api, imprintId],
    imprintId !== null,
  );
  const write = useCallback(
    (document: Record<string, unknown>, etag: string | null) =>
      api.putLayout(imprintId ?? "", document, etag),
    [api, imprintId],
  );
  return useDocResource(read, write);
}

// ---- apply (save + recompile; SYNCHRONOUS, ~2.7 s) -----------------------
export interface ApplyHook {
  run: (request: ApplyRequest) => Promise<ApplyResponse>;
  running: boolean;
  result: ApplyResponse | null;
  error: ConsoleApiError | null;
}

export function useApply(imprintId: string | null): ApplyHook {
  const api = useApiClient();
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<ApplyResponse | null>(null);
  const [error, setError] = useState<ConsoleApiError | null>(null);

  const run = useCallback(
    async (request: ApplyRequest) => {
      if (imprintId === null) {
        throw new ConsoleApiError({
          kind: "bad-request",
          status: 0,
          message: "apply needs an imprint",
          url: "",
        });
      }
      setRunning(true);
      setError(null);
      try {
        // No timeout of our own: the server's cap is 120 s and it holds the
        // per-imprint lock for the duration, so a client that gave up early
        // would leave the operator guessing whether the save landed.
        const response = await api.apply(imprintId, request);
        setResult(response);
        return response;
      } catch (cause) {
        const failure = asApiError(cause);
        setError(failure);
        throw failure;
      } finally {
        setRunning(false);
      }
    },
    [api, imprintId],
  );

  return { run, running, result, error };
}
