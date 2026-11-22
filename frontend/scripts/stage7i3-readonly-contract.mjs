/** Stage 7I.3: pure read-only evidence classifier; never makes network requests. */
const good = value => value && typeof value === "object" && !Array.isArray(value);
const statusCode = row => Number.isInteger(row?.statusCode) ? row.statusCode : null;
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const stamp = value => typeof value === "string" && Number.isFinite(Date.parse(value))
  && /(?:Z|[+-]\d{2}:\d{2})$/.test(value);
const permittedResult = result => result === null || (good(result) &&
  result.status === "FINAL" &&
  Number.isInteger(result.home_score) && result.home_score >= 0 &&
  Number.isInteger(result.away_score) && result.away_score >= 0 &&
  stamp(result.verified_at));

export function assessLiveReadOnlyEvidence(snap) {
  const blockers = [];
  if (!good(snap)) return { status:"NOT_VERIFIED", blockers:["EVIDENCE_MISSING"], SAFE_TO_DELETE:"NO", LIVE_DELETE:"NOT_RUN" };
  if (!good(snap.root) || statusCode(snap.root) !== 200) blockers.push("FRONTEND_NOT_VERIFIED");
  if (statusCode(snap.live) !== 200 || snap.live?.body?.status !== "ok")
    blockers.push("API_LIVENESS_NOT_VERIFIED");
  const ready = snap.ready?.body;
  if (statusCode(snap.ready) !== 200 || !good(ready) || ready.status !== "ok" ||
      ready.scope !== "web_api_transport" || ready.dependencies !== "not_checked")
    blockers.push("TRANSPORT_NOT_VERIFIED");
  const health = snap.health?.body;
  if (statusCode(snap.health) !== 200 || !good(health) ||
      health.status !== "ok" || health.database !== "CONNECTED" || health.database_schema !== "READY" ||
      health.r2 !== "CONNECTED") blockers.push("STORAGE_DEPENDENCIES_NOT_VERIFIED");
  const listing = snap.list?.body;
  if (statusCode(snap.list) !== 200 || !good(listing) || !Array.isArray(listing.items) ||
      !stamp(listing.as_of) || !["all","active","history"].includes(listing.source) ||
      listing.history_status === "PARTIAL" || !Array.isArray(listing.warnings) ||
      listing.warnings.length !== 0) {
    blockers.push("MATCH_LIST_NOT_VERIFIED");
  } else if (listing.items.length === 0) {
    blockers.push("MATCH_DATA_ABSENT");
  } else if (listing.items.length > 12 || listing.items.some(item => !good(item) || !uuid.test(item.id) ||
      typeof item.home_team !== "string" || !item.home_team.trim() ||
      typeof item.away_team !== "string" || !item.away_team.trim() ||
      !stamp(item.start_time) || !["POSTGRESQL","R2"].includes(item.storage) ||
      !permittedResult(item.result))) {
    blockers.push("MATCH_LIST_NOT_VERIFIED");
  }
  // A sample detail must refer to the same observed match; a 404/503 is not proof.
  const sample = listing?.items?.[0];
  const detail = snap.detail?.body;
  if (!sample || statusCode(snap.detail) !== 200 || !good(detail) ||
      detail.id !== sample.id || detail.storage !== sample.storage ||
      detail.home_team !== sample.home_team || detail.away_team !== sample.away_team ||
      !stamp(detail.start_time) || !permittedResult(detail.result)) {
    blockers.push("MATCH_DETAIL_NOT_VERIFIED");
  }
  // Readback of at least one R2 history detail is required, distinct from SQL-only list.
  const archive = snap.archive?.body;
  if (statusCode(snap.archive) !== 200 || !good(archive) ||
      !uuid.test(archive.id || "") || archive.storage !== "R2" ||
      !stamp(archive.archive_verified_at) || !permittedResult(archive.result)) {
    blockers.push("R2_HISTORY_NOT_VERIFIED");
  }
  // Domain signal isn't proven by a rendered page or a configured AI key.
  if (typeof snap.analysis_verified !== "boolean" || snap.analysis_verified !== true)
    blockers.push("ANALYSIS_NOT_VERIFIED");
  // These are only observations. Production delete is never authorized.
  return {
    status: blockers.length ? "NOT_VERIFIED" : "READ_ONLY_DOMAIN_EVIDENCE_VERIFIED",
    blockers: [...new Set(blockers)].sort(),
    SAFE_TO_DELETE:"NO", LIVE_DELETE:"NOT_RUN",
    note:"An offline evidence classifier, not deployment, backup, or deletion authorization."
  };
}
