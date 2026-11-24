export type DailyPerformance = { date: string; wins: number; losses: number; predictions: number; analyzed_matches: number; hit_rate: number | string | null };
export type PredictionSummary = { id: string; match_id: string; created_at: string; status: string; settled_at: string | null };
export function performanceSummary(rows: DailyPerformance[], today: string) {
 const end = new Date(`${today}T00:00:00Z`);
 end.setUTCDate(end.getUTCDate() - 29);
 const start = end.toISOString().slice(0,10);
 const days = rows.filter(row => row.date >= start && row.date <= today);
 const valid = days.filter(row => Number.isInteger(row.wins) && row.wins >= 0 && Number.isInteger(row.losses) && row.losses >= 0);
 const wins = valid.reduce((sum, row) => sum + row.wins, 0);
 const losses = valid.reduce((sum, row) => sum + row.losses, 0);
 return { wins, losses, settled: wins + losses, rate: wins + losses ? wins / (wins + losses) : null, days: valid.length };
}
export function scoreText(result?: { home_score: number | null; away_score: number | null } | null) {
 if (!result || !Number.isInteger(result.home_score) || !Number.isInteger(result.away_score) || result.home_score! < 0 || result.away_score! < 0) return null;
 return `${result.home_score} – ${result.away_score}`;
}
