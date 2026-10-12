import Link from "next/link";
import { db } from "@/core/db";
import { planOf } from "@/core/config";
import { spendStatus } from "@/core/usage";
import { currentCtx, currentWorkspace } from "@/lib/session";
import { InstallButton } from "@/components/InstallButton";

export const metadata = { title: "Home" };

export default async function Dashboard() {
  const ctx = await currentCtx();
  const ws = await currentWorkspace(ctx);
  const [pending, spend, recent] = await Promise.all([
    db.draft.count({ where: { workspaceId: ws.id, status: "pending" } }),
    spendStatus(ws.id),
    db.draft.findMany({ where: { workspaceId: ws.id }, orderBy: { createdAt: "desc" }, take: 3 }),
  ]);
  const plan = planOf(ws.plan);
  return (
    <div className="flex flex-col gap-6">
      <div>
        <p className="label">Good to see you</p>
        <h1 className="h1 mt-2">{pending ? `${pending} draft${pending > 1 ? "s" : ""} waiting for you` : "All caught up"}</h1>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <Link href="/app/approvals" className="btn min-h-14 text-base">Review approvals{pending ? ` (${pending})` : ""}</Link>
        <Link href="/app/chat" className="btn-ghost min-h-14 text-base">Ask your assistant</Link>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat label="Plan" value={plan.name.replace("Self-serve ", "")} sub={ws.status === "active" ? "active" : ws.status.replace("_", " ")} />
        <Stat label="AI spend (month)" value={`$${spend.used.toFixed(2)}`} sub={`cap $${spend.cap} · ${Math.round(spend.pct)}%`} warn={spend.level !== "ok"} />
        <Stat label="Drafts left" value={String(spend.draftsLeft)} sub={`of ${spend.draftCap} this month`} />
        <Stat label="AI key" value={ws.aiKeyMode === "byok" ? "Your own" : "Northstar"} sub={ws.aiKeyMode === "byok" ? "BYO OpenRouter" : "platform key"} />
      </div>
      <section className="card flex flex-col gap-3">
        <p className="label">Latest drafts</p>
        {recent.length === 0 && <p className="muted">No drafts yet. Try “draft a LinkedIn post about…” in Chat.</p>}
        {recent.map((d) => (
          <Link key={d.id} href={`/app/approvals#${d.id}`} className="no-underline flex justify-between gap-3 border-t border-line pt-3 first:border-0 first:pt-0">
            <span className="truncate text-soft">{d.text}</span><span className="chip shrink-0">{d.status}</span>
          </Link>
        ))}
      </section>
      <section className="card flex flex-col gap-3">
        <p className="label">On your phone</p>
        <p className="text-sm muted">Install Northstar on your home screen: approvals open in one tap, and posts open straight in your signed-in X, LinkedIn, Facebook or WhatsApp app.</p>
        <InstallButton />
      </section>
    </div>
  );
}

function Stat({ label, value, sub, warn }: { label: string; value: string; sub: string; warn?: boolean }) {
  return (
    <div className="card !p-4">
      <p className="label !text-[0.65rem]">{label}</p>
      <p className={`text-2xl font-extralight mt-1 ${warn ? "text-warn" : ""}`}>{value}</p>
      <p className="text-xs muted">{sub}</p>
    </div>
  );
}
