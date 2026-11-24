import {HealthPanel} from "@/components/health";
export default function System(){return <><header className="topbar"><div><div className="eyebrow">MAÇANALİZ / OPERASYON</div><h1 className="title">Sistem durumu</h1><p className="page-intro">Bağlantı durumları ve servislerin kullanıma hazır olma bilgisi.</p></div></header><div className="card"><HealthPanel/></div></>}
