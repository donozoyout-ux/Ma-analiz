import type { NextConfig } from "next";
const configuredApi = process.env.API_INTERNAL_URL || "http://localhost:8000";
const apiOrigin = configuredApi.includes("://") ? configuredApi : `http://${configuredApi}`;
const nextConfig: NextConfig = {
  reactStrictMode: true,
  outputFileTracingRoot: process.cwd(),
  output: "standalone",
  async rewrites() { return [{ source: "/api/v1/:path*", destination: `${apiOrigin}/api/v1/:path*` }]; },
};
export default nextConfig;
