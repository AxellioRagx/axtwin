/**
 * Hooks over the run-time client. The same `useResource` the Design area
 * uses (a value answers only for the subject it was read for, LES-259), plus
 * two small primitives the run-time screens need and the canvas did not:
 *
 *   - `useOnDemand` -- a read the operator ASKS for (live health): nothing is
 *     fetched on mount, the result keeps the subject it was read for, and a
 *     second click while one is in flight is ignored rather than raced.
 *   - `useNow` -- a ticking clock so "checked 40 s ago" stays true.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { asApiError, useResource, type Resource } from "./hooks";
import type { ConsoleApiError } from "./client";
import { useRuntimeApi } from "./runtimeContext";
import type {
  AccessFacts,
  PersonaCatalog,
  RangeDoc,
  ScenarioList,
  TargetList,
  TwinConfigList,
  TwinHostsDoc,
} from "./runtimeTypes";

export function useRange(): Resource<RangeDoc> {
  const api = useRuntimeApi();
  return useResource((signal) => api.getRange(signal), [api]);
}

export function useTargets(): Resource<TargetList> {
  const api = useRuntimeApi();
  return useResource((signal) => api.getTargets(signal), [api]);
}

export function useHosts(deployId: string | null, target: string | null): Resource<TwinHostsDoc> {
  const api = useRuntimeApi();
  return useResource(
    (signal) => api.getHosts(deployId ?? "", target, signal),
    [api, deployId, target],
    deployId !== null,
  );
}

export function useAccess(deployId: string | null, target: string | null): Resource<AccessFacts> {
  const api = useRuntimeApi();
  return useResource(
    (signal) => api.getAccess(deployId ?? "", target, signal),
    [api, deployId, target],
    deployId !== null,
  );
}

export function usePersonas(twin: string | null, enabled = true): Resource<PersonaCatalog> {
  const api = useRuntimeApi();
  return useResource((signal) => api.getPersonas(twin, signal), [api, twin], enabled);
}

export function useTwinConfigs(): Resource<TwinConfigList> {
  const api = useRuntimeApi();
  return useResource((signal) => api.listTwinConfigs(signal), [api]);
}

export function useScenarios(): Resource<ScenarioList> {
  const api = useRuntimeApi();
  return useResource((signal) => api.listScenarios(signal), [api]);
}

// ---- on demand -------------------------------------------------------------
export interface OnDemand<T> {
  data: T | null;
  error: ConsoleApiError | null;
  loading: boolean;
  /** When THIS browser received the answer (ms). The server's own
   *  `checked_at` is what the age is shown from; this is the fallback. */
  receivedAt: number | null;
  run: () => void;
}

/**
 * A read that happens only when asked. `subjectKey` names what it is FOR (a
 * deploy + host): a result for another subject is never returned, and an
 * answer that lands after the subject changed is dropped.
 */
export function useOnDemand<T>(
  subjectKey: string,
  load: (signal: AbortSignal) => Promise<T>,
): OnDemand<T> {
  const [slot, setSlot] = useState<{
    key: string;
    data: T | null;
    error: ConsoleApiError | null;
    receivedAt: number | null;
  } | null>(null);
  const [loadingKey, setLoadingKey] = useState<string | null>(null);
  const loadRef = useRef(load);
  loadRef.current = load;
  const keyRef = useRef(subjectKey);
  keyRef.current = subjectKey;
  const inFlight = useRef<AbortController | null>(null);

  useEffect(() => () => inFlight.current?.abort(), []);
  // A new subject abandons the old read: its answer would be for someone else.
  useEffect(() => {
    inFlight.current?.abort();
    inFlight.current = null;
    setLoadingKey(null);
  }, [subjectKey]);

  const run = useCallback(() => {
    if (inFlight.current) return; // double-click: one read at a time
    const key = keyRef.current;
    const controller = new AbortController();
    inFlight.current = controller;
    setLoadingKey(key);
    loadRef
      .current(controller.signal)
      .then((data) => {
        if (controller.signal.aborted || keyRef.current !== key) return;
        setSlot({ key, data, error: null, receivedAt: Date.now() });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted || keyRef.current !== key) return;
        setSlot({ key, data: null, error: asApiError(cause), receivedAt: Date.now() });
      })
      .finally(() => {
        if (inFlight.current === controller) inFlight.current = null;
        if (!controller.signal.aborted) setLoadingKey(null);
      });
  }, []);

  const current = slot !== null && slot.key === subjectKey;
  return {
    data: current ? slot.data : null,
    error: current ? slot.error : null,
    receivedAt: current ? slot.receivedAt : null,
    loading: loadingKey === subjectKey,
    run,
  };
}

/** Wall-clock ms, re-rendered every `intervalMs`. */
export function useNow(intervalMs = 5000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}
