/** Pure evidence consistency classifier, not an analysis/provenance verifier. */
const good = value => value && typeof value === "object" && !Array.isArray(value);
const statusCode = row => !row?.error && Number.isInteger(row?.statusCode) ? row.statusCode : null;
const uuid = value => typeof value === "string" && value.length === 36 &&
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
function stamp(value) {
  if (typeof value !== "string") return false;
  const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(?:Z|[+-](\d{2}):(\d{2}))$/.exec(value);
  if (!parts || parts[0] !== value || !Number.isFinite(Date.parse(value))) return false;
  const [,year,month,day,hour,minute,second,offsetHour="0",offsetMinute="0"] = parts.map((v,i)=>i ? Number(v || 0) : v);
  const end = new Date(0); end.setUTCFullYear(year,month,0);
  return month >= 1 && month <= 12 && day >= 1 && day <= end.getUTCDate() && hour < 24 && minute < 60 && second < 60 && offsetHour < 24 && offsetMinute < 60;
}
const finished = new Set(["FINISHED","FT","COMPLETED","FINAL"]);
const states = new Set([...finished,"SCHEDULED","NS","POSTPONED","LIVE","IN_PLAY","IN_PROGRESS","PAUSED","HT","CANCELLED"]);
const text = value => typeof value === "string" && value.trim().length > 0;
const licences = {
  "ODbL-1.0":["OpenLigaDB","https://www.openligadb.de/lizenz"],
  "CC0-1.0":["OpenFootball","https://github.com/openfootball/football.json/blob/master/LICENSE.md"],
};
function policy(source) {
  return good(source) && typeof source.licence === "string" && Object.hasOwn(licences,source.licence) && source.name === licences[source.licence][0] &&
    source.licence_url === licences[source.licence][1] && text(source.attribution);
}
function permittedResult(row, asOf) {
  const result = row.result;
  if (result === null) return !finished.has(row.status);
  return finished.has(row.status) && good(result) && result.status === "FINAL" &&
    Number.isInteger(result.home_score) && result.home_score >= 0 &&
    Number.isInteger(result.away_score) && result.away_score >= 0 && stamp(result.verified_at) &&
    Date.parse(row.start_time) <= Date.parse(result.verified_at) && Date.parse(result.verified_at) <= Date.parse(asOf);
}
function match(row, asOf) {
  return good(row) && uuid(row.id) && text(row.home_team) && text(row.away_team) && row.home_team !== row.away_team &&
    stamp(row.start_time) && states.has(row.status) && policy(row.source) &&
    ["POSTGRESQL","R2"].includes(row.storage) && permittedResult(row,asOf) &&
    (row.storage !== "R2" || (stamp(row.archive_verified_at) && Date.parse(row.archive_verified_at) <= Date.parse(asOf) &&
      (row.result === null || Date.parse(row.result.verified_at) <= Date.parse(row.archive_verified_at))));
}
function linked(row, sample) {
  if (!good(row) || !good(sample)) return false;
  const identity = ["id","storage","home_team","away_team","start_time","status","league_id","season"];
  const provenance = ["name","licence","attribution","licence_url","native_id"];
  if (identity.some(k=>row[k] !== sample[k]) || provenance.some(k=>row.source?.[k] !== sample.source?.[k])) return false;
  if (row.result === null || sample.result === null) return row.result === sample.result;
  return ["home_score","away_score","status","verified_at"].every(k=>row.result?.[k] === sample.result?.[k]);
}
export function assessLiveReadOnlyEvidence(snap) {
  const blockers = [];
  if (!good(snap)) return {status:"NOT_VERIFIED",blockers:["EVIDENCE_MISSING"],SAFE_TO_DELETE:"NO",LIVE_DELETE:"NOT_RUN"};
  if (!good(snap.root) || statusCode(snap.root) !== 200) blockers.push("FRONTEND_NOT_VERIFIED");
  if (statusCode(snap.live) !== 200 || snap.live?.body?.status !== "ok") blockers.push("API_LIVENESS_NOT_VERIFIED");
  const ready = snap.ready?.body;
  if (statusCode(snap.ready) !== 200 || !good(ready) || ready.status !== "ok" ||
      ready.scope !== "web_api_transport" || ready.dependencies !== "not_checked") blockers.push("TRANSPORT_NOT_VERIFIED");
  const health = snap.health?.body;
  if (statusCode(snap.health) !== 200 || !good(health) || health.status !== "ok" || health.database !== "CONNECTED" ||
      health.database_schema !== "READY" || health.r2 !== "CONNECTED") blockers.push("STORAGE_DEPENDENCIES_NOT_VERIFIED");
  const listing = snap.list?.body;
  if (statusCode(snap.list) !== 200 || !good(listing) || !Array.isArray(listing.items) || !stamp(listing.as_of) ||
      !["all","active","history"].includes(listing.source) || listing.history_status === "PARTIAL" ||
      !Array.isArray(listing.warnings) || listing.warnings.length !== 0) blockers.push("MATCH_LIST_NOT_VERIFIED");
  else if (listing.items.length === 0) blockers.push("MATCH_DATA_ABSENT");
  else if (listing.items.length > 12 || listing.items.some(row=>!match(row,listing.as_of))) blockers.push("MATCH_LIST_NOT_VERIFIED");
  const detail = snap.detail?.body, sample = listing?.items?.[0];
  if (!sample || statusCode(snap.detail) !== 200 || !match(detail,listing?.as_of) || !linked(detail,sample))
    blockers.push("MATCH_DETAIL_NOT_VERIFIED");
  const archive = snap.archive?.body, history = snap.history?.body;
  const indexed = good(history) && statusCode(snap.history) === 200 && history.source === "history" &&
    history.history_status === "VERIFIED" && Array.isArray(history.warnings) && history.warnings.length === 0 &&
    Array.isArray(history.items) && history.items.length > 0 && history.items.length <= 12 && stamp(history.as_of) &&
    history.items.every(row=>match(row,history.as_of) && row.storage === "R2");
  const source = indexed ? history.items[0] : null;
  if (!indexed || statusCode(snap.archive) !== 200 || !match(archive,history?.as_of) ||
      archive?.storage !== "R2" || !linked(archive,source)) blockers.push("R2_HISTORY_NOT_VERIFIED");
  // Only an independent operator can supply this assertion to the pure classifier.
  // The collector/CLI never make it: a summary or HTTP 200 proves no accuracy.
  if (snap.analysis_verified !== true) blockers.push("ANALYSIS_NOT_VERIFIED");
  return {status:blockers.length ? "NOT_VERIFIED" : "READ_ONLY_DOMAIN_EVIDENCE_VERIFIED",
    blockers:[...new Set(blockers)].sort(),SAFE_TO_DELETE:"NO",LIVE_DELETE:"NOT_RUN",
    analysis:snap.analysis_verified === true ? "OPERATOR_ASSERTED" : "NOT_VERIFIED",
    note:"Consistency classifier only; no analysis accuracy, deployment, recovery or deletion authorization."};
}
