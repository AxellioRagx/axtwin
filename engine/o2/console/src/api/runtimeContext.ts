/**
 * The run-time client in context -- provided, never imported, for the same
 * reason the Design client is (`context.ts`): a test hands the tree a fake.
 */
import { createContext, useContext } from "react";

import type { RuntimeApi } from "./runtime";

export const RuntimeApiContext = createContext<RuntimeApi | null>(null);

export function useRuntimeApi(): RuntimeApi {
  const client = useContext(RuntimeApiContext);
  if (!client) {
    throw new Error(
      "no run-time API client in context: wrap the tree in <RuntimeApiContext.Provider value={createRuntimeClient()}>",
    );
  }
  return client;
}
