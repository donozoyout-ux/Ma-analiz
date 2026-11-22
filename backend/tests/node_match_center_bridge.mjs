/** Offline stdin replay of synthetic API responses; cannot use any live transport. */
import {collectReadOnlyEvidence,publicProbeReport,ORIGIN,MAX_RESPONSE_BYTES} from "../../frontend/scripts/stage7i4-live-get-probe.mjs";
try {
  if(process.argv.length!==2)throw Error("FIXTURE_ARGUMENTS_FORBIDDEN");
  globalThis.fetch=()=>{throw Error("LIVE_TRANSPORT_FORBIDDEN")};
  const chunks=[];let size=0;
  for await(const chunk of process.stdin){size+=chunk.byteLength;if(size>MAX_RESPONSE_BYTES)throw Error("FIXTURE_OVERSIZE");chunks.push(chunk);}
  const map=JSON.parse(Buffer.concat(chunks).toString("utf8"));let count=0;
  const snapshot=await collectReadOnlyEvidence(async(url,opts)=>{
    if(url.origin!==ORIGIN || opts.method!=="GET" || opts.credentials!=="omit" || opts.redirect!=="error")throw Error("UNSAFE_REQUEST");
    count++;const row=map[url.pathname+url.search];
    return new Response(JSON.stringify(row?.body??{}),{status:row?.statusCode??404});
  });
  console.log(JSON.stringify({...publicProbeReport(snapshot),fixture_requests:count}));
} catch {
  console.log(JSON.stringify({fixture_execution:"FAILED",LIVE_MATCH_CENTER:"NOT_VERIFIED",analysis:"NOT_VERIFIED",SAFE_TO_DELETE:"NO",LIVE_DELETE:"NOT_RUN"}));
  process.exitCode=1;
}
