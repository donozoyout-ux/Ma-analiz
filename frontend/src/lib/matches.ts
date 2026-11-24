export type Match = { id: string; start_time: string; league_name: string | null; home_team: string; away_team: string; status: string;
 league_id?: string | null; season?: string | null; storage?: "POSTGRESQL" | "R2"; last_updated_at?: string | null; archive_verified_at?: string | null;
 source?: {name: string; licence: string; attribution: string; licence_url: string} | null;
 statistics?: {name: string; home: number; away: number; unit: string}[];
 result?: { home_score: number | null; away_score: number | null; status: string; verified_at: string | null; result_source?: string } | null;
 analysis_summary?: { analysis_status: string; signal: string; analysis_timestamp: string; model_version: string } | null;
};
export function formatMatchTime(value?: string | null) {
 if (!value || !/T.*(?:Z|[+-]\d{2}:\d{2})$/i.test(value) || Number.isNaN(Date.parse(value))) return "Zaman doğrulanmadı";
 return new Intl.DateTimeFormat("tr-TR", {dateStyle:"medium", timeStyle:"short", timeZone:"Europe/Istanbul"}).format(new Date(value));
}
export function verifiedScoreText(result: Match["result"]) {
 if (!result || result.status !== "FINAL" || !result.verified_at || formatMatchTime(result.verified_at) === "Zaman doğrulanmadı"
  || !Number.isInteger(result.home_score) || !Number.isInteger(result.away_score) || result.home_score! < 0 || result.away_score! < 0) return null;
 return `${result.home_score} – ${result.away_score}`;
}
export const matchHref = (match: Match) => `/matches/${encodeURIComponent(match.id)}${match.storage === "R2" ? "?source=history" : ""}`;
export type MatchFilters = {day: string; league: string; team: string; status: string; source: "all" | "active" | "history"};
export function matchQuery(filters: MatchFilters, cursor?: string) {
 const query = new URLSearchParams({source: filters.source, limit:"12"});
 for (const key of ["day","league","team","status"] as const) if (filters[key].trim()) query.set(key, filters[key].trim());
 if (cursor) query.set("cursor",cursor);
 return `match-center?${query.toString()}`;
}
export function isMatchReadPayload(value: unknown, list: boolean): boolean {
 if (!value || typeof value !== "object") return false;
 const isMatch = (row: unknown) => {
  if (!row || typeof row !== "object") return false;
  const item=row as Record<string,unknown>;
  return typeof item.id==="string" && typeof item.home_team==="string" && typeof item.away_team==="string"
   && typeof item.status==="string" && typeof item.start_time==="string" && ["POSTGRESQL","R2"].includes(String(item.storage));
 };
 if (!list) return isMatch(value);
 const page=value as Record<string,unknown>;
 return Array.isArray(page.items) && page.items.length<=12 && page.items.every(isMatch)
  && (page.next_cursor===null || typeof page.next_cursor==="string") && Array.isArray(page.warnings)
  && typeof page.as_of==="string";
}
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
