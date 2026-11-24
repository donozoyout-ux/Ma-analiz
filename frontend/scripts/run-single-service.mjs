#!/usr/bin/env node
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const backendRoot = path.join(repoRoot, "backend");
const frontendRoot = path.join(repoRoot, "frontend");

function readPort(name, value) {
  const port = Number(value);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`${name} must be an integer TCP port.`);
  }
  return port;
}

const renderPort = readPort("PORT", process.env.PORT);
const backendPort = readPort("BACKEND_PORT", process.env.BACKEND_PORT || "8000");
if (renderPort === backendPort) {
  throw new Error("PORT and BACKEND_PORT must be different.");
}

const pythonCommand = process.env.PYTHON_EXECUTABLE || (process.platform === "win32" ? "python" : "python3");
const runtimeEnv = { ...process.env, NODE_ENV: "production" };
const children = [];
const shutdownGraceMs = 25_000;
let shutdownPromise;
let isShuttingDown = false;

function childExited(child) {
  return child.exitCode !== null || child.signalCode !== null;
}

function waitForChild(child) {
  if (childExited(child)) return Promise.resolve();
  return new Promise((resolve) => child.once("close", resolve));
}

function shutdown(exitCode = 0, signal = "SIGTERM") {
  if (shutdownPromise) return shutdownPromise;
  isShuttingDown = true;
  if (exitCode !== 0) process.exitCode = exitCode;

  shutdownPromise = (async () => {
    for (const child of children) {
      if (!childExited(child)) child.kill(signal);
    }

    let graceTimer;
    const stoppedGracefully = await Promise.race([
      Promise.all(children.map(waitForChild)).then(() => true),
      new Promise((resolve) => {
        graceTimer = setTimeout(() => resolve(false), shutdownGraceMs);
      }),
    ]);
    clearTimeout(graceTimer);

    if (!stoppedGracefully) {
      for (const child of children) {
        if (!childExited(child)) child.kill("SIGKILL");
      }
      await Promise.all(children.map(waitForChild));
    }
  })();
  return shutdownPromise;
}

function startChild(name, command, args, cwd, env) {
  const child = spawn(command, args, { cwd, env, stdio: "inherit", windowsHide: true });
  children.push(child);
  if (child.pid) console.log(`${name} process started (pid=${child.pid}).`);
  child.once("error", () => {
    console.error(`${name} process could not be started.`);
    void shutdown(1);
  });
  child.once("exit", (code, signal) => {
    if (isShuttingDown) return;
    console.error(`${name} process exited unexpectedly (code=${code ?? "none"}, signal=${signal ?? "none"}).`);
    // Preserve a real non-zero child status for Render; an unexpected clean
    // exit or signal termination is still a service failure.
    void shutdown(code !== null && code !== 0 ? code : 1);
  });
  return child;
}

async function waitForBackend(child) {
  // Leave room for a cold, CPU-limited instance; this remains a bounded wait.
  const timeoutMs = Number(process.env.BACKEND_STARTUP_TIMEOUT_MS || "90000");
  const deadline = Date.now() + (Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs : 90_000);
  const healthUrl = `http://127.0.0.1:${backendPort}/health/live`;

  while (Date.now() < deadline) {
    if (isShuttingDown || childExited(child)) {
      throw new Error("Backend exited before becoming ready.");
    }
    try {
      const response = await fetch(healthUrl, { signal: AbortSignal.timeout(1000) });
      if (response.ok) {
        const body = await response.json();
        if (body.status === "ok") return;
      }
    } catch {
      // The API is expected to refuse connections until Uvicorn is ready.
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error("Backend did not become ready before the startup timeout.");
}

async function main() {
  const backend = startChild(
    "FastAPI",
    pythonCommand,
    ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(backendPort)],
    backendRoot,
    runtimeEnv,
  );

  try {
    await waitForBackend(backend);
    if (isShuttingDown) return;

    startChild(
      "Next.js",
      process.execPath,
      [path.join(frontendRoot, ".next", "standalone", "server.js")],
      frontendRoot,
      { ...runtimeEnv, HOSTNAME: "0.0.0.0", PORT: String(renderPort) },
    );
  } catch (error) {
    console.error(`Single-service startup failed: ${error.message}`);
    await shutdown(1);
  }
}

process.on("SIGTERM", () => {
  console.log("Single-service received SIGTERM; stopping both children.");
  void shutdown(0, "SIGTERM");
});
process.on("SIGINT", () => {
  console.log("Single-service received SIGINT; stopping both children.");
  void shutdown(0, "SIGINT");
});
void main();
