import { RecordsPanel } from "@/components/records-panel";
export default function Lessons() {
  return <><header className="topbar"><div><div className="eyebrow">MAÇANALİZ / DEĞERLENDİRME</div><h1 className="title">Eğitim merkezi</h1><p className="page-intro">Hatalardan çıkarılan dersler ve doğrulanmayı bekleyen öneriler.</p></div></header><RecordsPanel endpoint="lessons" columns={["date", "category", "title", "summary", "recommendation", "confidence", "status"]} empty="Henüz eğitim kaydı yok. Strateji değişiklikleri geriye dönük test edilmeden uygulanmaz." /></>;
}
