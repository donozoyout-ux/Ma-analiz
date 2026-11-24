import { EuropeanCups } from "@/components/european-cups";

export default function EuropePage() {
  return <>
    <header className="topbar">
      <div>
        <div className="eyebrow">MA-ANALİZ / AVRUPA KUPALARI</div>
        <h1 className="title">Bundesliga ve Avrupa Kupaları</h1>
        <p className="page-intro">2026–27 Bundesliga, Şampiyonlar Ligi, Avrupa Ligi ve Konferans Ligi maçlarını OpenLigaDB üzerinden görüntüle.</p>
      </div>
      <span className="today-chip">Türkiye saati · TSİ</span>
    </header>
    <EuropeanCups />
  </>;
}
