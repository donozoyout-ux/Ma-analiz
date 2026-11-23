"use client";

import { useEffect } from "react";

export function PwaRegister() {
  useEffect(() => {
    if (typeof window !== "undefined" && "serviceWorker" in navigator) {
      navigator.serviceWorker
        .register("/sw.js")
        .then((registration) => {
          // Check for service worker updates periodically
          registration.onupdatefound = () => {
            const installingWorker = registration.installing;
            if (installingWorker) {
              installingWorker.onstatechange = () => {
                if (
                  installingWorker.state === "installed" &&
                  navigator.serviceWorker.controller
                ) {
                  // New content available; silent auto-activate
                  console.info("Maç Analiz: Yeni sürüm mevcut, güncellendi.");
                }
              };
            }
          };
        })
        .catch((err) => {
          // Suppress error in non-supported or restricted environments
          if (process.env.NODE_ENV === "development") {
            console.debug("ServiceWorker registration skipped or failed:", err);
          }
        });
    }
  }, []);

  return null;
}
