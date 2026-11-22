"use client";
import { useEffect, useRef, type ReactNode } from "react";
import { attachDashboardTilt } from "@/lib/dashboard-tilt.mjs";

export function DashboardSurface({ children }: { children: ReactNode }) {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (root.current) return attachDashboardTilt(root.current, window);
  }, []);
  return <div ref={root} className="premium-dashboard">{children}</div>;
}
