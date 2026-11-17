const frontendOrigin = process.env.FRONTEND_ORIGIN?.trim();
const backendOrigin = process.env.BACKEND_ORIGIN?.trim();

if (!frontendOrigin || !backendOrigin) {
  console.error("NOT_TESTED: Set FRONTEND_ORIGIN and BACKEND_ORIGIN to deployed service URLs.");
  process.exit(2);
}

async function getJson(url, label) {
  let response;
  try {
    response = await fetch(url, { signal: AbortSignal.timeout(10_000) });
  } catch {
    throw new Error(`${label} request failed before receiving an HTTP response.`);
  }
  if (!response.ok) throw new Error(`${label} returned HTTP ${response.status}.`);
  try {
    return await response.json();
  } catch {
    throw new Error(`${label} did not return JSON.`);
  }
}

try {
  const backend = await getJson(new URL("/health/live", backendOrigin), "Backend liveness");
  if (backend.status !== "ok") throw new Error("Backend liveness payload is invalid.");

  const proxiedHealth = await getJson(new URL("/api/v1/health", frontendOrigin), "Frontend API rewrite");
  if (proxiedHealth.database !== "CONNECTED" || proxiedHealth.status !== "ok") {
    throw new Error("Frontend reached the API, but database readiness is not healthy.");
  }
  console.log("PASS: backend liveness and frontend-to-backend API rewrite are responding.");
} catch (error) {
  console.error(`FAIL: ${error.message}`);
  process.exit(1);
}
