import { RecordsPanel } from "@/components/records-panel";
export default function Predictions() {
  return <><header className="topbar"><div><div className="eyebrow">MAÇANALİZ / AI ANALİZİ</div><h1 className="title">Yapay zekâ tahminleri</h1><p className="page-intro">Model olasılıkları birer analizdir; gerçekleşmiş sonuç veya garanti değildir.</p></div></header><div className="status-strip">Yayın zamanı ve değiştirilemez yayın kaydı henüz mevcut değil. Kayıt ve analiz zamanları yayın zamanı sayılmaz.</div><RecordsPanel endpoint="predictions" columns={["created_at", "analysis_timestamp", "published_at", "market", "selection", "probability", "confidence", "model_version", "evidence_status", "status", "settled_at"]} empty="Kanıt şartlarını karşılayan tahmin kaydı yok." /></>;
}
