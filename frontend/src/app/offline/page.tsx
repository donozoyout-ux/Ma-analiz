import Link from "next/link";
import { WifiOff, RefreshCw } from "lucide-react";

export default function OfflinePage() {
  return (
    <div className="min-h-[70vh] flex items-center justify-center p-6">
      <div className="bg-white border border-slate-200 rounded-2xl max-w-md w-full p-8 text-center shadow-lg">
        <div className="w-16 h-16 mx-auto mb-5 bg-red-50 border-2 border-red-100 rounded-full flex items-center justify-center text-red-600">
          <WifiOff size={32} />
        </div>
        <span className="inline-block text-xs font-bold tracking-wider uppercase text-red-600 bg-red-50 px-3 py-1 rounded-full mb-3">
          ÇEVRİMDIŞI MOD
        </span>
        <h1 className="text-xl font-extrabold text-slate-900 mb-2">
          İnternet Bağlantısı Kesildi
        </h1>
        <p className="text-sm text-slate-600 leading-relaxed mb-6">
          Canlı maç sonuçları, oranlar ve yapay zekâ analizleri yalnızca gerçek zamanlı sunulur. Sahte veya eski verilerle işlem yapılmaması için canlı API çevrimdışı önbelleğe alınmaz.
        </p>
        <Link
          href="/"
          className="inline-flex items-center justify-center gap-2 w-full py-3 px-5 text-sm font-bold text-white bg-red-600 hover:bg-red-700 active:scale-[0.99] rounded-xl transition-all shadow-sm"
        >
          <RefreshCw size={16} />
          Tekrar Dene
        </Link>
        <div className="mt-4 p-3 bg-slate-50 border border-dashed border-slate-200 rounded-lg text-xs text-slate-500">
          🔒 Veri Güvenliği: Skor ve tahmin bütünlüğü gereği canlı veriler çevrimdışı önbelleklenmez.
        </div>
      </div>
    </div>
  );
}
