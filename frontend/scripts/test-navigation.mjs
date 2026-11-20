import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import ts from "typescript";
const source = await readFile(new URL("../src/lib/navigation.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { sections, selectedTab, tabHref, legacyDestinations, activeSection } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
test("five primary destinations with management separate", () => {
 assert.equal(Object.keys(sections).filter(k=>k!=="management").length+1,5);
 assert.equal(sections.management.href,"/management");
});
test("detail routes retain the match section without prefix collisions", () => {
 assert.equal(activeSection("/matches/example"),"/matches");
 assert.equal(activeSection("/matches-unrelated"),undefined);
 assert.equal(activeSection("/"),"/");
});
test("tabs fall back safely for invalid, missing, or repeated query parameters", () => {
 for (const key of Object.keys(sections)) {
  assert.equal(selectedTab(key),sections[key].tabs[0].id);
  assert.equal(selectedTab(key,"invalid"),sections[key].tabs[0].id);
  assert.equal(selectedTab(key,["invalid"]),sections[key].tabs[0].id);
  for(const tab of sections[key].tabs) assert.equal(selectedTab(key,tab.id),tab.id);
 }
});
test("all legacy destinations resolve to valid section tabs", () => {
 assert.equal(Object.keys(legacyDestinations).length,7);
 for(const target of Object.values(legacyDestinations)) {
  const url=new URL(target,"http://localhost");
  const entry=Object.entries(sections).find(([,s])=>s.href===url.pathname);
  assert.ok(entry);
  const [key]=entry;
  assert.equal(tabHref(key,selectedTab(key,url.searchParams.get("tab")??undefined)),target);
 }
});
