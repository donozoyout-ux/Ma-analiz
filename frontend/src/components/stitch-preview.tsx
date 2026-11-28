"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import {
  Activity, Archive, ArrowLeft, BarChart3, BrainCircuit, ChartNoAxesCombined,
  CircleHelp, LayoutDashboard, ShieldCheck,
} from "lucide-react";
import { FootballHome } from "@/components/football-home";
import { MatchCenter } from "@/components/match-center";
import { StitchPredictionLedger } from "@/components/stitch-prediction-ledger";
import Performance from "@/components/section-views/performance";
import { MatchCenterDetail } from "@/components/match-center-detail";

const views = [
  { id: "overview", label: "Genel Bakış", Icon: LayoutDashboard },
  { id: "matches", label: "Maç Merkezi", Icon: Activity },
  { id: "predictions", label: "AI Tahminleri", Icon: BrainCircuit },
  { id: "analysis", label: "İstatistik & Analiz", Icon: BarChart3 },
  { id: "archive", label: "Veri Arşivi", Icon: Archive },
  { id: "detail", label: "Maç Analiz Detayı", Icon: ChartNoAxesCombined },
] as const;

type View = (typeof views)[number]["id"];

export function StitchPreview() {
  const [view, setView] = useState<View>("overview");
  const [detail, setDetail] = useState<{id:string; source:"all"|"history"}|null>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const current = views.find(item => item.id === view) ?? views[0];
  const select = (next: View) => {
    setView(next);
    window.scrollTo({ top: 0, behavior: "auto" });
    requestAnimationFrame(() => contentRef.current?.focus());
  };

  return <div className="stitch-stage" data-testid="stitch-preview">
    <aside className="stitch-preview-sidebar" aria-label="Tasarım önizleme menüsü">
      <div className="stitch-preview-brand"><span className="stitch-preview-mark"><ChartNoAxesCombined size={22} /></span><span><strong>MA-ANALİZ</strong><small>FUTBOL VERİ PLATFORMU</small></span></div>
      <p className="stitch-preview-eyebrow">ÇALIŞMA ALANI</p>
      <nav aria-label="Stitch ekranları" className="stitch-preview-nav">
        {views.map(({ id, label, Icon }) => <button type="button" key={id} onClick={() => select(id)} className={"stitch-nav-item" + (view === id ? " is-active" : "")} aria-current={view === id ? "page" : undefined}><Icon size={19}/>{label}</button>)}
      </nav>
      <div className="stitch-preview-sidebar-footer"><ShieldCheck size={16}/><span>Yalnızca görsel önizleme. Veri yazma işlemi yok.</span></div>
      <Link className="stitch-back" href="/"><ArrowLeft size={16}/> Mevcut arayüze dön</Link>
    </aside>

    <div className="stitch-preview-main">
      <div className="stitch-preview-topbar">
        <div className="stitch-preview-top-heading"><span className="stitch-preview-mobile-logo"><ChartNoAxesCombined size={20}/> MA-ANALİZ</span><span className="stitch-preview-eyebrow">STITCH TASARIM ÖNİZLEMESİ / {current.label.toLocaleUpperCase("tr-TR")}</span></div>
        <span className="stitch-preview-label">TASLAK · GERÇEK API VERİSİ</span>
      </div>
      <p className="stitch-preview-data-note"><CircleHelp size={17} aria-hidden="true"/> Rakamlar, skorlar, tahminler ve arşiv bilgileri yalnızca mevcut API yanıtlarından gösterilir. Stitch örnek oranları ve örnek isabet yüzdeleri kullanılmaz.</p>
      <nav className="stitch-preview-context" aria-label="Diğer önizleme ekranları"><button type="button" onClick={() => select("archive")}>Veri Arşivi</button><button type="button" onClick={() => select("detail")}>Maç Detayı</button></nav>
      <span className="sr-only" role="status" aria-live="polite">{current.label} görünümü</span>
      <div className="stitch-preview-content" ref={contentRef} id="stitch-content" tabIndex={-1} onClickCapture={event => {
        if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        const anchor = (event.target as Element).closest("a");
        if (!anchor || anchor.target || anchor.origin !== window.location.origin) return;
        const found = /^\/matches\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i.exec(anchor.pathname);
        if (!found) return;
        event.preventDefault();
        setDetail({id:found[1], source:new URL(anchor.href).searchParams.get("source") === "history" ? "history" : "all"});
        select("detail");
      }}>
        {view === "overview" && <FootballHome />}
        {view === "matches" && <><header className="topbar"><div><div className="eyebrow">MA-ANALİZ / MAÇ MERKEZİ</div><h1 className="title">Maç Merkezi</h1><p className="page-intro">Lig, tarih ve takım filtreleriyle güncel kaynak bültenine erişin.</p></div></header><MatchCenter /></>}
        {view === "predictions" && <div className="stitch-view-predictions"><header className="topbar"><div><div className="eyebrow">MA-ANALİZ / AI TAHMİNLERİ</div><h1 className="title">AI Tahminleri</h1><p className="page-intro">Kayıtlı model seçimleri, kanıt durumu ve analiz zamanı.</p></div></header><StitchPredictionLedger /></div>}
        {view === "analysis" && <Performance strictResponse />}
        {view === "archive" && <><header className="topbar"><div><div className="eyebrow">MA-ANALİZ / VERİ ARŞİVİ</div><h1 className="title">Veri Arşivi</h1><p className="page-intro">PostgreSQL ve doğrulanmış arşiv kayıtları. Maç sayıları API kapsamına bağlıdır.</p></div></header><MatchCenter initialSource="history" lockHistory /></>}
        {view === "detail" && (detail ? <><header className="topbar"><h1 className="title">Maç Analiz Detayı</h1></header><MatchCenterDetail id={detail.id} source={detail.source}/></> : <section className="card stitch-preview-detail"><div className="eyebrow">MA-ANALİZ / MAÇ ANALİZ DETAYI</div><h1 className="title">Maç Analiz Detayı</h1><p className="page-intro">Bir maç seçerek gerçek kaydın detayına ulaşın. Maç seçilmeden skor veya tahmin gösterilmez.</p><p>Doğrulanmış oran verisi bulunmuyor.</p><button type="button" className="primary-button" onClick={() => select("archive")}>Arşivden maç seç</button></section>)}
      </div>
      <footer className="stitch-preview-footer">Bu ekran tasarım incelemesi içindir; gerçek oran, AI tahmini veya arşiv doğrulaması üretmez. Üretim temasını değiştirmez.</footer>
    </div>

    <nav className="stitch-preview-bottom-nav" aria-label="Mobil Stitch ekranları">
      {views.slice(0,4).map(({ id, Icon }, index) => <button type="button" key={id} aria-current={view === id ? "page" : undefined} className={view === id ? "is-active" : ""} onClick={() => select(id)}><Icon size={20}/><span>{["Genel Bakış", "Maçlar", "Tahminler", "Analiz"][index]}</span></button>)}
    </nav>
  </div>;
}
