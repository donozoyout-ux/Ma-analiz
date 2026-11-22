import assert from "node:assert/strict";
import {test} from "node:test";
import {collectReadOnlyEvidence,summarizeEvidence,ORIGIN} from "./stage7i4-live-get-probe.mjs";
import {assessLiveReadOnlyEvidence as assess} from "./stage7i3-readonly-contract.mjs";

const id="123e4567-e89b-42d3-a456-426614174000";
const older="123e4567-e89b-42d3-a456-426614174001";
const policy={name:"OpenLigaDB",licence:"ODbL-1.0",attribution:"SYNTHETIC OpenLigaDB fixture",licence_url:"https://www.openligadb.de/lizenz"};
const at="2026-11-22T14:40:00+00:00";
const match=(id,storage)=>({id,home_team:"Ev",away_team:"Dep",storage,start_time:at,status:"SCHEDULED",source:policy,result:null,
 ...(storage==="R2"?{archive_verified_at:at}:{})});
const responses={
 "/":{status:200,body:"<html>OK</html>"},
 "/health/live":{status:200,body:{status:"ok"}},
 "/health/ready":{status:200,body:{status:"ok",scope:"web_api_transport",dependencies:"not_checked"}},
 "/api/v1/health":{status:200,body:{status:"ok",database:"CONNECTED",database_schema:"READY",r2:"CONNECTED"}},
 "/api/v1/match-center?limit=1":{status:200,body:{items:[match(id,"POSTGRESQL")],as_of:at,source:"all",history_status:"NOT_READ",warnings:[]}},
 "/api/v1/match-center?source=history&limit=1":{status:200,body:{items:[match(older,"R2")],as_of:at,source:"history",history_status:"VERIFIED",warnings:[]}},
 ["/api/v1/match-center/"+id]:{status:200,body:match(id,"POSTGRESQL")},
 ["/api/v1/match-center/"+older+"?source=history"]:{status:200,body:match(older,"R2")}
};
function fakeFetch(log,patch={}){
 return async (url,opts)=>{
  log.push({url:String(url),opts});
  const key=url.pathname+url.search;
  const reply=Object.hasOwn(patch,key)?patch[key]:responses[key];
  if(!reply) throw Error("Unexpected request");
  return new Response(typeof reply.body === "string" ? reply.body : JSON.stringify(reply.body),{status:reply.status});
 };
}
test("GET-only and pinned HTTPS origin, no credentials, never claims analysis",async()=>{
 const calls=[];const snapshot=await collectReadOnlyEvidence(fakeFetch(calls));
 assert.equal(calls.length,8);
 for(const {url,opts} of calls){
  assert.ok(url.startsWith(ORIGIN+"/"));assert.equal(opts.method,"GET");
  assert.equal(opts.redirect,"error");assert.equal(opts.credentials,"omit");
  assert.ok(opts.signal instanceof AbortSignal);
 }
 assert.equal(snapshot.analysis_verified,false);
 const result=assess(snapshot);
 assert.equal(result.status,"NOT_VERIFIED");
 assert.deepEqual(result.blockers,["ANALYSIS_NOT_VERIFIED"]);
 assert.equal(result.SAFE_TO_DELETE,"NO");
 const summary=summarizeEvidence(snapshot);
 assert.equal(summary.list,200);
 assert.ok(!JSON.stringify(summary).includes(id));
 assert.ok(!JSON.stringify(summary).includes("Ev"));
});
test("empty lists never trigger arbitrary UUID requests",async()=>{
 const calls=[];const snapshot=await collectReadOnlyEvidence(fakeFetch(calls,{
 "/api/v1/match-center?limit=1":{status:200,body:{items:[],as_of:at,source:"all",warnings:[]}},
 "/api/v1/match-center?source=history&limit=1":{status:200,body:{items:[],as_of:at,source:"history",warnings:[]}}
 }));
 assert.equal(calls.length,6);
 assert.equal(snapshot.detail.statusCode,null);
 assert.equal(snapshot.archive.statusCode,null);
 assert.ok(assess(snapshot).blockers.includes("MATCH_DATA_ABSENT"));
});
test("503 is evidence failure, not success or private response leakage",async()=>{
 const calls=[];const snapshot=await collectReadOnlyEvidence(fakeFetch(calls,{
 "/api/v1/health":{status:503,body:{secret:"NEVER_LOG"}}
 }));
 assert.equal(snapshot.health.statusCode,503);
 assert.equal(snapshot.health.body,undefined);
 assert.ok(assess(snapshot).blockers.includes("STORAGE_DEPENDENCIES_NOT_VERIFIED"));
 assert.ok(!JSON.stringify(summarizeEvidence(snapshot)).includes("NEVER_LOG"));
});
test("network errors are normalized and never become success",async()=>{
 const snapshot=await collectReadOnlyEvidence(async()=>{throw Error("DATABASE_URL=secret")});
 assert.equal(snapshot.root.statusCode,null);
 assert.equal(snapshot.health.error,"NETWORK_OR_JSON_UNAVAILABLE");
 assert.ok(!JSON.stringify(summarizeEvidence(snapshot)).includes("secret"));
 assert.equal(assess(snapshot).status,"NOT_VERIFIED");
});
test("a detail request is rejected unless list returned a valid UUID",async()=>{
 const calls=[];await collectReadOnlyEvidence(fakeFetch(calls,{
 "/api/v1/match-center?limit=1":{status:200,body:{items:[{id:"https://evil.example"}],as_of:at,source:"all",warnings:[]}}
 }));
 assert.equal(calls.length,7);
 assert.ok(calls.every(x=>x.url.startsWith(ORIGIN)));
});
