export type Match = { id: string; start_time: string; league_name: string | null; home_team: string; away_team: string; status: string;
 result?: { home_score: number | null; away_score: number | null; status: string; verified_at: string | null; result_source: string } | null;
 analysis_summary?: { analysis_status: string; signal: string; analysis_timestamp: string; model_version: string } | null;
};
export const istanbulDay = (value: string | Date) => {
 const date = new Date(value);
 return Number.isNaN(date.getTime()) ? "" : new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Istanbul" }).format(date);
};
export const statusLabel = (s: string) => ({ SCHEDULED: "Planlandı", LIVE: "Canlı", FINISHED: "Tamamlandı", FT: "Tamamlandı", COMPLETED: "Tamamlandı", FINAL: "Tamamlandı", IN_PLAY: "Canlı", IN_PROGRESS: "Devam ediyor", PAUSED: "Duraklatıldı", HT: "Devre arası", AET: "Uzatma sonu", PEN: "Penaltılar", NS: "Başlamadı", POSTPONED: "Ertelendi", CANCELLED: "İptal" }[s.toUpperCase()] ?? "Durum belirtilmedi");
export function filterMatches(rows: Match[], f: { query: string; league: string; status: string; date: string; archive: boolean }) {
 const q = f.query.trim().toLocaleLowerCase("tr-TR");
 return rows.filter(r => (!f.archive || ["FINISHED", "FT", "COMPLETED"].includes(r.status.toUpperCase())) && (!f.date || istanbulDay(r.start_time) === f.date) && (!f.league || r.league_name === f.league) && (!f.status || r.status === f.status) && (!q || `${r.home_team} ${r.away_team} ${r.league_name ?? ""}`.toLocaleLowerCase("tr-TR").includes(q)));
}

export function summarizeTodaysMatches(rows: Match[], today: string) {
 const matches = rows.filter(row => istanbulDay(row.start_time) === today);
 const analyzed = matches.every(row => row.analysis_summary !== undefined)
  ? matches.filter(row => row.analysis_summary?.analysis_status === "ANALYZED").length : null;
 return { total: matches.length, analyzed };
}
