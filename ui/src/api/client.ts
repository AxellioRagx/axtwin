/**
 * One fetch wrapper, one error vocabulary, no state beyond the write token.
 *
 * Same-origin by default: `axrange serve --spa-dir ui/dist` co-serves this
 * bundle beside `/api`, and `npm run dev` proxies `/api` to it. Nothing is
 * compiled in (ui/Spec.md "Config").
 *
 * The write token lives in a module variable for the life of the tab. It is
 * never persisted (no localStorage, no cookie) and the header is OMITTED, not
 * sent empty, when none is set -- a deployment behind a same-origin session
 * supplies nothing and its writes still work.
 */
export type ApiErrorKind =
  | "bad-request" // 400 -- the message names the field and the accepted set
  | "unauthorized" // 401 -- no/invalid write token
  | "not-found" // 404
  | "conflict" // 409 -- a duplicate name, an existing bundle, a run in progress
  | "unprocessable" // 422 -- FastAPI validation
  | "upstream-failed" // 502 -- aximprint failed
  | "unavailable" // 503 -- a setting is unset (the message names it)
  | "http"
  | "network"
  | "malformed";

const KIND_BY_STATUS: Readonly<Record<number, ApiErrorKind>> = {
  400: "bad-request",
  401: "unauthorized",
  404: "not-found",
  409: "conflict",
  422: "unprocessable",
  502: "upstream-failed",
  503: "unavailable",
};

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number;
  /** The server's own `detail`, verbatim. */
  readonly detail: unknown;
  readonly url: string;

  constructor(init: { kind: ApiErrorKind; status: number; message: string; detail?: unknown; url: string }) {
    super(init.message);
    this.name = "ApiError";
    this.kind = init.kind;
    this.status = init.status;
    this.detail = init.detail ?? null;
    this.url = init.url;
  }
}

let writeToken: string | null = null;
const tokenListeners = new Set<() => void>();

export function setWriteToken(token: string | null): void {
  writeToken = token && token.trim() ? token.trim() : null;
  for (const listener of tokenListeners) listener();
}

export function getWriteToken(): string | null {
  return writeToken;
}

export function onWriteTokenChange(listener: () => void): () => void {
  tokenListeners.add(listener);
  return () => {
    tokenListeners.delete(listener);
  };
}

export interface RequestInit_ {
  query?: Record<string, string>;
  body?: unknown;
  /** Send the write token (writes only; reads carry nothing). */
  auth?: boolean;
  signal?: AbortSignal | undefined;
}

export type Requester = <T>(method: string, path: string, init?: RequestInit_) => Promise<T>;

export interface ClientOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
}

/**
 * The server's refusal, in its own words. `axrange serve` answers an
 * `ApiError` as `{error, detail}`; FastAPI's validation answers `{detail:
 * [...]}`; anything else is reported by status.
 */
export function messageFromBody(status: number, body: unknown): { message: string; detail: unknown } {
  if (body && typeof body === "object") {
    const b = body as Record<string, unknown>;
    if (typeof b.error === "string") return { message: b.error, detail: b.detail ?? null };
    if (typeof b.detail === "string") return { message: b.detail, detail: null };
    if (Array.isArray(b.detail)) {
      const parts = b.detail.map((d) => {
        const e = d as { loc?: unknown[]; msg?: string };
        const loc = Array.isArray(e.loc) ? e.loc.filter((x) => x !== "body").join(".") : "";
        return loc ? `${loc}: ${e.msg ?? "invalid"}` : (e.msg ?? "invalid");
      });
      return { message: parts.join("; "), detail: b.detail };
    }
  }
  return { message: `HTTP ${status}`, detail: body ?? null };
}

export function makeRequester(options: ClientOptions = {}): Requester {
  const base = (options.baseUrl ?? "").replace(/\/+$/, "");
  const doFetch = options.fetchImpl ?? globalThis.fetch.bind(globalThis);

  return async function request<T>(method: string, path: string, init: RequestInit_ = {}): Promise<T> {
    const q = init.query && Object.keys(init.query).length > 0 ? `?${new URLSearchParams(init.query)}` : "";
    const url = `${base}${path}${q}`;
    const headers: Record<string, string> = { Accept: "application/json" };
    if (init.body !== undefined) headers["Content-Type"] = "application/json";
    if (init.auth && writeToken) headers.Authorization = `Bearer ${writeToken}`;
    let response: Response;
    try {
      response = await doFetch(url, {
        method,
        headers,
        body: init.body === undefined ? undefined : JSON.stringify(init.body),
        signal: init.signal,
      });
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") throw e;
      throw new ApiError({ kind: "network", status: 0, message: `no answer from ${url}: ${String(e)}`, url });
    }
    if (response.status === 204) return undefined as T;
    const text = await response.text();
    let body: unknown = null;
    if (text) {
      try {
        body = JSON.parse(text);
      } catch {
        if (response.ok) {
          throw new ApiError({ kind: "malformed", status: response.status, message: `non-JSON answer from ${url}`, url });
        }
      }
    }
    if (!response.ok) {
      const { message, detail } = messageFromBody(response.status, body);
      throw new ApiError({ kind: KIND_BY_STATUS[response.status] ?? "http", status: response.status, message, detail, url });
    }
    return body as T;
  };
}
