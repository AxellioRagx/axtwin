/**
 * A typed run-time API double: every method refuses unless the test supplies
 * it, so a screen that calls something unexpected fails loudly instead of
 * rendering off `undefined` (the same rule `shell.test.tsx`'s `fakeApi` states
 * for the Design client).
 */
import { ConsoleApiError, type ConsoleApi } from "../../src/api/client";
import type { RuntimeApi } from "../../src/api/runtime";
import type { TwinHostsDoc } from "../../src/api/runtimeTypes";
// Imported, not read from disk: under jsdom `import.meta.url` is not a file
// URL (LES-260), and a JSON import is the bundler's own seam.
import corpTwin from "../fixtures/corp-twin-hosts.json";

export function fakeRuntime(overrides: Partial<RuntimeApi> = {}, calls: string[] = []): RuntimeApi {
  const base = new Proxy({} as RuntimeApi, {
    get: (_t, name: string) => () => {
      calls.push(`unexpected:${name}`);
      return Promise.reject(new Error(`${name} not used by this test`));
    },
  });
  return new Proxy(base, {
    get: (_t, name: string) => {
      const own = (overrides as Record<string, unknown>)[name];
      if (typeof own === "function") {
        return (...args: unknown[]) => {
          calls.push(name);
          return (own as (...a: unknown[]) => unknown)(...args);
        };
      }
      return (base as unknown as Record<string, unknown>)[name];
    },
  });
}

/** A Design client that is never expected to be called. */
export function unusedDesignApi(): ConsoleApi {
  return new Proxy({} as ConsoleApi, {
    get: (_t, name: string) => () => Promise.reject(new Error(`design ${name} not used by this test`)),
  });
}

export function apiError(kind: ConsoleApiError["kind"], status: number, message: string, detail: unknown = null): ConsoleApiError {
  return new ConsoleApiError({ kind, status, message, detail, url: "" });
}

/** The real 106-host corp-twin, in the `/hosts` shape (see its `_provenance`). */
export function corpTwinHosts(): TwinHostsDoc {
  // A fresh copy per call, so no test can mutate another's twin.
  return structuredClone(corpTwin) as unknown as TwinHostsDoc;
}

/** A never-settling promise, for "in flight" states. */
export function pending<T>(): Promise<T> {
  return new Promise<T>(() => undefined);
}

/** A promise the test resolves or rejects by hand (LES-323: hold a read open). */
export function deferred<T>(): { promise: Promise<T>; resolve: (v: T) => void; reject: (e: unknown) => void } {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}
