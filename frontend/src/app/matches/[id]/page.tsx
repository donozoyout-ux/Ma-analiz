import { SectionTabs } from "@/components/section-tabs";
import {MatchOdds} from "@/components/match-odds";
export default async function MatchDetail({params}:{params:Promise<{id:string}>}){const {id}=await params;return <><SectionTabs section="matches" active="detail" detailHref={`/matches/${id}`} /><header className="topbar"><div><div className="eyebrow">MAÇANALİZ / MAÇ DETAYI</div><h1 className="title">Maç ve oran geçmişi</h1><p className="page-intro">Açılış, güncel ve kapanış değerleri birbirinden ayrı gösterilir.</p></div></header><MatchOdds matchId={id}/></>}
