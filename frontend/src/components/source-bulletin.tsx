"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { CalendarDays, RefreshCw } from "lucide-react";
import { formatMatchTime, istanbulDay, statusLabel } from "@/lib/matches";

type Fixture = {
  id: string; provider_match_id: string; league_id: string; league_name: string;
  home_team: string; away_team: string; start_time: string; status: string;
  score: [number, number] | null; source: string;
};
type Bulletin = {
  matches: Fixture[]; available_leagues: string[];
  failures: { league_id: string; reason: string }[];
  window_start: string; window_end: string; last_observed_at: string;
  live_guaranteed: boolean;
};
const LEAGUES = [
  { id: "all", name: "Tüm turnuvalar" },
  { id: "de.bl1", name: "Bundesliga" },
  { id: "uefa.cl", name: "Şampiyonlar Ligi" },
  { id: "uefa.el", name: "Avrupa Ligi" },
  { id: "uefa.ecl", name: "Konferans Ligi" },
];
const LIMIT = 40;
const isFinished = (status: string) => ["FINISHED", "FT", "FINAL", "COMPLETED"].includes(status.toUpperCase());
const isLive = (status: string) => ["LIVE", "IN_PROGRESS", "IN_PLAY", "HT", "PAUSED"].includes(status.toUpperCase());
const isScheduled = (status: string) => ["SCHEDULED", "NS", "NOT_STARTED"].includes(status.toUpperCase());

