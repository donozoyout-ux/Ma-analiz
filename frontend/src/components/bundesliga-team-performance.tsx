"use client";

import { useEffect, useState } from "react";

type Stats = {
  team: string; played: number; wins: number; draws: number; losses: number;
  goals_for: number; goals_against: number; goal_difference: number;
  home_played: number; home_wins: number; away_played: number; away_wins: number;
  form: string[]; warnings: string[];
};

function validStats(value: unknown): value is Stats {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const data = value as Record<string, unknown>;
  const counts = ["played", "wins", "draws", "losses", "goals_for", "goals_against", "home_played", "home_wins", "away_played", "away_wins"];
  return typeof data.team === "string" && data.team.trim().length > 0 &&
    counts.every(key => typeof data[key] === "number" && Number.isSafeInteger(data[key]) && (data[key] as number) >= 0) &&
    typeof data.goal_difference === "number" && Number.isSafeInteger(data.goal_difference) &&
    Array.isArray(data.form) && data.form.every(item => typeof item === "string") &&
    Array.isArray(data.warnings) && data.warnings.every(item => typeof item === "string");
}

export function BundesligaTeamPerformance({ strictResponse = false }: { strictResponse?: boolean }) {
  const [team, setTeam] = useState("FC Bayern München");
  const [query, setQuery] = useState("FC Bayern München");
  const [data, setData] = useState<Stats | null>(null);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError(false);
    fetch("/api/v1/bundesliga/team-performance?team=" + encodeURIComponent(query), {
      signal: controller.signal,
    })
      .then(async response => {
        if (!response.ok) throw new Error("TEAM_STATS_UNAVAILABLE");
        const value: unknown = await response.json();
        if (strictResponse && !validStats(value)) {
          throw new Error("TEAM_STATS_UNAVAILABLE");
        }
        return value as Stats;
      })
      .then(setData)
      .catch(e => { if (e.name !== "AbortError") setError(true); });
    return () => controller.abort();
  }, [query, retry, strictResponse]);

  return <section className="explorer-stack" aria-label="Bundesliga takım performansı">
    <div className="list-toolbar card">
      <div>
        <strong>Bundesliga takım istatistikleri</strong>
        <span>Yalnızca doğrulanmış gerçek maç sonuçları · 2026–27</span>
      </div>
      <form onSubmit={event => {
        event.preventDefault();
        if (team.trim()) setQuery(team.trim());
      }}>
        <label htmlFor="bundesliga-team">Takım adı</label>
        <input id="bundesliga-team" value={team}
          onChange={event => setTeam(event.target.value)} maxLength={150}
          placeholder="Takım adı" />
        <button className="quiet-button" type="submit">Analiz et</button>
      </form>
    </div>
    {error ? <div className="card empty" role="alert">
      Takım verileri alınamadı. API veya veritabanı erişimini kontrol edin.
      <button className="quiet-button" onClick={() => setRetry(n => n + 1)}>
        Yeniden dene
      </button>
    </div> : data === null ? <div className="card empty" role="status">
      Gerçek maç istatistikleri yükleniyor…
    </div> : data.played === 0 ? <div className="card empty">
      Bu takım için doğrulanmış Bundesliga sonucu bulunamadı.
    </div> : <>
      <div className="grid performance-kpis">
        {[
          ["Maç", data.played], ["Galibiyet", data.wins],
          ["Beraberlik", data.draws], ["Mağlubiyet", data.losses],
          ["Atılan gol", data.goals_for], ["Yenilen gol", data.goals_against],
          ["Averaj", data.goal_difference],
        ].map(([label, value]) => <div className="card" key={label}>
          <div className="kpi-label">{label}</div>
          <div className="kpi-value">{value}</div>
        </div>)}
      </div>
      <div className="card">
        <strong>Ev / deplasman</strong>
        <p>Evde: {data.home_played} maç · {data.home_wins} galibiyet</p>
        <p>Deplasmanda: {data.away_played} maç · {data.away_wins} galibiyet</p>
        <p>Son 5 maç (yeniden eskiye): {data.form.join(" · ") || "—"}</p>
      </div>
    </>}
    <p className="scope-note">
      Kaynak: mevcut PostgreSQL'deki bağımsız doğrulanmış Bundesliga sonuçları.
      Eksik maçlar hesaplanmaz. Tahmin, bahis oranı veya uydurma veri gösterilmez.
    </p>
  </section>;
}
