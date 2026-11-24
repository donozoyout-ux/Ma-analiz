import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import http from "node:http";
import { test } from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/lib/service-readiness.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { serviceReadiness } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);

test("readiness checks the colocated API with a bounded uncached GET", async () => {
  const result = await serviceReadiness("8001", async (url, options) => {
    assert.equal(url, "http://127.0.0.1:8001/health/live");
    assert.equal(options.cache, "no-store"); assert.equal(options.redirect, "error");
    assert.ok(options.signal instanceof AbortSignal);
    return Response.json({ status: "ok", private_detail: "must-not-escape" });
  });
  assert.equal(result.status, "ok"); assert.equal(result.dependencies, "not_checked");
  assert.equal(result.scope, "web_api_transport"); assert.ok(!JSON.stringify(result).includes("private_detail"));
});

for (const response of [new Response("", { status: 503 }), new Response("redirect", { status: 302 }),
  new Response("not-json"), Response.json({ status: "degraded" })]) {
  test(`invalid backend response ${response.status} is not ready`, async () => {
    assert.equal((await serviceReadiness("8000", async () => response)).status, "unavailable");
  });
}
test("invalid port and transport failure leak no endpoint or exception", async () => {
  const invalid = await serviceReadiness("secret", () => { throw new Error("must not run"); });
  const failed = await serviceReadiness("8000", () => { throw new Error("postgresql://private:password@secret"); });
  assert.deepEqual(invalid, failed); assert.ok(!JSON.stringify(failed).includes("password"));
});

test("a hung API times out instead of leaving readiness pending", async () => {
  const server = http.createServer(() => {});
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  try {
    const start = Date.now();
    const result = await serviceReadiness(String(server.address().port));
    assert.equal(result.status, "unavailable");
    assert.ok(Date.now() - start < 4000);
  } finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
});
