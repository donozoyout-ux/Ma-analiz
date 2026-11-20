import Link from "next/link";
import { Clock3, ChartNoAxesCombined } from "lucide-react";
import { type Match, statusLabel } from "@/lib/matches";
import { scoreText } from "@/lib/football-summary";
import { displayStatus } from "@/lib/turkish";
export function MatchCard({ match, action }: { match: Match; action?: React.ReactNode }) {
 const score = scoreText(match.result);
 const analysis = match.analysis_summary;
 const kickoff = new Date(match.start_time);
 return <article className="football-match-card">
  <div className="tile-meta"><span>{match.league_name || "Lig belirtilmedi"}</span><span className={`badge ${["LIVE","IN_PLAY"].includes(match.status.toUpperCase()) ? "ok" : ""}`}>{statusLabel(match.status)}</span></div>
  <div className="football-teams"><Link href={`/matches/${match.id}`}>{match.home_team}</Link><div className="football-score"><strong>{score ?? "— : —"}</strong><small>{score ? match.result?.verified_at ? "Doğrulanmış skor" : "Sağlayıcı skoru" : "Skor verisi yok"}</small></div><Link href={`/matches/${match.id}`}>{match.away_team}</Link></div>
  <p className="kickoff-time"><Clock3 size={14}/>{Number.isNaN(kickoff.getTime()) ? "Başlangıç saati belirtilmedi" : kickoff.toLocaleString("tr-TR", {day:"numeric",month:"short",hour:"2-digit",minute:"2-digit",timeZone:"Europe/Istanbul"})}<span>TSİ</span></p>
  <div className="match-analysis-line"><ChartNoAxesCombined size={16}/><span>{analysis ? displayStatus(analysis.analysis_status) : "Henüz analiz kaydı yok"}</span>{analysis?.analysis_status === "ANALYZED" && <span className="analysis-decision">{displayStatus(analysis.signal)}</span>}</div>
  <div className="match-card-bottom"><small>{analysis ? `Model: ${analysis.model_version}` : "Maç detayında verileri inceleyin"}</small>{action ?? <Link className="text-link" href={`/matches/${match.id}`}>Maç analizi</Link>}</div>
 </article>;
}
