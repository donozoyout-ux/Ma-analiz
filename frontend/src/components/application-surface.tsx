"use client";
import { useEffect, useRef, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { attachDashboardTilt } from "@/lib/dashboard-tilt.mjs";

// One controller for all routes; no per-card state/listeners or nested surfaces.
export function ApplicationSurface({ children }: { children: ReactNode }) {
  const root = useRef<HTMLDivElement>(null);
  const pathname = usePathname();
  useEffect(() => {
    if (root.current) return attachDashboardTilt(root.current, window);
  }, [pathname]);
  return <div ref={root} className="shell">{children}</div>;
}
