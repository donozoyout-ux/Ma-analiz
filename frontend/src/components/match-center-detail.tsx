"use client";
import Link from "next/link";
import { useState } from "react";
import { ArrowLeft, CalendarDays, ShieldCheck } from "lucide-react";
import { type Match, formatMatchTime, statusLabel, verifiedScoreText } from "@/lib/matches";
import { useMatchRead } from "@/lib/use-match-read";
import { displayStatus } from "@/lib/turkish";
import { MatchOdds } from "./match-odds";
import { MatchShareActions } from "./match-share-actions";

export function MatchCenterDetail({id,source="all"}:{id:string;source?:"all"|"history"}) {
 const {data:match,error,refresh} = useMatchRead<Match>(`match-center/${encodeURIComponent(id)}?source=${source}`);
 const [legacy,setLegacy] = useState(false);
 if(error)return <section className="card empty" role="alert"><strong>Maç detayı okunamadı</strong><span>Kayıt bulunamıyor veya arşiv doğrulaması tamamlanamıyor. Skor üretilmedi.</span><button className="quiet-button" onClick={refresh}>Yeniden dene</button><Link className="text-link" href="/matches">Maç Merkezi’ne dön</Link></section>;
 if(!match)return <section className="card empty" role="status">Maç kaydı doğrulanıyor…</section>;
 const score=verifiedScoreText(match.result);
 return <div className="match-detail-stack">
  <div className="match-detail-toolbar"><Link className="text-link center-back" href="/matches"><ArrowLeft size={15}/> Maç Merkezi</Link><MatchShareActions homeTeam={match.home_team} awayTeam={match.away_team}/></div>
  <section className="match-hero card center-hero">
   <div><div className="section-kicker">{match.league_name??"Lig belirtilmedi"} · {match.season??"Sezon belirtilmedi"}</div><h2>{match.home_team}<span>karşı</span>{match.away_team}</h2><p><CalendarDays size={15}/>{formatMatchTime(match.start_time)} · TSİ</p><span className="badge">{statusLabel(match.status)}</span></div>
   <div className="center-score"><strong>{score??"— : —"}</strong><span>{score?"Doğrulanmış final skor":"Skor henüz doğrulanmadı"}</span>{score&&<small><ShieldCheck size={14}/>{formatMatchTime(match.result?.verified_at)}</small>}</div>
  </section>
  <section className="card center-provenance" aria-labelledby="match-source-heading">
   <div className="section-kicker">VERİNİN KAYNAĞI</div><h2 className="section-title" id="match-source-heading">{match.source?.name??"Kaynak doğrulanmadı"}</h2>
   <p>{match.source?.attribution??"Bu kayıt için doğrulanmış kaynak atfı mevcut değil."}</p>
   <div className="center-meta"><span>Kapsam <strong>{match.storage==="R2"?"Doğrulanmış R2 arşivi":"PostgreSQL operasyonel havuzu"}</strong></span><span>Son güncelleme <strong>{formatMatchTime(match.last_updated_at)}</strong></span>{match.archive_verified_at&&<span>Arşiv okuma doğrulaması <strong>{formatMatchTime(match.archive_verified_at)}</strong></span>}</div>
   {match.source&&<a className="text-link" href={match.source.licence_url} target="_blank" rel="noopener noreferrer">{match.source.licence} · Kaynak lisansı</a>}
  </section>
  <div className="grid football-insights">
   <section className="card insight-card"><div className="section-kicker">TEKNİK İSTATİSTİK</div><h2 className="section-title">Maç istatistikleri</h2>
    {match.statistics?.length ? <dl className="center-statistics">{match.statistics.map((row,i)=><div key={`${row.name}-${i}`}><dt>{({"Shots":"Şut","Shots On Goal":"İsabetli şut","Corner Kicks":"Korner","Possession":"Topa sahip olma","Fouls":"Faul","Yellow Cards":"Sarı kart","Red Cards":"Kırmızı kart","Offsides":"Ofsayt"} as Record<string,string>)[row.name]??"Doğrulanmış istatistik"}</dt><dd>{row.home} – {row.away}{row.unit==="percent"?" %":""}</dd></div>)}</dl>:<p className="insight-empty">İstatistik verisi mevcut değil</p>}
   </section>
   <section className="card insight-card"><div className="section-kicker">KAYITLI ANALİZ</div><h2 className="section-title">Analiz özeti</h2>
    {match.analysis_summary ? <><p>{displayStatus(match.analysis_summary.signal)}</p><p className="scope-note">Model {match.analysis_summary.model_version} · {formatMatchTime(match.analysis_summary.analysis_timestamp)}</p><p className="scope-note">Analiz değerlendirmedir; gerçekleşmiş sonuç veya kazanma garantisi değildir.</p></>:<p className="insight-empty">Analiz henüz hazırlanmadı</p>}
   </section>
  </div>
  <section className="card insight-card"><h2 className="section-title">Oran ve gelişmiş analiz kayıtları</h2><p className="scope-note">Yalnız mevcut kayıtlar gösterilir. Bu ekran analiz çalıştırmaz veya veri üretmez.</p>
   {match.storage==="POSTGRESQL" ? <button className="quiet-button" aria-expanded={legacy} onClick={()=>setLegacy(!legacy)}>{legacy?"Kayıtları kapat":"Mevcut kayıtları aç"}</button>:<p className="insight-empty">Bu tarihsel görünümde doğrulanmış oran kaydı sunulmuyor.</p>}
  </section>
  {legacy&&match.storage==="POSTGRESQL"&&<MatchOdds matchId={match.id}/>}
  <p className="scope-note">Saatler Europe/Istanbul saat dilimindedir. Görünüm kendiliğinden canlı yenilenmez; doğrulama zamanı son okumayı, güncelleme zamanı kaynak kaydını belirtir.</p>
 </div>;
}
