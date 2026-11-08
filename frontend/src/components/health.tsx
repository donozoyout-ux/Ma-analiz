"use client";
import { useEffect, useState } from "react";

type Health = Record<string,string>;
const services = [["API", "api"], ["PostgreSQL", "database"], ["R2 archive", "r2"], ["Worker", "worker"], ["Data provider", "provider"], ["Telegram", "telegram"], ["AI provider", "ai"]] as const;
export function HealthPanel(){
  const [health,setHealth]=useState<Health|null>(null);
  useEffect(()=>{fetch(`/api/v1/health`).then(r=>r.ok?r.json():Promise.reject()).then(setHealth).catch(()=>setHealth({api:"API_UNREACHABLE"}));},[]);
  return <div>{services.map(([label,key])=>{const value=key==="api"?(health?.status??"CHECKING"):(health?.[key]??"CHECKING");const ok=value==="configured"||value==="ok";return <div className="health-row" key={key}><span className="health-name">{label}</span><span className={`badge ${ok?"ok":""}`}>{value.replaceAll("_"," ").toUpperCase()}</span></div>})}</div>
}
