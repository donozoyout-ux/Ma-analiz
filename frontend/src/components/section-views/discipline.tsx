"use client";

import { useEffect, useMemo, useState } from "react";

type Metric = { sample: number; average_total: number | null; average_for: number | null; average_against: number | null };
type Summary = {
  source: string; match_count: number; metrics: Record<"corners" | "yellows" | "reds", Metric>;
  last_observed_at: string | null; note: string;
  archive_integrity: string; verified_r2_partitions: number;
};
type Match = {
  id: string; date: string; league_id: string; season: string;
  home_team: string; away_team: string;
  home_corners: number | null; away_corners: number | null;
  home_yellows: number | null; away_yellows: number | null;
  home_reds: number | null; away_reds: number | null;
};
type MatchResponse = {items: Match[]; next_offset: number | null};
const leagues = [["", "Üç lig birlikte"], ["en.pl", "Premier League"], ["de.bl1", "Bundesliga"], ["es.ll", "La Liga"]] as const;
const seasons = [["", "Tüm sezonlar"], ["2024-2025", "2024/25"], ["2025-2026", "2025/26"], ["2026-2027", "2026/27"]] as const;

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}
function count(value: number | null | undefined) { return value == null ? "—" : value.toLocaleString("tr-TR"); }
function metric(value: number | null | undefined) { return value == null ? "—" : value.toLocaleString("tr-TR", {maximumFractionDigits: 2}); }
function pair(a: number | null, b: number | null) { return `${count(a)} / ${count(b)}`; }
function safeSummary(value: unknown): value is Summary {
  if (!value || typeof value !== "object") return false;
  const s = value as Partial<Summary>;
  return Number.isSafeInteger(s.match_count) && s.match_count! >= 0 &&
    Number.isSafeInteger(s.verified_r2_partitions) && s.verified_r2_partitions! >= 0 &&
    typeof s.archive_integrity === "string" && !!s.metrics &&
    (["corners", "yellows", "reds"] as const).every(name => {
      const m = s.metrics?.[name]; return !!m && Number.isSafeInteger(m.sample) && m.sample >= 0 &&
        (m.average_total == null || asNumber(m.average_total) !== null) &&
        (m.average_for == null || asNumber(m.average_for) !== null) &&
        (m.average_against == null || asNumber(m.average_against) !== null);
    });
}
function safeMatches(value: unknown): value is MatchResponse {
  if (!value || typeof value !== "object") return false;
  const r = value as Partial<MatchResponse>;
  return Array.isArray(r.items) && (r.next_offset === null || Number.isSafeInteger(r.next_offset)) &&
    r.items.every((m: Match) => m && typeof m.id === "string" && typeof m.date === "string" &&
      typeof m.home_team === "string" && typeof m.away_team === "string" &&
      (["home_corners","away_corners","home_yellows","away_yellows","home_reds","away_reds"] as const)
        .every(key => m[key] === null || (Number.isSafeInteger(m[key]) && m[key]! >= 0)));
}

