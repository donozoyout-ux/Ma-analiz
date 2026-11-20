"use client";
import { displayStatus } from "@/lib/turkish";
import {useEffect,useState} from "react";
type TableSize={table:string;size_bytes:number;rows_estimate:number};
type Storage={postgres:{status:string;bytes:number|null;thresholds_mb:Record<string,number>;largest_tables:TableSize[]};r2:{status:string;bytes:number|null;object_count:number|null;daily_growth_bytes:number|null}};
const fmt=(n:number|null)=>n===null?"—":n<1048576?(n/1024).toFixed(1)+" KB":(n/1048576).toFixed(1)+" MB";
export function StoragePanel(){
 const [data,setData]=useState<Storage|null>(null);
 useEffect(()=>{fetch("/api/v1/storage").then(r=>r.ok?r.json():Promise.reject()).then(setData).catch(()=>setData(null));},[]);
 const growth=data?.r2.daily_growth_bytes;
 return <>
  <div className="health-row"><span className="health-name">PostgreSQL kullanımı</span><span className="badge">{displayStatus(data?.postgres.status ?? "UNAVAILABLE")}</span></div>
  <div className="storage-bar"><div className="storage-fill" style={{width:data?.postgres.bytes != null && data.postgres.thresholds_mb.critical > 0 ? `${Math.min(100,data.postgres.bytes / (data.postgres.thresholds_mb.critical * 1048576) * 100)}%` : "0%"}}/></div>
  <div className="storage-meta"><span>{fmt(data?.postgres.bytes??null)} kullanılıyor</span><span>{data?data.postgres.thresholds_mb.critical+" MB kritik eşik":"Eşik bilgisi yok"}</span></div>
  <div className="health-row" style={{marginTop:12}}><span className="health-name">R2 tarihsel arşivi</span><span className="badge">{displayStatus(data?.r2.status ?? "UNAVAILABLE")}</span></div>
  <div className="storage-meta"><span>{fmt(data?.r2.bytes??null)}</span><span>{data?.r2.object_count==null?"Nesne sayısı bilinmiyor":data.r2.object_count+" nesne"}</span></div>
  <div className="storage-meta" style={{marginTop:8}}><span>R2 günlük değişim</span><span>{growth==null?"Henüz veri yok":fmt(growth)}</span></div>
  {data?.postgres.largest_tables?.length ? <div style={{marginTop:18}}><h3>En büyük PostgreSQL tabloları</h3>{data.postgres.largest_tables.map(row=><div className="health-row" key={row.table}><span className="health-name">{row.table+" · "+row.rows_estimate.toLocaleString()+" satır"}</span><span className="badge">{fmt(row.size_bytes)}</span></div>)}</div>:null}
 </>;
}
