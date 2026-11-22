#!/usr/bin/env node
/** Never runs network activity unless the operator passes --live explicitly. */
import {collectReadOnlyEvidence,summarizeEvidence,ORIGIN} from "./stage7i4-live-get-probe.mjs";
import {assessLiveReadOnlyEvidence} from "./stage7i3-readonly-contract.mjs";
if(process.argv.length!==3||process.argv[2]!=="--live"){
  console.error("NOT_RUN: Explicit --live required; no network request made.");
  process.exitCode=2;
}else{
  const snapshot=await collectReadOnlyEvidence();
  const result=assessLiveReadOnlyEvidence(snapshot);
  // Summary is an allowlist: NO team names, match IDs, tokens, URLs or raw response.
  console.log(JSON.stringify({
    stage:"7I.4",origin:ORIGIN,method:"GET_ONLY",
    http:summarizeEvidence(snapshot),
    result:result.status,blockers:result.blockers,
    SAFE_TO_DELETE:"NO",LIVE_DELETE:"NOT_RUN",
    production_writes:"NOT_RUN",production_deploy:"NOT_RUN"
  },null,2));
  if(result.status!=="READ_ONLY_DOMAIN_EVIDENCE_VERIFIED")process.exitCode=1;
}
