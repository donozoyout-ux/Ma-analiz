import type { Metadata } from "next";
import "./globals.css";
import "./motion.css";
import { Sidebar } from "@/components/sidebar";

export const metadata: Metadata = { title: "Maçanaliz | Futbol Veri ve Analiz", description: "Gerçek maç verileri, oran hareketleri ve kanıta dayalı analiz paneli." };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="tr"><body><a className="skip-link" href="#main-content">İçeriğe geç</a><div className="shell"><Sidebar/><main id="main-content" className="main">{children}</main></div></body></html>;
}
