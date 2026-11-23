import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import "./globals.css";
import "./motion.css";
import "./premium-dashboard.css";
import { Sidebar } from "@/components/sidebar";
import { ApplicationSurface } from "@/components/application-surface";
import { PwaRegister } from "@/components/pwa-register";

const inter = localFont({src:"./fonts/inter.ttf",variable:"--font-inter",weight:"100 900",display:"swap"});
const space = localFont({src:"./fonts/spacegrotesk.ttf",variable:"--font-space",weight:"300 700",display:"swap"});
const bebas = localFont({src:"./fonts/bebasneue.ttf",variable:"--font-bebas",weight:"400",display:"swap",preload:false});

export const viewport: Viewport = {
  themeColor: "#FFFFFF",
  width: "device-width",
  initialScale: 1,
  maximumScale: 5,
};

export const metadata: Metadata = {
  title: "Maçanaliz | Futbol Veri ve Analiz",
  description: "Gerçek maç verileri, oran hareketleri ve kanıta dayalı analiz paneli.",
  manifest: "/manifest.json",
  appleWebApp: {
    capable: true,
    statusBarStyle: "default",
    title: "Maç Analiz",
  },
  icons: {
    icon: "/favicon.svg",
    apple: "/apple-touch-icon.png",
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="tr" className={`${inter.variable} ${space.variable} ${bebas.variable}`}>
      <body>
        <a className="skip-link" href="#main-content">İçeriğe geç</a>
        <ApplicationSurface>
          <div className="shell">
            <Sidebar />
            <main id="main-content" className="main">
              {children}
            </main>
          </div>
        </ApplicationSurface>
        <PwaRegister />
      </body>
    </html>
  );
}
