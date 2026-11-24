"use client";

import { displayStatus, marketLabel, apiErrorMessage } from "@/lib/turkish";
import { useEffect, useState } from "react";

type Snapshot = { price: number | string; captured_at: string; source: string | null; period: string; status: string };
type OddsRow = { bookmaker: string; market: string; selection: string; line: number | string | null; opening: Snapshot | null; latest: Snapshot | null; closing: Snapshot | null };
type LiveRow = { bookmaker: string; market: string; selection: string; line: number | string | null; price: number | string; captured_at: string; period: string };
type MatchDetail = { start_time: string; league_name: string; home_team: string; away_team: string; status: string; odds_summary: OddsRow[]; live_odds: LiveRow[] };
type Evidence = { score: number | null; confidence: number; sample_size: number; evidence: string; warnings: string[] };
type FormBlock = { form_5: Evidence; form_10: Evidence; form_15: Evidence; goals: { sample_size: number; avg_goals_for: number | null; avg_goals_against: number | null; btts: number | null; over_2_5: number | null } };
type Statistics = { status: string; home: FormBlock & { home_last_5: Evidence }; away: FormBlock & { away_last_5: Evidence }; h2h: Evidence; sample_size: number };
type Analysis = { final: { signal: string; score: number | null; confidence: string; risk: string; reasons: string[] }; historical: { status: string; sample_size: number }; value: { status: string; value: number | null }; ai: { status: string; output?: { summary?: string; conclusion?: string } | null } };

const formatPrice = (value: Snapshot | null) => value?.price == null ? "—" : Number(value.price).toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 3 });
const formatDate = (value: string) => new Intl.DateTimeFormat("tr-TR", { dateStyle: "medium", timeStyle: "short", timeZone: "Europe/Istanbul" }).format(new Date(value));
const percent = (value: number | null) => value == null ? "—" : `${(value * 100).toFixed(0)}%`;
const label = displayStatus;

function movement(row: OddsRow) {
  if (row.opening?.price == null || row.latest?.price == null) return "—";
  const opening = Number(row.opening.price);
  const latest = Number(row.latest.price);
  return opening > 0 ? `${((latest - opening) / opening * 100).toFixed(2)}%` : "—";
}

function EvidenceTile({ title, evidence }: { title: string; evidence: Evidence }) {
  return <div className="evidence-tile"><span>{title}</span><strong>{percent(evidence.score)}</strong><small>{evidence.sample_size} maçlık örnek</small></div>;
}

