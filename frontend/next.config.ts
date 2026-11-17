import type { NextConfig } from "next";
const isProduction = process.env.NODE_ENV === "production";
const configuredApi = process.env.API_INTERNAL_URL?.trim();
if (isProduction && !configuredApi) {
  throw new Error("API_INTERNAL_URL must be set for production builds.");
}
const apiTarget = configuredApi || "http://localhost:8000";
const apiOrigin = apiTarget.includes("://") ? apiTarget : `http://${apiTarget}`;
const apiHostname = new URL(apiOrigin).hostname.toLowerCase();
if (isProduction && ["localhost", "127.0.0.1", "::1", "0.0.0.0"].includes(apiHostname)) {
  throw new Error("API_INTERNAL_URL cannot target a loopback address in production.");
}
const nextConfig: NextConfig = {
  reactStrictMode: true,
  outputFileTracingRoot: process.cwd(),
  output: "standalone",
  async rewrites() { return [{ source: "/api/v1/:path*", destination: `${apiOrigin}/api/v1/:path*` }]; },
};
export default nextConfig;
