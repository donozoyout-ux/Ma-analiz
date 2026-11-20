import Link from "next/link";
import { CalendarDays, ChevronRight, CircleHelp, Sparkles } from "lucide-react";
import { HealthPanel } from "@/components/health";
import { StoragePanel } from "@/components/storage-panel";
import { OverviewMetrics } from "@/components/overview-metrics";
import { MatchExplorer } from "@/components/match-explorer";
import { DashboardCharts } from "@/components/dashboard-charts";

export default function Dashboard() {
  return (
    <>
      <header className="topbar page-heading">
        <div>
          <div className="eyebrow"><span className="eyebrow-mark" />MAÇANALİZ <span className="eyebrow-divider">/</span> VERİ MERKEZİ</div>
          <h1 className="title">Oyunun arkasındaki veriyi keşfet.</h1>
          <p className="page-intro">Maç takvimi, oran hareketleri ve doğrulanmış analizler tek ekranda.</p>
        </div>
        <div className="heading-actions">
          <span className="today-chip"><CalendarDays size={15} /> Güncel görünüm</span>
          <Link className="primary-button" href="/matches">Maçları incele</Link>
        </div>
      </header>

      <section className="welcome-panel">
        <div className="welcome-orbit orbit-one" /><div className="welcome-orbit orbit-two" />
        <div className="welcome-content">
          <div className="welcome-kicker"><Sparkles size={14} /> ANALİZ MASASI</div>
          <h2>Önce veri. Sonra yorum.</h2>
          <p>Fikstürden takım formuna, oran hareketlerinden model analizine: futbol verilerini aynı çalışma alanında değerlendirin.</p>
          <Link href="/matches" className="text-link">Bugünün maçlarına git <ChevronRight size={16} /></Link>
        </div>
        <div className="welcome-side-note"><CircleHelp size={15} /><span>Henüz veri yoksa değerler boş görünür; örnek veri kullanılmaz.</span></div>
      </section>

      <section className="section-block">
        <div className="section-heading-row"><div><div className="section-kicker">BUGÜNÜN PANOSU</div><h2 className="section-heading">Günlük veri özeti</h2></div><span className="data-source-label">PostgreSQL kayıtları</span></div>
        <OverviewMetrics />
      </section>

      <section className="grid dashboard-main-grid">
        <div className="card chart-card">
          <div className="section-head"><div><div className="section-kicker">GEÇMİŞTEN BUGÜNE</div><div className="section-title">Aktivite ve veri büyümesi</div></div><div className="chart-legend"><span><i className="legend-dot mint" />Maç</span><span><i className="legend-dot blue" />Depolama</span></div></div>
          <DashboardCharts />
        </div>
        <div className="card health-card">
          <div className="section-head"><div><div className="section-kicker">BAĞLANTI DURUMU</div><div className="section-title">Altyapı sağlığı</div></div><Link className="icon-link" href="/system" aria-label="Sistem ayrıntıları"><ChevronRight size={16} /></Link></div>
          <HealthPanel compact />
        </div>
      </section>

      <section className="grid dashboard-lower-grid">
        <div className="card storage-card">
          <div className="section-head"><div><div className="section-kicker">AKTİF + TARİHSEL</div><div className="section-title">Veri depolama</div></div><Link className="text-link compact-link" href="/storage">Ayrıntılar <ChevronRight size={15} /></Link></div>
          <StoragePanel />
        </div>
        <div className="matches-preview">
          <div className="section-head"><div><div className="section-kicker">SAĞLAYICIDAN GELEN</div><div className="section-title">Günün maçları</div></div><Link className="text-link compact-link" href="/matches">Tüm maçlar <ChevronRight size={15} /></Link></div>
          <MatchExplorer compact />
        </div>
      </section>
      <footer className="dashboard-footer"><span>MAÇANALİZ · VERİ KAYNAKLARI VE ANALİZ</span><span><i className="legend-dot mint" /> Değerler bağlı API’den yüklenir</span></footer>
    </>
  );
}
