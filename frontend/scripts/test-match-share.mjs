import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import ts from "typescript";
const source = await readFile(new URL("../src/lib/match-share.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { copyMatchLink, shareMatchLink } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
const data = { title: "Bayern – Köln | Maç Analiz", url: "https://example.test/matches/canonical?source=history&day=2026-11-20#result" };

test("copy preserves the complete current historical URL", async () => {
  let copied;
  assert.equal(await copyMatchLink({ clipboard: { writeText: async value => { copied = value; } } }, data.url), "copied");
  assert.equal(copied, data.url);
});
test("clipboard denial and unavailable clipboard return safe feedback", async () => {
  assert.equal(await copyMatchLink({}, data.url), "error");
  assert.equal(await copyMatchLink({ clipboard: { writeText: async () => { throw new DOMException("private", "NotAllowedError"); } } }, data.url), "error");
});
test("native share receives unchanged URL and title with browser context", async () => {
  const browser = { async share(value) { assert.equal(this, browser); assert.deepEqual(value, data); } };
  assert.equal(await shareMatchLink(browser, data), "shared");
});
test("unsupported share falls back to clipboard exactly once", async () => {
  const copied = [];
  assert.equal(await shareMatchLink({ clipboard: { writeText: async value => { copied.push(value); } } }, data), "copied");
  assert.deepEqual(copied, [data.url]);
});
test("canShare false falls back without opening native share", async () => {
  let copies = 0;
  assert.equal(await shareMatchLink({ canShare: () => false, share: async () => assert.fail("must not share"), clipboard: { writeText: async () => { copies++; } } }, data), "copied");
  assert.equal(copies, 1);
});
test("share cancellation clears previous feedback without copying", async () => {
  assert.equal(await shareMatchLink({ share: async () => { throw new DOMException("cancel", "AbortError"); }, clipboard: { writeText: async () => assert.fail("must not copy") } }, data), "idle");
});
test("share permission denial reports error without clipboard side effects", async () => {
  assert.equal(await shareMatchLink({ share: async () => { throw new DOMException("private", "NotAllowedError"); }, clipboard: { writeText: async () => assert.fail("must not copy") } }, data), "error");
});
test("failed fallback and capability check do not leak exceptions", async () => {
  assert.equal(await shareMatchLink({}, data), "error");
  assert.equal(await shareMatchLink({ share: async () => {}, canShare: () => { throw new Error("private"); } }, data), "error");
});
