/** Colocated web/API transport readiness, not database or archive readiness. */
export async function serviceReadiness(portValue: string, fetcher: typeof fetch = fetch) {
  const port = Number(portValue);
  let backend = "unavailable";
  if (Number.isInteger(port) && port > 0 && port <= 65535) {
    try {
      const response = await fetcher(`http://127.0.0.1:${port}/health/live`, {
        cache: "no-store", redirect: "error", signal: AbortSignal.timeout(2000),
      });
      if (response.ok && (await response.json()).status === "ok") backend = "ok";
    } catch {
      // Fixed public status only; never expose an internal URL or exception.
    }
  }
  return { status: backend === "ok" ? "ok" : "unavailable", scope: "web_api_transport",
    checks: { frontend: "ok", backend }, dependencies: "not_checked" };
}
