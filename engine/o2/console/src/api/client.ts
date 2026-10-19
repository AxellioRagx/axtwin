/**
 * The typed API client -- one fetch wrapper, one error vocabulary, no state.
 *
 * **Configuration is one value**, the API base URL (`docs/WORK_ORDER_console_
 * canvas.md` §6): empty (same-origin) is the normal case, both co-served
 * behind `axrange serve --spa-dir` and under `npm run dev`, whose proxy
 * forwards same-origin `/api` to a real `axrange serve`. No imprint id,
 * catalog, palette or cluster fact is compiled in.
 *
 * `VITE_AXRANGE_API` overrides it with an ABSOLUTE base URL, which makes the
 * browser talk **cross-origin and bypass the dev proxy**. The API installs no
 * CORS middleware, so this only works behind a deployment that adds its own;
 * to point the DEV SERVER at another API, set the unprefixed
 * `AXRANGE_DEV_API` (the proxy target) instead -- see `vite.config.ts`.
 *
 * **The write token is supplied at RUNTIME and never persisted.** It lives in
 * a module variable for the life of the tab (`setWriteToken`), so a reload
 * drops it; it is never written to `localStorage`/`sessionStorage`/a cookie
 * and never baked into the bundle. A deployment that fronts the API with a
 * same-origin session (or Grafana's datasource proxy, `15` §9) supplies
 * nothing at all and the writes still work -- which is why the header is
 * omitted rather than sent empty when no token is set.
 *
 * **Errors are typed by KIND, not by status**, because the recoveries differ:
 * a 412 means "reload and retry", a 409 means "an apply holds the lock, wait"
 * -- the S1 build split those two deliberately (`docs/design/15-console-
 * canvas.md` §10) and a client that lumped them would hide the difference
 * again. Every error carries the server's own `detail`, and the `etag` the
 * server hands back on a refused write is lifted out of it, because that is
 * the value the SPA must adopt to retry.
 */
import type {
  ApplyRequest,
  ApplyResponse,
  AssertionsDoc,
  CastSummary,
  ImprintDiff,
  ImprintList,
  ImprintProvenance,
  ImprintSummary,
  LayoutDoc,
  TopologyExport,
} from "./types";

export type ApiErrorKind =
  | "bad-request" // 400 -- the document is malformed; the message names the accepted set
  | "unauthorized" // 401 -- no/invalid write token
  | "not-found" // 404 -- unknown imprint, or an unknown `since=` fingerprint
  | "conflict" // 409 -- an apply holds this imprint's lock (wait, then retry)
  | "stale-etag" // 412 -- the file moved under you (reload, re-edit, retry)
  | "locked" // 423 -- the deploy is protected (override explicitly, or leave it)
  | "unprocessable" // 422 -- the compiler refused the file (exit 2), or FastAPI validation
  | "upstream-failed" // 502 -- the recompile failed or timed out
  | "unavailable" // 503 -- a setting is unset, or the bundle dir is unwritable
  | "http" // any other non-2xx
  | "network" // fetch never got an answer
  | "malformed"; // a 2xx whose body is not JSON

const KIND_BY_STATUS: Readonly<Record<number, ApiErrorKind>> = {
  400: "bad-request",
  401: "unauthorized",
  404: "not-found",
  409: "conflict",
  412: "stale-etag",
  423: "locked",
  422: "unprocessable",
  502: "upstream-failed",
  503: "unavailable",
};

export class ConsoleApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number;
  /** The server's `detail` payload, verbatim (object, list or string). */
  readonly detail: unknown;
  /** `detail.etag` where the server sent one -- the value a retry must adopt. */
  readonly etag: string | null;
  readonly url: string;

  constructor(init: {
    kind: ApiErrorKind;
    status: number;
    message: string;
    detail?: unknown;
    etag?: string | null;
    url: string;
  }) {
    super(init.message);
    this.name = "ConsoleApiError";
    this.kind = init.kind;
    this.status = init.status;
    this.detail = init.detail ?? null;
    this.etag = init.etag ?? null;
    this.url = init.url;
  }
}

/** Supplied by the host page/shell at runtime. Never compiled in. */
export type TokenProvider = () => string | null | undefined;

let writeToken: string | null = null;

/** Set the bearer token for this tab only. Not persisted anywhere. */
export function setWriteToken(token: string | null): void {
  writeToken = token && token.trim() ? token.trim() : null;
  for (const listener of tokenListeners) listener();
}

const tokenListeners = new Set<() => void>();

