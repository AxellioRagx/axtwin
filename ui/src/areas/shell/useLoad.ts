import { useCallback, useEffect, useState } from "react";

/** Load once per `deps` change, with an abort on unmount and a manual reload. */
export function useLoad<T>(load: (signal: AbortSignal) => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    const ctl = new AbortController();
    setLoading(true);
    load(ctl.signal).then(
      (d) => {
        if (ctl.signal.aborted) return;
        setData(d);
        setError(null);
        setLoading(false);
      },
      (e: unknown) => {
        if (ctl.signal.aborted) return;
        setError(e);
        setLoading(false);
      },
    );
    return () => ctl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  return { data, error, loading, reload };
}
