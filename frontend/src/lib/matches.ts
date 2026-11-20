export type Match = { id: string; start_time: string; league_name: string | null; home_team: string; away_team: string; status: string };
export const istanbulDay = (value: string | Date) => {
 const date = new Date(value);
 return Number.isNaN(date.getTime()) ? "" : new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Istanbul" }).format(date);
};
export const statusLabel = (s: string) => ({ SCHEDULED: "Planlandı", LIVE: "Canlı", FINISHED: "Tamamlandı", FT: "Tamamlandı", COMPLETED: "Tamamlandı", POSTPONED: "Ertelendi", CANCELLED: "İptal" }[s.toUpperCase()] ?? s);
export function filterMatches(rows: Match[], f: { query: string; league: string; status: string; date: string; archive: boolean }) {
 const q = f.query.trim().toLocaleLowerCase("tr-TR");
 return rows.filter(r => (!f.archive || ["FINISHED", "FT", "COMPLETED"].includes(r.status.toUpperCase())) && (!f.date || istanbulDay(r.start_time) === f.date) && (!f.league || r.league_name === f.league) && (!f.status || r.status === f.status) && (!q || `${r.home_team} ${r.away_team} ${r.league_name ?? ""}`.toLocaleLowerCase("tr-TR").includes(q)));
}
