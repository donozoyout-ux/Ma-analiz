/** Explicit opt-in, GET-only production evidence probe. No secrets or response bodies logged. */
export const ORIGIN = "https://ma-analiz-2.onrender.com";
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
function scalarResponse(response) { return {statusCode:response.status}; }
async function getJson(path, fetcher){
  try {
    const response=await fetcher(new URL(path,ORIGIN),{
      method:"GET",redirect:"error",cache:"no-store",credentials:"omit",
      headers:{Accept:"application/json"},signal:AbortSignal.timeout(10000)
    });
    if(!response.ok) return scalarResponse(response);
    const length=Number(response.headers?.get?.("content-length"));
    if(Number.isFinite(length)&&length>262144) return {statusCode:response.status,error:"RESPONSE_OVERSIZE"};
    const body=await response.json();
    if(JSON.stringify(body).length>262144) return {statusCode:response.status,error:"RESPONSE_OVERSIZE"};
    return {statusCode:response.status,body};
  }catch{return {statusCode:null,error:"NETWORK_OR_JSON_UNAVAILABLE"};}
}
export async function collectReadOnlyEvidence(fetcher=fetch){
  // Fixed paths and a pinned public HTTPS origin: never fetch user-provided URLs.
  const homepage=await (async()=>{
    try{const r=await fetcher(new URL("/",ORIGIN),{
      method:"GET",redirect:"error",cache:"no-store",credentials:"omit",
      signal:AbortSignal.timeout(10000)});return scalarResponse(r);
    }catch{return {statusCode:null,error:"NETWORK_UNAVAILABLE"};}
  })();
  const [live,ready,health,list,history]=await Promise.all([
    getJson("/health/live",fetcher),getJson("/health/ready",fetcher),
    getJson("/api/v1/health",fetcher),getJson("/api/v1/match-center?limit=1",fetcher),
    getJson("/api/v1/match-center?source=history&limit=1",fetcher)
  ]);
  const first=list.body?.items?.[0]?.id;
  const old=history.body?.items?.[0]?.id;
  const detail=UUID.test(first||"")
    ? await getJson("/api/v1/match-center/"+first,fetcher)
    : {statusCode:null,error:"NO_VERIFIED_MATCH_ID"};
  const archive=UUID.test(old||"")
    ? await getJson("/api/v1/match-center/"+old+"?source=history",fetcher)
    : {statusCode:null,error:"NO_VERIFIED_HISTORY_ID"};
  // Return only minimal public fields expected by the offline assessor;
  // callers MUST NOT print the collected snapshot, which may contain match data.
  return {root:homepage,live,ready,health,list,detail,archive,
    analysis_verified:false}; // requires independent operator verification
}
export function summarizeEvidence(snapshot){
  const statusKeys=["root","live","ready","health","list","detail","archive"];
  return Object.fromEntries(statusKeys.map(k=>[k,
    Number.isInteger(snapshot?.[k]?.statusCode)?snapshot[k].statusCode:"NOT_VERIFIED"]));
}
