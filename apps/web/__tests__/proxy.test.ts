import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The dashboard's action buttons run in the browser, which must never hold the
 * API token. They call same-origin `/api/...`; this route handler is what adds
 * the credential, server-side, on the way through.
 */

const ORIGINAL_ENV = { ...process.env };

beforeEach(() => {
  vi.resetModules();
});

afterEach(() => {
  process.env = { ...ORIGINAL_ENV };
  vi.restoreAllMocks();
});

function captureFetch() {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response('{"ok":true}', { status: 200, headers: { "Content-Type": "application/json" } }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("API proxy route", () => {
  it("forwards the path and query to the backend", async () => {
    const fetchMock = captureFetch();
    process.env.JOBPILOT_API_URL = "http://api.internal:8000";
    const { proxyToApi } = await import("@/lib/proxy");

    await proxyToApi(new Request("http://localhost:3000/api/queue?status=queued"), ["queue"]);

    expect(fetchMock.mock.calls[0][0]).toBe("http://api.internal:8000/api/queue?status=queued");
  });

  it("attaches the token from the server environment", async () => {
    const fetchMock = captureFetch();
    process.env.JOBPILOT_API_TOKEN = "s3cret-token";
    const { proxyToApi } = await import("@/lib/proxy");

    await proxyToApi(
      new Request("http://localhost:3000/api/queue/7/approve", { method: "POST" }),
      ["queue", "7", "approve"],
    );

    const init = fetchMock.mock.calls[0][1];
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer s3cret-token");
  });

  it("sends no Authorization header when no token is configured", async () => {
    const fetchMock = captureFetch();
    delete process.env.JOBPILOT_API_TOKEN;
    const { proxyToApi } = await import("@/lib/proxy");

    await proxyToApi(new Request("http://localhost:3000/api/health"), ["health"]);

    const init = fetchMock.mock.calls[0][1];
    expect(new Headers(init.headers).get("Authorization")).toBeNull();
  });

  it("passes the backend's status through instead of masking it", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response('{"detail":"gate failed"}', {
          status: 409,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const { proxyToApi } = await import("@/lib/proxy");

    const response = await proxyToApi(
      new Request("http://localhost:3000/api/queue/7/approve", { method: "POST" }),
      ["queue", "7", "approve"],
    );

    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({ detail: "gate failed" });
  });
});
