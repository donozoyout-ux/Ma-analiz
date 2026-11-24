import { SectionTabs } from "@/components/section-tabs";
import {MatchCenterDetail} from "@/components/match-center-detail";
export default async function MatchDetail({params,searchParams}:{params:Promise<{id:string}>;searchParams:Promise<{source?:string}>}) {
 const {id}=await params;const query=await searchParams;
 return <><SectionTabs section="matches" active="detail" detailHref={`/matches/${id}`} /><header className="topbar"><div><div className="eyebrow">MA-ANALİZ / MAÇ DETAYI</div><h1 className="title">Maç kaydı ve doğrulanmış sonuç</h1><p className="page-intro">Kaynağı belli veriler, aynı maç kimliğiyle.</p></div></header><MatchCenterDetail id={id} source={query.source==="history"?"history":"all"}/></>;
}
