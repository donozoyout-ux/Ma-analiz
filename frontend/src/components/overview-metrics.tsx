import { CalendarDays, ChartNoAxesCombined, Target } from "lucide-react";
export function OverviewMetrics({ matches, analyzed, rate, loading }: { matches: number | null; analyzed: number | null; rate: number | null; loading: boolean }) {
 const items = [
  { label: "SQL’deki bugünkü maçlar", value: matches, note: "Yalnızca kaydedilmiş maçlar · kaynak bülteni aşağıda", Icon: CalendarDays },
  { label: "Analiz edilen maçlar", value: analyzed, note: "Bugünün maçları · model analizi tamamlanan", Icon: ChartNoAxesCombined },
  { label: "Doğrulanmış tahmin isabeti", value: rate == null ? null : `%${(rate * 100).toFixed(1)}`, note: "Son 30 gün · sonuçlandırılmış kayıtlar", Icon: Target },
 ];
 return <section className="grid football-kpis" aria-label="Futbol analiz özeti" aria-busy={loading}>
  {items.map(({label,value,note,Icon}) => <div className="card football-kpi" key={label}><div className="football-kpi-top"><span>{label}</span><Icon size={19}/></div><strong className="football-kpi-value">{value ?? "—"}</strong><small>{value == null ? "Doğrulanmış veri bekleniyor" : note}</small></div>)}
 </section>;
}
