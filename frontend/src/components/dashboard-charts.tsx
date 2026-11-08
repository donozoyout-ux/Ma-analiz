"use client";
import {useEffect,useState} from "react";
type Metric={date:string;total_matches:number;analyzed_matches:number;predictions:number;wins:number;losses:number;hit_rate:number|null;paper_pl:number|null;r2_bytes:number;postgres_bytes:number};
const avg=(xs:number[])=>xs.length?xs.reduce((a,b)=>a+b,0)/xs.length:null;
const growth=(today:number|undefined,yesterday:number|undefined)=>today===undefined||yesterday===undefined||yesterday===0?"—":(((today-yesterday)/yesterday)*100).toFixed(1)+"%";
function Bars({rows,value,color}:{rows:Metric[];value:(row:Metric)=>number;color:string}){
  if(!rows.length)return <div className="empty" style={{minHeight:96}}>No data yet</div>;
  const nums=rows.map(value), max=Math.max(...nums,1);
  return <div style={{height:110,display:"flex",alignItems:"end",gap:5,paddingTop:12}}>{rows.map((row,i)=><div key={row.date} title={row.date+": "+value(row).toLocaleString()} style={{flex:1,height:Math.max(3,(nums[i]/max)*100)+"%",background:color,borderRadius:"3px 3px 0 0",opacity:.8}}/>)}</div>;
}
export function DashboardCharts(){
  const [rows,setRows]=useState<Metric[]|null>(null);
  useEffect(()=>{fetch("/api/v1/metrics/daily?limit=30").then(r=>r.ok?r.json():Promise.reject()).then(setRows).catch(()=>setRows(null));},[]);
  const series=(rows??[]).slice(-30), today=series.at(-1), yesterday=series.at(-2);
  const match7=avg(series.slice(-7).map(x=>x.total_matches)), match30=avg(series.map(x=>x.total_matches));
  const total=(x:Metric)=>x.postgres_bytes+x.r2_bytes;
  const storage7=avg(series.slice(-7).map(total)), storage30=avg(series.map(total));
  return <div className="grid" style={{gap:18}}>
    <section><div className="health-row"><span className="health-name">Daily match count</span><span className="section-note">7d avg {match7===null?"—":match7.toFixed(1)} · 30d avg {match30===null?"—":match30.toFixed(1)} · growth {growth(today?.total_matches,yesterday?.total_matches)}</span></div><Bars rows={series} value={x=>x.total_matches} color="#65e5b0"/></section>
    <section><div className="health-row"><span className="health-name">Storage growth · PostgreSQL + R2</span><span className="section-note">Today {today?(total(today)/1048576).toFixed(1)+" MB":"—"} · yesterday {yesterday?(total(yesterday)/1048576).toFixed(1)+" MB":"—"} · 7d avg {storage7===null?"—":(storage7/1048576).toFixed(1)+" MB"} · 30d avg {storage30===null?"—":(storage30/1048576).toFixed(1)+" MB"} · growth {growth(today?total(today):undefined,yesterday?total(yesterday):undefined)}</span></div><Bars rows={series} value={total} color="#76a9ff"/></section>
  </div>;
}
