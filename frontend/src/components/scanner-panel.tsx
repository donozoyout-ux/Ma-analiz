"use client";

import { useCallback, useEffect, useState } from "react";

type ScannerStatus = {
  enabled?: boolean;
  status?: string;
  last_run?: string | null;
  next_run?: string | null;
  last_success?: string | null;
  last_error?: string | null;
  matches_scanned?: number;
  upcoming_matches?: number;
  signals?: number;
  no_bet?: number;
  provider?: string;
  provider_latency_ms?: number | null;
  data_quality?: { analyzed?: number; records_24h?: number };
};

type ScannerResult = {
  id: string;
  home_team: string;
  away_team: string;
  league?: string | null;
  market?: string | null;
  selection?: string | null;
  odds?: number | null;
  value?: number | null;
  status: "SIGNAL" | "NO BET";
  reason?: string[];
  data_quality?: string;
};

const timeLabel = (value?: string | null) => value ? new Date(value).toLocaleString() : "—";

export function ScannerPanel() {
  const [status, setStatus] = useState<ScannerStatus | null>(null);
  const [rows, setRows] = useState<ScannerResult[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [statusResponse, resultsResponse] = await Promise.all([
        fetch("/api/v1/scanner/status", { cache: "no-store" }),
        fetch("/api/v1/scanner/results?limit=50", { cache: "no-store" }),
      ]);
      if (!statusResponse.ok || !resultsResponse.ok) throw new Error("SCANNER_API_UNAVAILABLE");
      const nextStatus = await statusResponse.json() as ScannerStatus;
      const nextRows = await resultsResponse.json() as ScannerResult[];
      setStatus(nextStatus);
      setRows(nextRows);
      setError(null);
    } catch {
      setError("Scanner durumu şu anda alınamıyor.");
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const runNow = async () => {
    if (running) return;
    setRunning(true);
    try {
      const response = await fetch("/api/v1/scanner/run", { method: "POST" });
      if (!response.ok) throw new Error("SCANNER_RUN_FAILED");
      await refresh();
    } catch {
      setError("Scanner çalıştırılamadı. API veya veritabanı bağlantısını kontrol edin.");
    } finally {
      setRunning(false);
    }
  };

  const state = status?.status ?? "IDLE";
  const stateClass = state === "ACTIVE" ? "ok" : state === "ERROR" ? "error" : "";

  return <section className="scanner-panel">
    <div className="scanner-summary card">
      <div className="section-head">
        <div><div className="eyebrow">Goaloo · scheduled analysis</div><h2 className="section-title">Scanner</h2></div>
        <div className="scanner-actions"><span className={`badge ${stateClass}`}>{state}</span>
          <button className="scanner-run" onClick={runNow} disabled={running || !status?.enabled}>
            {running ? "Running…" : "Run now"}
          </button>
        </div>
      </div>
      {error && <div className="scanner-error">{error}</div>}
      <div className="scanner-stats">
        <div><span>Last scan</span><strong>{timeLabel(status?.last_run)}</strong></div>
        <div><span>Next scan</span><strong>{timeLabel(status?.next_run)}</strong></div>
        <div><span>Matches scanned</span><strong>{status?.matches_scanned ?? 0}</strong></div>
        <div><span>Signals</span><strong>{status?.signals ?? 0}</strong></div>
        <div><span>NO BET</span><strong>{status?.no_bet ?? 0}</strong></div>
        <div><span>Provider</span><strong>{status?.provider ?? "NOT_CONFIGURED"}</strong></div>
        <div><span>Upcoming</span><strong>{status?.upcoming_matches ?? 0}</strong></div>
        <div><span>Data quality (24h)</span><strong>{status?.data_quality?.analyzed ?? 0} analyzed</strong></div>
      </div>
      {status?.last_error && <div className="scanner-error">Last job error: {status.last_error}</div>}
    </div>

    <div className="card scanner-results-card">
      <div className="section-head"><div className="section-title">Match analysis results</div>
        <div className="section-note">Only configured upcoming fixtures · observed data</div></div>
      {rows.length === 0 ? <div className="empty scanner-empty">No scanner results yet.</div> :
        <div className="scanner-table-wrap"><table className="scanner-table"><thead><tr>
          <th>Match</th><th>League</th><th>Market</th><th>Odds</th><th>Value</th><th>Status</th>
        </tr></thead><tbody>{rows.map(row => <tr key={row.id}>
          <td><strong>{row.home_team} – {row.away_team}</strong><small>{timeLabel((row as ScannerResult & {start_time?: string}).start_time)}</small></td>
          <td>{row.league ?? "—"}</td><td>{row.market && row.selection ? `${row.market} · ${row.selection}` : "—"}</td>
          <td>{row.odds ?? "—"}</td><td>{row.value == null ? "—" : `${(row.value * 100).toFixed(1)}%`}</td>
          <td><span className={`badge ${row.status === "SIGNAL" ? "ok" : ""}`}>{row.status}</span>
            {row.status === "NO BET" && <small className="scanner-reason">{(row.reason ?? ["INSUFFICIENT_DATA"]).join(" · ")}</small>}</td>
        </tr>)}</tbody></table></div>}
    </div>
  </section>;
}
