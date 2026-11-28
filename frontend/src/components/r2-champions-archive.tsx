"use client";

import { useEffect, useState } from "react";

type Match = {
  id: string;
  date: string;
  kickoff_utc: string | null;
  home_team: string;
  away_team: string;
  status: string;
  regulation: [number, number] | null;
  round: string;
  verification: string;
};
type Archive = {
  league: string;
  season: string;
  source: string;
  page: number;
  page_size: number;
  total: number;
  matches: Match[];
};

export function R2ChampionsArchive() {
  const [team, setTeam] = useState("");
  const [page, setPage] = useState(1);
  const [report, setReport] = useState<Archive | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(false);
    const params = new URLSearchParams({ page: String(page), page_size: "25", team });
    fetch(`/api/v1/archive/r2/champions-league?${params}`, {
      signal: controller.signal, cache: "no-store",
    })
      .then(response => {
        if (!response.ok) throw new Error("R2 arşivine ulaşılamadı");
        return response.json() as Promise<Archive>;
      })
      .then(result => {
        if (result.league !== "uefa.cl" || result.season !== "2024-2025" ||
            result.source !== "R2" || !Array.isArray(result.matches)) {
          throw new Error("Arşiv doğrulanamadı");
        }
        if (!controller.signal.aborted) setReport(result);
      })
      .catch(() => { if (!controller.signal.aborted) setError(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [page, team]);

  const count = report?.total ?? 0;
  return <section className="card match-list-panel" aria-label="R2 Şampiyonlar Ligi arşivi">
    <div className="list-toolbar"><div>
      <strong>Şampiyonlar Ligi 2024–25 · R2 arşivi</strong>
      <span>{loading ? "Arşiv doğrulanıyor…" : error ? "Arşive erişilemiyor" : `${count} maç bulundu`}</span>
    </div></div>
    <div className="filter-grid center-filter-grid">
      <label><span>Takım ara</span><input aria-label="Arşivde takım ara"
        placeholder="Takım adı" value={team} onChange={event => { setPage(1); setTeam(event.target.value); }} /></label>
    </div>
    {loading ? <p role="status">R2 dosyası doğrulanıyor…</p> :
     error ? <p role="alert">R2 arşivine şu anda erişilemiyor. Arşiv yüklenmemiş veya servis R2'ye bağlanamıyor olabilir.</p> :
     report && report.matches.length === 0 ? <p>Bu filtre için maç bulunamadı.</p> :
     <div className="records-scroll" role="region" tabIndex={0} aria-label="Arşiv maç tablosu">
       <table className="records-table">
         <thead><tr><th>Tarih</th><th>Tur</th><th>Maç</th><th>90 dakika skoru</th></tr></thead>
         <tbody>{report?.matches.map(match => <tr key={match.id}>
           <td>{match.date}</td><td>{match.round}</td>
           <td>{match.home_team} – {match.away_team}</td>
           <td>{match.regulation ? `${match.regulation[0]} – ${match.regulation[1]}` : "—"}</td>
         </tr>)}</tbody>
       </table>
     </div>}
    {!error && !loading && count > 25 && <div className="list-toolbar">
      <button type="button" className="quiet-button" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>Önceki</button>
      <span>Sayfa {page} / {Math.ceil(count / 25)}</span>
      <button type="button" className="quiet-button" disabled={page * 25 >= count} onClick={() => setPage(p => p + 1)}>Sonraki</button>
    </div>}
    <p className="scope-note">Kaynak: OpenFootball · CC0. R2'den SHA-256 doğrulamasıyla okunur. Oran verisi içermez; PostgreSQL kullanılmaz.</p>
  </section>;
}
