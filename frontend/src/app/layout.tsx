import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";
import "./motion.css";
import { Sidebar } from "@/components/sidebar";
const inter = localFont({src:"./fonts/inter.ttf",variable:"--font-inter",weight:"100 900",display:"swap"});
const space = localFont({src:"./fonts/spacegrotesk.ttf",variable:"--font-space",weight:"300 700",display:"swap"});
const bebas = localFont({src:"./fonts/bebasneue.ttf",variable:"--font-bebas",weight:"400",display:"swap",preload:false});

export const metadata: Metadata = { title: "Maçanaliz | Futbol Veri ve Analiz", description: "Gerçek maç verileri, oran hareketleri ve kanıta dayalı analiz paneli." };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="tr" className={`${inter.variable} ${space.variable} ${bebas.variable}`}><body><a className="skip-link" href="#main-content">İçeriğe geç</a><div className="shell"><Sidebar/><main id="main-content" className="main">{children}</main></div></body></html>;
}
