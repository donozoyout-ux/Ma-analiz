import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import ts from "typescript";
const source = await readFile(new URL("../src/lib/matches.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { filterMatches, istanbulDay } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
// Synthetic fixtures are test-only and never reach the application.
const rows = [
 { id: "a", start_time: "2026-11-19T22:30:00Z", home_team: "İstanbul Test", away_team: "Test B", league_name: "Test Lig", status: "SCHEDULED" },
 { id: "b", start_time: "2026-11-19T18:00:00Z", home_team: "Test C", away_team: "Test D", league_name: "Diğer Lig", status: "FINISHED" }
];
const base = { query: "", league: "", status: "", date: "", archive: false };
test("Istanbul date includes UTC midnight rollover", () => { assert.equal(istanbulDay(rows[0].start_time), "2026-11-20"); assert.equal(istanbulDay("invalid"), ""); });
test("date filter never includes yesterday's matches", () => assert.deepEqual(filterMatches(rows, {...base, date:"2026-11-20"}).map(r=>r.id), ["a"]));
test("Turkish search and combined filters", () => { assert.equal(filterMatches(rows, {...base, query:"istanbul", league:"Test Lig", status:"SCHEDULED"}).length, 1); assert.equal(filterMatches(rows, {...base, query:"istanbul", status:"FINISHED"}).length, 0); });
test("archive excludes unfinished matches", () => assert.deepEqual(filterMatches(rows, {...base, archive:true}).map(r=>r.id), ["b"]));
test("empty response stays empty", () => assert.deepEqual(filterMatches([], base), []));
