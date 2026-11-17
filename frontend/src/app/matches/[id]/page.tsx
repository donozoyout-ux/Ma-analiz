import {MatchOdds} from "@/components/match-odds";
export default async function MatchDetail({params}:{params:Promise<{id:string}>}){const {id}=await params;return <><header className="topbar"><div><div className="eyebrow">Match detail</div><h1 className="title">Bookmaker odds</h1></div></header><MatchOdds matchId={id}/></>}
