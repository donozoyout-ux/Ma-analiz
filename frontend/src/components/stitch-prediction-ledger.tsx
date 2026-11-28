"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useApiList } from "@/lib/use-api-list";
import { formatMatchTime, type Match } from "@/lib/matches";

type Prediction = {id:string; match_id:string; market:string; selection:string; status:string; model_version:string; created_at:string; analysis_timestamp?:string; settled_at?:string|null};
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const text = (value: unknown) => typeof value === "string" && value.trim() ? value : "—";

/** Active records have no immutable publication evidence and must not be called a ledger. */
export function StitchPredictionLedger() {
 const predictions = useApiList<Prediction>("predictions?limit=500");
 const matches = useApiList<Match>("matches?limit=500");
 const [market,setMarket] = useState(""); const [model,setModel] = useState("");
 const [status,setStatus] = useState(""); const [league,setLeague] = useState("");
 const [start,setStart] = useState(""); const [end,setEnd] = useState("");
 const byId = useMemo(() => new Map((matches.rows ?? []).filter(x=>x && typeof x.id === "string").map(x=>[x.id,x])), [matches.rows]);
 const valid = (predictions.rows ?? []).filter(p=>p && typeof p === "object" && typeof p.match_id === "string");
 const options = (values:unknown[]) => [...new Set(values.filter((v):v is string => typeof v === "string" && !!v.trim()))].sort();
 const shown = valid.filter(p=> {
  const m = byId.get(p.match_id);
  const day = typeof p.created_at === "string" && Number.isFinite(Date.parse(p.created_at)) ? new Intl.DateTimeFormat("sv-SE", {timeZone:"Europe/Istanbul"}).format(new Date(p.created_at)) : "";
  return (!market || p.market === market) && (!model || p.model_version === model) && (!status || p.status === status) && (!league || m?.league_name === league) && (!start || (!!day && day >= start)) && (!end || (!!day && day <= end));
 });
 const loaded = predictions.rows !== null && matches.rows !== null && !predictions.error && !matches.error;
 return <section className="explorer-stack" aria-labelledby="temporary-predictions">
  <div className="card"><h2 id="temporary-predictions" className="section-title">Tahmin kayıtları</h2><p className="scope-note">Mevcut geçici kayıtlarla sınırlı; yayın doğrulaması yok. Kayıt zamanı yayın zamanı değildir. En fazla 500 aktif kayıt ve 500 maç okunur; geçmiş kapsamı eksik olabilir.</p>
   <div className="grid performance-kpis">{["Yayınlanmış tahmin", "Sonuçlanan", "Tutan", "Tutmayan", "Bekleyen", "İptal / void", "Yayın isabeti"].map(label=><div className="card" key={label}><div className="kpi-label">{label}</div><div className="kpi-value">—</div></div>)}</div>
   <p className="scope-note">Yayın defteri ve uygun oran kanıtı bulunmadığından lig/pazar/model kırılımları, kalibrasyon ve oran grafiği doğrulanmış performans olarak hesaplanamaz. Doğrulanmış oran verisi bulunmuyor.</p>
  </div>
  <div className="card filter-panel"><div className="filter-grid center-filter-grid">
   <label><span>Başlangıç · TSİ</span><input type="date" value={start} onChange={e=>setStart(e.target.value)}/></label>
   <label><span>Bitiş · TSİ</span><input type="date" value={end} onChange={e=>setEnd(e.target.value)}/></label>
   {[{label:"Lig",value:league,set:setLeague,values:options([...byId.values()].map(x=>x.league_name))},{label:"Pazar",value:market,set:setMarket,values:options(valid.map(x=>x.market))},{label:"Model sürümü",value:model,set:setModel,values:options(valid.map(x=>x.model_version))},{label:"Durum",value:status,set:setStatus,values:options(valid.map(x=>x.status))}].map(f=><label key={f.label}><span>{f.label}</span><select value={f.value} onChange={e=>f.set(e.target.value)}><option value="">Tümü</option>{f.values.map(v=><option key={v} value={v}>{v}</option>)}</select></label>)}
  </div></div>
  {predictions.error || matches.error ? <div className="card empty" role="alert">Kayıtlar okunamadı. <button className="quiet-button" onClick={()=>{predictions.refresh();matches.refresh();}}>Yeniden dene</button></div> : !loaded ? <div className="card empty" role="status">Tahmin kayıtları yükleniyor…</div> : !shown.length ? <div className="card empty">Bu filtrelerde geçici tahmin kaydı bulunamadı.</div> : <div className="card records-scroll" tabIndex={0} role="region" aria-label="Geçici tahmin kayıtları"><p className="scope-note">Gösterilen kayıt: {shown.length}; yayın kanıtı olan örneklem: bilinmiyor.</p><table className="records-table"><caption className="sr-only">Yayın kanıtı bulunmayan geçici tahmin kayıtları</caption><thead><tr>{["Maç", "Pazar", "Seçim", "Kayıt · TSİ", "Yayın", "Model", "Oran", "Durum", "Sonuç zamanı"].map(x=><th key={x} scope="col">{x}</th>)}</tr></thead><tbody>{shown.map((p,i)=>{const m=byId.get(p.match_id); const label=m ? `${m.home_team} – ${m.away_team}` : "Maç kaydı bulunamadı";return <tr key={p.id || `${p.match_id}-${i}`}><td>{m && UUID.test(p.match_id) ? <Link href={`/matches/${encodeURIComponent(p.match_id)}`}>{label}</Link> : label}</td><td>{text(p.market)}</td><td>{text(p.selection)}</td><td>{formatMatchTime(p.created_at)}</td><td>Doğrulanmadı</td><td>{text(p.model_version)}</td><td>Doğrulanmış oran yok</td><td>{text(p.status)}</td><td>{formatMatchTime(p.settled_at)}</td></tr>})}</tbody></table></div>}
 </section>;
}
