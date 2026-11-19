import Link from "next/link";
import { Activity, BarChart3, Boxes, BrainCircuit, CircleDollarSign, Gauge, LayoutDashboard, ScanSearch, Settings2, ShieldCheck, Trophy } from "lucide-react";

const links = [
  ["Overview", "/", LayoutDashboard], ["Matches", "/matches", Activity], ["Predictions", "/predictions", BrainCircuit],
  ["Paper coupons", "/coupons", Trophy], ["Performance", "/performance", BarChart3], ["Lessons", "/lessons", Gauge],
  ["Scanner", "/scanner", ScanSearch],
  ["Storage", "/storage", Boxes], ["System health", "/system", ShieldCheck], ["Settings", "/settings", Settings2],
] as const;
export function Sidebar(){return <aside className="sidebar"><Link className="brand" href="/"><span className="brand-mark"><CircleDollarSign size={18}/></span>MAÇ ANALİZ</Link><div className="nav-label">Workspace</div><nav className="nav-links">{links.map(([name,href,Icon],i)=><Link className={`nav-link ${i===0?"active":""}`} href={href} key={href}><Icon size={16}/>{name}</Link>)}</nav><div className="footer">Analysis and paper simulation only</div></aside>}