export function MatchOdds({ matchId }: { matchId: string }) {
  const [match, setMatch] = useState<MatchDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [statistics, setStatistics] = useState<Statistics | null>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`/api/v1/matches/${matchId}`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) {
          let payload: Record<string, unknown> = {};
          try { payload = await response.json(); } catch { /* no response body */ }
          throw new Error(String(payload.detail ?? `API_${response.status}`));
        }
        return response.json();
      })
      .then(setMatch)
      .catch((reason: unknown) => { if (reason instanceof Error && reason.name !== "AbortError") setError(reason.message); });
    fetch(`/api/v1/analysis/${matchId}/statistics`, { signal: controller.signal }).then((response) => response.ok ? response.json() : null).then(setStatistics).catch(() => setStatistics(null));
    fetch(`/api/v1/analysis/${matchId}`, { signal: controller.signal }).then((response) => response.ok ? response.json() : null).then(setAnalysis).catch(() => setAnalysis(null));
    return () => controller.abort();
  }, [matchId]);

  if (error) return <div className="card empty">{apiErrorMessage(error)}</div>;
  if (!match) return <div className="card empty" aria-live="polite">Maç ve oran kayıtları yükleniyor…</div>;

  return <div className="match-detail-stack">
    <section className="match-hero card">
      <div><div className="section-kicker">{match.league_name || "LİG BİLGİSİ YOK"} · {label(match.status)}</div><h2>{match.home_team}<span>karşı</span>{match.away_team}</h2><p>{formatDate(match.start_time)}</p></div>
      <div className="match-source-note"><i className="legend-dot mint" /> Sağlayıcı kayıtları</div>
    </section>

    <section className="card odds-section">
      <div className="section-head"><div><div className="section-kicker">SAĞLAYICI ORAN KAYITLARI</div><div className="section-title">Oran hareketleri</div><p className="page-intro">Açılış, en güncel ve kapanış fiyatları ayrı tutulur. Canlı fiyatlar kapanış hesabına katılmaz.</p></div></div>
      {match.odds_summary.length === 0 ? <div className="empty">Bu maç için henüz oran kaydı yok.</div> : <div className="records-scroll" tabIndex={0} role="region" aria-label="Maç ve oran tablosu"><table className="records-table"><thead><tr>{["Sağlayıcı", "Pazar", "Seçim", "Çizgi", "Açılış", "Güncel", "Kapanış", "Hareket"].map((heading) => <th key={heading}>{heading}</th>)}</tr></thead><tbody>
        {match.odds_summary.map((row, index) => <tr key={`${row.bookmaker}-${row.market}-${row.selection}-${row.line ?? ""}-${index}`}>
          <td>{row.bookmaker}</td><td>{marketLabel(row.market)}</td><td>{marketLabel(row.selection)}</td><td>{row.line ?? "—"}</td>
          <td title={row.opening?.captured_at ? formatDate(row.opening.captured_at) : ""}>{formatPrice(row.opening)}</td>
          <td title={row.latest?.captured_at ? formatDate(row.latest.captured_at) : ""}>{formatPrice(row.latest)}</td>
          <td title={row.closing?.captured_at ? formatDate(row.closing.captured_at) : ""}>{formatPrice(row.closing)}</td><td>{movement(row)}</td>
        </tr>)}
      </tbody></table></div>}
    </section>

    {match.live_odds.length > 0 && <section className="card">
      <div className="section-head"><div><div className="section-kicker">AYRI TUTULAN CANLI KAYITLAR</div><div className="section-title">Canlı oranlar</div></div></div>
      <div className="records-scroll" tabIndex={0} role="region" aria-label="Maç ve oran tablosu"><table className="records-table"><thead><tr>{["Sağlayıcı", "Pazar", "Seçim", "Çizgi", "Fiyat", "Kayıt zamanı"].map((heading) => <th key={heading}>{heading}</th>)}</tr></thead><tbody>{match.live_odds.map((row, index) => <tr key={`${row.bookmaker}-${row.market}-${row.selection}-${index}`}><td>{row.bookmaker}</td><td>{marketLabel(row.market)}</td><td>{marketLabel(row.selection)}</td><td>{row.line ?? "—"}</td><td>{Number(row.price).toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 3 })}</td><td>{formatDate(row.captured_at)}</td></tr>)}</tbody></table></div>
    </section>}

    <section className="card">
      <div className="section-head"><div><div className="section-kicker">DOĞRULANMIŞ GEÇMİŞ</div><div className="section-title">Takım istatistikleri</div></div><span className="badge">{statistics ? label(statistics.status) : "VERİ BEKLENİYOR"}</span></div>
      {!statistics ? <div className="empty">İstatistik için PostgreSQL veya R2 tarihsel verisi bekleniyor.</div> : <>
        <div className="form-columns">
          <div className="form-team"><h3>{match.home_team}<small>EV SAHİBİ</small></h3><div className="evidence-grid"><EvidenceTile title="Son 5" evidence={statistics.home.form_5} /><EvidenceTile title="Son 10" evidence={statistics.home.form_10} /><EvidenceTile title="Son 15" evidence={statistics.home.form_15} /></div><p className="goals-summary">Maç başına gol: {statistics.home.goals.avg_goals_for?.toFixed(2) ?? "—"} · yenen: {statistics.home.goals.avg_goals_against?.toFixed(2) ?? "—"} · KG: {percent(statistics.home.goals.btts)}</p></div>
          <div className="form-team"><h3>{match.away_team}<small>DEPLASMAN</small></h3><div className="evidence-grid"><EvidenceTile title="Son 5" evidence={statistics.away.form_5} /><EvidenceTile title="Son 10" evidence={statistics.away.form_10} /><EvidenceTile title="Son 15" evidence={statistics.away.form_15} /></div><p className="goals-summary">Maç başına gol: {statistics.away.goals.avg_goals_for?.toFixed(2) ?? "—"} · yenen: {statistics.away.goals.avg_goals_against?.toFixed(2) ?? "—"} · KG: {percent(statistics.away.goals.btts)}</p></div>
        </div>
        <div className="h2h-note">İkili rekabet · {statistics.h2h.sample_size} doğrulanmış maç <span>{displayStatus(statistics.h2h.evidence)}</span></div>
      </>}
    </section>

    <section className="card analysis-summary">
      <div className="section-head"><div><div className="section-kicker">GROQ + İSTATİSTİKSEL KANIT</div><div className="section-title">Analiz kararı</div></div><span className={`badge ${analysis?.final.signal === "SIGNAL" ? "ok" : ""}`}>{analysis ? displayStatus(analysis.final.signal) : "BEKLENİYOR"}</span></div>
      {!analysis ? <div className="empty">Henüz analiz kaydı yok. Otomatik taramanın tamamlanması bekleniyor.</div> : <>
        <div className="analysis-meta"><span>Tarihsel kanıt <strong>{displayStatus(analysis.historical.status)}</strong></span><span>Değer analizi <strong>{displayStatus(analysis.value.status)}</strong></span><span>Yapay zekâ <strong>{displayStatus(analysis.ai.status)}</strong></span></div>
        {analysis.final.reasons.length > 0 && <p className="analysis-reasons">Karar gerekçeleri: {analysis.final.reasons.map((reason) => displayStatus(reason)).join(" · ")}</p>}
        {analysis.ai.output?.summary && <p className="ai-summary">{analysis.ai.output.summary}</p>}
      </>}
    </section>
  </div>;
}
