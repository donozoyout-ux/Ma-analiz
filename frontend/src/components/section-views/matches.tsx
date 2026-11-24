import Link from "next/link";
import { MatchCenter } from "@/components/match-center";
export default function Page() { return <><header className="topbar"><div><div className="eyebrow">MA-ANALİZ / MAÇ MERKEZİ</div><h1 className="title">Maç Merkezi</h1><p className="page-intro">Güncel kayıtlar ve doğrulanmış futbol geçmişi. Aynı maç, tek görünüm.</p></div><span className="today-chip">Türkiye saati · TSİ</span></header><p className="scope-note"><Link href="/europe">Avrupa Kupaları: Şampiyonlar Ligi, Avrupa Ligi, Konferans Ligi →</Link></p><MatchCenter /></>; }
