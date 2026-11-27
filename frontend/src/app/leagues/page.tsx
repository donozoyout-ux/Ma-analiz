"use client";

import { useEffect, useState } from "react";

type Rate = { actual: number; expected: number; difference: number };
type Rates = { sample: number; win?: Rate; draw?: Rate; upset?: Rate };
type Favorite = Rates & {
  status: string;
  bins?: (Rates & { lower: number; upper: number | null })[];
  seasons?: Record<string, Rates>;
  backtest?: { test_season: string; train_sample: number; test_sample: number; historical_brier: number; market_brier: number }[];
};
type League = {
  league: string; name: string; group: string; total: number; verified: number; odds: number;
  missing_result_fraction: number | null; missing_odds_fraction: number | null;
  updated_at: string | null; favorites: Favorite; history: Favorite; sources: [string, string][];
};

const groups = ["Avrupa kupaları", "Süper Lig", "Bundesliga", "Belçika", "Diğer ligler"];
const percentage = (value?: number | null) => value == null ? "—" :
  new Intl.NumberFormat("tr-TR", { style: "percent", maximumFractionDigits: 1 }).format(value);
const dateTime = new Intl.DateTimeFormat("tr-TR", { timeZone: "Europe/Istanbul", dateStyle: "medium", timeStyle: "short" });

export default function LeagueSelection() {
  const [season, setSeason] = useState("2026-2027");
  const [group, setGroup] = useState(groups[0]);
  const [report, setReport] = useState<{ season: string; leagues: League[] } | null>(null);
  const [errorSeason, setErrorSeason] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    fetch(`/api/v1/league-selection?season=${season}`, { signal: controller.signal, cache: "no-store" })
      .then(res => { if (!res.ok) throw Error(); return res.json(); })
      .then(value => { if (!controller.signal.aborted) { setReport(value); setErrorSeason(""); } })
      .catch(() => { if (!controller.signal.aborted) setErrorSeason(season); });
    return () => controller.abort();
  }, [season]);

  const rows = report?.season === season ? report.leagues.filter(row => row.group === group)
    .sort((a, b) => (a.favorites.upset?.actual ?? 2) - (b.favorites.upset?.actual ?? 2)) : null;

  return <main className="explorer-stack">
    <header className="topbar"><div>
      <h1 className="title">Lig Seçimi</h1>
      <p className="page-intro">Sezon kapsamı ve normal süre sonuçlarına göre favori güvenilirliği.</p>
    </div></header>
    <section className="card filter-panel">
      <div className="filter-grid center-filter-grid">
        <label><span>Lig grubu</span><select value={group} onChange={event => setGroup(event.target.value)}>
          {groups.map(item => <option key={item}>{item}</option>)}
        </select></label>
        <label><span>Sezon</span><select value={season} onChange={event => setSeason(event.target.value)}>
          {["2024-2025", "2025-2026", "2026-2027"].map(item => <option key={item}>{item}</option>)}
        </select></label>
      </div>
    </section>
    {errorSeason === season ? <p role="alert">Sezon kapsamı şu anda alınamıyor.</p> :
      !rows ? <p role="status">Sezon kapsamı yükleniyor…</p> :
        <section aria-label="Lig kapsamı ve favori sonuçları">
          {rows.map(row => <article className="card filter-panel" key={row.league}>
            <h2>{row.name}</h2>
            {row.total === 0 ? <p>Bu sezon için arşivlenmiş maç verisi yok.</p> :
              <dl style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(145px, 1fr))", gap: "1rem" }}>
                <div><dt>Toplam maç</dt><dd>{row.total}</dd></div>
                <div><dt>Skoru doğrulanmış</dt><dd>{row.verified}</dd></div>
                <div><dt>Maç öncesi oranı olan</dt><dd>{row.odds}</dd></div>
                <div><dt>Doğrulanmış skoru eksik</dt><dd>{percentage(row.missing_result_fraction)}</dd></div>
                <div><dt>Oranı eksik</dt><dd>{percentage(row.missing_odds_fraction)}</dd></div>
                <div><dt>Favori galibiyeti</dt><dd>{percentage(row.favorites.win?.actual)}</dd></div>
                <div><dt>Sürpriz mağlubiyet</dt><dd>{percentage(row.favorites.upset?.actual)}</dd></div>
                <div><dt>Son güncelleme</dt><dd>{row.updated_at ? dateTime.format(new Date(row.updated_at)) : "—"}</dd></div>
              </dl>}
            {row.total > 0 && row.favorites.status === "INSUFFICIENT_SAMPLE" &&
              <p>Yetersiz örneklem ({row.favorites.sample}/200 uygun maç).</p>}
            {row.favorites.win && <details>
              <summary>Normal süre sonuçları ve beklenen olasılıklar</summary>
              <div>Beraberlik {percentage(row.favorites.draw?.actual)} · Beklenen favori galibiyeti {percentage(row.favorites.win.expected)}
                {" · "}Fark {(row.favorites.win.difference * 100).toLocaleString("tr-TR", { maximumFractionDigits: 1 })} yüzde puan
                {" · "}{row.favorites.sample} maç</div>
              <ul>{row.favorites.bins?.filter(bin => bin.sample > 0).map(bin => <li key={bin.lower}>
                {bin.lower}–{bin.upper ?? "üstü"} oran · {bin.sample} maç · {percentage(bin.win?.actual)} galibiyet
                {" · "}{percentage(bin.draw?.actual)} beraberlik · {percentage(bin.upset?.actual)} sürpriz
              </li>)}</ul>
            </details>}
            {row.history.seasons && <details>
              <summary>Sezon karşılaştırması ve zaman sıralı test</summary>
              <ul>{Object.entries(row.history.seasons).map(([year, item]) => <li key={year}>
                {year} · {item.sample} maç · {percentage(item.win?.actual)} favori galibiyeti · {percentage(item.upset?.actual)} sürpriz
              </li>)}</ul>
              {row.history.backtest?.map(fold => <p key={fold.test_season}>
                {fold.test_season} · Eğitim {fold.train_sample}, test {fold.test_sample} maç · Brier {fold.historical_brier.toFixed(3)}
                {" · "}Oran olasılığı Brier {fold.market_brier.toFixed(3)}
              </p>)}
            </details>}
            {row.sources.length ? <p>Kaynaklar {row.sources.map(([source, licence]) =>
              source === "openligadb" ? <span key={source}><a href="https://www.openligadb.de">OpenLigaDB</a> ({licence}) </span> :
                <span key={source}>{source} ({licence}) </span>)}</p> : null}
          </article>)}
        </section>}
    <p>Geçmiş sonuçlar kesin tahmin sunmaz. Düşük sürpriz oranı kârlılığı garanti etmez.</p>
  </main>;
}
