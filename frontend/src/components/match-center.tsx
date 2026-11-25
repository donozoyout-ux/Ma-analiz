"use client";
import { useState } from "react";
import Link from "next/link";
import { CalendarDays, Goal, LayoutGrid, List, RefreshCw, Search } from "lucide-react";
import { type Match, type MatchFilters, formatMatchTime, matchHref, matchQuery, statusLabel, verifiedScoreText } from "@/lib/matches";
import { useMatchRead } from "@/lib/use-match-read";
import { MatchCard } from "./match-card";
import { SourceBulletin } from "./source-bulletin";

type Page = {items:Match[]; next_cursor:string|null; as_of:string; history_status:string; warnings:string[];
 scope:{scanned:number; limit:number; start_utc:string; end_utc:string}};
export function MatchCenter({initialSource="all"}:{initialSource?:MatchFilters["source"]}) {
 const defaults:MatchFilters = {day:"",league:"",team:"",status:"",source:initialSource};
 const [filters,setFilters] = useState(defaults), [draft,setDraft] = useState(defaults);
 const [cursors,setCursors] = useState<(string|undefined)[]>([undefined]);
 const [page,setPage] = useState(0), [cards,setCards] = useState(true);
 const {data,error,refresh} = useMatchRead<Page>(matchQuery(filters,cursors[page]));
 const rows = data && Array.isArray(data.items) ? data.items : null;
 const swapKey = `${cards ? "cards" : "table"}-${page}-${filters.day}-${filters.league}-${filters.team}-${filters.status}-${filters.source}-${error ? "error" : rows ? rows.length : "loading"}`;
 const apply = (next:MatchFilters) => {setFilters(next);setDraft(next);setCursors([undefined]);setPage(0);};
 const reload = () => {setCursors([undefined]);setPage(0);refresh();};
 return <div className="explorer-stack match-center">
  {initialSource !== "history" ? <>
    <SourceBulletin />
    <p className="scope-note">Kaynakta bugün, canlı ve yaklaşan karşılaşmalar üstte gösterilir. Önceki sonuçlar ve doğrulanmış SQL/R2 kayıtları için <Link href="/archive">geçmiş maç arşivine git</Link>. Oranlar ve istatistikler yalnız doğrulanmış kaynaklardan gösterilir.</p>
  </> : <p className="scope-note">Bu bölüm yalnız PostgreSQL ve doğrulanmış R2 arşivini gösterir. Güncel maçlar için <Link href="/matches">Maç Merkezi</Link> sayfasını kullan.</p>}
  {initialSource === "history" && <>
  <form className="card filter-panel" onSubmit={event=>{event.preventDefault();apply(draft);}}>
   <div className="filter-heading"><span><Search size={17}/> Maçları bul</span><button className="quiet-button" type="button" onClick={()=>apply(defaults)}>Sıfırla</button></div>
   <div className="filter-grid center-filter-grid">
    <label><span>Tarih · Türkiye saati</span><input type="date" min="2000-01-01" max="2100-12-31" value={draft.day} onChange={e=>setDraft({...draft,day:e.target.value})}/></label>
    <label><span>Lig</span><select value={draft.league} onChange={e=>setDraft({...draft,league:e.target.value})}><option value="">Tüm ligler</option><option value="de.bl1">Bundesliga</option><option value="en.pl">Premier League</option><option value="tr.sl">Süper Lig</option></select></label>
    <label><span>Takım adı</span><input maxLength={80} placeholder="Takım ara…" value={draft.team} onChange={e=>setDraft({...draft,team:e.target.value})}/></label>
    <label><span>Maç durumu</span><select value={draft.status} onChange={e=>setDraft({...draft,status:e.target.value})}><option value="">Tüm durumlar</option><option value="scheduled">Planlanan</option><option value="in_progress">Devam eden</option><option value="finished">Tamamlanan</option></select></label>
    <label><span>Kayıt kapsamı</span><select value={draft.source} onChange={e=>setDraft({...draft,source:e.target.value as MatchFilters["source"]})}><option value="all">Aktif ve tarihsel · tek liste</option><option value="active">Aktif · PostgreSQL</option><option value="history">Tarihsel · doğrulanmış arşiv</option></select></label>
    <button className="quiet-button center-submit" type="submit"><Search size={16}/> Filtrele</button>
   </div>
  </form>
  <section className="card match-list-panel" aria-label="Maç kayıtları" aria-busy={!data&&!error}>
   <div className="list-toolbar"><div><strong>{filters.source === "history" ? "Doğrulanmış tarihsel maçlar" : "Maç bülteni"}</strong><span>{rows ? `Bu sayfada ${rows.length} maç · ${filters.day ? "Seçili tarih" : filters.source === "history" ? "Son 120 gün" : "Son 31 gün"}` : "Kayıtlar denetleniyor"}</span></div>
    <div className="toolbar-buttons"><button className={`icon-link ${cards?"selected":""}`} aria-label="Kart görünümü" aria-pressed={cards} onClick={()=>setCards(true)}><LayoutGrid size={17}/></button><button className={`icon-link ${!cards?"selected":""}`} aria-label="Tablo görünümü" aria-pressed={!cards} onClick={()=>setCards(false)}><List size={17}/></button><button className="icon-link" aria-label="Maçları yenile" onClick={reload}><RefreshCw size={17}/></button></div>
   </div>
   <div key={swapKey} className="results-swap">{error ? <div className="empty" role="alert"><Goal size={32}/><strong>Maç verisine ulaşılamadı</strong><span>SQL veya doğrulanmış arşiv kaynağı şu an okunamıyor. Sonuç üretilmedi.</span><button className="quiet-button" onClick={reload}>Yeniden dene</button></div>
    : !rows ? <div className="center-loading" role="status"><span>Maç kayıtları yükleniyor…</span><div aria-hidden="true" className="match-cards">{[0,1,2].map(i=><div className="match-skeleton" key={i}/>)}</div></div>
    : rows.length===0 ? <div className="empty"><CalendarDays size={32}/><strong>Bu kapsamda maç bulunamadı</strong><span>{filters.day ? "Başka bir tarih veya kapsam seçebilirsiniz." : filters.source === "history" ? "Son 120 günde bu filtrelere uyan doğrulanmış arşiv kaydı yok. Daha eski bir gün seçebilirsiniz." : "Son 31 günde bu filtrelere uyan doğrulanmış kayıt yok. Daha eski bir gün seçebilirsiniz."}</span>{data?.next_cursor && <span>Arama sınırı nedeniyle sonraki sayfayı da inceleyebilirsiniz.</span>}</div>
    : cards ? <div className="match-cards">{rows.map(match=><MatchCard key={match.id} match={match}/>)}</div>
    : <div className="records-scroll" tabIndex={0} role="region" aria-label="Maç ve oran tablosu"><table className="records-table"><caption className="sr-only">Türkiye saatine göre maçlar ve doğrulanmış sonuçlar</caption><thead><tr>{["Başlangıç · TSİ","Karşılaşma","Lig","Doğrulanmış skor","Durum","Kapsam"].map(label=><th scope="col" key={label}>{label}</th>)}</tr></thead><tbody>{rows.map(match=><tr key={match.id}><td>{formatMatchTime(match.start_time)}</td><td><Link className="teams-link" href={matchHref(match)}>{match.home_team}<br/>{match.away_team}</Link></td><td>{match.league_name??"Lig belirtilmedi"}</td><td>{verifiedScoreText(match.result)??"Skor henüz doğrulanmadı"}</td><td>{statusLabel(match.status)}</td><td>{match.storage==="R2"?"Doğrulanmış arşiv":"Aktif kayıt"}<small className="table-secondary">{formatMatchTime(match.last_updated_at??match.result?.verified_at)}</small></td></tr>)}</tbody></table></div>}</div>
  </section>
  {data?.warnings.length ? <div className="center-notice" role="alert">Bazı arşiv kayıtları doğrulanamadığı için gösterilmedi. <button className="quiet-button" onClick={reload}>Yeniden dene</button></div>:null}
  <nav className="match-pagination" aria-label="Maç sayfaları"><button className="quiet-button" disabled={page===0||!rows} onClick={()=>setPage(page-1)}>Önceki</button><span aria-live="polite">Sayfa {page+1}</span><button className="quiet-button" disabled={!rows||!data?.next_cursor} onClick={()=>{setCursors([...cursors.slice(0,page+1),data!.next_cursor!]);setPage(page+1);}}>Sonraki</button></nav>
  <p className="scope-note">{data ? `Okuma zamanı: ${formatMatchTime(data.as_of)} · ` : ""}Saatler Türkiye saatidir. Aktif havuz sınırlıdır; tarih seçilmezse arşivde son 120 gün, diğer kapsamda son 31 gün taranır. Arşivde yalnız bu sayfa için doğrulanan parçalar okunur. Kayıtlar UUID ile tekilleştirilir; arşiv kopyası SQL kaydı henüz temizlenmeden de bulunabilir. Canlı skor garantisi veya otomatik yenileme yoktur.</p>
  <p className="scope-note">Bundesliga kayıtları mevcut kaynak politikalarıyla gösterilir. Premier League karantinada, Süper Lig için uygun kaynak mevcut değil; başka lig verileriyle doldurulmaz.</p>
  </>}
 </div>;
}
