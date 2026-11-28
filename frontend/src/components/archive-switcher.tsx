"use client";

import { useState } from "react";
import { R2BundesligaArchive } from "@/components/r2-bundesliga-archive";
import { MatchCenter } from "@/components/match-center";

export function ArchiveSwitcher() {
  const [active, setActive] = useState<"bundesliga" | "operational">("bundesliga");
  return <div className="explorer-stack">
    <div className="filter-grid center-filter-grid" role="group" aria-label="Arşiv türü">
      <button type="button" className={active === "bundesliga" ? "quiet-button selected" : "quiet-button"}
        aria-pressed={active === "bundesliga"} onClick={() => setActive("bundesliga")}>
        Bundesliga · 2024–27 R2
      </button>
      <button type="button" className={active === "operational" ? "quiet-button selected" : "quiet-button"}
        aria-pressed={active === "operational"} onClick={() => setActive("operational")}>
        Diğer doğrulanmış kayıtlar
      </button>
    </div>
    {active === "bundesliga"
      ? <R2BundesligaArchive />
      : <><p className="scope-note">Bu bölüm ayrı PostgreSQL / operasyonel R2 arşivini tarar. Bundesliga sezon arşivindeki 708 maç üst sekmededir.</p>
          <MatchCenter initialSource="history" /></>}
  </div>;
}
