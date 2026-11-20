import { spawn } from "node:child_process";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = path.resolve(frontendRoot, "..");
const supervisorPath = path.join(frontendRoot, "scripts", "run-single-service.mjs");
const backendPort = 8000;
const shutdownSignal = process.platform === "win32" ? "SIGINT" : "SIGTERM";

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close((error) => error ? reject(error) : resolve(port));
    });
  });
}

function isPortFree(port) {
  return new Promise((resolve) => {
    const socket = net.connect({ host: "127.0.0.1", port });
    socket.once("connect", () => { socket.destroy(); resolve(false); });
    socket.once("error", () => resolve(true));
  });
}

async function waitForResponse(url, child, output, timeoutMs = 45_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`Single-service supervisor exited with ${child.exitCode}.\n${output()}`);
    }
    try {
      return await fetch(url, { signal: AbortSignal.timeout(1500) });
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }
  throw new Error(`Timed out waiting for ${url}.\n${output()}`);
}

async function expectOffline(url) {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(800) });
    throw new Error(`${url} remained reachable with HTTP ${response.status} after shutdown.`);
  } catch (error) {
    if (error.message.includes("remained reachable")) throw error;
  }
}

if (!(await isPortFree(backendPort))) {
  console.error(`NOT_TESTED: Internal backend port ${backendPort} is already in use.`);
  process.exit(2);
}

const renderPort = await freePort();
const safeEnv = { ...process.env };
for (const name of Object.keys(safeEnv)) {
  if (/(?:API_KEY|ACCESS_KEY|SECRET|PASSWORD|TOKEN|CREDENTIAL|DATABASE_URL)/i.test(name)) safeEnv[name] = "";
}
delete safeEnv.API_INTERNAL_URL;
delete safeEnv.SINGLE_SERVICE_MODE;
Object.assign(safeEnv, {
  APP_ENV: "production",
  SYSTEM_DIAGNOSTICS_TOKEN: "local-test-only-diagnostics-token-32-characters",
  CORS_ORIGINS: "",
  DATABASE_URL: "",
  MATCH_PROVIDER: "",
  PROVIDER_NAME: "",
  PROVIDER_BASE_URL: "",
  PROVIDER_API_KEY: "",
  PROVIDER_API_TOKEN: "",
  SPORTMONKS_API_TOKEN: "",
  ENABLED_LEAGUES: "",
  ENABLED_SEASONS: "",
  R2_ENDPOINT: "",
  R2_ACCESS_KEY_ID: "",
  R2_SECRET_ACCESS_KEY: "",
  R2_BUCKET: "",
  AI_PROVIDER: "",
  AI_API_KEY: "",
  GROQ_API_KEY: "",
  GROQ_MODEL: "",
  TELEGRAM_BOT_TOKEN: "",
  TELEGRAM_CHAT_ID: "",
  BACKEND_PORT: String(backendPort),
  PORT: String(renderPort),
  NODE_ENV: "production",
  NEXT_TELEMETRY_DISABLED: "1",
});

const python = process.env.PYTHON_EXECUTABLE || (process.platform === "win32" ? "python" : "python3");
safeEnv.PYTHON_EXECUTABLE = python;
const child = spawn(process.execPath, [supervisorPath], {
  cwd: repoRoot,
  env: safeEnv,
  stdio: ["ignore", "pipe", "pipe"],
  windowsHide: true,
});
let logs = "";
const appendLog = (chunk) => { logs = (logs + chunk.toString()).slice(-12_000); };
child.stdout.on("data", appendLog);
child.stderr.on("data", appendLog);

