import type { NextConfig } from "next";
const isProduction = process.env.NODE_ENV === "production";
const configuredApi = process.env.API_INTERNAL_URL?.trim();
const apiTarget = configuredApi || "http://127.0.0.1:8000";
const apiOrigin = apiTarget.includes("://") ? apiTarget : `http://${apiTarget}`;
const parsedApiOrigin = new URL(apiOrigin);
if (isProduction && configuredApi && !["http:", "https:"].includes(parsedApiOrigin.protocol)) {
  throw new Error("API_INTERNAL_URL must use HTTP or HTTPS.");
}
const nextConfig: NextConfig = {
  reactStrictMode: true,
  outputFileTracingRoot: process.cwd(),
  output: "standalone",
  async rewrites() {
    return [
      { source: "/api/v1/:path*", destination: `${apiOrigin}/api/v1/:path*` },
      { source: "/health/live", destination: `${apiOrigin}/health/live` },
    ];
  },
};
export default nextConfig;
