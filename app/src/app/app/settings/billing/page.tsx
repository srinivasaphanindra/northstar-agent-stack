import { db } from "@/core/db";
import { PLANS, planOf } from "@/core/config";
import { choosePlan, invoicesFor } from "@/core/billing";
import { monthUsage, spendStatus } from "@/core/usage";
import { run, s } from "@/lib/act";
import { currentCtx } from "@/lib/session";
import { Flash } from "@/components/Flash";
import { SubmitButton } from "@/components/SubmitButton";
import { PaymentBox } from "@/components/PaymentBox";

export const metadata = { title: "Plan & billing" };
const P = "/app/settings/billing";

async function act(f: FormData) {
  "use server";
  await run(P, async () => {
    const ctx = await currentCtx();
    const inv = await choosePlan(ctx, s(f, "plan"), s(f, "byok") === "on");
    return inv ? "Plan chosen. Pay with the details below; we'll confirm it by hand." : "Plan changed.";
  });
}

const fmt = (d: Date) => d.toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", day: "numeric", month: "short", year: "numeric" });

export default async function Billing({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const sp = await searchParams;
  const ctx = await currentCtx();
  const ws = await db.workspace.findUniqueOrThrow({ where: { id: ctx.workspaceId } });
  const invoices = await invoicesFor(ctx);
  const pending = invoices.find((i) => i.status === "pending");
  const u = await monthUsage(ws.id);
  const sp2 = await spendStatus(ws.id);
  return (
    <div className="flex flex-col gap-6">
      <h1 className="h2">Plan & billing</h1>
      <Flash sp={sp} />
      <div className="card flex flex-wrap gap-6 justify-between">
        <div><p className="label">Plan</p><p className="text-2xl font-extralight">{planOf(ws.plan).name}</p><p className="text-sm muted">{ws.status === "active" ? "Active" : "Waiting for payment confirmation"}</p></div>
        <div><p className="label">This month</p><p className="text-sm text-soft">{u.drafts} drafts · {u.messages} messages · {u.llmCalls} AI calls · {u.publishes} sent to Buffer</p><p className="text-sm muted">AI: ${u.totalCost.toFixed(3)} (cap ${sp2.cap})</p></div>
      </div>
      {pending && <PaymentBox amountInr={pending.amountInr} invoiceId={pending.id} planName={planOf(pending.plan).name} />}
      {ctx.role === "owner" && (
        <form action={act} className="card flex flex-col gap-3">
          <p className="label">Change plan</p>
          <select name="plan" defaultValue={ws.plan} className="bg-black border border-line rounded-full px-4 min-h-12">
            {Object.values(PLANS).map((p) => <option key={p.id} value={p.id}>{p.name}: {p.inr ? `₹${p.inr} (₹${p.byokInr} own key)` : "free"}</option>)}
          </select>
          <label className="flex items-center gap-3 min-h-11 text-sm"><input type="checkbox" name="byok" defaultChecked={ws.aiKeyMode === "byok"} className="size-5 accent-white" /> I use my own OpenRouter key</label>
          <SubmitButton className="btn md:self-start">Choose</SubmitButton>
        </form>
      )}
      <section>
        <p className="label mb-2">Invoices</p>
        {invoices.length === 0 ? <p className="muted text-sm">None yet.</p> : (
          <div className="overflow-x-auto"><table className="table"><thead><tr><th>Date</th><th>Plan</th><th>Amount</th><th>Status</th><th>Ref</th></tr></thead>
            <tbody>{invoices.map((i) => <tr key={i.id}><td>{fmt(i.createdAt)}</td><td>{planOf(i.plan).name}</td><td>₹{i.amountInr.toLocaleString("en-IN")}</td><td>{i.status}</td><td className="muted">{i.id.slice(-6)}{i.reference ? ` · ${i.reference}` : ""}</td></tr>)}</tbody></table></div>
        )}
        <p className="text-xs muted mt-2">GST invoices come later. Prices shown are per month.</p>
      </section>
    </div>
  );
}
