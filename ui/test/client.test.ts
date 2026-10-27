import { describe, expect, it } from "vitest";

import { ApiError, makeRequester, messageFromBody, setWriteToken } from "../src/api/client";

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
  it("sends the token on writes only, and omits the header when unset", async () => {
    const seen: Array<{ url: string; init: RequestInit }> = [];
    const request = makeRequester({ fetchImpl: fetchStub(200, { ok: true }, seen) });
    setWriteToken(null);
    await request("GET", "/api/discovery/sources");
    await request("POST", "/api/discovery/sources", { body: { a: 1 }, auth: true });
    expect((seen[0]?.init.headers as Record<string, string>).Authorization).toBeUndefined();
    expect((seen[1]?.init.headers as Record<string, string>).Authorization).toBeUndefined();
    setWriteToken("T");
    await request("GET", "/api/discovery/sources");
    await request("POST", "/api/discovery/sources", { body: { a: 1 }, auth: true });
    expect((seen[2]?.init.headers as Record<string, string>).Authorization).toBeUndefined();
    expect((seen[3]?.init.headers as Record<string, string>).Authorization).toBe("Bearer T");
    expect(seen[3]?.init.body).toBe('{"a":1}');
    setWriteToken(null);
  });
  it("types a refusal by status and keeps the detail", async () => {
    const request = makeRequester({ fetchImpl: fetchStub(409, { error: "already exists", detail: { runs: ["disc-1"] } }, []) });
    const err = await request("DELETE", "/api/discovery/sources/x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).kind).toBe("conflict");
    expect((err as ApiError).message).toBe("already exists");
    expect((err as ApiError).detail).toEqual({ runs: ["disc-1"] });
  });
  it("treats 204 as done", async () => {
    const request = makeRequester({ fetchImpl: fetchStub(204, undefined, []) });
    await expect(request("DELETE", "/api/discovery/sources/x", { auth: true })).resolves.toBeUndefined();
  });
});
