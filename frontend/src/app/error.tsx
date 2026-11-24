"use client";
export default function ErrorPage({ reset }: { reset: () => void }) {
 return <section className="card empty" role="alert"><h1 className="title">Sayfa yüklenemedi</h1><p>Geçici bir hata oluştu. Sayfayı yeniden yüklemeyi deneyin.</p><button className="quiet-button" onClick={reset}>Yeniden dene</button></section>;
}
