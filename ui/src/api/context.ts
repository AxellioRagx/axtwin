import { createContext, useContext } from "react";

import type { Api } from "./discovery";

export const ApiContext = createContext<Api | null>(null);

export function useApi(): Api {
  const api = useContext(ApiContext);
  if (!api) throw new Error("useApi: no ApiContext provider above this component");
  return api;
}
