"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowUpRight, BrainCircuit, ChartNoAxesCombined, CalendarDays, RefreshCw } from "lucide-react";
import { useApiList } from "@/lib/use-api-list";
import { type Match, istanbulDay, summarizeTodaysMatches } from "@/lib/matches";
import { performanceSummary, type DailyPerformance, type PredictionSummary } from "@/lib/football-summary";
import { SourceBulletin } from "./source-bulletin";
import { OverviewMetrics } from "./overview-metrics";
export function FootballHome({stitchTitle=false}: {stitchTitle?:boolean} = {}) {
 const matches = useApiList<Match>("matches?limit=500");
 const predictions = useApiList<PredictionSummary>("predictions?limit=500");
 const performance = useApiList<DailyPerformance>("performance?limit=30");
 const [today, setToday] = useState("");
 useEffect(() => { const update = () => setToday(istanbulDay(new Date())); update(); const timer = setInterval(update,60000); return () => clearInterval(timer); }, []);
 const matchSummary = matches.rows && today ? summarizeTodaysMatches(matches.rows,today) : null;
 const summary = performance.rows && today ? performanceSummary(performance.rows,today) : null;
 const todaysPredictions = predictions.rows?.filter(row => istanbulDay(row.created_at) === today);
 const pending = todaysPredictions?.filter(row => row.status === "PENDING").length;
 const refreshAll = () => { matches.refresh(); predictions.refresh(); performance.refresh(); };
 return <div className="premium-dashboard">
  <div className="dashboard-context"><span>Çalışma alanı <span aria-hidden="true">/</span> <strong>Genel Bakış</strong></span><span className="dashboard-edition">MAÇ ANALİZ <span aria-hidden="true">●</span> FUTBOL ODAKLI</span></div>
  <header className="topbar football-heading"><div><div className="eyebrow"><span aria-hidden="true"/> OYUNU VERİYLE OKUYUN</div><h1 className="title">{stitchTitle ? "Genel Bakış" : "Futbol Analiz Merkezi"}{!stitchTitle && <span aria-hidden="true">.</span>}</h1><p className="page-intro">Günün maçları, sahadaki veriler ve analizler.<br className="dashboard-intro-break"/> Futbola daha yakından bakın.</p></div><div className="heading-actions"><span className="today-chip"><CalendarDays size={17}/>{today ? new Date(`${today}T12:00:00+03:00`).toLocaleDateString("tr-TR",{day:"numeric",month:"long",year:"numeric"}) : "Bugün"}</span><button className="quiet-button home-refresh" onClick={refreshAll}><RefreshCw size={16}/>Verileri yenile</button></div></header>
  <p className="scope-note home-scope" role="status"><strong>BETA / TEST SÜRÜMÜ</strong> — Maç bülteni OpenLigaDB’den okunur; yukarıdaki analiz sayaçları yalnızca veritabanına kaydedilmiş verileri sayar. Kaynağın sunmadığı oranlar ve tahminler üretilmez.</p>
  <section className="home-match-section" aria-labelledby="daily-matches-heading"><div className="section-heading-row"><div><div className="section-kicker">BUGÜN & YAKLAŞAN</div><h2 className="section-heading" id="daily-matches-heading">Canlı ve oynanacak maçlar</h2></div><Link className="text-link dashboard-center-link" href="/matches">Tüm maçlar ve filtreler <ArrowUpRight size={16}/></Link></div><SourceBulletin variant="home" /></section>
  <OverviewMetrics matches={matchSummary?.total ?? null} analyzed={matchSummary?.analyzed ?? null} rate={summary?.rate ?? null} loading={!matches.rows && !matches.error}/>
  {matches.rows?.length === 500 && <p className="scope-note home-scope">Maç sayıları ve filtreler API’nin döndürdüğü ilk 500 aktif kayıtla sınırlıdır.</p>}

  <div className="grid football-insights">
   <section className="card insight-card" aria-labelledby="ai-summary-heading"><div className="insight-heading"><span className="insight-icon"><BrainCircuit size={22}/></span><div><div className="section-kicker">MODELİN DEĞERLENDİRMESİ</div><h2 id="ai-summary-heading">AI Tahminleri</h2></div></div><p className="insight-description">Maç öncesi olasılıkları ve kanıta dayalı model seçimlerini inceleyin.</p>
    {predictions.error ? <p className="insight-empty" role="alert">Tahmin verisine ulaşılamadı. <button className="quiet-button" onClick={predictions.refresh}>Yeniden dene</button></p> : predictions.rows === null ? <p className="insight-empty" role="status">Tahmin kayıtları yükleniyor…</p> : !todaysPredictions?.length ? <p className="insight-empty">Bugün için kayıtlı tahmin yok. Model kaydı oluştuğunda burada gösterilecek.</p> : <div className="insight-stat-grid"><div><strong>{todaysPredictions.length}</strong><span>Bugün kaydedilen</span></div><div><strong>{pending}</strong><span>Sonuç bekleyen</span></div><div><strong>{new Set(todaysPredictions.map(row=>row.match_id)).size}</strong><span>Farklı maç</span></div></div>}
    <div className="insight-footer"><small>Olasılık, gerçekleşmiş sonuç değildir. En fazla 500 aktif tahmin kaydı.</small><Link className="text-link" href="/predictions">Tahminleri incele</Link></div>
   </section>
   <section className="card insight-card" aria-labelledby="performance-summary-heading"><div className="insight-heading"><span className="insight-icon"><ChartNoAxesCombined size={22}/></span><div><div className="section-kicker">SON 30 GÜN</div><h2 id="performance-summary-heading">Tahmin performansı</h2></div></div><p className="insight-description">Başarılı ve başarısız sonuçları birlikte değerlendirin.</p>
    {performance.error ? <p className="insight-empty" role="alert">Performans verisine ulaşılamadı. <button className="quiet-button" onClick={performance.refresh}>Yeniden dene</button></p> : performance.rows === null ? <p className="insight-empty" role="status">Sonuç kayıtları yükleniyor…</p> : !summary?.settled ? <p className="insight-empty">Doğrulanmış sonuç bekleniyor. Sonuç oluşmadan başarı yüzdesi gösterilmez.</p> : <><div className="insight-stat-grid"><div><strong>{summary.settled}</strong><span>Sonuçlandırılan</span></div><div><strong>{summary.wins}</strong><span>Başarılı</span></div><div><strong>{summary.losses}</strong><span>Başarısız</span></div></div><div className="outcome-track" role="img" aria-label={`${summary.wins} başarılı, ${summary.losses} başarısız tahmin`}><span style={{width:`${summary.rate! * 100}%`}}/></div></>}
    <div className="insight-footer"><small>Günlük sonuç kayıtları (UTC). Bekleyen tahminler isabet hesabına katılmaz.</small><Link className="text-link" href="/analysis">Performansı incele</Link></div>
   </section>
  </div><p className="home-data-note">Maç ve tahmin verileri bağlı kaynaklardan alınır. Skor veya analiz bilgisi yoksa açıkça belirtilir. Veriler kendiliğinden canlı güncellenmez.</p>
 </div>;
}
