"use client";
import { useState } from "react";
import { handoffLinks } from "@/core/handoff";

/** "Open in app": the client posts from their own signed-in phone app, text prefilled where possible. */
export function HandoffButtons({ text, url, platforms }: { text: string; url?: string; platforms?: string[] }) {
  const [msg, setMsg] = useState("");
  const links = handoffLinks(text, { url });
  const order = (id: string) => (platforms?.includes(id) ? 0 : 1);
  const sorted = [...links].sort((a, b) => order(a.id) - order(b.id));

  async function copy() {
    try { await navigator.clipboard.writeText(text); return true; }
    catch {
      const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select();
      const ok = document.execCommand("copy"); ta.remove(); return ok;
    }
  }
  async function go(id: string) {
    const l = links.find((x) => x.id === id)!;
    if (id === "share") {
      const nav = navigator as Navigator & { share?: (d: ShareData) => Promise<void> };
      if (nav.share) { try { await nav.share({ text }); } catch { /* cancelled */ } }
      else { await copy(); setMsg("Sharing isn't supported here; text copied instead."); }
      return;
    }
    if (l.copyFirst) { const ok = await copy(); setMsg(ok ? l.note || "Copied" : "Couldn't copy; select the text above."); }
    if (l.url) window.open(l.url, "_blank", "noopener,noreferrer");
  }
  return (
    <div className="flex flex-col gap-2">
      <div className="grid grid-cols-2 gap-2">
        {sorted.map((l) => (
          <button key={l.id} type="button" onClick={() => go(l.id)} className={l.id === "share" ? "btn btn-sm col-span-2" : "btn-ghost btn-sm"} title={l.note}>
            {l.label}
          </button>
        ))}
      </div>
      {msg && <p className="text-xs text-ok" role="status">{msg}</p>}
    </div>
  );
}
