"use client";

import { useState } from "react";
import { Check, Copy, Share2 } from "lucide-react";

type Feedback = "idle" | "copied" | "shared" | "error";

export function MatchShareActions({ homeTeam, awayTeam }: { homeTeam: string; awayTeam: string }) {
  const [feedback, setFeedback] = useState<Feedback>("idle");
  const [busy, setBusy] = useState(false);

  async function copyLink() {
    if (busy) return;
    setBusy(true);
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(window.location.href);
      setFeedback("copied");
    } catch {
      setFeedback("error");
    } finally {
      setBusy(false);
    }
  }

  async function shareLink() {
    if (busy) return;
    if (!navigator.share) {
      await copyLink();
      return;
    }
    setBusy(true);
    try {
      await navigator.share({
        title: `${homeTeam} – ${awayTeam} | Maç Analiz`,
        url: window.location.href,
      });
      setFeedback("shared");
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        setFeedback("error");
      }
    } finally {
      setBusy(false);
    }
  }

  return <div className="match-share-actions" aria-label="Maç bağlantısını paylaş">
    <div className="match-share-buttons">
      <button className="quiet-button match-share-button" type="button" onClick={copyLink} disabled={busy}>
        {feedback === "copied" ? <Check size={16} aria-hidden="true"/> : <Copy size={16} aria-hidden="true"/>}
        {feedback === "copied" ? "Kopyalandı" : "Bağlantıyı kopyala"}
      </button>
      <button className="quiet-button match-share-button" type="button" onClick={shareLink} disabled={busy}>
        <Share2 size={16} aria-hidden="true"/> Paylaş
      </button>
    </div>
    <span className="match-share-feedback" role="status" aria-live="polite">
      {feedback === "copied" ? "Maç bağlantısı panoya kopyalandı." :
        feedback === "shared" ? "Paylaşım tamamlandı." :
        feedback === "error" ? "Bağlantı paylaşılamadı. Tarayıcının adres çubuğundan kopyalayabilirsiniz." : ""}
    </span>
  </div>;
}
