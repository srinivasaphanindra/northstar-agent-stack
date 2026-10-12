import type { Usage } from "@/core/clients/caps";

export const PLATFORM_LABEL: Record<string, string> = { x: "X", linkedin: "LinkedIn", facebook: "Facebook", website: "Website", other: "Bot", all: "All" };

export function CapBar({ name, u }: { name: string; u: Usage | null }) {
  if (!u) return null;
  const pct = Math.min(u.pct ?? 0, 100);
  return (
    <div className="flex flex-col gap-1">
      <div className="flex justify-between text-xs"><span className="muted">{name}</span><span className={u.over ? "text-warn" : ""}>{u.label}</span></div>
      <div className="h-1.5 rounded-full bg-white/10 overflow-hidden" role="meter" aria-label={name} aria-valuenow={u.used} aria-valuemax={u.cap}>
        <div className={`h-full ${u.over ? "bg-warn" : "bg-white/70"}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

/** Tiny per-day bar chart (no chart lib). */
export function Bars({ values, days, label }: { values: number[]; days: string[]; label: string }) {
  const max = Math.max(1, ...values);
  const total = values.reduce((a, b) => a + b, 0);
  return (
    <div className="flex flex-col gap-1">
      <div className="flex justify-between text-xs"><span className="muted">{label}</span><span>{total}</span></div>
      <div className="flex items-end gap-[2px] h-10" aria-label={`${label}: ${total}`}>
        {values.map((v, i) => <div key={days[i]} title={`${days[i]}: ${v}`} className={`flex-1 rounded-sm ${v ? "bg-white/70" : "bg-white/10"}`} style={{ height: `${v ? Math.max(12, (v / max) * 100) : 6}%` }} />)}
      </div>
    </div>
  );
}