/** Be told when the token changes (the header's "write token" indicator, which
 *  must agree with the Design area's own field). Returns the unsubscribe. */
export function onWriteTokenChange(listener: () => void): () => void {
  tokenListeners.add(listener);
  return () => {
    tokenListeners.delete(listener);
  };
}

export function getWriteToken(): string | null {
  return writeToken;
}

export interface ClientOptions {
  /** "" (same-origin, the co-served default) or an absolute origin. */
  baseUrl?: string;
  token?: TokenProvider;
  /** Injected by tests; defaults to the global `fetch`. */
  fetchImpl?: typeof fetch;
}

/** The default base URL: same-origin, unless the dev build was told otherwise. */
export function defaultBaseUrl(): string {
  const configured: unknown = import.meta.env?.VITE_AXRANGE_API;
  return typeof configured === "string" ? configured.replace(/\/+$/, "") : "";
}

export interface ConsoleApi {
  listImprints(signal?: AbortSignal): Promise<ImprintList>;
  getImprint(id: string, signal?: AbortSignal): Promise<ImprintSummary>;
  getProvenance(id: string, signal?: AbortSignal): Promise<ImprintProvenance>;
  getTopology(id: string, signal?: AbortSignal): Promise<TopologyExport>;
  getCast(id: string, signal?: AbortSignal): Promise<CastSummary>;
  getAssertions(id: string, signal?: AbortSignal): Promise<AssertionsDoc>;
  putAssertions(
    id: string,
    document: Record<string, unknown>,
    etag: string | null,
  ): Promise<AssertionsDoc>;
  getLayout(id: string, signal?: AbortSignal): Promise<LayoutDoc>;
  putLayout(
    id: string,
    document: Record<string, unknown>,
    etag: string | null,
  ): Promise<LayoutDoc>;
  apply(id: string, request: ApplyRequest): Promise<ApplyResponse>;
  getDiff(id: string, since: string, signal?: AbortSignal): Promise<ImprintDiff>;
}

/** One request, typed by the caller. Shared by the Design client below and
 *  the run-time client (`api/runtime.ts`), so both speak one error vocabulary
 *  and one token rule (LES-025: one copy). */
export interface RequestInit_ {
  query?: Record<string, string>;
  body?: unknown;
  etag?: string | null;
  signal?: AbortSignal | undefined;
  auth?: boolean;
  /** `text` returns the body as a string (a YAML export); default JSON. */
  as?: "json" | "text";
}

export type Requester = <T>(method: string, path: string, init?: RequestInit_) => Promise<T>;

export function makeRequester(options: ClientOptions = {}): Requester {
  const base = (options.baseUrl ?? defaultBaseUrl()).replace(/\/+$/, "");
  const token = options.token ?? getWriteToken;
  const doFetch = options.fetchImpl ?? globalThis.fetch.bind(globalThis);

  function url(path: string, query?: Record<string, string>): string {
    const q = query && Object.keys(query).length > 0 ? `?${new URLSearchParams(query).toString()}` : "";
    return `${base}${path}${q}`;
  }

  function authHeaders(): Record<string, string> {
    // OMITTED, not empty: a deployment behind a same-origin session or the
    // Grafana datasource proxy supplies no token and must not be sent one.
    const value = token();
    return value ? { Authorization: `Bearer ${value}` } : {};
  }

  return async function request<T>(method: string, path: string, init: RequestInit_ = {}): Promise<T> {
    const target = url(path, init.query);
    const headers: Record<string, string> = {
      Accept: init.as === "text" ? "text/plain, text/yaml, */*" : "application/json",
    };
    if (init.body !== undefined) headers["Content-Type"] = "application/json";
    if (init.auth) Object.assign(headers, authHeaders());
    // BOTH channels, exactly as the server accepts them: the header for a
    // well-behaved client and the same value in the body, so a proxy that
    // strips `If-Match` cannot silently turn a guarded write into an
    // unguarded one (`15` §10).
    if (init.etag) headers["If-Match"] = `"${init.etag}"`;

    let response: Response;
    try {
      response = await doFetch(target, {
        method,
        headers,
        ...(init.body === undefined ? {} : { body: JSON.stringify(init.body) }),
        ...(init.signal ? { signal: init.signal } : {}),
      });
    } catch (cause) {
      throw new ConsoleApiError({
        kind: "network",
        status: 0,
        message: `${method} ${target}: no response (${describe(cause)})`,
        url: target,
      });
    }

    if (init.as === "text" && response.ok) {
      return (await response.text()) as T;
    }
    const payload = await readJson(response);
    if (!response.ok) throw toError(response, payload, target, method);
    if (payload.ok) return payload.value as T;
    throw new ConsoleApiError({
      kind: "malformed",
      status: response.status,
      message: `${method} ${target}: expected a JSON body (${payload.reason})`,
      url: target,
    });
  };
}