export function HistoricalDisciplinePanel() {
  const [league, setLeague] = useState("");
  const [season, setSeason] = useState("");
  const [teamInput, setTeamInput] = useState("");
  const [team, setTeam] = useState("");
  const [offset, setOffset] = useState(0);
  const [state, setState] = useState<"loading" | "ok" | "error">("loading");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [matches, setMatches] = useState<MatchResponse | null>(null);
  const [refresh, setRefresh] = useState(0);
  const params = useMemo(() => {
    const q = new URLSearchParams();
    if (league) q.set("league", league);
    if (season) q.set("season", season);
    if (team) q.set("team", team);
    return q.toString();
  }, [league, season, team]);

  useEffect(() => {
    const controller = new AbortController();
    setState("loading"); setSummary(null); setMatches(null);
    const query = params ? params + "&" : "";
    Promise.all([
      fetch("/api/v1/historical-discipline/summary?" + params, {signal: controller.signal, cache: "no-store"}),
      fetch("/api/v1/historical-discipline/matches?" + query + "limit=20&offset=" + offset,
        {signal: controller.signal, cache: "no-store"}),
    ]).then(async ([a, b]) => {
      if (!a.ok || !b.ok) throw new Error("HISTORICAL_STATS_UNAVAILABLE");
      const [s, m]: unknown[] = await Promise.all([a.json(), b.json()]);
      if (!safeSummary(s) || !safeMatches(m)) throw new Error("INVALID_STATS_RESPONSE");
      return [s, m] as [Summary, MatchResponse];
    }).then(([s, m]) => {
      setSummary(s); setMatches(m); setState("ok");
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setState("error");
    });
    return () => controller.abort();
  }, [params, offset, refresh]);

  const entries = [
    {key: "corners", title: "Toplam korner / maç", subtitle: "Ev + deplasman"},
    {key: "yellows", title: "Sarı kart / maç", subtitle: "Ev + deplasman"},
    {key: "reds", title: "Kırmızı kart / maç", subtitle: "Ev + deplasman"},
  ] as const;
  const max = Math.max(1, ...(summary ? entries.map(e => summary.metrics[e.key].average_total ?? 0) : [0]));

  return <div className="explorer-stack" data-testid="historical-discipline">
    <header className="topbar">
      <div><div className="eyebrow">MA-ANALİZ / TARİHSEL MAÇ İSTATİSTİKLERİ</div>
        <h1 className="title">Korner & Kart Analizi</h1>
        <p className="page-intro">Premier League, Bundesliga ve La Liga · 2024/25 sezonundan itibaren.</p>
      </div>
    </header>
    <p className="status-strip">Yalnızca R2'de doğrulanmış ve izinli dosyalardan içe aktarılan kaynak istatistikleri. Resmî maç skoru veya tahmin sonucu olarak doğrulanmış sayılmaz.</p>
    <form className="card filter-panel" onSubmit={event => {event.preventDefault();setOffset(0);setTeam(teamInput.trim());}}>
      <div className="filter-grid center-filter-grid">
        <label><span>Lig</span><select aria-label="İstatistik ligi" value={league} onChange={e=>{setLeague(e.target.value);setOffset(0);}}>
          {leagues.map(([value,label])=><option key={value} value={value}>{label}</option>)}
        </select></label>
        <label><span>Sezon</span><select aria-label="İstatistik sezonu" value={season} onChange={e=>{setSeason(e.target.value);setOffset(0);}}>
          {seasons.map(([value,label])=><option key={value} value={value}>{label}</option>)}
        </select></label>
        <label><span>Takım (kaynakta yazdığı gibi)</span><input maxLength={150} value={teamInput}
          onChange={e=>setTeamInput(e.target.value)} placeholder="Örn. Bayern Munich" /></label>
        <button className="quiet-button" type="submit">Filtrele</button>
      </div>
    </form>
    {state === "loading" && <div className="card empty" role="status">Korner ve kart istatistikleri yükleniyor…</div>}
    {state === "error" && <div className="card empty" role="alert">İstatistik arşivi henüz yüklenmemiş veya erişilemiyor.
      <button className="quiet-button" onClick={()=>setRefresh(n=>n+1)}>Tekrar dene</button></div>}
    {state === "ok" && summary && matches && <>
      <p className="scope-note" role="status">R2 arşiv durumu: {summary.archive_integrity === "VERIFIED_R2_LEDGER"
        ? "Doğrulama kaydı bulunan arşiv" : "Henüz doğrulanmış R2 arşivi yok"} ·
        Kaydedilen arşiv dosyası: {summary.verified_r2_partitions}</p>
      <section className="grid performance-kpis" aria-label="Korner ve kart özetleri">
        <div className="card"><div className="kpi-label">İstatistik bulunan maç</div>
          <div className="kpi-value">{count(summary.match_count)}</div></div>
        {entries.map(e => <div className="card" key={e.key}>
          <div className="kpi-label">{e.title}</div>
          <div className="kpi-value">{metric(summary.metrics[e.key].average_total)}</div>
          <div className="scope-note">Örneklem: {count(summary.metrics[e.key].sample)} maç</div>
        </div>)}
      </section>
      {!summary.match_count ? <div className="card empty">Bu filtrede yüklenmiş korner/kart verisi yok. Gerçek CSV dosyaları izinli biçimde içe aktarılınca görünecek.</div>
        : <section className="card" aria-label="Korner kart karşılaştırması">
          <h2 className="section-title">Maç başına ortalama</h2>
          {entries.map(e => { const v = summary.metrics[e.key].average_total;
            return <div key={e.key} style={{marginBlock:"18px"}}><div className="list-toolbar" style={{display:"flex",justifyContent:"space-between"}}>
              <span>{e.title} · n={summary.metrics[e.key].sample}</span><strong>{metric(v)}</strong></div>
              <div role="img" aria-label={e.title + ": " + metric(v)} style={{height:12,borderRadius:10,background:"var(--line, #ddd)",overflow:"hidden"}}>
                <div style={{width:v==null?"0%":(100*v/max)+"%",height:"100%",borderRadius:10,background:"var(--premium-red, #d71932)"}} />
              </div></div>;
          })}
          {team && <p className="scope-note">Takım özelinde değerler: {entries.map(e => `${e.title}: ${metric(summary.metrics[e.key].average_for)} yapılan, ${metric(summary.metrics[e.key].average_against)} rakibin`).join(" · ")}</p>}
        </section>}
      <section className="card" aria-labelledby="discipline-results">
        <h2 className="section-title" id="discipline-results">Maç bazlı korner & kart kayıtları</h2>
        <div className="records-scroll" role="region" tabIndex={0} aria-label="Maç istatistik tablosu">
          <table className="records-table"><thead><tr>{["Tarih","Lig","Sezon","Ev sahibi","Deplasman","Korner ev/dep","Sarı ev/dep","Kırmızı ev/dep"].map(x=><th key={x} scope="col">{x}</th>)}</tr></thead>
            <tbody>{matches.items.map(m=><tr key={m.id}><td>{m.date}</td><td>{leagues.find(l=>l[0]===m.league_id)?.[1] ?? m.league_id}</td>
              <td>{m.season}</td><td>{m.home_team}</td><td>{m.away_team}</td>
              <td>{pair(m.home_corners,m.away_corners)}</td><td>{pair(m.home_yellows,m.away_yellows)}</td><td>{pair(m.home_reds,m.away_reds)}</td>
            </tr>)}</tbody></table>
        </div>
        <div className="row" style={{display:"flex",gap:12,marginTop:16,alignItems:"center"}}>
          <button type="button" className="quiet-button" disabled={offset === 0} onClick={()=>setOffset(Math.max(0,offset-20))}>Önceki</button>
          <span>Sayfa {offset/20+1}</span>
          <button type="button" className="quiet-button" disabled={matches.next_offset===null} onClick={()=>setOffset(offset+20)}>Sonraki</button>
        </div>
      </section>
    </>}
    <p className="scope-note">Kaynak: <a href="https://www.football-data.co.uk/data.php" target="_blank" rel="noopener noreferrer">Football-Data.co.uk</a>.
      Eksik istatistikler sıfır değildir. Kaydedilen kaynak gözlem zamanı eski tahminlerin yayımlanmış kanıtı sayılmaz.
      Kaynak kullanım izinleri ayrı değerlendirilmelidir. Bahis oranları alınmaz.</p>
  </div>;
}
