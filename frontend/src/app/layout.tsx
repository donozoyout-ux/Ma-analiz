import type { Metadata } from "next";
import "./globals.css";
import { Sidebar } from "@/components/sidebar";

export const metadata: Metadata = { title: "Maç Analiz | Analytics", description: "Sports analytics and paper simulation dashboard" };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="tr"><body><div className="shell"><Sidebar/><main className="main">{children}</main></div></body></html>;
}
