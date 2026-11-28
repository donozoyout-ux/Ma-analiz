import { MatchCenter } from "@/components/match-center";
import { R2BundesligaArchive } from "@/components/r2-bundesliga-archive";
export default function Page() { return <><header className="topbar"><div><div className="eyebrow">MA-ANALİZ / VERİ ARŞİVİ</div><h1 className="title">Geçmiş maç arşivi</h1><p className="page-intro">Doğrulanmış arşiv parçalarındaki maçları inceleyin. Kalıcı geçmiş, aynı maç kimliği.</p></div><span className="today-chip">Türkiye saati · TSİ</span></header><R2BundesligaArchive /><MatchCenter initialSource="history" /></>; }
