export const sections = {
 matches: { title: "Maç Merkezi", href: "/matches", tabs: [{ id: "matches", label: "Günün maçları" }, { id: "odds", label: "Oran analizi" }] },
 predictions: { title: "AI Tahminleri", href: "/predictions", tabs: [{ id: "predictions", label: "Yapay zekâ tahminleri" }, { id: "coupons", label: "Kupon simülasyonu" }] },
 analysis: { title: "İstatistik & Analiz", href: "/analysis", tabs: [{ id: "performance", label: "Tahmin performansı" }, { id: "lessons", label: "Eğitim merkezi" }] },
 archive: { title: "Veri Arşivi", href: "/archive", tabs: [{ id: "archive", label: "Geçmiş maçlar" }, { id: "storage", label: "Veri deposu" }] },
 management: { title: "Yönetim & Ayarlar", href: "/management", tabs: [{ id: "system", label: "Sistem durumu" }, { id: "settings", label: "Ayarlar" }] },
} as const;
export type SectionKey = keyof typeof sections;
export function selectedTab(section: SectionKey, value?: string | string[]) {
 return sections[section].tabs.find(tab => tab.id === value)?.id ?? sections[section].tabs[0].id;
}
export function tabHref(section: SectionKey, tab: string) {
 const config = sections[section];
 return tab === config.tabs[0].id ? config.href : `${config.href}?tab=${encodeURIComponent(tab)}`;
}
export const legacyDestinations = {
 odds: "/matches?tab=odds", coupons: "/predictions?tab=coupons", performance: "/analysis", lessons: "/analysis?tab=lessons", storage: "/archive?tab=storage", system: "/management", settings: "/management?tab=settings",
} as const;
export function activeSection(pathname: string) {
 if (pathname === "/") return "/";
 return Object.values(sections).find(s => pathname === s.href || pathname.startsWith(`${s.href}/`))?.href;
}