export function createClient(options: ClientOptions = {}): ConsoleApi {
  const request = makeRequester(options);

  return {
    listImprints: (signal) =>
      request<ImprintList>("GET", "/api/imprints", { signal }),
    getImprint: (id, signal) =>
      request<ImprintSummary>("GET", `/api/imprints/${enc(id)}`, { signal }),
    getProvenance: (id, signal) =>
      request<ImprintProvenance>(
        "GET",
        `/api/imprints/${enc(id)}/provenance`,
        { signal },
      ),
    getTopology: (id, signal) =>
      request<TopologyExport>("GET", `/api/imprints/${enc(id)}/topology`, {
        signal,
      }),
    getCast: (id, signal) =>
      request<CastSummary>("GET", `/api/imprints/${enc(id)}/cast`, { signal }),
    getAssertions: (id, signal) =>
      request<AssertionsDoc>("GET", `/api/imprints/${enc(id)}/assertions`, {
        signal,
      }),
    putAssertions: (id, document, etag) =>
      request<AssertionsDoc>("PUT", `/api/imprints/${enc(id)}/assertions`, {
        body: etag ? { ...document, etag } : document,
        etag,
        auth: true,
      }),
    getLayout: (id, signal) =>
      request<LayoutDoc>("GET", `/api/imprints/${enc(id)}/layout`, { signal }),
    putLayout: (id, document, etag) =>
      request<LayoutDoc>("PUT", `/api/imprints/${enc(id)}/layout`, {
        body: etag ? { ...document, etag } : document,
        etag,
        auth: true,
      }),
    apply: (id, req) =>
      request<ApplyResponse>("POST", `/api/imprints/${enc(id)}/apply`, {
        body: req,
        etag: req.etag ?? null,
        auth: true,
      }),
    getDiff: (id, since, signal) =>
      request<ImprintDiff>("GET", `/api/imprints/${enc(id)}/diff`, {
        query: { since },
        signal,
      }),
  };
}

function enc(id: string): string {
  return encodeURIComponent(id);
}

type JsonRead =
  | { ok: true; value: unknown }
  | { ok: false; reason: string; text: string };

async function readJson(response: Response): Promise<JsonRead> {
  let text: string;
  try {
    text = await response.text();
  } catch (cause) {
    return { ok: false, reason: describe(cause), text: "" };
  }
  if (!text) return { ok: false, reason: "empty body", text };
  try {
    return { ok: true, value: JSON.parse(text) as unknown };
  } catch {
    return { ok: false, reason: "not JSON", text };
  }
}

/**
 * Two body shapes reach here and both are handled rather than guessed at:
 * `axrange.api.service.ApiError` renders `{error, detail}`, while FastAPI's
 * own request validation renders `{detail: [...]}` with no `error` at all.
 */
function toError(
  response: Response,
  payload: JsonRead,
  target: string,
  method: string,
): ConsoleApiError {
  const kind = KIND_BY_STATUS[response.status] ?? "http";
  const body: Record<string, unknown> =
    payload.ok && isRecord(payload.value) ? payload.value : {};
  const detail = "detail" in body ? body["detail"] : undefined;
  const message =
    typeof body["error"] === "string" && body["error"]
      ? body["error"]
      : typeof detail === "string" && detail
        ? detail
        : payload.ok
          ? `${method} ${target}: HTTP ${response.status}`
          : `${method} ${target}: HTTP ${response.status} (${payload.text.slice(0, 200)})`;
  return new ConsoleApiError({
    kind,
    status: response.status,
    message,
    detail: detail ?? null,
    etag: etagFrom(detail) ?? etagFromHeader(response),
    url: target,
  });
}

/** The server hands the on-disk etag back inside `detail` on a refused write. */
function etagFrom(detail: unknown): string | null {
  if (isRecord(detail) && typeof detail["etag"] === "string") {
    return detail["etag"];
  }
  return null;
}

function etagFromHeader(response: Response): string | null {
  const raw = response.headers.get("ETag");
  return raw ? raw.replace(/^"|"$/g, "") : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function describe(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}
