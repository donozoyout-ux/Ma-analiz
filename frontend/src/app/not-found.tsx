import Link from "next/link";
export default function NotFound() {
 return <section className="card empty"><h1 className="title">Sayfa bulunamadı</h1><p>Aradığınız sayfa mevcut değil. Ana sayfadan maçlara ve analizlere ulaşabilirsiniz.</p><Link className="primary-button" href="/">Futbol Analiz Merkezi’ne dön</Link></section>;
}
