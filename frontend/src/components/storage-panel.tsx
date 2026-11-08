"use client";
import {useEffect,useState} from "react";
type TableSize={table:string;size_bytes:number;rows_estimate:number};
type Storage={postgres:{status:string;bytes:number|null;thresholds_mb:Record<string,number>;largest_tables:TableSize[]};r2:{status:string;bytes:number|null;object_count:number|null}};
const fmt=(n:number|null)=>n===null?"—":n<1048576?`${(n/1024).toFixed(1)} KB`:`${(n/1048576).toFixed(1)} MB`;
export function StoragePanel(){
  const [data,setData]=useState<Storage|null>(null);
  useEffect(()=>{fetch(`/api/v1/storage`).then(r=>r.ok?r.json():Promise.reject()).then(setData).catch(()=>setData(null));},[]);
  return <>
    <div className="health-row"><span className="health-name">PostgreSQL usage</span><span className="badge">{data?.postgres.status??"UNAVAILABLE"}</span></div>
    <div className="storage-bar"><div className="storage-fill"/></div>
    <div className="storage-meta"><span>{fmt(data?.postgres.bytes??null)} used</span><span>{data?`${data.postgres.thresholds_mb.critical} MB critical`:"Thresholds unavailable"}</span></div>
    <div className="health-row" style={{marginTop:12}}><span className="health-name">R2 historical archive</span><span className="badge">{data?.r2.status??"UNAVAILABLE"}</span></div>
    <div className="storage-meta"><span>{fmt(data?.r2.bytes??null)}</span><span>{data?.r2.object_count===null||data?.r2.object_count===undefined?"Object count unavailable":`${data.r2.object_count} objects`}</span></div>
    {data?.postgres.largest_tables?.length ? <div style={{marginTop:18}}><h3>Largest PostgreSQL tables</h3>{data.postgres.largest_tables.map(row=><div className="health-row" key={row.table}><span className="health-name">{row.table} · {row.rows_estimate.toLocaleString()} rows</span><span className="badge">{fmt(row.size_bytes)}</span></div>)}</div>:null}
  </>;
}
