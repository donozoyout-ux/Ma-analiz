"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { CalendarDays, RefreshCw } from "lucide-react";
import { formatMatchTime, statusLabel } from "@/lib/matches";

type Fixture = {
  id: string; provider_match_id: string; league_id: string; league_name: string;
  home_team: string; away_team: string; start_time: string; status: string;
  score: [number, number] | null; source: string;
};
type Bulletin = {
  matches: Fixture[]; available_leagues: string[];
  failures: { league_id: string; reason: string }[];
  window_start: string; window_end: string;
  last_observed_at: string; live_guaranteed: boolean;
};
const LEAGUES = [
  { id: "all", name: "Tüm turnuvalar" },
  { id: "de.bl1", name: "Bundesliga" },
  { id: "uefa.cl", name: "Şampiyonlar Ligi" },
  { id: "uefa.el", name: "Avrupa Ligi" },
  { id: "uefa.ecl", name: "Konferans Ligi" },
];
const DISPLAY_LIMIT = 16;

export function SourceBulletin() {
  const [data, setData] = useState<Bulletin | null>(null);
  const [error, setError] = useState("");
  const [version, setVersion] = useState(0);
  const [league, setLeague] = useState("all");
  const [query, setQuery] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    fetch("/api/v1/source-bulletin", { signal: controller.signal, cache: "no-store" })
      .then(async response => {
        if (!response.ok) throw new Error("Kaynak bültenine erişilemedi");
        return response.json() as Promise<Bulletin>;
      })
      .then(value => {
        if (!Array.isArray(value.matches) || !Array.isArray(value.failures) ||
            !Array.isArray(value.available_leagues) || value.matches.length > 200 ||
            value.matches.some(row => typeof row.home_team !== "string" ||
              typeof row.away_team !== "string" || typeof row.start_time !== "string" ||
              typeof row.league_id !== "string")) throw new Error("Kaynak yanıtı doğrulanamadı");
        if (!controller.signal.aborted) setData(value);
      })
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Kaynak hatası"); });
    return () => controller.abort();
  }, [version]);

  const shown = useMemo(() => (data?.matches ?? []).filter(match =>
    (league === "all" || match.league_id === league) &&
    (!query.trim() || `${match.home_team} ${match.away_team}`.toLocaleLowerCase("tr-TR")
      .includes(query.trim().toLocaleLowerCase("tr-TR")))).slice(0, DISPLAY_LIMIT), [data, league, query]);
  const failedLeagues = data?.failures.map(row =>
    LEAGUES.find(item => item.id === row.league_id)?.name ?? row.league_id).join(", ");
  return <section className="card match-list-panel" aria-label="İnternetten gerçek maç bülteni">
    <div className="list-toolbar">
      <div><strong>Gerçek kaynak maç bülteni</strong>
        <span>OpenLigaDB · Son 3 gün ve gelecek 7 gün · 15 dakika önbellek</span>
      </div>
      <button className="quiet-button" type="button" onClick={() => setVersion(n => n + 1)}>
        <RefreshCw size={16}/> Yenile
      </button>
    </div>
    <div className="filter-grid center-filter-grid" style={{padding:"16px"}}>
      <label><span>Turnuva</span><select aria-label="Kaynak lig filtresi" value={league}
        onChange={event => setLeague(event.target.value)}>
        {LEAGUES.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select></label>
      <label><span>Takım ara</span><input value={query} placeholder="Takım adı"
        maxLength={80} onChange={event => setQuery(event.target.value)} /></label>
    </div>
    {error ? <div className="empty" role="alert"><strong>Veri kaynağına bağlanılamadı</strong><span>{error}. Sonuç uydurulmadı.</span></div> :
      !data ? <div className="center-loading" role="status"><span>OpenLigaDB fikstürü okunuyor…</span></div> :
      <><div style={{padding:"0 16px 12px"}}>
        {data.failures.length > 0 && <p className="scope-note" role="alert">
          Bazı liglerin kaynağı şu anda alınamadı: {failedLeagues}. Eksik ligler boş maç olarak sayılmaz.</p>}
        {shown.length === 0 ? <div className="empty"><CalendarDays size={25}/>
          <strong>Seçilen turnuva ve aralıkta kaynak maçı bulunamadı</strong>
          <span>Başka bir lig seçebilir veya <Link href="/europe">Avrupa maçlarının tarihini değiştirebilirsin</Link>.</span>
        </div> : <div className="records-scroll" tabIndex={0} role="region" aria-label="Kaynak futbol maçları">
          <table className="records-table"><thead><tr>
            <th>Tarih · TSİ</th><th>Turnuva</th><th>Karşılaşma</th><th>Kaynak durumu</th><th>Skor</th>
          </tr></thead><tbody>{shown.map(match => <tr key={match.id}>
            <td>{formatMatchTime(match.start_time)}</td>
            <td>{match.league_name}</td>
            <td>{match.home_team} – {match.away_team}</td>
            <td>{statusLabel(match.status)}</td>
            <td>{Array.isArray(match.score) ? `${match.score[0]} – ${match.score[1]}` : "—"}</td>
          </tr>)}</tbody></table>
        </div>}
      </div>
      <p className="scope-note" style={{padding:"0 16px 16px"}}>
        {data.matches.length} kaynak maçı · Son gözlem {formatMatchTime(data.last_observed_at)}.
        Skorlar anlık yayın garantisi taşımaz; kaynak gözlemidir.
        Bu bülten henüz SQL/R2 arşivi, doğrulanmış tahmin veya oran verisi değildir.
      </p></>}
  </section>;
}
