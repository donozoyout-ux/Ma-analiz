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
 const workspace = useRef<HTMLDivElement>(null);
 const [rail,setRail] = useState({y:0,height:0,visible:false});
 useEffect(()=>{
  const box=workspace.current;
  if(!box)return;
  const measure=()=>{
   const link=box.querySelector<HTMLElement>('[aria-current="page"]');
   if(!link||!box.getClientRects().length){setRail(value=>({...value,visible:false}));return;}
   const rect=link.getBoundingClientRect(), parent=box.getBoundingClientRect();
   setRail({y:rect.top-parent.top+box.scrollTop+8,height:Math.max(0,rect.height-16),visible:true});
  };
  measure();const observer=new ResizeObserver(measure);observer.observe(box);
  return()=>observer.disconnect();
 },[pathname,open]);
 useEffect(() => {
  if (!open) return;
  const close = (e: KeyboardEvent) => { if (e.key === "Escape") { setOpen(false); toggle.current?.focus(); } };
  document.addEventListener("keydown", close);
  return () => document.removeEventListener("keydown", close);
 }, [open]);
 return <aside className={`sidebar streamlined-sidebar${open ? " menu-open" : ""}`}>
 <div className="sidebar-header"><Link className="brand" href="/" aria-label="Ma-analiz ana sayfa" onClick={() => setOpen(false)}><span className="brand-mark"><ChartNoAxesCombined size={19} /></span><span className="brand-copy"><strong>MA-ANALİZ</strong><small>FUTBOL VERİ PLATFORMU</small></span></Link>
 <button ref={toggle} className="mobile-menu-toggle" aria-expanded={open} aria-controls="workspace-navigation" aria-label={open ? "Menüyü kapat" : "Menüyü aç"} onClick={() => setOpen(!open)}>{open ? <X size={22} /> : <Menu size={22} />}</button></div>
 <div ref={workspace} id="workspace-navigation" className="workspace-navigation"><span aria-hidden="true" className="nav-motion-indicator" style={{transform:`translateY(${rail.y}px)`,height:rail.height,opacity:rail.visible?1:0}}/><div className="nav-label">ÇALIŞMA ALANI</div><nav className="nav-links" aria-label="Ana menü">
 {links.map(({title, href, Icon}) => <Link key={href} href={href} className={`nav-link${active === href ? " active" : ""}`} aria-current={active === href ? "page" : undefined} onClick={() => setOpen(false)}><Icon size={18} strokeWidth={1.8} /><span>{title}</span></Link>)}
 </nav><div className="management-navigation"><Link href="/management" className={`nav-link${active === "/management" ? " active" : ""}`} aria-current={active === "/management" ? "page" : undefined} onClick={() => setOpen(false)}><Settings2 size={18} /><span>Yönetim & Ayarlar</span></Link><p>Veriye dayalı futbol analizi</p></div></div></aside>;
}