let failed = false;
try {
  const origin = `http://127.0.0.1:${renderPort}`;
  const home = await waitForResponse(new URL("/", origin), child, () => logs);
  if (!home.ok) throw new Error(`Next.js root returned HTTP ${home.status}.`);
  const html = await home.text();
  if (!html.toLowerCase().includes("<!doctype html")) throw new Error("Next.js root did not return an HTML document.");
  for (const text of ["Futbol Analiz Merkezi", "Günün Maçları", "Doğrulanmış tahmin isabeti", "AI Tahminleri", "Tahmin performansı"]) {
    if (!html.includes(text)) throw new Error(`Home is missing football content: ${text}`);
  }
  for (const text of ["Altyapı sağlığı", "Aktivite ve veri büyümesi", "System Health", "PostgreSQL kullanımı"]) {
    if (html.includes(text)) throw new Error(`Technical panel remains on the football home: ${text}`);
  }
  const staticAsset = [...html.matchAll(/(?:src|href)="([^"]*\/_next\/static\/[^"]+)"/g)]
    .map((match) => match[1])[0];
  if (!staticAsset) throw new Error("Next.js root did not reference a standalone static asset.");
  const staticResponse = await fetch(new URL(staticAsset, origin), { signal: AbortSignal.timeout(5000) });
  if (!staticResponse.ok) throw new Error(`Next.js static asset returned HTTP ${staticResponse.status}.`);

  const liveResponse = await fetch(new URL("/health/live", origin), { signal: AbortSignal.timeout(5000) });
  if (!liveResponse.ok || (await liveResponse.json()).status !== "ok") {
    throw new Error("The public liveness route did not reach FastAPI successfully.");
  }

  const apiResponse = await fetch(new URL("/api/v1/health", origin), { signal: AbortSignal.timeout(10_000) });
  if (!apiResponse.ok) throw new Error(`The API rewrite returned HTTP ${apiResponse.status}.`);
  const diagnostics = await apiResponse.json();
  if (!("database" in diagnostics) || !("provider" in diagnostics) || !diagnostics.services) {
    throw new Error("The API rewrite did not return the FastAPI diagnostics schema.");
  }
  if (diagnostics.database !== "NOT_CONFIGURED") {
    throw new Error("Smoke test did not isolate the database configuration as intended.");
  }
  const systemResponse = await fetch(new URL("/system", origin));
  const systemHtml = await systemResponse.text();
  if (!systemResponse.ok || /type=["']password["']|diagnostics-token|SYSTEM_DIAGNOSTICS_TOKEN/.test(systemHtml)) {
    throw new Error("System page must be public and contain no diagnostics password form.");
  }
  const adminResponse = await fetch(new URL("/api/v1/system/connections", origin), {method:"POST"});
  if (adminResponse.status !== 401) throw new Error("Detailed diagnostics must require authorization.");
  for (const key of ["inventory", "current_revisions", "expected_revisions", "target_fingerprint", "error_family", "error_category"]) {
    if (JSON.stringify(diagnostics).includes(`"${key}"`)) throw new Error("Public health leaked diagnostic details.");
  }
  // Exercise real production route rendering and permanent legacy redirects.
  for (const [source, destination] of Object.entries({
    odds: "/matches?tab=odds", coupons: "/predictions?tab=coupons", performance: "/analysis",
    lessons: "/analysis?tab=lessons", storage: "/archive?tab=storage", system: "/management", settings: "/management?tab=settings",
  })) {
    const response = await fetch(new URL(`/${source}`, origin), { redirect: "manual" });
    if (response.status !== 308 || response.headers.get("location") !== destination) {
      throw new Error(`Legacy /${source} did not redirect permanently to ${destination}.`);
    }
  }
  for (const [route, heading] of [
    ["/matches", "Günün maçları"], ["/matches?tab=odds", "Oran analizi"],
    ["/matches/test-only-id", "Maç ve oran geçmişi"],
    ["/predictions", "Yapay zekâ tahminleri"], ["/predictions?tab=coupons", "Kuponlar"],
    ["/analysis", "Tahmin performansı"], ["/analysis?tab=lessons", "Eğitim merkezi"],
    ["/archive", "Geçmiş maç arşivi"], ["/archive?tab=storage", "Veri deposu"],
    ["/management", "Sistem durumu"], ["/management?tab=settings", "Ayarlar"],
    ["/analysis?tab=unknown", "Tahmin performansı"],
  ]) {
    const response = await fetch(new URL(route, origin));
    const content = await response.text();
    if (!response.ok || !content.includes(`>${heading}</h1>`) || !content.includes('class="section-tabs"')) {
      throw new Error(`Section ${route} did not render its original feature and tabs.`);
    }
    const navigation = content.split('aria-label="Ana menü"')[1]?.split("</nav>")[0];
    if ((navigation?.match(/<a /g) ?? []).length !== 5) throw new Error("Primary menu must contain exactly five destinations.");
  }
  console.log("PASS: Seven legacy redirects, twelve section views, and five primary navigation entries.");
  const shutdownNote = process.platform === "win32"
    ? "Windows signal-handler semantics NOT_TESTED"
    : "SIGTERM graceful shutdown verified";
  console.log(`PASS: Next.js standalone server on PORT=${renderPort}; /health/live and /api/v1/health reached FastAPI through rewrites; ${shutdownNote}.`);
} catch (error) {
  failed = true;
  console.error(`FAIL: ${error.message}`);
} finally {
  if (child.exitCode === null) {
    child.kill(shutdownSignal);
    await new Promise((resolve) => {
      const timer = setTimeout(resolve, 15_000);
      child.once("close", () => { clearTimeout(timer); resolve(); });
    });
  }
  if (child.exitCode === null && child.signalCode === null) {
    child.kill("SIGKILL");
    failed = true;
    console.error(`FAIL: single-service supervisor did not stop after ${shutdownSignal}.`);
  } else if (child.exitCode !== 0) {
    if (process.platform !== "win32" || child.signalCode !== shutdownSignal) {
      failed = true;
      console.error(`FAIL: supervisor exited with ${child.exitCode ?? `signal ${child.signalCode}`} after shutdown signal.`);
    }
  }
}

try {
  await expectOffline(`http://127.0.0.1:${renderPort}/`);
  await expectOffline(`http://127.0.0.1:${backendPort}/health/live`);
} catch (error) {
  failed = true;
  console.error(`FAIL: ${error.message}`);
}

if (failed) {
  if (logs) console.error("Sanitized service output:\n" + logs);
  process.exitCode = 1;
}
