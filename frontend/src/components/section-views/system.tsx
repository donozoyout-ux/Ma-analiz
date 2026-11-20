import {HealthPanel} from "@/components/health";
export default function System(){return <><header className="topbar"><div><div className="eyebrow">MAÇANALİZ / OPERASYON</div><h1 className="title">Sistem durumu</h1><p className="page-intro">Bağlantılar ve servis readiness bilgisi.</p></div></header><div className="card"><HealthPanel/></div></>}
