import Link from "next/link";
import { sections, tabHref, type SectionKey } from "@/lib/navigation";
export function SectionTabs({ section, active, detailHref }: { section: SectionKey; active: string; detailHref?: string }) {
 const config = sections[section];
 return <nav className="section-tabs" aria-label={`${config.title} sekmeleri`}>
 {config.tabs.map(tab => <Link key={tab.id} href={tabHref(section, tab.id)} className={`section-tab${active === tab.id ? " active" : ""}`} aria-current={active === tab.id ? "page" : undefined}>{tab.label}</Link>)}
 {detailHref && <Link href={detailHref} className="section-tab active" aria-current="page">Maç detayı</Link>}
 </nav>;
}
