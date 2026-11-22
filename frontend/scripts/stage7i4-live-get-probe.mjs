/** Operator-only live GET adapter; tests inject fetch. Never logs snapshots. */
import {assessLiveReadOnlyEvidence} from "./stage7i3-readonly-contract.mjs";
export const ORIGIN = "https://ma-analiz-2.onrender.com";
export const MAX_RESPONSE_BYTES = 262144;
export const REQUEST_TIMEOUT_MS = 10000;
export const isUuid = value => typeof value === "string" && value.length === 36 &&
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
export const inCI = () => [process.env.CI, process.env.GITHUB_ACTIONS].some(v => v && v !== "false" && v !== "0");
const code = response => Number.isInteger(response?.status) && response.status >= 100 && response.status <= 599 ? response.status : null;
class ProbeFailure extends Error {}
function cancel(body) { try { Promise.resolve(body?.cancel?.()).catch(() => {}); } catch {} }

async function request(path, fetcher, timeoutMs, json = true) {
  const controller = new AbortController();
  let reader, response;
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let onAbort;
  const aborted = new Promise((_, reject) => {
    onAbort = () => reject(new ProbeFailure("REQUEST_TIMEOUT"));
    controller.signal.addEventListener("abort", onAbort, {once:true});
  });
  try {
    const operation = (async () => {
      response = await fetcher(new URL(path, ORIGIN), {
        method:"GET", redirect:"error", cache:"no-store", credentials:"omit",
        headers:{Accept:json ? "application/json" : "text/html"}, signal:controller.signal,
      });
      if (controller.signal.aborted) throw new ProbeFailure("REQUEST_TIMEOUT");
      if (response.redirected || response.type === "opaqueredirect" || (code(response) >= 300 && code(response) < 400))
        throw new ProbeFailure("REDIRECT_NOT_ALLOWED");
      if (response.url) {
        const url = new URL(response.url);
        if (url.origin !== ORIGIN || url.username || url.password) throw new ProbeFailure("RESPONSE_ORIGIN_NOT_ALLOWED");
      }
      if (!json || code(response) !== 200) { cancel(response.body); return {statusCode:code(response)}; }
      const length = Number(response.headers?.get?.("content-length"));
      if (Number.isFinite(length) && length > MAX_RESPONSE_BYTES) throw new ProbeFailure("RESPONSE_OVERSIZE");
      if (!response.body?.getReader) throw new ProbeFailure("BODY_STREAM_UNAVAILABLE");
      reader = response.body.getReader();
      const bytes = new Uint8Array(MAX_RESPONSE_BYTES); let size = 0;
      while (true) {
        const {value, done} = await reader.read();
        if (controller.signal.aborted) throw new ProbeFailure("REQUEST_TIMEOUT");
        if (done) break;
        if (!(value instanceof Uint8Array)) throw new ProbeFailure("INVALID_RESPONSE_BYTES");
        if (size + value.byteLength > MAX_RESPONSE_BYTES) throw new ProbeFailure("RESPONSE_OVERSIZE");
        bytes.set(value,size); size += value.byteLength;
      }
      const body = JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(bytes.subarray(0,size)));
      return {statusCode:code(response),body};
    })();
    return await Promise.race([operation,aborted]);
  } catch (error) {
    return {statusCode:code(response),error:error instanceof ProbeFailure ? error.message : json ? "NETWORK_OR_JSON_UNAVAILABLE" : "NETWORK_UNAVAILABLE"};
  } finally {
    clearTimeout(timer); controller.signal.removeEventListener("abort",onAbort);
    controller.abort();
    if (reader) { try { Promise.resolve(reader.cancel()).catch(() => {}); } catch {} }
    else cancel(response?.body);
  }
}

export async function collectReadOnlyEvidence(fetcher, {operatorOptIn=false,timeoutMs=REQUEST_TIMEOUT_MS}={}) {
  // A default/global network transport is unavailable without interactive opt-in.
  // CI may only use injected local fixtures, never the production origin.
  if (!fetcher || fetcher === globalThis.fetch) {
    if (!operatorOptIn || inCI()) return {analysis_verified:false,probe_status:"NOT_RUN",error:"OPERATOR_OPT_IN_REQUIRED_OR_CI_BLOCKED"};
    fetcher = globalThis.fetch;
  }
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > REQUEST_TIMEOUT_MS) throw new Error("INVALID_TEST_TIMEOUT");
  const homepage = await request("/",fetcher,timeoutMs,false);
  const [live,ready,health,list,history] = await Promise.all([
    request("/health/live",fetcher,timeoutMs),request("/health/ready",fetcher,timeoutMs),
    request("/api/v1/health",fetcher,timeoutMs),request("/api/v1/match-center?limit=1",fetcher,timeoutMs),
    request("/api/v1/match-center?source=history&limit=1",fetcher,timeoutMs),
  ]);
  const first = list.body?.items?.[0]?.id, old = history.body?.items?.[0]?.id;
  const detail = isUuid(first) ? await request("/api/v1/match-center/"+first,fetcher,timeoutMs)
    : {statusCode:null,error:"NO_VERIFIED_MATCH_ID"};
  const archive = isUuid(old) ? await request("/api/v1/match-center/"+old+"?source=history",fetcher,timeoutMs)
    : {statusCode:null,error:"NO_VERIFIED_HISTORY_ID"};
  return {root:homepage,live,ready,health,list,history,detail,archive,analysis_verified:false};
}

export function summarizeEvidence(snapshot) {
  return Object.fromEntries(["root","live","ready","health","list","history","detail","archive"].map(k=>[k,
    Number.isInteger(snapshot?.[k]?.statusCode) && snapshot[k].statusCode >= 100 && snapshot[k].statusCode <= 599
      ? snapshot[k].statusCode : "NOT_VERIFIED"]));
}

export function publicProbeReport(snapshot) {
  const result = assessLiveReadOnlyEvidence({...snapshot,analysis_verified:false});
  return {stage:"7I.6",method:"GET_ONLY",http:summarizeEvidence(snapshot),result:result.status,blockers:result.blockers,
    analysis_verified:false,analysis:"NOT_VERIFIED",LIVE_MATCH_CENTER:"NOT_VERIFIED",SAFE_TO_DELETE:"NO",LIVE_DELETE:"NOT_RUN",
    AUTO_RETENTION_DELETE_ENABLED:false,OPERATIONAL_DELETE_ENABLED:false,production_writes:"NOT_RUN",production_deploy:"NOT_RUN"};
}
