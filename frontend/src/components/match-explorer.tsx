"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { Search, SlidersHorizontal, RefreshCw, List, LayoutGrid, Goal } from "lucide-react";
import { filterMatches, istanbulDay, statusLabel, type Match } from "@/lib/matches";
import { MatchOdds } from "./match-odds";
export function MatchExplorer({ mode = "today", compact = false }: { mode?: "today" | "archive" | "odds"; compact?: boolean }) {
 const [rows, setRows] = useState<Match[] | null>(null), [error, setError] = useState(false), [reload, setReload] = useState(0);
 const [query, setQuery] = useState(""), [league, setLeague] = useState(""), [status, setStatus] = useState(""), [date, setDate] = useState("");
 const [cards, setCards] = useState(false), [selected, setSelected] = useState("");
 useEffect(() => { if (mode === "today") setDate(istanbulDay(new Date())); }, [mode]);
 useEffect(() => {
  const c = new AbortController(); setRows(null); setError(false);
  fetch("/api/v1/matches?limit=500", { signal: c.signal, cache: "no-store" }).then(async r => { if (!r.ok) throw new Error(); const d = await r.json(); if (!Array.isArray(d)) throw new Error(); return d; }).then(setRows).catch(e => { if (e.name !== "AbortError") setError(true); });
  return () => c.abort();
 }, [reload]);
 const filtered = filterMatches(rows ?? [], { query, league, status, date, archive: mode === "archive" });
 const visible = compact ? filtered.slice(0, 5) : filtered;
 const leagues = [...new Set((rows ?? []).map(r => r.league_name).filter((x): x is string => Boolean(x)))].sort();
 const action = (m: Match) => mode === "odds" ? <button className="quiet-button" onClick={() => setSelected(m.id)}>Oran analizi</button> : <Link className="text-link" href={`/matches/${m.id}`}>İncele</Link>;
 return <div className="explorer-stack">
 {!compact && <div className="card filter-panel"><div className="filter-heading"><span><SlidersHorizontal size={17} /> Maçları filtrele</span><button className="quiet-button" onClick={() => { setQuery(""); setLeague(""); setStatus(""); setDate(mode === "today" ? istanbulDay(new Date()) : ""); }}>Sıfırla</button></div><div className="filter-grid">
 <label className="search-field"><span>Takım veya lig ara</span><div><Search size={17} /><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Takım adı yazın…" /></div></label>
 <label><span>Lig</span><select value={league} onChange={e => setLeague(e.target.value)}><option value="">Tüm ligler</option>{leagues.map(l => <option key={l}>{l}</option>)}</select></label>
 <label><span>Durum</span><select value={status} onChange={e => setStatus(e.target.value)}><option value="">Tüm durumlar</option>{[...new Set((rows ?? []).map(r => r.status))].map(s => <option key={s} value={s}>{statusLabel(s)}</option>)}</select></label>
 <label><span>Tarih · İstanbul</span><input type="date" value={date} onChange={e => setDate(e.target.value)} /></label></div></div>}
 <section className="card match-list-panel"><div className="list-toolbar"><div><strong>{mode === "archive" ? "Tamamlanan karşılaşmalar" : mode === "odds" ? "Analiz için maç seçin" : "Maç bülteni"}</strong><span>{rows ? `${filtered.length} maç · En fazla 500 aktif kayıt` : "Sağlayıcı kayıtları"}</span></div><div className="toolbar-buttons">{!compact && <><button aria-label="Tablo görünümü" aria-pressed={!cards} className={`icon-link ${!cards ? "selected" : ""}`} onClick={() => setCards(false)}><List size={17} /></button><button aria-label="Kart görünümü" aria-pressed={cards} className={`icon-link ${cards ? "selected" : ""}`} onClick={() => setCards(true)}><LayoutGrid size={17} /></button></>}<button className="icon-link" aria-label="Maçları yenile" onClick={() => setReload(x => x + 1)}><RefreshCw size={16} /></button></div></div>
 {error ? <div className="empty" role="alert"><Goal size={30} /><strong>Maç verisine ulaşılamadı</strong><span>API bağlantısını kontrol edin veya yeniden deneyin.</span><button className="quiet-button" onClick={() => setReload(x => x + 1)}>Yeniden dene</button></div> : rows === null ? <div className="empty" role="status">Maç kayıtları yükleniyor…</div> : !visible.length ? <div className="empty"><Goal size={34} /><strong>Bu görünümde maç bulunamadı</strong><span>{rows.length ? "Diğer kayıtlar için filtreleri değiştirebilirsiniz." : "Sağlayıcıdan maç kaydı geldiğinde burada listelenecek."}</span></div> : cards ? <div className="match-cards">{visible.map(m => <article className="match-tile" key={m.id}><div className="tile-meta"><span>{m.league_name ?? "Lig belirtilmedi"}</span><span className="badge">{statusLabel(m.status)}</span></div><h3>{m.home_team}<small>karşı</small>{m.away_team}</h3><p>{new Date(m.start_time).toLocaleString("tr-TR", { timeZone: "Europe/Istanbul" })}</p>{action(m)}</article>)}</div> : <div className="records-scroll"><table className="records-table"><thead><tr><th>Başlangıç</th><th>Karşılaşma</th><th>Lig</th><th>Durum</th><th><span className="sr-only">İşlem</span></th></tr></thead><tbody>{visible.map(m => <tr key={m.id}><td><span className="match-time">{new Date(m.start_time).toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Istanbul" })}</span><small className="table-secondary">{istanbulDay(m.start_time)}</small></td><td><Link href={`/matches/${m.id}`} className="teams-link"><span>{m.home_team}</span><span>{m.away_team}</span></Link></td><td>{m.league_name ?? "—"}</td><td><span className={`badge ${m.status.toUpperCase() === "LIVE" ? "ok" : ""}`}>{statusLabel(m.status)}</span></td><td>{action(m)}</td></tr>)}</tbody></table></div>}</section>
 {!compact && <p className="scope-note">{mode === "archive" ? "Aktif havuzda tutulan tamamlanmış maçlar gösterilir. R2 tarihsel arşivi için listeleme API’si henüz mevcut değil." : "Saatler Europe/Istanbul dilimindedir. Durumlar son sağlayıcı kaydını gösterir; canlı veri garantisi değildir."}</p>}
 {mode === "odds" && selected && <section aria-label="Seçili maçın oran analizi"><MatchOdds key={selected} matchId={selected} /></section>}</div>;
}
