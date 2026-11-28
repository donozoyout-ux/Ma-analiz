import Link from "next/link";
import { EuropeanCups } from "@/components/european-cups";
import { R2ChampionsArchive } from "@/components/r2-champions-archive";

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
    <p><Link href="/leagues">Lig Seçimi ve sezon kapsamı</Link></p>
    <EuropeanCups />
    <R2ChampionsArchive />
  </>;
}
