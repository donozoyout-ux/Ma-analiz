"use client";

import { useState } from "react";
import Link from "next/link";
import {
  Activity, Archive, ArrowLeft, BarChart3, BrainCircuit, ChartNoAxesCombined,
  CircleHelp, LayoutDashboard, ShieldCheck,
} from "lucide-react";
import { FootballHome } from "@/components/football-home";
import { MatchCenter } from "@/components/match-center";
import Predictions from "@/components/section-views/predictions";
import Performance from "@/components/section-views/performance";

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
  const current = views.find(item => item.id === view) ?? views[0];
  const select = (next: View) => {
    setView(next);
    window.scrollTo({ top: 0, behavior: "auto" });
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
      <main className="stitch-preview-content" key={view} id="stitch-content" tabIndex={-1}>
        {view === "overview" && <FootballHome />}
        {view === "matches" && <><header className="topbar"><div><div className="eyebrow">MA-ANALİZ / MAÇ MERKEZİ</div><h1 className="title">Maç Merkezi</h1><p className="page-intro">Lig, tarih ve takım filtreleriyle güncel kaynak bültenine erişin.</p></div></header><MatchCenter /></>}
        {view === "predictions" && <Predictions />}
        {view === "analysis" && <Performance strictResponse />}
        {view === "archive" && <><header className="topbar"><div><div className="eyebrow">MA-ANALİZ / VERİ ARŞİVİ</div><h1 className="title">Veri Arşivi</h1><p className="page-intro">PostgreSQL ve doğrulanmış arşiv kayıtları. Maç sayıları API kapsamına bağlıdır.</p></div></header><MatchCenter initialSource="history" lockHistory /></>}
        {view === "detail" && <section className="card stitch-preview-detail"><div className="eyebrow">MA-ANALİZ / MAÇ ANALİZ DETAYI</div><h1 className="title">Maç Analiz Detayı</h1><p className="page-intro">Bir maç seçerek gerçek kaydın detayına ulaşın. Maç seçilmeden skor veya tahmin gösterilmez.</p><p>Doğrulanmış oran yoksa maç detayında ayrıca belirtilir.</p><button type="button" className="primary-button" onClick={() => select("archive")}>Arşivden maç seç</button></section>}
      </main>
      <footer className="stitch-preview-footer">Bu ekran tasarım incelemesi içindir; gerçek oran, AI tahmini veya arşiv doğrulaması üretmez. Üretim temasını değiştirmez.</footer>
    </div>

    <nav className="stitch-preview-bottom-nav" aria-label="Mobil Stitch ekranları">
      {views.map(({ id, label, Icon }) => <button type="button" key={id} aria-current={view === id ? "page" : undefined} className={view === id ? "is-active" : ""} onClick={() => select(id)}><Icon size={20}/><span>{label}</span></button>)}
    </nav>
  </div>;
}
