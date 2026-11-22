import assert from "node:assert/strict";
import {test} from "node:test";
import {createServer} from "node:http";
import {spawnSync} from "node:child_process";
import {fileURLToPath} from "node:url";
import {assessLiveReadOnlyEvidence as assess} from "./stage7i3-readonly-contract.mjs";
import {collectReadOnlyEvidence as collect,publicProbeReport,ORIGIN,MAX_RESPONSE_BYTES as MAX} from "./stage7i4-live-get-probe.mjs";
import {snapshot,fixtureFetch,routes,ids} from "./fixtures/stage7i6.mjs";
const listPath="/api/v1/match-center?limit=1";
function swap(factory,calls=[]) {
  const fallback=fixtureFetch(snapshot(),calls);
  return (url,opts)=>url.pathname+url.search===listPath ? factory(url,opts) : fallback(url,opts);
}
function rejected(value,blocker) {
  const result=assess({...value,analysis_verified:true}); // Independent synthetic oracle must not mask bad domain data.
  assert.equal(result.status,"NOT_VERIFIED");assert.ok(result.blockers.includes(blocker));assert.equal(result.SAFE_TO_DELETE,"NO");
}

test("local HTTP fixture traverses list UUID/detail and indexed history UUID/detail without production egress",async t=>{
  const map=routes();const received=[];
  const server=createServer((req,res)=>{received.push(req.url);assert.equal(req.method,"GET");const row=map.get(req.url);
    res.writeHead(row?.statusCode??404,{"content-type":"application/json"});res.end(JSON.stringify(row?.body??{}));});
  await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
  t.after(()=>new Promise(resolve=>{server.close(resolve);server.closeAllConnections();}));
  const local="http://127.0.0.1:"+server.address().port;const calls=[];
  const result=await collect(async(url,opts)=>{
    assert.equal(url.origin,ORIGIN);assert.equal(opts.method,"GET");assert.equal(opts.credentials,"omit");assert.equal(opts.redirect,"error");
    calls.push({url,opts});const response=await fetch(local+url.pathname+url.search,opts);
    return new Response(response.body,{status:response.status,headers:response.headers}); // Test-only pinned-to-loopback adapter.
  });
  assert.equal(received.length,8);assert.equal(new Set(calls.map(c=>c.opts.signal)).size,8);
  assert.ok(received.indexOf(listPath)<received.indexOf("/api/v1/match-center/"+ids[0]));
  assert.ok(received.includes("/api/v1/match-center/"+ids[1]+"?source=history"));
  assert.deepEqual(assess(result).blockers,["ANALYSIS_NOT_VERIFIED"]);
  assert.equal(result.analysis_verified,false);assert.equal(publicProbeReport(result).LIVE_MATCH_CENTER,"NOT_VERIFIED");
});

