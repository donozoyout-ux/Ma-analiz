/** Entirely synthetic HTTP snapshots. No real match observations or secrets. */
export const ids=["123e4567-e89b-42d3-a456-426614174000","123e4567-e89b-42d3-a456-426614174001"];
export const at="2026-11-25T12:00:00+00:00";
export function snapshot() {
  const match=(id,storage)=>({id,home_team:"SYNTHETIC_HOME",away_team:"SYNTHETIC_AWAY",start_time:"2026-11-23T10:00:00+00:00",
    league_id:"de.bl1",season:"2026-2027",storage,status:"FINISHED",source:{name:"OpenLigaDB",licence:"ODbL-1.0",
      attribution:"SYNTHETIC OpenLigaDB fixture",licence_url:"https://www.openligadb.de/lizenz",native_id:"SYNTHETIC_ONLY"},
    result:{home_score:2,away_score:1,status:"FINAL",verified_at:"2026-11-23T12:00:00+00:00"},
    analysis_summary:{analysis_status:"ANALYZED",signal:"NO BET",analysis_timestamp:"2026-11-23T09:00:00+00:00",model_version:"SYNTHETIC"},
    ...(storage==="R2"?{archive_verified_at:at}:{})});
  const item=match(ids[0],"POSTGRESQL"),history=match(ids[1],"R2");
  return {root:{statusCode:200},live:{statusCode:200,body:{status:"ok"}},
    ready:{statusCode:200,body:{status:"ok",scope:"web_api_transport",dependencies:"not_checked"}},
    health:{statusCode:200,body:{status:"ok",database:"CONNECTED",database_schema:"READY",r2:"CONNECTED"}},
    list:{statusCode:200,body:{items:[item],as_of:at,source:"all",history_status:"NOT_READ",warnings:[]}},
    history:{statusCode:200,body:{items:[history],as_of:at,source:"history",history_status:"VERIFIED",warnings:[]}},
    detail:{statusCode:200,body:structuredClone(item)},archive:{statusCode:200,body:structuredClone(history)},
    analysis_verified:true}; // Test oracle only; no live verification.
}
export function routes(s=snapshot()) {
  return new Map([["/",s.root],["/health/live",s.live],["/health/ready",s.ready],["/api/v1/health",s.health],
    ["/api/v1/match-center?limit=1",s.list],["/api/v1/match-center?source=history&limit=1",s.history],
    ["/api/v1/match-center/"+ids[0],s.detail],["/api/v1/match-center/"+ids[1]+"?source=history",s.archive]]);
}
export function fixtureFetch(s=snapshot(), calls=[]) {
  const map=routes(s);
  return async (url,opts)=>{
    calls.push({url,opts});const row=map.get(url.pathname+url.search);
    if(!row)throw Error("UNEXPECTED_FIXTURE_PATH");
    return new Response(JSON.stringify(row.body ?? "synthetic root"),{status:row.statusCode});
  };
}
