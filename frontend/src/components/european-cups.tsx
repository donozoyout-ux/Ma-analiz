"use client";

import { useEffect, useState } from "react";
import { CalendarDays, RefreshCw } from "lucide-react";
import { formatMatchTime, istanbulDay, statusLabel } from "@/lib/matches";

type Cup = { slug: string; name: string; league_id: string; shortcut: string; season: string };
type CupMatch = { provider_match_id: string; league_id: string; home_team: string; away_team: string;
  start_time: string; status: string; score: [number, number] | null };
type Catalog = { competitions: Cup[]; odds_available: boolean; attribution: string };
type DayResponse = { matches: CupMatch[]; league_name: string; attribution: string; odds_available: boolean };

const FALLBACK_CUPS: Cup[] = [
  { slug: "bundesliga", name: "Bundesliga", league_id: "de.bl1", shortcut: "bl1", season: "2026-2027" },
  { slug: "champions-league", name: "UEFA Champions League", league_id: "uefa.cl", shortcut: "ucl", season: "2026-2027" },
  { slug: "europa-league", name: "UEFA Europa League", league_id: "uefa.el", shortcut: "uel2026", season: "2026-2027" },
  { slug: "conference-league", name: "UEFA Conference League", league_id: "uefa.ecl", shortcut: "uecl2026", season: "2026-2027" },
];

export function EuropeanCups() {
  const [cups, setCups] = useState(FALLBACK_CUPS);
  const [slug, setSlug] = useState("bundesliga");
  const [day, setDay] = useState(() => istanbulDay(new Date()));
  const [rows, setRows] = useState<CupMatch[] | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/v1/competitions/europe", { signal: controller.signal })
      .then(response => response.ok ? response.json() as Promise<Catalog> : Promise.reject())
      .then(value => {
        if (Array.isArray(value.competitions) && value.competitions.length === 3) setCups([FALLBACK_CUPS[0], ...value.competitions]);
      })
      .catch(() => {});
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setRows(null); setError("");
    const apiPath = slug === "bundesliga" ? "competitions/bundesliga/matches" : `competitions/europe/${slug}/matches`;
    fetch(`/api/v1/${apiPath}?day=${encodeURIComponent(day)}`,
      { signal: controller.signal, cache: "no-store" })
      .then(async response => {
        if (response.status === 429) throw new Error("Kaynak çağrı sınırı doldu. Bir dakika sonra tekrar deneyebilirsin.");
        if (!response.ok) throw new Error("Turnuva verisine ulaşılamadı. Lütfen daha sonra tekrar dene.");
        return response.json() as Promise<DayResponse>;
      })
      .then(value => {
        if (!Array.isArray(value.matches) || value.matches.length > 150
          || value.matches.some(row => row.league_id !== cups.find(c => c.slug === slug)?.league_id)) {
          throw new Error("Kaynak verisi doğrulanamadı.");
        }
        if (!controller.signal.aborted) setRows(value.matches);
      })
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Veri alınamadı."); });
    return () => controller.abort();
  }, [slug, day, refresh]); // use only the currently selected competition, never mix sources

  return <div className="explorer-stack">
    <div className="card filter-panel">
      <div className="filter-heading"><span><CalendarDays size={17} /> Bundesliga ve Avrupa kupaları</span><span>2026–2027</span></div>
      <div className="filter-grid center-filter-grid">
        <label><span>Turnuva</span>
          <select aria-label="Avrupa turnuvası" value={slug} onChange={e => setSlug(e.target.value)}>
            {cups.map(cup => <option key={cup.slug} value={cup.slug}>{cup.name}</option>)}
          </select>
        </label>
        <label><span>Tarih · Türkiye saati</span>
          <input type="date" aria-label="Maç tarihi" min="2026-07-01" max="2027-06-30" value={day} onChange={e => setDay(e.target.value)} />
        </label>
        <button className="quiet-button center-submit" type="button" onClick={() => setRefresh(n => n + 1)}>
          <RefreshCw size={16} /> Yenile
        </button>
      </div>
    </div>
    <section className="card match-list-panel" aria-live="polite" aria-busy={!rows && !error}>
      <div className="list-toolbar"><div>
        <strong>{cups.find(c => c.slug === slug)?.name ?? "Avrupa turnuvası"}</strong>
        <span>{rows ? `${rows.length} maç bulundu` : "Veriler kontrol ediliyor"}</span>
      </div></div>
      {error ? <div className="empty" role="alert"><strong>{error}</strong></div>
      : !rows ? <div className="center-loading" role="status">OpenLigaDB maçları yükleniyor…</div>
      : rows.length === 0 ? <div className="empty"><CalendarDays size={28}/><strong>Bu tarihte kayıtlı maç bulunamadı</strong><span>Başka bir tarih deneyebilirsin.</span></div>
      : <div className="records-scroll" role="region" tabIndex={0} aria-label="Avrupa kupası maçları">
          <table className="records-table"><thead><tr><th>Tarih · TSİ</th><th>Maç</th><th>Durum</th><th>Kaynak skoru</th></tr></thead>
            <tbody>{rows.map(match => <tr key={match.provider_match_id}>
              <td>{formatMatchTime(match.start_time)}</td>
              <td>{match.home_team} – {match.away_team}</td>
              <td>{statusLabel(match.status)}</td>
              <td>{match.score ? `${match.score[0]} – ${match.score[1]}` : "Henüz yok"}</td>
            </tr>)}</tbody>
          </table>
        </div>}
    </section>
    <p className="scope-note">Veri: OpenLigaDB · ODbL 1.0. Skorlar tek kaynak gözlemidir, bağımsız doğrulama değildir. Oran verisi bu kaynakta yoktur. Bu ekran henüz PostgreSQL/R2 arşivi değil, salt okunur kaynak görünümüdür.</p>
  </div>;
}
