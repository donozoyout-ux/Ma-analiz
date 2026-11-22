import assert from "node:assert/strict";
import { test } from "node:test";
import { assessLiveReadOnlyEvidence as assess } from "./stage7i3-readonly-contract.mjs";

const id="123e4567-e89b-42d3-a456-426614174000";
const aid="123e4567-e89b-42d3-a456-426614174001";
const when="2026-11-22T11:00:00+00:00";
const item={id, home_team:"Ev", away_team:"Deplasman",start_time:when,storage:"POSTGRESQL",result:null};
const history={...item,id:aid,storage:"R2",archive_verified_at:when};
function evidence(){
  return {
    root:{statusCode:200},live:{statusCode:200,body:{status:"ok"}},
    ready:{statusCode:200,body:{status:"ok",scope:"web_api_transport",dependencies:"not_checked"}},
    health:{statusCode:200,body:{status:"ok",database:"CONNECTED",database_schema:"READY",r2:"CONNECTED"}},
    list:{statusCode:200,body:{items:[item],as_of:when,source:"all",history_status:"NOT_READ",warnings:[]}},
    detail:{statusCode:200,body:{...item}},archive:{statusCode:200,body:{...history}},
    analysis_verified:true
  };
}
test("fully supplied fixture is advisory only and never authorizes DELETE",()=>{
  const r=assess(evidence());
  assert.equal(r.status,"READ_ONLY_DOMAIN_EVIDENCE_VERIFIED");
  assert.deepEqual(r.blockers,[]);
  assert.equal(r.SAFE_TO_DELETE,"NO");assert.equal(r.LIVE_DELETE,"NOT_RUN");
});
const cases=[
  ["missing evidence",s=>delete s.root,"FRONTEND_NOT_VERIFIED"],
  ["liveness alone",s=>s.health={statusCode:503},"STORAGE_DEPENDENCIES_NOT_VERIFIED"],
  ["wrong schema",s=>s.health.body.database_schema="INCOMPLETE","STORAGE_DEPENDENCIES_NOT_VERIFIED"],
  ["transport partial",s=>s.ready.body.dependencies="connected","TRANSPORT_NOT_VERIFIED"],
  ["empty list",s=>s.list.body.items=[],"MATCH_DATA_ABSENT"],
  ["partial history",s=>s.list.body.history_status="PARTIAL","MATCH_LIST_NOT_VERIFIED"],
  ["untrusted score",s=>s.list.body.items[0]={...item,result:{status:"FINAL",home_score:-1,away_score:0,verified_at:when}},"MATCH_LIST_NOT_VERIFIED"],
  ["missing detail",s=>delete s.detail,"MATCH_DETAIL_NOT_VERIFIED"],
  ["detail mismatch",s=>s.detail.body.id=aid,"MATCH_DETAIL_NOT_VERIFIED"],
  ["no R2 detail",s=>s.archive.body.storage="POSTGRESQL","R2_HISTORY_NOT_VERIFIED"],
  ["archived timestamp absent",s=>delete s.archive.body.archive_verified_at,"R2_HISTORY_NOT_VERIFIED"],
  ["configured AI not proof",s=>s.analysis_verified=false,"ANALYSIS_NOT_VERIFIED"],
  ["auth errors not success",s=>s.list={statusCode:401},"MATCH_LIST_NOT_VERIFIED"]
];
for(const [name,change,expected] of cases){
 test(name+" fails closed",()=>{const s=evidence();change(s);const r=assess(s);
 assert.equal(r.status,"NOT_VERIFIED");assert.ok(r.blockers.includes(expected));assert.equal(r.SAFE_TO_DELETE,"NO")});
}
test("missing data is not healthy",()=>{
  const r=assess(null);assert.equal(r.status,"NOT_VERIFIED");assert.deepEqual(r.blockers,["EVIDENCE_MISSING"]);
});
