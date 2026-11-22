#!/usr/bin/env node
/** Interactive operator opt-in only. Never run production GETs from CI. */
import {collectReadOnlyEvidence,publicProbeReport,inCI} from "./stage7i4-live-get-probe.mjs";
if(process.argv.length !== 3 || process.argv[2] !== "--live" || inCI()) {
  console.error("NOT_RUN: Interactive --live required; CI and extra arguments forbidden; no request made.");
  process.exitCode=2;
} else {
  try {
    const snapshot=await collectReadOnlyEvidence(undefined,{operatorOptIn:true});
    const report=publicProbeReport(snapshot);
    console.log(JSON.stringify(report,null,2));
    if(report.result!=="READ_ONLY_DOMAIN_EVIDENCE_VERIFIED")process.exitCode=1;
  } catch {
    console.error("NOT_VERIFIED: PROBE_UNAVAILABLE; no raw error exported.");process.exitCode=1;
  }
}
