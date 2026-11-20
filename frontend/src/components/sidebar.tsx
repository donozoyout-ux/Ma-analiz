"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, BarChart3, Boxes, BrainCircuit, ChartNoAxesCombined, LayoutDashboard, Menu, Settings2, X } from "lucide-react";
import { activeSection, sections } from "@/lib/navigation";
const links = [
 { title: "Genel Bakış", href: "/", Icon: LayoutDashboard },
 { ...sections.matches, Icon: Activity },
 { ...sections.predictions, Icon: BrainCircuit },
 { ...sections.analysis, Icon: BarChart3 },
 { ...sections.archive, Icon: Boxes },
];
export function Sidebar() {
 const pathname = usePathname(), active = activeSection(pathname);
 const [open, setOpen] = useState(false);
 const toggle = useRef<HTMLButtonElement>(null);
 useEffect(() => {
  if (!open) return;
  const close = (e: KeyboardEvent) => { if (e.key === "Escape") { setOpen(false); toggle.current?.focus(); } };
  document.addEventListener("keydown", close);
  return () => document.removeEventListener("keydown", close);
 }, [open]);
 return <aside className={`sidebar streamlined-sidebar${open ? " menu-open" : ""}`}>
 <div className="sidebar-header"><Link className="brand" href="/" aria-label="Ma-analiz ana sayfa" onClick={() => setOpen(false)}><span className="brand-mark"><ChartNoAxesCombined size={19} /></span><span className="brand-copy"><strong>MA-ANALİZ</strong><small>FUTBOL VERİ PLATFORMU</small></span></Link>
 <button ref={toggle} className="mobile-menu-toggle" aria-expanded={open} aria-controls="workspace-navigation" aria-label={open ? "Menüyü kapat" : "Menüyü aç"} onClick={() => setOpen(!open)}>{open ? <X size={22} /> : <Menu size={22} />}</button></div>
 <div id="workspace-navigation" className="workspace-navigation"><div className="nav-label">ÇALIŞMA ALANI</div><nav className="nav-links" aria-label="Ana menü">
 {links.map(({title, href, Icon}) => <Link key={href} href={href} className={`nav-link${active === href ? " active" : ""}`} aria-current={active === href ? "page" : undefined} onClick={() => setOpen(false)}><Icon size={18} strokeWidth={1.8} /><span>{title}</span></Link>)}
 </nav><div className="management-navigation"><Link href="/management" className={`nav-link${active === "/management" ? " active" : ""}`} aria-current={active === "/management" ? "page" : undefined} onClick={() => setOpen(false)}><Settings2 size={18} /><span>Yönetim & Ayarlar</span></Link><p>Veriye dayalı futbol analizi</p></div></div></aside>;
}
