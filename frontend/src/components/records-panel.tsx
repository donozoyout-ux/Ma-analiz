"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { displayStatus, marketLabel, apiErrorMessage } from "@/lib/turkish";

type Row = Record<string, unknown>;
const columnLabels: Record<string, string> = {
  start_time: "BAŞLANGIÇ", created_at: "KAYIT TARİHİ", published_at: "YAYIN ZAMANI", analysis_timestamp: "ANALİZ ZAMANI", model_version: "MODEL SÜRÜMÜ", settled_at: "SONUÇLANDIRMA", date: "TARİH", league_name: "LİG",
  home_team: "EV SAHİBİ", away_team: "DEPLASMAN", status: "DURUM", market: "PAZAR",
  selection: "SEÇİM", probability: "OLASILIK", confidence: "GÜVEN", evidence_status: "KANIT",
  type: "TÜR", total_odds: "TOPLAM ORAN", items: "SEÇİMLER", hit_rate: "İSABET",
  total_matches: "MAÇ", analyzed_matches: "ANALİZ", predictions: "TAHMİN", wins: "KAZANÇ",
  losses: "KAYIP", r2_bytes: "R2", postgres_bytes: "POSTGRESQL", category: "KATEGORİ",
  title: "BAŞLIK", summary: "ÖZET", recommendation: "ÖNERİ",
};

function formatValue(column: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (column === "items" && Array.isArray(value)) return `${value.length} seçim`;
  if (["start_time", "created_at", "date", "published_at", "analysis_timestamp", "settled_at"].includes(column)) {
    const date = new Date(String(value));
    if (!Number.isNaN(date.getTime())) {
      return column === "date"
        ? new Intl.DateTimeFormat("tr-TR", { dateStyle: "medium", timeZone: "Europe/Istanbul" }).format(date)
        : new Intl.DateTimeFormat("tr-TR", { dateStyle: "medium", timeStyle: "short", timeZone: "Europe/Istanbul" }).format(date);
    }
  }
  if (["probability", "hit_rate", "confidence"].includes(column) && Number.isFinite(Number(value))) return `${(Number(value) * 100).toFixed(1)}%`;
  if (["r2_bytes", "postgres_bytes"].includes(column) && typeof value === "number") return `${(value / 1048576).toFixed(1)} MB`;
  if (typeof value === "number") return Number.isInteger(value) ? value.toLocaleString("tr-TR") : value.toLocaleString("tr-TR", { maximumFractionDigits: 2 });
  if (typeof value === "object") return "Ayrıntılar için kaydı açın";
  const raw = String(value);
  if (["status", "evidence_status", "type", "category"].includes(column)) return displayStatus(raw);
  if (["market", "selection"].includes(column)) return marketLabel(raw);
  return raw;
}

export function RecordsPanel({ endpoint, columns, empty }: { endpoint: string; columns: string[]; empty: string }) {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setRows(null); setError(null);
    fetch(`/api/v1/${endpoint}`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) {
          let body: Row = {};
          try { body = await response.json(); } catch { /* empty error response */ }
          throw new Error(String(body.detail || `API_${response.status}`));
        }
        const data = await response.json();
        if (!Array.isArray(data)) throw new Error("INVALID_RESPONSE");
        return data;
      })
      .then(setRows)
      .catch((reason: unknown) => {
        if (reason instanceof Error && reason.name === "AbortError") return;
        setError(reason instanceof Error ? reason.message : "API_UNAVAILABLE");
      });
    return () => controller.abort();
  }, [endpoint]);

  if (error) return <div className="card empty">{apiErrorMessage(error)}</div>;
  if (rows === null) return <div className="card empty" aria-live="polite">Doğrulanmış kayıtlar yükleniyor…</div>;
  if (rows.length === 0) return <div className="card empty">{empty}</div>;

  return (
    <div className="card records-card">
      <div className="records-scroll"><table className="records-table">
        <thead><tr>{columns.map((column) => <th key={column}>{columnLabels[column] ?? column.replaceAll("_", " ").toLocaleUpperCase("tr-TR")}</th>)}</tr></thead>
        <tbody>{rows.map((row, index) => {
          const key = String(row.id ?? row.date ?? index);
          const detailHref = row.id && columns.some((column) => column === "home_team") ? `/matches/${row.id}` : null;
          return <tr key={key}>
            {columns.map((column) => <td key={column}>
              {detailHref && (column === "home_team" || column === "away_team")
                ? <Link className="record-link" href={detailHref}>{formatValue(column, row[column])}</Link>
                : formatValue(column, row[column])}
            </td>)}
          </tr>;
        })}</tbody>
      </table></div>
    </div>
  );
}
