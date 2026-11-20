"use client";
import { FormEvent, useState } from "react";

type Inventory = {status:string; emptiness:string; schemas?:string[]; relations?:{schema:string; name:string; kind:string; estimated_rows:number|null}[]; target_fingerprint?:string; target_confirmation?:string; configured_database_name_matches?:boolean; server_version_num?:string; is_replica?:boolean};
type Check = {status:string; inventory?:Inventory; alembic_version_exists?:boolean; alembic_version_state?:string; error_family?:string; http_status?:number; service_code?:string; missing_env:string[]; invalid_env:string[]; schema?:string; current_revisions?:string[]; expected_revisions?:string[]; unknown_revision?:boolean; default_schema?:string; other_schemas?:Record<string,string[]>; alembic_version_other_schemas?:string[]; schema_issues?:string[]; error_category?:string; missing_tables?:string[]; missing_columns?:string[]};
type Report = {checked_at:string; postgres:Check; r2:Check};
const labels:Record<string,string> = {CONNECTED:"Bağlantı başarılı", ACCESSIBLE:"Bucket erişimi başarılı", NOT_CONFIGURED:"Yapılandırma eksik", INVALID_CONFIGURATION:"Yapılandırma geçersiz", FAILED:"Kontrol başarısız", READY:"Hazır", INCOMPLETE:"Tablo veya sütun eksik", NOT_VERIFIED:"Doğrulanmadı"};

export function ConnectionChecks(){
  const [token,setToken]=useState("");
  const [report,setReport]=useState<Report|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  async function check(event:FormEvent<HTMLFormElement>){
    event.preventDefault(); setBusy(true); setError(""); setReport(null);
    const credential=token; setToken("");
    try {
      const response=await fetch("/api/v1/system/connections",{method:"POST",headers:{Authorization:`Bearer ${credential}`},cache:"no-store"});
      if(!response.ok){
        setError(response.status===401?"Erişim anahtarı geçersiz.":response.status===503?"Render Environment içinde SYSTEM_DIAGNOSTICS_TOKEN tanımlayın (en az 32 karakter).":response.status===429?"Kontrol devam ediyor veya çok yakın zamanda çalıştırıldı. 30 saniye sonra tekrar deneyin.":"Bağlantı kontrolü tamamlanamadı.");
        return;
      }
      setReport(await response.json());
    } catch {setError("API erişilemiyor. Sonuç doğrulanamadı.");}
    finally {setBusy(false);}
  }
  return <section className="card" aria-label="Güvenli bağlantı kontrolleri">
    <h2>PostgreSQL ve Cloudflare R2 bağlantıları</h2>
    <p>Salt okunur kontrol: PostgreSQL tabloları ve sütunları ile R2 bucket erişimi doğrulanır. R2 yazma yetkisi ve arşiv bütünlüğü bu kontrolün kapsamında değildir.</p>
    <form onSubmit={check}>
      <label htmlFor="diagnostics-token">Sistem kontrol anahtarı</label>{" "}
      <input id="diagnostics-token" type="password" value={token} onChange={e=>setToken(e.target.value)} autoComplete="off" required minLength={32} disabled={busy}/>{" "}
      <button type="submit" disabled={busy}>{busy?"Kontrol ediliyor…":"Bağlantıları kontrol et"}</button>
    </form>
    <p>Anahtar Render’daki SYSTEM_DIAGNOSTICS_TOKEN değeridir. Tarayıcıda kalıcı saklanmaz; her kontrolde yeniden girilir.</p>
    <div aria-live="polite">
      {error&&<p role="alert">{error}</p>}
      {report&&<><p>Son kontrol: {new Date(report.checked_at).toLocaleString("tr-TR")}</p>
        {([['PostgreSQL',report.postgres],['Cloudflare R2',report.r2]] as const).map(([name,result])=><div key={name}>
          <h3>{name}: {labels[result.status]??"Doğrulanmadı"}</h3>
          {result.schema&&<p>Tablolar ve sütunlar: {labels[result.schema]??"Doğrulanmadı"}</p>}
          {result.error_category&&<p>Hata kategorisi: {result.error_category}</p>}
          {result.error_family&&<p>Tanılama: {result.error_family} · HTTP: {result.http_status??"Yok"} · Servis kodu: {result.service_code??"Yok"}</p>}
          {result.alembic_version_state&&<p>alembic_version tablosu: {result.alembic_version_exists?"Var":"Yok"} · Durum: {result.alembic_version_state}</p>}
          {result.inventory&&<div>
            <h4>Veritabanı envanteri: {result.inventory.status}</h4>
            <p>Boşluk kontrolü: {result.inventory.emptiness}. Kapsam: kullanıcı tabloları, görünümler ve sequence nesneleri. Satır tahmini sıfır olması boşluk kanıtı değildir.</p>
            <p>Hedef parmak izi: {result.inventory.target_fingerprint??"Doğrulanmadı"} · Hedef eşleşmesi: {result.inventory.target_confirmation??"NOT_VERIFIED"}</p>
            <p>Yapılandırılan veritabanı adı eşleşiyor: {result.inventory.configured_database_name_matches===undefined?"Doğrulanmadı":result.inventory.configured_database_name_matches?"Evet":"Hayır"} · PostgreSQL sürüm numarası: {result.inventory.server_version_num??"Yok"} · Replika: {result.inventory.is_replica===undefined?"Doğrulanmadı":result.inventory.is_replica?"Evet":"Hayır"}</p>
            <p>Şemalar: {result.inventory.schemas?.join(", ")??"Doğrulanmadı"}</p>
            {!!result.inventory.relations?.length&&<table><thead><tr><th>Şema</th><th>Nesne</th><th>Tür</th><th>Tahmini satır</th></tr></thead><tbody>{result.inventory.relations.map(row=><tr key={`${row.schema}.${row.name}`}><td>{row.schema}</td><td>{row.name}</td><td>{row.kind}</td><td>{row.estimated_rows??"Bilinmiyor"}</td></tr>)}</tbody></table>}
          </div>}
          {result.current_revisions&&<p>Alembic revision: {result.current_revisions.join(", ")||"Yok"}{result.unknown_revision?" · Bilinmeyen revision mevcut":""} · Beklenen: {result.expected_revisions?.join(", ")}</p>}
          {result.default_schema&&<p>Varsayılan şema: {result.default_schema}</p>}
          {Object.entries(result.other_schemas??{}).map(([schema,tables])=><p key={schema}>Diğer şema ({schema}): {tables.join(", ")}</p>)}
          {!!result.alembic_version_other_schemas?.length&&<p>Diğer şemalarda Alembic tablosu: {result.alembic_version_other_schemas.join(", ")}</p>}
          {!!result.schema_issues?.length&&<p>Şema uyarıları: {result.schema_issues.join(", ")}</p>}
          {!!result.missing_env.length&&<p>Eksik değişkenler: {result.missing_env.join(", ")}</p>}
          {!!result.invalid_env.length&&<p>Geçersiz değişkenler: {result.invalid_env.join(", ")}</p>}
          {!!result.missing_tables?.length&&<p>Eksik tablolar: {result.missing_tables.join(", ")}</p>}
          {!!result.missing_columns?.length&&<p>Eksik sütunlar: {result.missing_columns.join(", ")}</p>}
        </div>)}
      </>}
    </div>
  </section>;
}
