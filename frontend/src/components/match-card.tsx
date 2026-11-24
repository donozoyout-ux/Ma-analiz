import Link from "next/link";
import { Clock3, ChartNoAxesCombined } from "lucide-react";
import { type Match, statusLabel, verifiedScoreText, formatMatchTime, matchHref } from "@/lib/matches";
import { displayStatus } from "@/lib/turkish";
export function MatchCard({ match, action }: { match: Match; action?: React.ReactNode }) {
 const score = verifiedScoreText(match.result);
 const analysis = match.analysis_summary;
 return <article className="football-match-card">
  <div className="tile-meta"><span>{match.league_name || "Lig belirtilmedi"}</span><span className={`badge ${["LIVE","IN_PLAY"].includes(match.status.toUpperCase()) ? "ok" : ""}`}>{statusLabel(match.status)}</span></div>
  <div className="football-teams"><Link href={matchHref(match)}>{match.home_team}</Link><div className="football-score"><strong>{score ?? "— : —"}</strong><small>{score ? "Doğrulanmış skor" : "Skor henüz doğrulanmadı"}</small></div><Link href={matchHref(match)}>{match.away_team}</Link></div>
  <p className="kickoff-time"><Clock3 size={14}/>{formatMatchTime(match.start_time)}<span>TSİ</span></p>
  {match.storage && <p className="match-freshness">{match.storage === "R2" ? "Doğrulanmış arşiv" : "Operasyonel kayıt"} · Güncelleme: {formatMatchTime(match.last_updated_at ?? match.result?.verified_at)}</p>}
  <div className="match-analysis-line"><ChartNoAxesCombined size={16}/><span>{analysis ? displayStatus(analysis.analysis_status) : "Henüz analiz kaydı yok"}</span>{analysis?.analysis_status === "ANALYZED" && <span className="analysis-decision">{displayStatus(analysis.signal)}</span>}</div>
  <div className="match-card-bottom"><small>{analysis ? `Model: ${analysis.model_version}` : "Maç detayında verileri inceleyin"}</small>{action ?? <Link className="text-link" href={matchHref(match)}>Maç detayı</Link>}</div>
 </article>;
}
