import { describe, expect, it } from "vitest";

import { ApiError, makeRequester, messageFromBody } from "../src/api/client";
import { makeApi } from "../src/api/discovery";

function fetchStub(status: number, body: unknown, seen: Array<{ url: string; init: RequestInit }>) {
  return (async (url: string | URL | Request, init?: RequestInit) => {
    seen.push({ url: String(url), init: init ?? {} });
    return new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
}

describe("the server's refusal, in its own words", () => {
  it("reads an ApiError body", () => {
    expect(messageFromBody(400, { error: "endpoint must be an absolute http(s) base URL", detail: null }).message).toBe(
      "endpoint must be an absolute http(s) base URL",
    );
  });
  it("reads a FastAPI validation body", () => {
    const { message } = messageFromBody(422, { detail: [{ loc: ["body", "name"], msg: "Field required" }] });
    expect(message).toBe("name: Field required");
  });
  it("falls back to the status", () => {
    expect(messageFromBody(500, "boom").message).toBe("HTTP 500");
  });
});

describe("the requester", () => {
  it("holds no credential: no Authorization header on any call, credentials included for a proxy's cookie", async () => {
    const seen: Array<{ url: string; init: RequestInit }> = [];
    const request = makeRequester({ fetchImpl: fetchStub(200, { ok: true }, seen) });
    await request("GET", "/api/discovery/sources");
    await request("POST", "/api/discovery/sources", { body: { a: 1 } });
    for (const call of seen) {
      expect((call.init.headers as Record<string, string>).Authorization).toBeUndefined();
      expect(call.init.credentials).toBe("include");
    }
    expect(seen[1]?.init.body).toBe('{"a":1}');
  });
  it("asks who it is, and reads a twin-config without any header (the server decides whether the document comes)", async () => {
    const seen: Array<{ url: string; init: RequestInit }> = [];
    const api = makeApi(makeRequester({ fetchImpl: fetchStub(200, { user: "ann", can_write: true, mode: "proxy", signin_url: "" }, seen) }));
    await api.whoami();
    await api.getTwinConfig("my-lab");
    expect(seen[0]?.url).toBe("/api/whoami");
    expect(seen[1]?.url).toBe("/api/twin-configs/my-lab");
    expect((seen[1]?.init.headers as Record<string, string>).Authorization).toBeUndefined();
  });
  it("types a 401 as sign-in and a 403 as read-only", async () => {
    const e401 = await makeRequester({ fetchImpl: fetchStub(401, { detail: "sign in required: no identity header from a trusted proxy" }, []) })("POST", "/api/x").catch((e: unknown) => e);
    expect((e401 as ApiError).kind).toBe("unauthorized");
    const e403 = await makeRequester({ fetchImpl: fetchStub(403, { detail: "forbidden" }, []) })("POST", "/api/x").catch((e: unknown) => e);
    expect((e403 as ApiError).kind).toBe("forbidden");
  });
  it("types a refusal by status and keeps the detail", async () => {
    const request = makeRequester({ fetchImpl: fetchStub(409, { error: "already exists", detail: { runs: ["disc-1"] } }, []) });
    const err = await request("DELETE", "/api/discovery/sources/x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).kind).toBe("conflict");
    expect((err as ApiError).message).toBe("already exists");
    expect((err as ApiError).detail).toEqual({ runs: ["disc-1"] });
  });
  it("names a route the running server does not serve on a 405", async () => {
    const request = makeRequester({ fetchImpl: fetchStub(405, { detail: "Method Not Allowed" }, []) });
    const err = await request("POST", "/api/twin-configs", { body: { id: "x", files: {} } }).catch((e: unknown) => e);
    expect((err as ApiError).status).toBe(405);
    expect((err as ApiError).message).toBe("the running API does not serve POST /api/twin-configs (Method Not Allowed): it predates this console build; restart `axrange serve` from this checkout");
  });
  it("treats 204 as done", async () => {
    const request = makeRequester({ fetchImpl: fetchStub(204, undefined, []) });
    await expect(request("DELETE", "/api/discovery/sources/x")).resolves.toBeUndefined();
  });
});
