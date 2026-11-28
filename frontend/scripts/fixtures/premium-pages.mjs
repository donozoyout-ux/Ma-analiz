// UI-only fixtures. Not imported into src, never a production fallback.
export const clock = '2026-11-25T12:00:00Z';
export const uid = 'a1111111-1111-4111-8111-111111111111';
const source = {name:'OpenLigaDB',licence:'ODbL-1.0',licence_url:'https://opendatacommons.org/licenses/odbl/1-0/',attribution:'SENTETİK UI TESTİ · OpenLigaDB atıf görünümü; gerçek maç verisi değildir.'};
export const match = {id:uid,home_team:'SENTETİK Ev Takımı',away_team:'SENTETİK Konuk Takımı',league_id:'de.bl1',league_name:'Bundesliga',season:'2026-2027',start_time:'2026-11-25T10:00:00Z',status:'FINISHED',storage:'POSTGRESQL',last_updated_at:'2026-11-25T11:40:00Z',source,statistics:[],analysis_summary:null,result:{home_score:1,away_score:1,status:'FINAL',verified_at:'2026-11-25T11:30:00Z'}};
export const history = {...match,storage:'R2',archive_verified_at:clock};
export const routes = ['/', '/matches', '/matches?tab=odds', '/matches/'+uid, '/matches/'+uid+'?source=history', '/predictions', '/predictions?tab=coupons', '/analysis', '/analysis?tab=lessons', '/archive', '/archive?tab=storage', '/management', '/management?tab=settings', '/odds', '/coupons', '/performance', '/lessons', '/storage', '/system', '/settings', '/premium-ui-not-found'];
export function fixture(url, mode) {
 const path=url.pathname;
 if(mode==='error')return {status:503,body:{detail:'SYNTHETIC_UNAVAILABLE'}};
 if(path==='/api/v1/source-bulletin') return {body:{matches:mode==='empty'?[]:[{id:'openligadb:synthetic',provider_match_id:'synthetic',league_id:'de.bl1',league_name:'Bundesliga',home_team:'SENTETİK Ev Takımı',away_team:'SENTETİK Konuk Takımı',start_time:'2026-11-25T18:00:00Z',status:'SCHEDULED',score:null,source:'OpenLigaDB'}],available_leagues:['de.bl1'],failures:[],window_start:'2026-11-22',window_end:'2026-12-02',last_observed_at:clock,live_guaranteed:false}};
 if(path==='/api/v1/bundesliga/team-performance')return {body:{team:'SENTETİK takım',played:1,wins:0,draws:1,losses:0,goals_for:1,goals_against:1,goal_difference:0,home_played:1,home_wins:0,away_played:0,away_wins:0,form:['D'],warnings:[]}};
 if(path==='/api/v1/health')return {body:{status:'degraded',database:'NOT_CONFIGURED',database_schema:'NOT_VERIFIED',r2:'NOT_CONFIGURED',worker:'NOT_RUN',archive:'NOT_RUN',telegram:'NOT_CONFIGURED',services:{provider:'NOT_CONFIGURED',goaloo:'NOT_CONFIGURED',sportmonks:'NOT_CONFIGURED',odds:'NOT_CONFIGURED',historical_data:'NOT_CONFIGURED',scanner:'NOT_CONFIGURED',groq:'NOT_CONFIGURED'}}};
 if(path==='/api/v1/storage')return {body:{postgres:{status:'NOT_CONFIGURED',bytes:null,thresholds_mb:{normal:300,warning:500,high:750,critical:750},largest_tables:[]},r2:{status:'NOT_CONFIGURED',bytes:null,object_count:null,daily_growth_bytes:null}}};
 if(path==='/api/v1/match-center') {
  let items=mode==='empty'?[]:[url.searchParams.get('source')==='history'?history:match];
  if(url.searchParams.get('team') && !match.home_team.toLocaleLowerCase('tr').includes(url.searchParams.get('team').toLocaleLowerCase('tr')))items=[];
  if(url.searchParams.get('day') && url.searchParams.get('day')!=='2026-11-25')items=[];
  if(url.searchParams.get('league') && url.searchParams.get('league')!=='de.bl1')items=[];
  return {body:{items,next_cursor:null,as_of:clock,history_status:url.searchParams.get('source')==='history'?'VERIFIED':'NOT_READ',warnings:[],scope:{scanned:items.length,limit:12,start_utc:'2026-10-25T21:00:00Z',end_utc:'2026-11-25T21:00:00Z'}}};
 }
 if(path.startsWith('/api/v1/match-center/'))return path.endsWith(uid)?{body:url.searchParams.get('source')==='history'?history:match}:{status:404,body:{detail:'MATCH_NOT_FOUND'}};
 if(path==='/api/v1/matches')return {body:mode==='empty'?[]:[match]};
 if(path.startsWith('/api/v1/matches/'))return {body:{...match,odds_summary:[],live_odds:[]}};
 if(path.startsWith('/api/v1/analysis/'))return {body:null};
 if(mode==='empty')return {body:[]};
 if(path==='/api/v1/predictions')return {body:[{id:'synthetic-prediction',match_id:uid,created_at:clock,analysis_timestamp:clock,published_at:null,market:'MATCH_RESULT',selection:'HOME',probability:.5,confidence:.5,model_version:'SENTETİK-TEST',evidence_status:'NOT_VERIFIED',status:'PENDING',settled_at:null}]};
 if(path==='/api/v1/performance')return {body:[{date:'2026-11-25',predictions:2,wins:1,losses:1,hit_rate:.5,paper_pl:null}]};
 if(path==='/api/v1/coupons')return {body:[{id:'synthetic-coupon',created_at:clock,type:'PAPER_PENDING',total_odds:null,confidence:null,status:'PENDING',items:[]}]};
 if(path==='/api/v1/lessons')return {body:[{id:'synthetic-lesson',date:'2026-11-25',category:'PROPOSAL',title:'SENTETİK eğitim kaydı',summary:'Yalnız tablo yerleşimi testi.',recommendation:'Gerçek işlem yapılmaz.',confidence:null,status:'PENDING'}]};
 return {body:[]};
}
