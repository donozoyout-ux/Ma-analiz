"use client";
import { useCallback, useEffect, useState } from "react";
export type ApiList<T> = { rows: T[] | null; error: boolean; refresh: () => void };
export function useApiList<T>(endpoint: string, enabled = true): ApiList<T> {
 const [rows, setRows] = useState<T[] | null>(null);
 const [error, setError] = useState(false);
 const [version, setVersion] = useState(0);
 const refresh = useCallback(() => setVersion(v => v + 1), []);
 useEffect(() => {
  if (!enabled) return;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort("timeout"), 15000);
  let active = true;
  setRows(null); setError(false);
  fetch(`/api/v1/${endpoint}`, { signal: controller.signal, cache: "no-store" })
   .then(async response => { if (!response.ok) throw new Error(); const data = await response.json(); if (!Array.isArray(data)) throw new Error(); return data; })
   .then(data => { if (active) setRows(data); })
   .catch(() => { if (active) setError(true); }).finally(() => clearTimeout(timeout));
  return () => { active = false; clearTimeout(timeout); controller.abort(); };
 }, [endpoint, enabled, version]);
 return { rows, error, refresh };
}
