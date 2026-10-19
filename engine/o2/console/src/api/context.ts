/**
 * The client is provided, never imported, so a test (or the Grafana embed,
 * `15` §9) can hand the tree a differently-configured one without touching a
 * module global.
 */
import { createContext, useContext } from "react";

import type { ConsoleApi } from "./client";

export const ApiContext = createContext<ConsoleApi | null>(null);

export function useApiClient(): ConsoleApi {
  const client = useContext(ApiContext);
  if (!client) {
    throw new Error(
      "no API client in context: wrap the tree in <ApiContext.Provider value={createClient(...)}>",
    );
  }
  return client;
}
