"use client";
import { useEffect, useState } from "react";

type ProviderHealth = {provider:string;status:string;display_status:string;latency_ms:number|null;error_code:string|null;last_successful_request:string|null;coverage:{leagues:string[];seasons:string[]}};
type Health = Record<string,string> & {provider_health?:ProviderHealth;services?:Record<string,string>};
const services = [["API", "api"], ["PostgreSQL connection", "database"], ["PostgreSQL schema", "database_schema"], ["R2 archive", "r2"], ["Archive", "archive"], ["Worker", "worker"], ["Data provider", "services.provider"], ["Goaloo", "services.goaloo"], ["Sportmonks", "services.sportmonks"], ["Odds", "services.odds"], ["Historical data", "services.historical_data"], ["Scanner", "services.scanner"], ["Telegram", "telegram"], ["Groq", "services.groq"]] as const;
export function HealthPanel(){
  const [health,setHealth]=useState<Health|null>(null);
  useEffect(()=>{fetch(`/api/v1/health`).then(r=>r.ok?r.json():Promise.reject()).then(setHealth).catch(()=>setHealth({api:"API_UNREACHABLE"}));},[]);
  const provider=health?.provider_health;
  const coverage=provider?.coverage;
  return <div>{services.map(([label,key])=>{const [group,field]=key.split(".");const value=key==="api"?(health?.status??"CHECKING"):group==="services"?(health?.services?.[field]??"CHECKING"):(health?.[key]??"CHECKING");const ok=["ready","configured","connected","ok","PROVIDER_OK","ONLINE","SUCCESS","VERIFIED"].includes(value.toLowerCase());return <div className="health-row" key={key}><span className="health-name">{label}</span><span className={`badge ${ok?"ok":""}`}>{value.replaceAll("_"," ").toUpperCase()}</span></div>})}
    {provider&&<div className="health-row"><span className="health-name">Provider detail</span><span>{provider.provider} · {provider.display_status} · {provider.latency_ms==null?"latency unavailable":`${Math.round(provider.latency_ms)} ms`}</span></div>}
    {provider&&<div className="health-row"><span className="health-name">Last successful request</span><span>{provider.last_successful_request??"None"}</span></div>}
    {provider&&<div className="health-row"><span className="health-name">Configured coverage</span><span>{coverage?.leagues?.length?`Leagues: ${coverage.leagues.join(", ")}`:"No leagues configured"}{coverage?.seasons?.length?` · Seasons: ${coverage.seasons.join(", ")}`:""}</span></div>}
  </div>
}
