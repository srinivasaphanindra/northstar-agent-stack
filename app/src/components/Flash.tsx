export function Flash({ sp }: { sp: Record<string, string | string[] | undefined> }) {
  const ok = typeof sp.ok === "string" ? sp.ok : "";
  const e = typeof sp.e === "string" ? sp.e : "";
  if (!ok && !e) return null;
  return <div className={e ? "flash-err" : "flash-ok"} role="status">{e || ok}</div>;
}