for(const slot of ["list","detail","history","archive"])for(const status of [401,404,503]) {
 test(`HTTP ${status} at ${slot} never proves the domain`,()=>{const s=snapshot();s[slot]={statusCode:status};
   assert.equal(assess(s).status,"NOT_VERIFIED");assert.equal(assess(s).SAFE_TO_DELETE,"NO");});
}
const mutations=[
 ["empty list",s=>s.list.body.items=[],"MATCH_DATA_ABSENT"],
 ["empty history",s=>s.history.body.items=[],"R2_HISTORY_NOT_VERIFIED"],
 ["partial history",s=>s.history.body.history_status="PARTIAL","R2_HISTORY_NOT_VERIFIED"],
 ["detail wrong UUID",s=>s.detail.body.id=ids[1],"MATCH_DETAIL_NOT_VERIFIED"],
 ["detail wrong team",s=>s.detail.body.home_team="UNRELATED","MATCH_DETAIL_NOT_VERIFIED"],
 ["detail wrong date",s=>s.detail.body.start_time="2026-11-22T10:00:00+00:00","MATCH_DETAIL_NOT_VERIFIED"],
 ["detail wrong score",s=>s.detail.body.result.home_score=3,"MATCH_DETAIL_NOT_VERIFIED"],
 ["detail different native source ID",s=>s.detail.body.source.native_id="UNRELATED","MATCH_DETAIL_NOT_VERIFIED"],
 ["detail different league",s=>s.detail.body.league_id="en.pl","MATCH_DETAIL_NOT_VERIFIED"],
 ["history wrong UUID",s=>s.archive.body.id=ids[0],"R2_HISTORY_NOT_VERIFIED"],
 ["history wrong team",s=>s.archive.body.away_team="UNRELATED","R2_HISTORY_NOT_VERIFIED"],
 ["history wrong date",s=>s.archive.body.start_time="2026-11-22T10:00:00+00:00","R2_HISTORY_NOT_VERIFIED"],
 ["history wrong score",s=>s.archive.body.result.away_score=9,"R2_HISTORY_NOT_VERIFIED"],
 ["history source missing",s=>delete s.archive.body.source,"R2_HISTORY_NOT_VERIFIED"],
 ["history attribution missing",s=>delete s.archive.body.source.attribution,"R2_HISTORY_NOT_VERIFIED"],
 ["history policy absent from list",s=>delete s.history.body.items[0].source,"R2_HISTORY_NOT_VERIFIED"],
 ["history unsupported licence",s=>s.archive.body.source.licence="UNKNOWN","R2_HISTORY_NOT_VERIFIED"],
 ["history licence URL mismatch",s=>s.archive.body.source.licence_url="https://untrusted.invalid","R2_HISTORY_NOT_VERIFIED"],
 ["future readback",s=>s.archive.body.archive_verified_at="2026-11-26T12:00:00+00:00","R2_HISTORY_NOT_VERIFIED"],
 ["readback before final verification",s=>s.archive.body.archive_verified_at="2026-11-23T11:00:00+00:00","R2_HISTORY_NOT_VERIFIED"],
 ["list finished score absent",s=>s.list.body.items[0].result=null,"MATCH_LIST_NOT_VERIFIED"],
 ["list score property absent",s=>delete s.list.body.items[0].result,"MATCH_LIST_NOT_VERIFIED"],
 ["detail finished score missing",s=>s.detail.body.result=null,"MATCH_DETAIL_NOT_VERIFIED"],
 ["history final verification absent",s=>delete s.archive.body.result.verified_at,"R2_HISTORY_NOT_VERIFIED"],
 ["history negative score",s=>s.archive.body.result.home_score=-1,"R2_HISTORY_NOT_VERIFIED"],
 ["history fractional score",s=>s.archive.body.result.away_score=1.5,"R2_HISTORY_NOT_VERIFIED"],
 ["history boolean score",s=>s.archive.body.result.away_score=true,"R2_HISTORY_NOT_VERIFIED"],
 ["history future final",s=>s.archive.body.result.verified_at="2026-11-26T12:00:00+00:00","R2_HISTORY_NOT_VERIFIED"],
 ["history final before kickoff",s=>s.archive.body.result.verified_at="2026-11-23T09:00:00+00:00","R2_HISTORY_NOT_VERIFIED"],
 ["impossible calendar date",s=>s.detail.body.start_time="2026-02-31T10:00:00+00:00","MATCH_DETAIL_NOT_VERIFIED"],
 ["naive date",s=>s.detail.body.start_time="2026-11-23T10:00:00","MATCH_DETAIL_NOT_VERIFIED"],
 ["unknown match status",s=>s.archive.body.status="UNVERIFIED","R2_HISTORY_NOT_VERIFIED"],
 ["response error despite status 200",s=>s.detail.error="RESPONSE_OVERSIZE","MATCH_DETAIL_NOT_VERIFIED"],
];
for(const [name,change,blocker] of mutations)test(name+" fails closed",()=>{const s=snapshot();change(s);rejected(s,blocker);});

test("pending result stays null and zero-zero FINAL is retained without invented scores",()=>{
 const s=snapshot();for(const row of [s.list.body.items[0],s.detail.body]){row.status="SCHEDULED";row.result=null;}
 assert.equal(assess(s).status,"READ_ONLY_DOMAIN_EVIDENCE_VERIFIED");
 for(const row of [s.history.body.items[0],s.archive.body])row.result={...row.result,home_score:0,away_score:0};
 assert.equal(assess(s).status,"READ_ONLY_DOMAIN_EVIDENCE_VERIFIED");
});

for(const id of ["https://private.invalid/TOKEN",ids[0]+"\n",ids[0]+"?token=PRIVATE","../private",{toString:()=>ids[0]},null,42]) {
 test("noncanonical identifier never reaches a detail URL "+typeof id,async()=>{
  const s=snapshot();s.list.body.items[0].id=id;const calls=[];const response=await collect(fixtureFetch(s,calls));
  assert.equal(response.detail.error,"NO_VERIFIED_MATCH_ID");assert.equal(calls.length,7);
  assert.ok(calls.every(c=>c.url.origin===ORIGIN));
 });
}

