/**
 * One fetch wrapper, one error vocabulary, no state.
 *
 * Same-origin by default: `axrange serve --spa-dir ui/dist` co-serves this
 * bundle beside `/api`, and `npm run dev` proxies `/api` to it. Nothing is
 * compiled in (ui/Spec.md "Config").
 *
 * The console holds no credential (ui/Spec.md "Seam", `api/Spec.md` A8):
 * every request goes with `credentials: "include"` so a reverse proxy's
 * session cookie rides along, and the API decides, per deployment, whom to
 * trust. A write is attempted, never pre-judged; a `401` means sign in, a
 * `403` means this account is read-only here.
 */
export type ApiErrorKind =
  | "bad-request" // 400 -- the message names the field and the accepted set
  | "unauthorized" // 401 -- sign in required (the deployment's auth mode found no identity)
  | "forbidden" // 403 -- signed in, but this account may not write here
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
  403: "forbidden",
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

export interface RequestInit_ {
  query?: Record<string, string>;
  body?: unknown;
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
    let response: Response;
    try {
      response = await doFetch(url, {
        method,
        headers,
        credentials: "include",
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
      // A 405 on an API route means the running server has no such route:
      // it predates this console build (a route was added since it started).
      const said = response.status === 405 && path.startsWith("/api/") ? `the running API does not serve ${method} ${path} (${message}): it predates this console build; restart \`axrange serve\` from this checkout` : message;
      throw new ApiError({ kind: KIND_BY_STATUS[response.status] ?? "http", status: response.status, message: said, detail, url });
    }
    return body as T;
  };
}
