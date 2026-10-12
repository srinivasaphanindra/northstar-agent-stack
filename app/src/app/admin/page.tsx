import Link from "next/link";
import { notFound } from "next/navigation";
import { db } from "@/core/db";
import { planOf } from "@/core/config";
import { markPaid } from "@/core/billing";
import { monthUsage, platformSpendStatus } from "@/core/usage";
import { run, s } from "@/lib/act";
import { requireUser } from "@/lib/session";
import { Flash } from "@/components/Flash";
import { SubmitButton } from "@/components/SubmitButton";

export const metadata = { title: "Admin" };

async function paid(f: FormData) {
  "use server";
  await run("/admin", async () => {
    const u = await requireUser();
    await markPaid(u.id, s(f, "invoiceId"), s(f, "method"), s(f, "reference"));
    return "Marked paid; workspace is active.";
  });
}

const fmt = (d: Date) => d.toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

export default async function Admin({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const sp = await searchParams;
  const user = await requireUser();
  if (!user.isPlatformAdmin) notFound();
  const wss = await db.workspace.findMany({ orderBy: { createdAt: "desc" }, include: { members: { include: { user: { select: { email: true } } } } } });
  const rows = await Promise.all(wss.map(async (w) => ({
    w,
    u: await monthUsage(w.id),
    pending: await db.invoice.findFirst({ where: { workspaceId: w.id, status: "pending" }, orderBy: { createdAt: "desc" } }),
    drafts: await db.draft.count({ where: { workspaceId: w.id, status: "pending" } }),
  })));
  const auditRows = await db.auditLog.findMany({ orderBy: { createdAt: "desc" }, take: 80 });
  const wsName = new Map(wss.map((w) => [w.id, w.name]));
  const total = rows.reduce((a, r) => a + r.u.totalCost, 0);
  const users = await db.user.count();
  const failedJobs = await db.job.count({ where: { status: "failed" } });
  const plat = await platformSpendStatus();
  const platAlerts = await db.auditLog.findMany({ where: { action: "platform.daily_ai_cap" }, orderBy: { createdAt: "desc" }, take: 5 });

  return (
    <main className="max-w-6xl mx-auto px-5 py-6 flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <span className="brand">NORTHSTAR · ADMIN</span>
        <div className="flex gap-2"><Link href="/admin/clients" className="chip no-underline">Clients</Link><Link href="/app" className="chip no-underline">Back to app</Link></div>
      </div>
      <Flash sp={sp} />
      {plat.level === "stop" && (
        <div className="card border border-warn/50 bg-warn/5">
          <p className="label text-warn">Platform AI daily cap hit</p>
          <p className="mt-1">Shared OpenRouter spend today is <b>${plat.used.toFixed(3)}</b> of <b>${plat.cap.toFixed(2)}</b> ({plat.calls} calls). Platform-key chats are hard-stopped until tomorrow (IST). Workspaces on BYOK are unaffected.</p>
        </div>
      )}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <div className="card !p-4"><p className="label">Workspaces</p><p className="text-2xl font-extralight">{wss.length}</p></div>
        <div className="card !p-4"><p className="label">Users</p><p className="text-2xl font-extralight">{users}</p></div>
        <div className="card !p-4"><p className="label">AI spend (month)</p><p className="text-2xl font-extralight">${total.toFixed(2)}</p></div>
        <div className={`card !p-4 ${plat.level === "stop" ? "border-warn/50" : ""}`}><p className="label">Platform AI (today)</p><p className={`text-2xl font-extralight ${plat.level === "stop" ? "text-warn" : ""}`}>${plat.used.toFixed(2)}<span className="text-sm muted"> / ${plat.cap}</span></p></div>
        <div className="card !p-4"><p className="label">Failed jobs</p><p className={`text-2xl font-extralight ${failedJobs ? "text-warn" : ""}`}>{failedJobs}</p></div>
      </div>

      <section className="flex flex-col gap-3">
        <p className="label">Workspaces</p>
        {rows.map(({ w, u, pending, drafts }) => (
          <div key={w.id} className="card flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2 justify-between">
              <div><p className="text-lg font-light">{w.name}</p><p className="text-xs muted">{w.members.map((m) => `${m.user.email} (${m.role})`).join(", ")} · created {fmt(w.createdAt)}</p></div>
              <div className="flex flex-wrap gap-2">
                <span className="chip">{planOf(w.plan).name}</span>
                <span className={w.status === "active" ? "pill-ok" : "pill-warn"}>{w.status.replace("_", " ")}</span>
                <span className="chip">key: {w.aiKeyMode}</span>
                <span className="chip">setup: {w.onboardingStep}</span>
                {w.paused && <span className="pill-bad">paused</span>}
              </div>
            </div>
            <p className="text-sm muted">Month: ${u.totalCost.toFixed(3)} AI ({u.llmCalls} calls, {u.tokens} tokens; BYOK ${u.byokCost.toFixed(3)}) · {u.messages} messages · {u.drafts} drafts ({drafts} pending) · {u.publishes} sent to Buffer</p>
            {pending && (
              <form action={paid} className="flex flex-wrap gap-2 items-center border-t border-line pt-3">
                <input type="hidden" name="invoiceId" value={pending.id} />
                <span className="text-sm">Invoice {pending.id.slice(-6)}: ₹{pending.amountInr.toLocaleString("en-IN")} ({planOf(pending.plan).name})</span>
                <select name="method" className="bg-black border border-line rounded-full px-3 min-h-10 text-sm"><option value="upi">UPI</option><option value="gumroad">Gumroad</option><option value="other">Other</option></select>
                <input name="reference" placeholder="UPI ref / Gumroad sale id" className="input !w-56 !py-2 text-sm" />
                <SubmitButton className="btn btn-sm">Mark paid</SubmitButton>
              </form>
            )}
          </div>
        ))}
      </section>

      {platAlerts.length > 0 && (
        <section className="flex flex-col gap-2">
          <p className="label">Platform AI cap alerts (latest)</p>
          {platAlerts.map((a) => (
            <div key={a.id} className="card !p-3 text-sm"><span className="muted">{fmt(a.createdAt)}</span> · {a.target} · {a.detail}</div>
          ))}
        </section>
      )}

      <section>
        <p className="label mb-2">Audit log (latest 80, IST)</p>
        <div className="overflow-x-auto"><table className="table">
          <thead><tr><th>When</th><th>Workspace</th><th>Actor</th><th>Action</th><th>Target</th></tr></thead>
          <tbody>{auditRows.map((a) => <tr key={a.id}><td className="whitespace-nowrap">{fmt(a.createdAt)}</td><td>{a.workspaceId ? wsName.get(a.workspaceId) ?? a.workspaceId.slice(-6) : "-"}</td><td>{a.actorType}</td><td>{a.action}</td><td className="muted break-all">{a.target}</td></tr>)}</tbody>
        </table></div>
      </section>
    </main>
  );
}
