/**
 * Same-origin proxy to the FastAPI backend.
 *
 * The dashboard's action buttons run in the browser. When the API is secured,
 * something has to add the bearer token — and it must not be the browser, which
 * would put the credential in front of anyone who opens devtools. This runs on
 * the Next server, reads the token from the server environment, and forwards.
 *
 * It replaces the old `rewrites()` entry, which could not add a header.
 *
 * The logic lives here rather than in the route file so it can be imported by a
 * test: the route's own path contains `[...path]`, which a bundler reads as a
 * glob.
 */

/** Hop-by-hop and body-framing headers must not be copied onto a new request. */
const STRIPPED = new Set(["host", "connection", "content-length", "transfer-encoding"]);

export async function proxyToApi(request: Request, path: string[]): Promise<Response> {
  const base = process.env.JOBPILOT_API_URL ?? "http://127.0.0.1:8000";
  const url = new URL(request.url);
  const target = `${base}/api/${path.join("/")}${url.search}`;

  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!STRIPPED.has(key.toLowerCase())) headers.set(key, value);
  });
  const token = process.env.JOBPILOT_API_TOKEN;
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const method = request.method;
  const body = method === "GET" || method === "HEAD" ? undefined : await request.arrayBuffer();

  const response = await fetch(target, { method, headers, body, cache: "no-store" });

  // Status and body pass through untouched: the dashboard reads `detail` off a
  // 409 to explain why the whitelist gate blocked an approval, and a proxy that
  // flattened that would turn a precise refusal into "something went wrong".
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers: response.headers,
  });
}
