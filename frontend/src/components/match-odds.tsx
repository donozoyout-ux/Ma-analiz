"use client";
import {useEffect,useState} from "react";

type Snapshot={price:number|string;captured_at:string;source:string|null;period:string;status:string};
type OddsRow={bookmaker:string;market:string;selection:string;line:number|string|null;opening:Snapshot|null;latest:Snapshot|null;closing:Snapshot|null};
type LiveRow={bookmaker:string;market:string;selection:string;line:number|string|null;price:number|string;captured_at:string;period:string};
type MatchDetail={start_time:string;league_name:string;home_team:string;away_team:string;status:string;odds_summary:OddsRow[];live_odds:LiveRow[]};
type Evidence={score:number|null;confidence:number;sample_size:number;evidence:string;warnings:string[]};
type Statistics={status:string;home:{form_5:Evidence;form_10:Evidence;form_15:Evidence;home_last_5:Evidence;goals:{sample_size:number;avg_goals_for:number|null;avg_goals_against:number|null;btts:number|null;over_2_5:number|null}};away:{form_5:Evidence;form_10:Evidence;form_15:Evidence;away_last_5:Evidence;goals:{sample_size:number;avg_goals_for:number|null;avg_goals_against:number|null;btts:number|null;over_2_5:number|null}};h2h:Evidence;sample_size:number};
type Analysis={final:{signal:string;score:number|null;confidence:string;risk:string;reasons:string[]};historical:{status:string;sample_size:number};value:{status:string;value:number|null};ai:{status:string;summary:string|null}};

function price(value:Snapshot|null){return value?.price==null?"—":Number(value.price).toFixed(3).replace(/0+$/,'').replace(/\.$/,'');}
function movement(row:OddsRow){
  if(row.opening?.price==null||row.latest?.price==null)return "—";
  const opening=Number(row.opening.price);const latest=Number(row.latest.price);
  return opening>0?`${((latest-opening)/opening*100).toFixed(2)}%`:"—";
}

export function MatchOdds({matchId}:{matchId:string}){
  const [match,setMatch]=useState<MatchDetail|null>(null);const [error,setError]=useState<string|null>(null);
  const [statistics,setStatistics]=useState<Statistics|null>(null);const [analysis,setAnalysis]=useState<Analysis|null>(null);
  useEffect(()=>{fetch(`/api/v1/matches/${matchId}`).then(async response=>{if(!response.ok){let payload:Record<string,unknown>={};try{payload=await response.json()}catch{};throw new Error(String(payload.detail??`API_${response.status}`))}return response.json()}).then(setMatch).catch(reason=>setError(reason.message));},[matchId]);
  useEffect(()=>{fetch(`/api/v1/analysis/${matchId}/statistics`).then(r=>r.ok?r.json():null).then(setStatistics);fetch(`/api/v1/analysis/${matchId}`).then(r=>r.ok?r.json():null).then(setAnalysis);},[matchId]);
  if(error)return <div className="card empty">{error.replaceAll("_"," ")}</div>;
  if(!match)return <div className="card empty">Veriler yükleniyor…</div>;
  return <>
    <section className="card" style={{marginBottom:16}}><h2 className="title" style={{fontSize:20}}>{match.home_team} — {match.away_team}</h2><p>{match.league_name??"Lig bilgisi bekleniyor"} · {new Date(match.start_time).toLocaleString("tr-TR")} · {match.status}</p></section>
    <section className="card" style={{overflowX:"auto",marginBottom:16}}><h2>BOOKMAKER ODDS</h2>
      {match.odds_summary.length===0?<p className="empty">Veri bekleniyor</p>:<table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}><thead><tr>{["Bookmaker","Market","Selection","Line","Opening","Latest","Closing","Movement"].map(label=><th key={label} style={{textAlign:"left",padding:"10px 12px",color:"#8492a8",fontWeight:500,borderBottom:"1px solid #202d40"}}>{label}</th>)}</tr></thead><tbody>{match.odds_summary.map((row,index)=><tr key={`${row.bookmaker}-${row.market}-${row.selection}-${row.line??""}-${index}`}>
        <td>{row.bookmaker}</td><td>{row.market}</td><td>{row.selection}</td><td>{row.line??"—"}</td>
        <td title={row.opening?.captured_at??""}>{price(row.opening)}</td><td title={row.latest?.captured_at??""}>{price(row.latest)}</td><td title={row.closing?.captured_at??""}>{price(row.closing)}</td><td>{movement(row)}</td>
      </tr>)}</tbody></table>}
    </section>
    {match.live_odds.length>0&&<section className="card" style={{overflowX:"auto"}}><h2>LIVE ODDS · Closing hesabına katılmaz</h2><table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}><thead><tr>{["Bookmaker","Market","Selection","Line","Price","Captured at"].map(label=><th key={label} style={{textAlign:"left",padding:"10px 12px",color:"#8492a8",fontWeight:500,borderBottom:"1px solid #202d40"}}>{label}</th>)}</tr></thead><tbody>{match.live_odds.map((row,index)=><tr key={`${row.bookmaker}-${row.market}-${row.selection}-${index}`}><td>{row.bookmaker}</td><td>{row.market}</td><td>{row.selection}</td><td>{row.line??"—"}</td><td>{Number(row.price).toFixed(3)}</td><td>{new Date(row.captured_at).toLocaleString("tr-TR")}</td></tr>)}</tbody></table></section>}
    <section className="card" style={{marginTop:16}}><h2>VERIFIED STATISTICS</h2>{!statistics?<p className="empty">Provider veya veritabanı bekleniyor</p>:<><p>{statistics.status.replaceAll("_"," ")} · {statistics.sample_size} doğrulanmış geçmiş maç</p><div className="grid two-col"><div><h3>{match.home_team} · ev sahibi</h3><p>Son 5 form: {statistics.home.form_5.score==null?"Veri bekleniyor":`${(statistics.home.form_5.score*100).toFixed(0)}%`} · örnek {statistics.home.form_5.sample_size}</p><p>Gol ort.: {statistics.home.goals.avg_goals_for?.toFixed(2)??"—"} / yenen {statistics.home.goals.avg_goals_against?.toFixed(2)??"—"} · BTTS {statistics.home.goals.btts==null?"—":`${(statistics.home.goals.btts*100).toFixed(0)}%`}</p></div><div><h3>{match.away_team} · deplasman</h3><p>Son 5 form: {statistics.away.form_5.score==null?"Veri bekleniyor":`${(statistics.away.form_5.score*100).toFixed(0)}%`} · örnek {statistics.away.form_5.sample_size}</p><p>Gol ort.: {statistics.away.goals.avg_goals_for?.toFixed(2)??"—"} / yenen {statistics.away.goals.avg_goals_against?.toFixed(2)??"—"} · BTTS {statistics.away.goals.btts==null?"—":`${(statistics.away.goals.btts*100).toFixed(0)}%`}</p></div></div><p>H2H: {statistics.h2h.evidence} · örnek {statistics.h2h.sample_size}</p></>}</section>
    <section className="card" style={{marginTop:16}}><h2>ANALYSIS DECISION</h2>{!analysis?<p className="empty">Analiz kaynağı bekleniyor</p>:<><p>{analysis.final.signal} · {analysis.final.confidence} · {analysis.final.reasons.join(" · ")}</p><p>Historical: {analysis.historical.status.replaceAll("_"," ")} · Value: {analysis.value.status.replaceAll("_"," ")} · AI: {analysis.ai.status.replaceAll("_"," ")}</p></>}</section>
  </>;
}
