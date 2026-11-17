import type { NextConfig } from "next";
const isProduction = process.env.NODE_ENV === "production";
const singleServiceMode = process.env.SINGLE_SERVICE_MODE === "true";
const configuredApi = process.env.API_INTERNAL_URL?.trim();
if (isProduction && !configuredApi) {
  throw new Error("API_INTERNAL_URL must be set for production builds.");
}
const apiTarget = configuredApi || "http://localhost:8000";
const apiOrigin = apiTarget.includes("://") ? apiTarget : `http://${apiTarget}`;
const parsedApiOrigin = new URL(apiOrigin);
const apiHostname = parsedApiOrigin.hostname.toLowerCase();
const loopbackHost = ["localhost", "127.0.0.1", "::1"].includes(apiHostname);
if (isProduction && loopbackHost && !singleServiceMode) {
  throw new Error("API_INTERNAL_URL cannot target a loopback address in production.");
}
if (isProduction && singleServiceMode && !loopbackHost) {
  throw new Error("SINGLE_SERVICE_MODE requires API_INTERNAL_URL to target loopback.");
}
if (isProduction && !["http:", "https:"].includes(parsedApiOrigin.protocol)) {
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
