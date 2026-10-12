// Manual payments: UPI QR / Gumroad link. The admin marks an invoice paid by hand -> workspace active.
import { db } from "./db";
import { config, PLANS, planOf, type PlanId } from "./config";
import { AppError } from "./errors";
import { audit } from "./audit";
import { can, type Ctx } from "./tenancy";

export function upiLink(amountInr: number, note: string) {
  const p = new URLSearchParams({ pa: config.upiId, pn: config.upiPayee, am: amountInr.toFixed(2), cu: "INR", tn: note.slice(0, 60) });
  return `upi://pay?${p.toString()}`;
}

export function priceFor(plan: PlanId, byok: boolean) {
  const p = PLANS[plan];
  return byok ? p.byokInr : p.inr;
}

export async function choosePlan(ctx: Ctx, plan: string, byok: boolean) {
  if (!can(ctx, "owner")) throw new AppError("forbidden", "Only the owner can choose the plan");
  if (!(plan in PLANS)) throw new AppError("invalid", "Unknown plan");
  const p = plan as PlanId;
  const amount = priceFor(p, byok);
  await db.invoice.updateMany({ where: { workspaceId: ctx.workspaceId, status: "pending" }, data: { status: "void" } });
  const inv = amount > 0 ? await db.invoice.create({ data: { workspaceId: ctx.workspaceId, plan: p, amountInr: amount } }) : null;
  await db.workspace.update({
    where: { id: ctx.workspaceId },
    data: { plan: p, status: amount === 0 ? "active" : "pending_payment", ...(byok ? {} : { aiKeyMode: "platform" }) },
  });
  await audit(ctx.workspaceId, { type: "user", userId: ctx.userId }, "billing.plan_chosen", p, { amountInr: amount, byok });
  return inv;
}

export async function invoicesFor(ctx: Ctx) {
  return db.invoice.findMany({ where: { workspaceId: ctx.workspaceId }, orderBy: { createdAt: "desc" } });
}

/** Platform admin only. */
export async function markPaid(adminUserId: string, invoiceId: string, method: string, reference: string) {
  const admin = await db.user.findUnique({ where: { id: adminUserId } });
  if (!admin?.isPlatformAdmin) throw new AppError("forbidden", "Admins only");
  const inv = await db.invoice.findUnique({ where: { id: invoiceId } });
  if (!inv) throw new AppError("not_found", "Invoice not found");
  if (inv.status === "paid") return inv;
  const updated = await db.invoice.update({ where: { id: inv.id }, data: { status: "paid", method: method.slice(0, 20), reference: reference.slice(0, 120), paidAt: new Date(), markedBy: adminUserId } });
  await db.workspace.update({ where: { id: inv.workspaceId }, data: { status: "active", plan: inv.plan } });
  await audit(inv.workspaceId, { type: "admin", userId: adminUserId }, "billing.marked_paid", inv.id, { amountInr: inv.amountInr, method, reference });
  return updated;
}

export { planOf };
