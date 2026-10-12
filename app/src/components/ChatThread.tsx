"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Bubble, type Msg } from "./ChatLog";

type M = Msg & { meta?: string };

export function ChatThread({ conversationId, initial }: { conversationId: string; initial: M[] }) {
  const [msgs, setMsgs] = useState<M[]>(initial);
  const [waiting, setWaiting] = useState(false);
  const [text, setText] = useState("");
  const [err, setErr] = useState("");
  const end = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    const r = await fetch(`/api/conversations/${conversationId}/messages`, { cache: "no-store" });
    if (!r.ok) return;
    const j = (await r.json()) as { messages: M[]; waiting: boolean };
    setMsgs(j.messages);
    setWaiting(j.waiting);
  }, [conversationId]);

  useEffect(() => {
    const t = setInterval(load, waiting ? 1500 : 8000);
    return () => clearInterval(t);
  }, [load, waiting]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [msgs.length, waiting]);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    const t = text.trim();
    if (!t) return;
    setErr("");
    setText("");
    setMsgs((m) => [...m, { id: `tmp-${Date.now()}`, role: "user", content: t, createdAt: new Date().toISOString() }]);
    setWaiting(true);
    const r = await fetch(`/api/conversations/${conversationId}/messages`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: t }) });
    if (!r.ok) setErr(((await r.json().catch(() => ({}))) as { error?: string }).error ?? "Couldn't send");
    void load();
  }

  const drafts = (m: M) => { try { return (JSON.parse(m.meta ?? "{}").draftIds ?? []) as string[]; } catch { return []; } };
  const suggestions = ["Draft a LinkedIn post about our weekend offer", "Give me 3 post ideas for this week", "remember: we never post discounts"];

  return (
    <div className="flex flex-col min-h-[calc(100dvh-12rem)] md:min-h-[calc(100dvh-8rem)]">
      <div className="flex-1 flex flex-col gap-3 pb-4">
        {msgs.length === 0 && (
          <div className="flex flex-col gap-2 mt-4">
            <p className="muted text-sm">Try one of these:</p>
            {suggestions.map((s) => <button key={s} onClick={() => setText(s)} className="btn-ghost btn-sm justify-start text-left">{s}</button>)}
          </div>
        )}
        {msgs.map((m) => (
          <div key={m.id} className="flex flex-col gap-1">
            <Bubble m={m} />
            {drafts(m).length > 0 && <Link href="/app/approvals" className="self-start chip no-underline ml-1">Open in Approvals →</Link>}
          </div>
        ))}
        {waiting && <div className="text-sm muted animate-pulse">Assistant is typing…</div>}
        <div ref={end} />
      </div>
      {err && <p className="flash-err mb-2">{err}</p>}
      <form onSubmit={send} className="sticky bottom-[calc(64px+env(safe-area-inset-bottom))] md:bottom-0 bg-black/90 backdrop-blur py-3 flex gap-2 items-end border-t border-line">
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={1} placeholder="Message your assistant" aria-label="Message"
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && window.matchMedia("(min-width: 768px)").matches) { e.preventDefault(); void send(e); } }}
          className="flex-1 resize-none bg-[#111] border border-line rounded-3xl px-4 py-3 text-base outline-none focus:border-white/40 max-h-40" />
        <button type="submit" className="btn !px-5" disabled={!text.trim()}>Send</button>
      </form>
    </div>
  );
}
