"use client";
import {useEffect,useState} from "react";
type Row=Record<string,unknown>;
export function RecordsPanel({endpoint,columns,empty}:{endpoint:string;columns:string[];empty:string}){
 const [rows,setRows]=useState<Row[]|null>(null);const [error,setError]=useState<string|null>(null);
 useEffect(()=>{fetch(`/api/v1/${endpoint}`).then(async r=>{if(!r.ok){let body:Row={};try{body=await r.json()}catch{};throw new Error(String(body.detail||`API_${r.status}`))}return r.json()}).then(setRows).catch(e=>setError(e.message));},[endpoint]);
 if(error)return <div className="card empty">{error.replaceAll("_"," ")}</div>;
 if(rows===null)return <div className="card empty">Loading verified records…</div>;
 if(rows.length===0)return <div className="card empty">{empty}</div>;
 const label=(value:unknown)=>value===null||value===undefined?"—":typeof value==="object"?JSON.stringify(value):String(value);
 return <div className="card" style={{overflowX:"auto"}}><table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}><thead><tr>{columns.map(c=><th key={c} style={{textAlign:"left",padding:"10px 12px",color:"#8492a8",fontWeight:500,borderBottom:"1px solid #202d40"}}>{c.replaceAll("_"," ").toUpperCase()}</th>)}</tr></thead><tbody>{rows.map((row,i)=><tr key={String(row.id??row.date??i)}>{columns.map(c=><td key={c} style={{padding:"12px",borderBottom:"1px solid #1c293a",color:"#c5cfdd",whiteSpace:"nowrap"}}>{label(row[c])}</td>)}</tr>)}</tbody></table></div>
}
