import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import ts from "typescript";
async function load(name) {
 const source = await readFile(new URL(`../src/lib/${name}.ts`, import.meta.url), "utf8");
 const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
 return import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
}
const { performanceSummary, scoreText } = await load("football-summary");
const { displayStatus, apiErrorMessage } = await load("turkish");
test("no history and pending-only history never produce a success percentage", () => {
 assert.equal(performanceSummary([], "2026-11-20").rate, null);
 assert.equal(performanceSummary([{date:"2026-11-20",wins:0,losses:0,predictions:8,hit_rate:0.99}], "2026-11-20").rate,null);
});
test("rate uses settled counts, weighted across days, and excludes old or future rows", () => {
 const rows=[{date:"2026-11-20",wins:1,losses:0},{date:"2026-11-19",wins:1,losses:8},{date:"2026-10-21",wins:999,losses:0},{date:"2026-11-21",wins:999,losses:0}];
 const summary=performanceSummary(rows,"2026-11-20");
 assert.equal(summary.rate,0.2); assert.equal(summary.settled,10);
});
test("malformed results cannot create fabricated percentages", () => {
 assert.equal(performanceSummary([{date:"2026-11-20",wins:-1,losses:4},{date:"2026-11-19",wins:null,losses:0}],"2026-11-20").rate,null);
});
test("missing scores remain empty while a real goalless draw is preserved", () => {
 assert.equal(scoreText(),null); assert.equal(scoreText({home_score:null,away_score:null}),null);
 assert.equal(scoreText({home_score:0,away_score:0}),"0 – 0");
 assert.equal(scoreText({home_score:2,away_score:1}),"2 – 1");
 assert.equal(scoreText({home_score:-1,away_score:0}),null);
});
test("status and error messages are Turkish instead of raw server codes", () => {
 assert.equal(displayStatus("ANALYZED"),"Analiz tamamlandı");
 assert.equal(displayStatus("NO BET"),"İşlem önerisi yok");
 assert.equal(displayStatus("FUTURE_UNKNOWN_CODE"),"Durum bilgisi mevcut değil");
 assert.equal(apiErrorMessage("DATABASE_URL_INVALID"),"Veriye ulaşılamadı. Lütfen daha sonra yeniden deneyin.");
});
