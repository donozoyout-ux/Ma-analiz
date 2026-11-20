"use client";
import { useEffect, useState } from "react";
import { displayStatus } from "@/lib/turkish";
type ProviderHealth = {provider:string;display_status:string;latency_ms:number|null;last_successful_request:string|null;coverage?:{leagues:string[];seasons:string[]}};
type Health = {status?:string;api?:string;provider_health?:ProviderHealth;services?:Record<string,string>;[key:string]:unknown};
const services = [["API", "api"], ["PostgreSQL bağlantısı", "database"], ["PostgreSQL şeması", "database_schema"], ["R2 arşivi", "r2"], ["Arşivleme", "archive"], ["Arka plan işçisi", "worker"], ["Veri sağlayıcı", "services.provider"], ["Goaloo", "services.goaloo"], ["Sportmonks", "services.sportmonks"], ["Oran akışı", "services.odds"], ["Tarihsel veri", "services.historical_data"], ["Tarayıcı", "services.scanner"], ["Telegram", "telegram"], ["Groq yapay zekâ", "services.groq"]] as const;
const healthy = new Set(["connected","configured","ok","provider_ok","online","success","verified","ready"]);
export function HealthPanel({compact=false}:{compact?:boolean}) {
 const [health,setHealth] = useState<Health|null>(null);
 useEffect(() => { const c = new AbortController(); fetch("/api/v1/health",{signal:c.signal}).then(r=>r.ok?r.json():Promise.reject()).then(setHealth).catch(e=>{if(e.name!=="AbortError")setHealth({api:"API_UNREACHABLE"});}); return ()=>c.abort(); },[]);
 const provider=health?.provider_health;
 return <div aria-live="polite">{(compact?services.filter(([,key])=>["api","database","r2","services.provider","services.groq"].includes(key)):services).map(([label,key]) => {
  const [group,field]=key.split(".");
  const value=key==="api" ? health?.status??health?.api : group==="services" ? health?.services?.[field] : health?.[key];
  const status=typeof value==="string" ? value : health?.api==="API_UNREACHABLE" ? "UNAVAILABLE" : health ? "UNKNOWN" : "CHECKING";
  return <div className="health-row" key={key}><span className="health-name">{label}</span><span className={`badge ${healthy.has(status.toLowerCase())?"ok":""}`}>{displayStatus(status)}</span></div>;
 })}
 {!compact&&provider&&<><div className="health-row"><span className="health-name">Sağlayıcı ayrıntısı</span><span>{provider.provider} · {displayStatus(provider.display_status)} · {provider.latency_ms==null?"Gecikme ölçülmedi":`${Math.round(provider.latency_ms)} ms`}</span></div><div className="health-row"><span className="health-name">Son başarılı istek</span><span>{provider.last_successful_request ? new Date(provider.last_successful_request).toLocaleString("tr-TR",{timeZone:"Europe/Istanbul"}) : "Henüz yok"}</span></div><div className="health-row"><span className="health-name">Kapsam</span><span>{provider.coverage?.leagues?.length?`Ligler: ${provider.coverage.leagues.join(", ")}`:"Lig ayarı yok"}{provider.coverage?.seasons?.length?` · Sezonlar: ${provider.coverage.seasons.join(", ")}`:""}</span></div></>}
 </div>;
}
