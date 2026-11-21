import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import ts from "typescript";
const source=await readFile(new URL("../src/lib/matches.ts",import.meta.url),"utf8");
const js=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext}}).outputText;
const {formatMatchTime,verifiedScoreText,matchHref,matchQuery}=await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
test("Turkey time is explicit, including UTC date rollover",()=>{
 assert.match(formatMatchTime("2026-11-20T22:30:00Z"),/21.*Kas.*2026.*01:30/);
 assert.match(formatMatchTime("2026-11-20T19:30:00+00:00"),/22:30/);
 assert.equal(formatMatchTime("2026-11-20T19:30:00"),"Zaman doğrulanmadı");
 assert.equal(formatMatchTime(null),"Zaman doğrulanmadı");
});
test("score requires verified FINAL and real nonnegative integers",()=>{
 const verified={home_score:1,away_score:1,status:"FINAL",verified_at:"2026-11-20T22:08:00Z"};
 assert.equal(verifiedScoreText(verified),"1 – 1");
 for(const patch of [{verified_at:null},{status:"IN_PROGRESS"},{home_score:null},{away_score:-1},{home_score:1.5},{verified_at:"2026-11-20T22:08:00"}])
  assert.equal(verifiedScoreText({...verified,...patch}),null);
 assert.equal(verifiedScoreText(null),null);
});
test("historical detail retains canonical UUID and explicit archive scope",()=>{
 assert.equal(matchHref({id:"canonical",storage:"R2"}),"/matches/canonical?source=history");
 assert.equal(matchHref({id:"canonical",storage:"POSTGRESQL"}),"/matches/canonical");
});
test("all filters and keyset cursor are encoded, never a write endpoint",()=>{
 const query=matchQuery({source:"history",day:"2026-11-20",league:"de.bl1",team:"  Bayern München  ",status:"finished"},"canonical");
 const parsed=new URLSearchParams(query.split("?")[1]);
 assert.equal(parsed.get("team"),"Bayern München");assert.equal(parsed.get("day"),"2026-11-20");
 assert.equal(parsed.get("league"),"de.bl1");assert.equal(parsed.get("status"),"finished");
 assert.equal(parsed.get("source"),"history");assert.equal(parsed.get("cursor"),"canonical");assert.equal(parsed.get("limit"),"12");
});
