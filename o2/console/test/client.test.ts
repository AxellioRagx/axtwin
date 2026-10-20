import { afterEach, describe, expect, it } from "vitest";

import {
  ConsoleApiError,
  createClient,
  setWriteToken,
  type ApiErrorKind,
} from "../src/api/client";

interface Call {
  url: string;
  init: RequestInit;
}

/** A fetch double that records what it was asked and answers from a queue. */
function stubFetch(
  answers: Array<{ status: number; body?: unknown; text?: string; headers?: Record<string, string> }>,
): { fetchImpl: typeof fetch; calls: Call[] } {
  const calls: Call[] = [];
  const queue = [...answers];
  const fetchImpl: typeof fetch = async (input, init) => {
    calls.push({ url: String(input), init: init ?? {} });
    const answer = queue.shift() ?? { status: 200, body: {} };
    const text =
      answer.text !== undefined ? answer.text : JSON.stringify(answer.body ?? {});
    return new Response(text, {
      status: answer.status,
      headers: { "Content-Type": "application/json", ...(answer.headers ?? {}) },
    });
  };
  return { fetchImpl, calls };
}

function headerOf(call: Call, name: string): string | undefined {
  const headers = call.init.headers as Record<string, string> | undefined;
  return headers?.[name];
}

afterEach(() => setWriteToken(null));

describe("error mapping", () => {
  const cases: Array<[number, ApiErrorKind]> = [
    [400, "bad-request"],
    [401, "unauthorized"],
    [404, "not-found"],
    [409, "conflict"],
    [412, "stale-etag"],
    [422, "unprocessable"],
    [502, "upstream-failed"],
    [503, "unavailable"],
  ];

  for (const [status, kind] of cases) {
    it(`maps ${status} to ${kind} and carries the server's message`, async () => {
      const { fetchImpl } = stubFetch([
        { status, body: { error: `refused at ${status}`, detail: { hint: "x" } } },
      ]);
      const api = createClient({ fetchImpl });
      const failure = await api.getCast("corpapcn-golden").catch((e: unknown) => e);
      expect(failure).toBeInstanceOf(ConsoleApiError);
      const error = failure as ConsoleApiError;
      expect(error.kind).toBe(kind);
      expect(error.status).toBe(status);
      expect(error.message).toBe(`refused at ${status}`);
      expect(error.detail).toEqual({ hint: "x" });
    });
  }

  it("distinguishes 409 (an apply holds the lock) from 412 (stale etag)", async () => {
    // Two different recoveries -- "wait" vs "reload and retry" -- which the
    // S1 build split on purpose (`15` §10).
    const { fetchImpl } = stubFetch([
      { status: 409, body: { error: "an apply is in progress" } },
      { status: 412, body: { error: "stale", detail: { etag: "on-disk-9" } } },
    ]);
    const api = createClient({ fetchImpl });
    const conflict = (await api
      .putAssertions("i", {}, "mine")
      .catch((e: unknown) => e)) as ConsoleApiError;
    const stale = (await api
      .putAssertions("i", {}, "mine")
      .catch((e: unknown) => e)) as ConsoleApiError;
    expect(conflict.kind).toBe("conflict");
    expect(stale.kind).toBe("stale-etag");
  });

  it("lifts the on-disk etag out of `detail` so a retry has one", async () => {
    const { fetchImpl } = stubFetch([
      { status: 412, body: { error: "stale", detail: { etag: "on-disk-9" } } },
    ]);
    const api = createClient({ fetchImpl });
    const error = (await api
      .putAssertions("i", { schema: "axr-assertions/1" }, "mine")
      .catch((e: unknown) => e)) as ConsoleApiError;
    expect(error.etag).toBe("on-disk-9");
  });

  it("lifts the etag a FAILED APPLY hands back for the file it did save", async () => {
    const { fetchImpl } = stubFetch([
      {
        status: 502,
        body: { error: "the recompile failed", detail: { etag: "saved-7" } },
      },
    ]);
    const api = createClient({ fetchImpl });
    const error = (await api
      .apply("i", { document: {} })
      .catch((e: unknown) => e)) as ConsoleApiError;
    expect(error.kind).toBe("upstream-failed");
    expect(error.etag).toBe("saved-7");
  });

  it("handles FastAPI's own `{detail: [...]}` validation shape", async () => {
    const { fetchImpl } = stubFetch([
      { status: 422, body: { detail: [{ loc: ["body"], msg: "field required" }] } },
    ]);
    const api = createClient({ fetchImpl });
    const error = (await api.getCast("i").catch((e: unknown) => e)) as ConsoleApiError;
    expect(error.kind).toBe("unprocessable");
    expect(Array.isArray(error.detail)).toBe(true);
    expect(error.message).toContain("422");
  });

  it("maps an unmapped status to `http` rather than to a familiar kind", async () => {
    const { fetchImpl } = stubFetch([{ status: 418, body: { error: "teapot" } }]);
    const api = createClient({ fetchImpl });
    const error = (await api.getCast("i").catch((e: unknown) => e)) as ConsoleApiError;
    expect(error.kind).toBe("http");
    expect(error.status).toBe(418);
  });

  it("reports a fetch that never answered as `network`", async () => {
    const fetchImpl: typeof fetch = () => Promise.reject(new TypeError("boom"));
    const api = createClient({ fetchImpl });
    const error = (await api.getCast("i").catch((e: unknown) => e)) as ConsoleApiError;
    expect(error.kind).toBe("network");
    expect(error.status).toBe(0);
    expect(error.message).toContain("boom");
  });

  it("reports a 200 that is not JSON as `malformed`, never as data", async () => {
    const { fetchImpl } = stubFetch([{ status: 200, text: "<html>proxy error</html>" }]);
    const api = createClient({ fetchImpl });
    const error = (await api.getCast("i").catch((e: unknown) => e)) as ConsoleApiError;
    expect(error.kind).toBe("malformed");
  });
});

