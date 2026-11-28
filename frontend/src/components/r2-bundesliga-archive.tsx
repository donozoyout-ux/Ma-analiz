"use client";

import { useEffect, useState } from "react";

type ArchiveMatch = {
  id: string;
  date: string;
  home_team: string;
  away_team: string;
  round: string;
  status: string;
  regulation: [number, number] | null;
  verification: string;
};
type ArchiveResponse = {
  season: string;
  league: string;
  source: string;
  total: number;
  page: number;
  page_size: number;
  matches: ArchiveMatch[];
};

const SEASONS = [
  { id: "2024-2025", name: "2024–25" },
  { id: "2025-2026", name: "2025–26" },
  { id: "2026-2027", name: "2026–27 · 28 Kasım'a kadar" },
];

export function R2BundesligaArchive() {
  const [season, setSeason] = useState("2024-2025");
  const [team, setTeam] = useState("");
  const [page, setPage] = useState(1);
  const [report, setReport] = useState<ArchiveResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(false);
    setReport(null);
    const params = new URLSearchParams({ season, team, page: String(page), page_size: "25" });
    fetch(`/api/v1/archive/r2/bundesliga?${params}`, {
      cache: "no-store", signal: controller.signal,
    })
      .then(response => {
        if (!response.ok) throw new Error("R2 archive unavailable");
        return response.json() as Promise<ArchiveResponse>;
      })
      .then(data => {
        if (data.season !== season || data.league !== "de.bl1" ||
            data.source !== "R2" || !Array.isArray(data.matches)) {
          throw new Error("Invalid R2 archive");
        }
        if (!controller.signal.aborted) setReport(data);
      })
      .catch(() => { if (!controller.signal.aborted) setError(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [season, team, page]);

  const total = report?.total ?? 0;
  return <section className="card match-list-panel" aria-label="Bundesliga R2 maç arşivi">
    <div className="list-toolbar">
      <div>
        <strong>Bundesliga · Doğrulanmış R2 arşivi</strong>
        <span>{loading ? "Arşiv okunuyor…" : error ? "Arşive ulaşılamıyor" : `${total} maç bulundu`}</span>
      </div>
      <span className="today-chip">OpenFootball · CC0</span>
    </div>
    <div className="filter-grid center-filter-grid">
      <label>
        <span>Sezon</span>
        <select aria-label="Bundesliga sezonu" value={season}
          onChange={event => { setSeason(event.target.value); setPage(1); }}>
          {SEASONS.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      </label>
      <label>
        <span>Takım ara</span>
        <input aria-label="Bundesliga arşivinde takım ara" placeholder="Bayern, Dortmund…"
          value={team} onChange={event => { setTeam(event.target.value); setPage(1); }} />
      </label>
    </div>
    {loading ? <p role="status">R2 manifesti ve maç dosyası SHA-256 ile doğrulanıyor…</p> :
     error ? <p role="alert">Bundesliga R2 arşivine erişilemiyor. Dosya doğrulaması veya sunucu bağlantısı başarısız olabilir.</p> :
     report && report.matches.length === 0 ? <p>Bu filtrede maç bulunamadı.</p> :
     <div className="records-scroll" role="region" tabIndex={0} aria-label="Bundesliga maç sonuçları">
       <table className="records-table">
         <thead><tr><th>Tarih</th><th>Hafta</th><th>Maç</th><th>90 dakika</th><th>Durum</th></tr></thead>
         <tbody>{report?.matches.map(match => <tr key={match.id}>
           <td>{match.date}</td>
           <td>{match.round}</td>
           <td>{match.home_team} – {match.away_team}</td>
           <td>{match.regulation ? `${match.regulation[0]} – ${match.regulation[1]}` : "—"}</td>
           <td>{match.regulation ? "Kaynak skoru" : "Skor belirsiz"}</td>
         </tr>)}</tbody>
       </table>
     </div>}
    {!error && !loading && total > 25 && <div className="list-toolbar">
      <button type="button" className="quiet-button" disabled={page <= 1}
        onClick={() => setPage(current => current - 1)}>Önceki</button>
      <span>Sayfa {page} / {Math.ceil(total / 25)}</span>
      <button type="button" className="quiet-button" disabled={page * 25 >= total}
        onClick={() => setPage(current => current + 1)}>Sonraki</button>
    </div>}
    <p className="scope-note">Kaynak: OpenFootball · CC0. Skorlar kaynak tarafından bildirildi; bağımsız doğrulama yapılmadı. Oran verisi yoktur. R2 salt okunur; PostgreSQL kullanılmaz.</p>
  </section>;
}
