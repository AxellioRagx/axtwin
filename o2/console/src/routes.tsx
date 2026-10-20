/**
 * The router -- the one app (ruling §2): Range · Catalog · Design · Telemetry,
 * with a Twin page under the Range.
 *
 * **One catch-all, not a pattern per page** -- deliberate, and unchanged from
 * the canvas: the URL grammars are pure, round-trip-tested functions
 * (`model/appRoutes.ts` for the run-time areas, `model/zoom.ts` for Design),
 * and declaring them again as `<Route path=...>` patterns would create a
 * second spelling no test compares against the first. React Router is here for
 * history, `<Link>` and the back button.
 */
import { BrowserRouter, MemoryRouter, Route, Routes } from "react-router";
import type { ReactNode } from "react";

import { ApiContext } from "./api/context";
import { ConsoleApiError, type ConsoleApi } from "./api/client";
import { RuntimeApiContext } from "./api/runtimeContext";
import { createRuntimeClient, type RuntimeApi } from "./api/runtime";
import { AppFrame } from "./areas/shell/AppFrame";
import { WriteAccessProvider } from "./areas/shell/writeAccess";

export function ConsoleRoutes(): JSX.Element {
  return (
    <Routes>
      <Route path="*" element={<AppFrame />} />
    </Routes>
  );
}

function Providers({
  client,
  runtime,
  children,
}: {
  client: ConsoleApi;
  runtime: RuntimeApi;
  children: ReactNode;
}): JSX.Element {
  return (
    <ApiContext.Provider value={client}>
      <RuntimeApiContext.Provider value={runtime}>
        <WriteAccessProvider>{children}</WriteAccessProvider>
      </RuntimeApiContext.Provider>
    </ApiContext.Provider>
  );
}

export function App({ client, runtime }: { client: ConsoleApi; runtime?: RuntimeApi }): JSX.Element {
  return (
    <Providers client={client} runtime={runtime ?? createRuntimeClient()}>
      <BrowserRouter>
        <ConsoleRoutes />
      </BrowserRouter>
    </Providers>
  );
}

/**
 * A run-time client that answers every call with a refusal -- what a test of
 * the Design area gets when it hands the tree no run-time client, so the
 * header's cluster indicator says "unknown" instead of reaching the network.
 */
export function unavailableRuntime(): RuntimeApi {
  const refuse = (): Promise<never> =>
    Promise.reject(
      new ConsoleApiError({ kind: "network", status: 0, message: "no run-time API in this test", url: "" }),
    );
  return new Proxy({} as RuntimeApi, { get: () => refuse });
}

/** The same tree with an in-memory history -- what the tests render, so they
 *  exercise the real shell rather than a stand-in. */
export function TestApp({
  client,
  runtime,
  url,
  children,
}: {
  client: ConsoleApi;
  runtime?: RuntimeApi;
  url: string;
  children?: ReactNode;
}): JSX.Element {
  return (
    <Providers client={client} runtime={runtime ?? unavailableRuntime()}>
      <MemoryRouter initialEntries={[url]}>{children ?? <ConsoleRoutes />}</MemoryRouter>
    </Providers>
  );
}
