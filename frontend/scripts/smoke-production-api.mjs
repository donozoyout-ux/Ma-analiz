const serviceOrigin = process.env.SERVICE_ORIGIN?.trim();

if (!serviceOrigin) {
  console.error("NOT_TESTED: Set SERVICE_ORIGIN to the deployed single-service URL.");
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
  const frontend = await fetch(new URL("/", serviceOrigin), { signal: AbortSignal.timeout(10_000) });
  if (!frontend.ok) throw new Error(`Frontend returned HTTP ${frontend.status}.`);

  const liveness = await getJson(new URL("/health/live", serviceOrigin), "Proxied FastAPI liveness");
  if (liveness.status !== "ok") throw new Error("FastAPI liveness payload is invalid.");

  const diagnostics = await getJson(new URL("/api/v1/health", serviceOrigin), "Frontend-to-FastAPI API rewrite");
  if (!("database" in diagnostics) || !("provider" in diagnostics) || !diagnostics.services) {
    throw new Error("API diagnostics response does not match the FastAPI health schema.");
  }
  console.log(`PASS: frontend, liveness rewrite, and API rewrite respond; database=${diagnostics.database}.`);
} catch (error) {
  console.error(`FAIL: ${error.message}`);
  process.exit(1);
}
