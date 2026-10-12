"use client";
import { useState } from "react";

/** Web push opt-in (stub): asks permission and registers the endpoint; the server doesn't send pushes yet. */
export function PushStub() {
  const [state, setState] = useState<string>("");
  async function enable() {
    if (!("Notification" in window) || !("serviceWorker" in navigator)) return setState("This browser doesn't support notifications.");
    const p = await Notification.requestPermission();
    if (p !== "granted") return setState("Notifications are blocked for this site.");
    const reg = await navigator.serviceWorker.ready;
    const key = document.querySelector<HTMLMetaElement>('meta[name="vapid-key"]')?.content;
    let endpoint = "";
    if (key && reg.pushManager) {
      try { endpoint = (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key })).endpoint; } catch { /* stub */ }
    }
    await fetch("/api/push/subscribe", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ endpoint }) });
    setState("Notifications on (coming soon: new drafts will ping your phone).");
  }
  return (
    <div className="flex flex-col gap-2 items-start">
      <button className="btn-ghost btn-sm" onClick={enable}>Turn on notifications</button>
      {state && <p className="text-sm muted">{state}</p>}
    </div>
  );
}
