"use client";
import { useEffect, useState } from "react";
import { istanbulDay } from "@/lib/matches";
import { Activity, Database, Target, TrendingUp, Trophy } from "lucide-react";

type Row = Record<string, number | string | null>;
type Snapshot = { matches: Row[]; predictions: Row[]; performance: Row[] } | null;
const dayInIstanbul = istanbulDay;

export function OverviewMetrics() {
  const [data, setData] = useState<Snapshot>(null);
  useEffect(() => {
    Promise.all(["matches?limit=500", "predictions?limit=500", "performance"].map(async (path) => {
      const response = await fetch(`/api/v1/${path}`);
      if (!response.ok) throw new Error(`API_${response.status}`);
      const rows = await response.json();
      if (!Array.isArray(rows)) throw new Error("INVALID_RESPONSE");
      return rows;
    })).then(([matches, predictions, performance]) => setData({ matches, predictions, performance }))
      .catch(() => setData(null));
  }, []);

  const today = dayInIstanbul(new Date());
  const todaysMatches = data?.matches.filter((row) => dayInIstanbul(new Date(String(row.start_time))) === today).length;
  const metric = data?.performance.find((row) => row.date === today);
  const values = [
    todaysMatches === undefined ? "—" : String(todaysMatches),
    metric?.analyzed_matches == null ? "—" : String(metric.analyzed_matches),
    metric?.predictions == null ? "—" : String(metric.predictions),
    metric?.hit_rate == null ? "—" : `${(Number(metric.hit_rate) * 100).toFixed(1)}%`,
    metric?.paper_pl == null ? "—" : Number(metric.paper_pl).toFixed(2),
  ];
  const items = [
    ["BUGÜNÜN MAÇLARI", Activity], ["ANALİZ EDİLEN", Target],
    ["TAHMİNLER", TrendingUp], ["İSABET ORANI", Trophy], ["SANAL KÂR / ZARAR", Database],
  ] as const;

  return (
    <section className="grid kpis" aria-label="Günlük performans göstergeleri">
      {items.map(([label, Icon], index) => (
        <div className="card kpi-card" key={label}>
          <div className="kpi-label">{label}</div>
          <div className="kpi-value"><Icon aria-hidden="true" />{values[index]}</div>
          <div className="kpi-note">{data ? index === 0 ? "İlk 500 aktif kayıt içinden" : "Günlük API özeti" : "Doğrulanmış veri bekleniyor"}</div>
        </div>
      ))}
    </section>
  );
}