describe("requests", () => {
  it("is same-origin by default and prefixes an explicit base URL", async () => {
    const { fetchImpl, calls } = stubFetch([{ status: 200 }, { status: 200 }]);
    await createClient({ fetchImpl }).listImprints();
    await createClient({ fetchImpl, baseUrl: "http://127.0.0.1:8011/" }).listImprints();
    expect(calls[0]!.url).toBe("/api/imprints");
    expect(calls[1]!.url).toBe("http://127.0.0.1:8011/api/imprints");
  });

  it("hits the eight Console routes at the paths the API publishes", async () => {
    const { fetchImpl, calls } = stubFetch(new Array(8).fill({ status: 200 }));
    const api = createClient({ fetchImpl });
    await api.getTopology("corpapcn-golden");
    await api.getCast("corpapcn-golden");
    await api.getAssertions("corpapcn-golden");
    await api.putAssertions("corpapcn-golden", {}, null);
    await api.getLayout("corpapcn-golden");
    await api.putLayout("corpapcn-golden", {}, null);
    await api.apply("corpapcn-golden", { document: {} });
    await api.getDiff("corpapcn-golden", "fp-1");
    expect(calls.map((c) => `${String(c.init.method)} ${c.url}`)).toEqual([
      "GET /api/imprints/corpapcn-golden/topology",
      "GET /api/imprints/corpapcn-golden/cast",
      "GET /api/imprints/corpapcn-golden/assertions",
      "PUT /api/imprints/corpapcn-golden/assertions",
      "GET /api/imprints/corpapcn-golden/layout",
      "PUT /api/imprints/corpapcn-golden/layout",
      "POST /api/imprints/corpapcn-golden/apply",
      "GET /api/imprints/corpapcn-golden/diff?since=fp-1",
    ]);
  });

  it("sends the etag in BOTH If-Match and the body", async () => {
    // The server accepts both on purpose, so a header-stripping proxy cannot
    // turn a guarded write into an unguarded one (`15` §10).
    const { fetchImpl, calls } = stubFetch([{ status: 200 }]);
    await createClient({ fetchImpl }).putAssertions(
      "i",
      { schema: "axr-assertions/1" },
      "etag-1",
    );
    expect(headerOf(calls[0]!, "If-Match")).toBe('"etag-1"');
    expect(JSON.parse(String(calls[0]!.init.body))).toMatchObject({ etag: "etag-1" });
  });

  it("omits the Authorization header when no token is set", async () => {
    const { fetchImpl, calls } = stubFetch([{ status: 200 }]);
    await createClient({ fetchImpl }).putAssertions("i", {}, null);
    expect(headerOf(calls[0]!, "Authorization")).toBeUndefined();
  });

  it("sends a runtime token as a bearer, and never persists it", async () => {
    const { fetchImpl, calls } = stubFetch([{ status: 200 }, { status: 200 }]);
    setWriteToken("s3cret");
    const api = createClient({ fetchImpl });
    await api.putAssertions("i", {}, null);
    await api.getCast("i");
    expect(headerOf(calls[0]!, "Authorization")).toBe("Bearer s3cret");
    // Reads carry no token: they are open on a trusted bind.
    expect(headerOf(calls[1]!, "Authorization")).toBeUndefined();
    // Nothing is written anywhere a reload could recover it.
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
    expect(document.cookie).toBe("");
  });
});
