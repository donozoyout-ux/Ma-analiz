"use client";
import { useEffect, useState } from "react";

type ProviderHealth = {provider:string;status:string;display_status:string;latency_ms:number|null;error_code:string|null;last_successful_request:string|null;coverage:{leagues:string[];seasons:string[]}};
type Health = Record<string,string> & {provider_health?:ProviderHealth;services?:Record<string,string>};
const services = [["API", "api"], ["PostgreSQL bağlantısı", "database"], ["PostgreSQL şeması", "database_schema"], ["R2 arşivi", "r2"], ["Arşivleme", "archive"], ["Worker", "worker"], ["Veri sağlayıcı", "services.provider"], ["Goaloo", "services.goaloo"], ["Sportmonks", "services.sportmonks"], ["Oran akışı", "services.odds"], ["Tarihsel veri", "services.historical_data"], ["Tarayıcı", "services.scanner"], ["Telegram", "telegram"], ["Groq AI", "services.groq"]] as const;
const healthyStatuses = new Set(["connected", "configured", "ok", "provider_ok", "online", "success", "verified", "ready"]);
export function HealthPanel({compact=false}:{compact?:boolean}){
  const [health,setHealth]=useState<Health|null>(null);
  useEffect(()=>{fetch(`/api/v1/health`).then(r=>r.ok?r.json():Promise.reject()).then(setHealth).catch(()=>setHealth({api:"API_UNREACHABLE"}));},[]);
  const provider=health?.provider_health;
  const coverage=provider?.coverage;
  return <div aria-live="polite">{(compact?services.filter(([,key])=>["api","database","r2","services.provider","services.groq"].includes(key)):services).map(([label,key])=>{const [group,field]=key.split(".");const value=key==="api"?(health?.status??health?.api??(health?.api==="API_UNREACHABLE"?"VERİ ALINAMADI":"CHECKING")):group==="services"?(health?.services?.[field]??(health?.api==="API_UNREACHABLE"?"VERİ ALINAMADI":"CHECKING")):(health?.[key]??(health?.api==="API_UNREACHABLE"?"VERİ ALINAMADI":"CHECKING"));const ok=healthyStatuses.has(value.toLowerCase());return <div className="health-row" key={key}><span className="health-name">{label}</span><span className={`badge ${ok?"ok":""}`}>{value==="CHECKING"?"KONTROL EDİLİYOR":value.replaceAll("_"," ").toLocaleUpperCase("tr-TR")}</span></div>})}
    {!compact&&provider&&<div className="health-row"><span className="health-name">Sağlayıcı ayrıntısı</span><span>{provider.provider} · {provider.display_status} · {provider.latency_ms==null?"gecikme yok":`${Math.round(provider.latency_ms)} ms`}</span></div>}
    {!compact&&provider&&<div className="health-row"><span className="health-name">Son başarılı istek</span><span>{provider.last_successful_request??"Henüz yok"}</span></div>}
    {!compact&&provider&&<div className="health-row"><span className="health-name">Kapsam</span><span>{coverage?.leagues?.length?`Ligler: ${coverage.leagues.join(", ")}`:"Lig ayarı yok"}{coverage?.seasons?.length?` · Sezonlar: ${coverage.seasons.join(", ")}`:""}</span></div>}
  </div>
}
