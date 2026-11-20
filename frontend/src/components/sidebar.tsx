"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity, BarChart3, Boxes, BrainCircuit, ChartNoAxesCombined, Gauge,
  LayoutDashboard, Settings2, ShieldCheck, Trophy,
} from "lucide-react";

const links = [
  ["Genel Bakış", "/", LayoutDashboard],
  ["Günün maçları", "/matches", Activity],
  ["Yapay zekâ tahminleri", "/predictions", BrainCircuit],
  ["Geçmiş maç arşivi", "/archive", Boxes],
  ["Oran analizi", "/odds", Activity],
  ["Kuponlar", "/coupons", Trophy],
  ["Tahmin performansı", "/performance", BarChart3],
  ["Eğitim merkezi", "/lessons", Gauge],
  ["Veri deposu", "/storage", Boxes],
  ["Sistem durumu", "/system", ShieldCheck],
  ["Ayarlar", "/settings", Settings2],
] as const;

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="sidebar">
      <Link className="brand" href="/" aria-label="Maçanaliz ana sayfa">
        <span className="brand-mark"><ChartNoAxesCombined size={19} /></span>
        <span className="brand-copy"><strong>MA-ANALİZ</strong><small>FUTBOL VERİ PLATFORMU</small></span>
      </Link>
      <div className="nav-label">ÇALIŞMA ALANI</div>
      <nav className="nav-links" aria-label="Ana menü">
        {links.map(([name, href, Icon]) => {
          const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
          return (
            <Link className={`nav-link${active ? " active" : ""}`} href={href} key={href} aria-current={active ? "page" : undefined}>
              <Icon size={17} strokeWidth={1.8} /><span>{name}</span>
            </Link>
          );
        })}
      </nav>
      <div className="sidebar-bottom">
        <div className="sidebar-note"><ShieldCheck size={15} />Yalnızca doğrulanmış veri</div>
        <small>Analiz ve sanal kupon simülasyonu</small>
      </div>
    </aside>
  );
}
