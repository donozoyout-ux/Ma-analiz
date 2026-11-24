import { RecordsPanel } from "@/components/records-panel";
export default function Coupons() {
  return <><header className="topbar"><div><div className="eyebrow">SANAL KUPON / GERÇEK İŞLEM YOK</div><h1 className="title">Kuponlar</h1><p className="page-intro">Sistemin oluşturduğu kupon kayıtları ve sonuç durumu.</p></div></header><RecordsPanel endpoint="coupons" columns={["created_at", "type", "total_odds", "confidence", "status", "items"]} empty="Henüz kupon kaydı yok. Sistem gerçek bahis işlemi yapmaz." /></>;
}