for(const header of [null,"1"])test("stream byte cap applies without trustworthy Content-Length "+header,async()=>{
 let cancelled=false,pulls=0;
 const body=new ReadableStream({pull(c){pulls++;c.enqueue(new Uint8Array(16384));},cancel(){cancelled=true;}});
 const response=await collect(swap(()=>new Response(body,{headers:header?{"content-length":header}:{}})));
 assert.equal(response.list.error,"RESPONSE_OVERSIZE");assert.ok(cancelled && pulls<25);rejected(response,"MATCH_LIST_NOT_VERIFIED");
});
test("UTF-8 bytes are bounded rather than JSON character count",async()=>{
 const bytes=new TextEncoder().encode(JSON.stringify({value:"ü".repeat(MAX/2)}));assert.ok(bytes.byteLength>MAX);
 const response=await collect(swap(()=>new Response(bytes,{headers:{"content-length":"1"}})));
 assert.equal(response.list.error,"RESPONSE_OVERSIZE");
});
test("oversize header rejects without consuming the response",async()=>{
 let cancelled=false;const body=new ReadableStream({cancel(){cancelled=true;}});
 const response=await collect(swap(()=>new Response(body,{headers:{"content-length":String(MAX+1)}})));
 assert.equal(response.list.error,"RESPONSE_OVERSIZE");assert.ok(cancelled);
});
for(const phase of ["headers","body"])test("timeout bounds stalled "+phase,{timeout:3000},async()=>{
 let cancelled=false;
 const transport=phase==="headers"?()=>new Promise(()=>{}):()=>new Response(new ReadableStream({cancel(){cancelled=true;}}));
 const response=await collect(swap(transport),{timeoutMs:20});
 assert.equal(response.list.error,"REQUEST_TIMEOUT");if(phase==="body")assert.ok(cancelled);
 rejected(response,"MATCH_LIST_NOT_VERIFIED");
});
for(const kind of ["status","redirected","origin","userinfo"])test("redirect or response origin cannot bypass the pinned origin "+kind,async()=>{
 const response=new Response("{}",{status:kind==="status"?302:200});
 if(kind==="redirected")Object.defineProperty(response,"redirected",{value:true});
 if(kind==="origin")Object.defineProperty(response,"url",{value:"https://private.invalid/SECRET"});
 if(kind==="userinfo")Object.defineProperty(response,"url",{value:"https://user:SECRET@ma-analiz-2.onrender.com/private"});
 const calls=[];const result=await collect(swap(()=>response,calls));
 assert.ok(result.list.error);assert.equal(result.detail.statusCode,null);
 assert.ok(calls.every(c=>c.url.origin===ORIGIN));assert.ok(!JSON.stringify(publicProbeReport(result)).includes("SECRET"));
});
for(const payload of ["not-json",new Uint8Array([0xff]),"{\"__proto__\":{\"analysis_verified\":true}}"])
 test("invalid or hostile body is not domain evidence "+typeof payload,async()=>{
  const result=await collect(swap(()=>new Response(payload)));assert.equal(assess(result).status,"NOT_VERIFIED");
 });

test("analysis summary, embedded booleans and raw private data never become verification or public output",async()=>{
 const s=snapshot();s.detail.body.analysis_verified=true;s.detail.body.secret="TOKEN_PRIVATE";
 const response=await collect(fixtureFetch(s));response.analysis_verified=true; // Public report must still refuse it.
 const report=publicProbeReport(response);const text=JSON.stringify(report);
 assert.equal(report.analysis_verified,false);assert.equal(report.analysis,"NOT_VERIFIED");assert.equal(report.LIVE_MATCH_CENTER,"NOT_VERIFIED");
 assert.equal(report.result,"NOT_VERIFIED");assert.equal(report.SAFE_TO_DELETE,"NO");
 for(const secret of [...ids,"SYNTHETIC_HOME","SYNTHETIC_AWAY","TOKEN_PRIVATE","analysis_summary"])assert.ok(!text.includes(secret));
});

test("default network collector is not enabled by importing or calling it",async()=>{
 const result=await collect();assert.equal(result.probe_status,"NOT_RUN");assert.equal(result.analysis_verified,false);
});
const runner=fileURLToPath(new URL("./run-stage7i4-live-probe.mjs",import.meta.url));
const fixtureUrl=new URL("./fixtures/stage7i6.mjs",import.meta.url).href;
function cli(args,{ci="true",stub=false}={}) {
 const preload=stub?`import {fixtureFetch} from ${JSON.stringify(fixtureUrl)};globalThis.fetch=fixtureFetch();`
  :`globalThis.fetch=()=>{process.stderr.write('NETWORK_ATTEMPTED');throw Error('TOKEN_PRIVATE')};`;
 return spawnSync(process.execPath,["--import","data:text/javascript,"+encodeURIComponent(preload),runner,...args],
  {encoding:"utf8",timeout:5000,env:{PATH:process.env.PATH,SYSTEMROOT:process.env.SYSTEMROOT,CI:ci,GITHUB_ACTIONS:ci}});
}
for(const args of [[],["--live"],["--live","https://private.invalid/TOKEN"],["--live","--token=TOKEN_PRIVATE"]])
 test("CLI blocks CI or arbitrary arguments without a network attempt "+args.length,()=>{
  const child=cli(args,{ci:args.length===1?"true":"false"});assert.equal(child.status,2);assert.ok(!child.stderr.includes("NETWORK_ATTEMPTED"));
  assert.ok(!child.stderr.includes("TOKEN_PRIVATE"));assert.equal(child.stdout,"");
 });
test("interactive CLI fixture report is secret-free and never claims real analysis",()=>{
 const child=cli(["--live"],{ci:"false",stub:true});assert.equal(child.status,1);
 const report=JSON.parse(child.stdout);assert.equal(report.analysis,"NOT_VERIFIED");assert.equal(report.http.history,200);
 for(const value of [...ids,"SYNTHETIC_HOME","SYNTHETIC_AWAY"])assert.ok(!child.stdout.includes(value));
 assert.equal(report.SAFE_TO_DELETE,"NO");assert.equal(report.LIVE_MATCH_CENTER,"NOT_VERIFIED");
});
