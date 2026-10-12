"use client";
import { useEffect } from "react";

/** Registers the service worker (installable PWA). Skipped inside a native (Capacitor) shell, which has its own. */
export function PwaRegister() {
  useEffect(() => {
    const w = window as unknown as { Capacitor?: unknown };
    if (w.Capacitor || !("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
  }, []);
  return null;
}
