/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    // Proxy to FastAPI so the browser talks to one origin.
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.JOBPILOT_API_URL ?? "http://127.0.0.1:8000"}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
