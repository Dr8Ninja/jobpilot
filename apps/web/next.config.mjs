/** @type {import('next').NextConfig} */
const nextConfig = {
  // No `rewrites()` here any more. `app/api/[...path]/route.ts` proxies to
  // FastAPI instead, because a rewrite cannot attach the bearer token the API
  // expects once auth is switched on — and that token must stay on the server,
  // never in the browser.
};

export default nextConfig;