export function SourceBulletin({variant = "full"}: {variant?: "full" | "home"}) {
  const [data, setData] = useState<Bulletin | null>(null);
  const [error, setError] = useState("");
  const [version, setVersion] = useState(0);
  const [league, setLeague] = useState("all");
  const [status, setStatus] = useState("upcoming");
  const [day, setDay] = useState("");
  const [query, setQuery] = useState("");
  const [today, setToday] = useState("");
  useEffect(() => {
    const update = () => setToday(istanbulDay(new Date()));
    update();
    const interval = setInterval(update, 60_000);
    return () => clearInterval(interval);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    fetch("/api/v1/source-bulletin", {signal: controller.signal, cache: "no-store"})
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
      .catch(reason => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Kaynak hatası");
      });
    return () => controller.abort();
  }, [version]);

  const summary = useMemo(() => {
    const all = data?.matches ?? [];
    return {
      live: all.filter(row => isLive(row.status) && istanbulDay(row.start_time) === today).length,
      todayUpcoming: all.filter(row => isScheduled(row.status) && istanbulDay(row.start_time) === today).length,
      future: all.filter(row => isScheduled(row.status) && istanbulDay(row.start_time) > today).length,
    };
  }, [data, today]);

  const shown = useMemo(() => {
    const text = query.trim().toLocaleLowerCase("tr-TR");
    const rows = (data?.matches ?? []).filter(match => {
      const matchDay = istanbulDay(match.start_time);
      if (league !== "all" && match.league_id !== league) return false;
      if (day && matchDay !== day) return false;
      if (text && !`${match.home_team} ${match.away_team}`.toLocaleLowerCase("tr-TR").includes(text)) return false;
      if (status === "live") return isLive(match.status);
      if (status === "scheduled") return isScheduled(match.status) && matchDay >= today;
      if (status === "finished") return isFinished(match.status);
      if (status === "upcoming") return matchDay >= today && (isLive(match.status) || isScheduled(match.status));
      return true;
    });
    // Live fixtures first, then today's and future fixtures. Past results appear only
    // after the user explicitly switches the status filter.
    rows.sort((a,b) => {
      if (status === "upcoming" || status === "live") {
        const live = Number(isLive(b.status)) - Number(isLive(a.status));
        if (live) return live;
      }
      return a.start_time.localeCompare(b.start_time);
    });
    return rows.slice(0, LIMIT);
  }, [data, day, league, query, status, today]);
  const failedLeagues = data?.failures.map(row =>
    LEAGUES.find(item => item.id === row.league_id)?.name ?? row.league_id).join(", ");
  return <section className="card match-list-panel" aria-label="Günlük ve yaklaşan futbol maçları">
    <div className="list-toolbar">
      <div><strong>{variant === "home" ? "Bugün ve yaklaşan maçlar" : "Maç bülteni · canlı ve yaklaşan"}</strong>
        <span>OpenLigaDB · Türkiye saati · 15 dakika kaynak önbelleği · anlık yayın garantisi yok</span>
      </div>
      <button className="quiet-button" type="button" onClick={() => setVersion(n => n + 1)}>
        <RefreshCw size={16}/> Yenile
      </button>
    </div>
    <div className="filter-grid center-filter-grid" style={{padding:"16px"}}>
      <label><span>Lig / kupa</span><select aria-label="Kaynak lig filtresi" value={league}
        onChange={event => setLeague(event.target.value)}>
        {LEAGUES.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select></label>
      <label><span>Maç durumu</span><select aria-label="Maç durumu filtresi" value={status}
        onChange={event => setStatus(event.target.value)}>
        <option value="upcoming">Canlı ve oynanacak</option>
        <option value="live">Sadece canlı</option>
        <option value="scheduled">Oynanacak</option>
        <option value="finished">Tamamlanan</option>
        <option value="all">Tüm durumlar</option>
      </select></label>
      <label><span>Tarih · TSİ</span><input aria-label="Maç tarihi filtresi" type="date"
        value={day} min={data?.window_start} max={data?.window_end}
        onChange={event => setDay(event.target.value)}/></label>
      <label><span>Takım ara</span><input value={query} placeholder="Takım adı"
        maxLength={80} onChange={event => setQuery(event.target.value)}/></label>
      {(day || query || league !== "all" || status !== "upcoming") &&
        <button type="button" className="quiet-button center-submit" onClick={() => {
          setLeague("all"); setStatus("upcoming"); setDay(""); setQuery("");
        }}>Filtreleri sıfırla</button>}
    </div>
    {error ? <div className="empty" role="alert"><strong>Veri kaynağına bağlanılamadı</strong>
      <span>{error}. Sonuç uydurulmadı.</span></div> :
      !data ? <div className="center-loading" role="status"><span>Güncel fikstür okunuyor…</span></div> :
      <>
        <div style={{padding:"0 16px 12px"}}>
          <p className="scope-note" role="status">Bugün {summary.live} canlı · {summary.todayUpcoming} oynanacak ·
            Sonraki günlerde {summary.future} planlı maç. Görünen: {shown.length}.
            {status === "upcoming" ? " Geçmiş sonuçlar bu görünümde gizlenir." : ""}
          </p>
          {data.failures.length > 0 && <p className="scope-note" role="alert">
            Bazı kaynaklar okunamadı: {failedLeagues}. Bu ligler sıfır maç kabul edilmez.</p>}
          {shown.length === 0 ? <div className="empty"><CalendarDays size={25}/>
            <strong>Seçilen filtrelerde maç bulunamadı</strong>
            <span>Bugün maç yoksa gelecek 7 gün içindeki fikstür görünür. Filtreyi değiştirebilir veya
              {" "}<Link href="/europe">Avrupa kupalarında başka tarih seçebilirsin</Link>.</span>
          </div> : <div className="records-scroll source-fixtures-scroll" tabIndex={0} role="region" aria-label="Kaynak futbol maçları">
            <table className="records-table source-fixtures-table"><thead><tr>
              <th>Tarih · TSİ</th><th>Turnuva</th><th>Karşılaşma</th><th>Durum</th><th>Skor</th>
            </tr></thead><tbody>{shown.map(match => <tr key={match.id}>
              <td data-label="Tarih · TSİ">{formatMatchTime(match.start_time)}</td>
              <td data-label="Turnuva">{match.league_name}</td>
              <td data-label="Karşılaşma">{match.home_team} – {match.away_team}</td>
              <td data-label="Durum">{statusLabel(match.status)}</td>
              <td data-label="Skor">{Array.isArray(match.score) ? `${match.score[0]} – ${match.score[1]}` : "—"}</td>
            </tr>)}</tbody></table>
          </div>}
        </div>
        <p className="scope-note" style={{padding:"0 16px 16px"}}>
          Son kaynak gözlemi: {formatMatchTime(data.last_observed_at)} · {data.matches.length} maçlık kaynak penceresi.
          Bu bülten SQL/R2 arşivi, canlı skor garantisi veya doğrulanmış oran değildir.
        </p>
      </>}
  </section>;
}
