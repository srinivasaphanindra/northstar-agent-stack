// Approval-first publishing. The agent can only create drafts (proposeDraft). Approving needs a human
// owner/admin Ctx; the state change is atomic so a draft is published at most once.
import { db } from "./db";
import { config } from "./config";
import { AppError } from "./errors";
import { audit, type Actor } from "./audit";
import { can, type Ctx } from "./tenancy";
import { rulesFor, checkContent, postCapCheck } from "./guardrails";
import { spendStatus, recordEvent } from "./usage";
import { safeJson } from "./integrations";
import { publish, type PublishMode } from "./buffer";
import { bufferToken, channelPickFor } from "./buffer-oauth";
import { approveTimeCheck } from "./content-check";
import { enqueue } from "./jobs";

export const PLATFORMS = ["x", "linkedin", "facebook", "instagram"] as const;
const LIMITS: Record<string, number> = { x: 280, linkedin: 3000, facebook: 5000, instagram: 2200 };

export type ProposeResult =
  | { ok: true; draftId: string; text: string; platforms: string[] }
  | { ok: false; reason: string; alternative: string };

export async function proposeDraft(workspaceId: string, input: { text: string; platforms: string[]; conversationId?: string | null; mode?: "queue" | "now" }, actor: Actor = { type: "agent" }): Promise<ProposeResult> {
  const ws = await db.workspace.findUniqueOrThrow({ where: { id: workspaceId } });
  const text = String(input.text ?? "").trim();
  const platforms = [...new Set((input.platforms ?? []).map((p) => String(p).toLowerCase()).filter((p) => (PLATFORMS as readonly string[]).includes(p)))];
  if (!text) return { ok: false, reason: "The draft is empty.", alternative: "Tell me what the post should be about." };
  if (!platforms.length) return { ok: false, reason: "No platform was picked.", alternative: "Say which platform: X, LinkedIn, Facebook or Instagram." };
  if (ws.paused) return { ok: false, reason: "This workspace is paused (kill switch is on).", alternative: "An owner can resume it in Settings → Guardrails." };
  for (const p of platforms) if (text.length > LIMITS[p]) return { ok: false, reason: `It's ${text.length} characters; ${p} allows ${LIMITS[p]}.`, alternative: "I can shorten it." };
  const rules = await rulesFor(workspaceId);
  const c = checkContent(text, rules);
  if (!c.ok) {
    await audit(workspaceId, actor, "draft.blocked", "", { reason: c.reason });
    return c;
  }
  const s = await spendStatus(workspaceId);
  if (s.draftsLeft <= 0) return { ok: false, reason: `This month's ${s.draftCap} drafts are used up on your plan.`, alternative: "Upgrade the plan, or edit an existing draft instead." };
  const d = await db.draft.create({ data: { workspaceId, conversationId: input.conversationId ?? null, text, platforms: JSON.stringify(platforms), mode: input.mode === "now" ? "now" : "queue", createdBy: actor.type } });
  await audit(workspaceId, actor, "draft.create", d.id, { platforms });
  await enqueue("channel.notify_draft", { draftId: d.id }, workspaceId);
  return { ok: true, draftId: d.id, text, platforms };
}

export async function listDrafts(ctx: Ctx, status?: string) {
  return db.draft.findMany({ where: { workspaceId: ctx.workspaceId, ...(status ? { status } : {}) }, orderBy: { createdAt: "desc" }, take: 100 });
}

export async function getDraft(ctx: Ctx, id: string) {
  const d = await db.draft.findFirst({ where: { id, workspaceId: ctx.workspaceId } });
  if (!d) throw new AppError("not_found", "Draft not found");
  return d;
}

function needApprover(ctx: Ctx) {
  if (!can(ctx, "admin")) throw new AppError("forbidden", "Only owners and admins can approve or change drafts");
}

export async function editDraft(ctx: Ctx, id: string, text: string) {
  needApprover(ctx);
  const d = await getDraft(ctx, id);
  if (d.status !== "pending") throw new AppError("conflict", "This draft was already handled");
  const t = text.trim();
  if (!t) throw new AppError("invalid", "Text can't be empty");
  const c = checkContent(t, await rulesFor(ctx.workspaceId));
  if (!c.ok) throw new AppError("invalid", `${c.reason} ${c.alternative}`);
  const r = await db.draft.updateMany({ where: { id, workspaceId: ctx.workspaceId, status: "pending", version: d.version }, data: { text: t, version: d.version + 1 } });
  if (r.count !== 1) throw new AppError("conflict", "Someone else changed this draft; reload");
  await audit(ctx.workspaceId, { type: "user", userId: ctx.userId }, "draft.edit", id, { version: d.version + 1 });
}

export async function rejectDraft(ctx: Ctx, id: string) {
  needApprover(ctx);
  await getDraft(ctx, id);
  const r = await db.draft.updateMany({ where: { id, workspaceId: ctx.workspaceId, status: "pending" }, data: { status: "rejected", decidedBy: ctx.userId, decidedAt: new Date() } });
  if (r.count !== 1) throw new AppError("conflict", "This draft was already handled");
  await audit(ctx.workspaceId, { type: "user", userId: ctx.userId }, "draft.reject", id);
}

const MIN_LEAD_MS = 5 * 60_000, MAX_LEAD_MS = 180 * 86400_000;
/** "scheduled" needs a time 5 minutes to 180 days ahead. */
export function checkSchedule(mode: PublishMode, at: Date | null | undefined, now = new Date()): Date | null {
  if (mode !== "scheduled") return null;
  if (!at || Number.isNaN(at.getTime())) throw new AppError("invalid", "Pick a date and time for the scheduled post.");
  const lead = at.getTime() - now.getTime();
  if (lead < MIN_LEAD_MS) throw new AppError("invalid", "Pick a time at least 5 minutes from now (or choose Post now).");
  if (lead > MAX_LEAD_MS) throw new AppError("invalid", "Pick a time within the next 180 days.");
  return at;
}

/** Parses an <input type="datetime-local"> value as IST (the app's time zone). */
export function parseIstLocal(v: string): Date | null {
  const m = v.trim().match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/);
  if (!m) return null;
  const d = new Date(`${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:00+05:30`);
  return Number.isNaN(d.getTime()) ? null : d;
}

export type ApproveResult = { status: string; note: string; published: string[]; keptAsDraft: string[] };

export async function approveDraft(ctx: Ctx, id: string, opts: { text?: string; mode?: PublishMode; scheduledAt?: Date | null } = {}): Promise<ApproveResult> {
  needApprover(ctx);
  if (opts.text !== undefined) {
    const cur = await getDraft(ctx, id);
    if (opts.text.trim() !== cur.text) await editDraft(ctx, id, opts.text);
  }
  const d = await getDraft(ctx, id);
  if (d.status !== "pending") throw new AppError("conflict", "This draft was already handled");
  const ws = await db.workspace.findUniqueOrThrow({ where: { id: ctx.workspaceId } });
  if (ws.paused) throw new AppError("limit", "The workspace is paused, so nothing can be published. Resume it in Guardrails first.");
  if (ws.status !== "active") throw new AppError("limit", "Publishing starts once the plan is active (payment confirmed). Your draft is saved.");
  const rules = await rulesFor(ctx.workspaceId);
  const c = checkContent(d.text, rules); // re-check the exact text right before publishing
  if (!c.ok) {
    await db.draft.updateMany({ where: { id, workspaceId: ctx.workspaceId, status: "pending" }, data: { guardNote: c.reason } });
    throw new AppError("invalid", `${c.reason} ${c.alternative}`);
  }
  const mode: PublishMode = opts.mode ?? (d.mode as PublishMode);
  const scheduledAt = checkSchedule(mode, opts.scheduledAt ?? d.scheduledAt);
  const platforms = safeJson<string[]>(d.platforms, []);
  // Approve-time content check: numbers must be in notes + AI classifier (on the exact text being published).
  const ai = await approveTimeCheck(ctx.workspaceId, d.text, platforms, rules);
  if (!ai.ok) {
    await db.draft.updateMany({ where: { id, workspaceId: ctx.workspaceId, status: "pending" }, data: { guardNote: ai.reason } });
    await audit(ctx.workspaceId, { type: "system" }, "draft.check_blocked", id, { category: ai.category });
    throw new AppError("invalid", `${ai.reason} ${ai.alternative}`);
  }
  await audit(ctx.workspaceId, { type: "system" }, "draft.check_passed", id, ai.skipped ? { skipped: ai.skipped } : { model: ai.model });
  const caps = await postCapCheck(ctx.workspaceId, platforms, rules);
  if (!caps.allowed.length) throw new AppError("limit", `Today's post cap is reached for ${caps.over.join(", ")}. It stays a draft; approve it tomorrow or raise the cap in Guardrails.`);
  // Atomic claim: only one approval can win.
  const claim = await db.draft.updateMany({ where: { id, workspaceId: ctx.workspaceId, status: "pending" }, data: { status: "approved", decidedBy: ctx.userId, decidedAt: new Date(), mode, scheduledAt } });
  if (claim.count !== 1) throw new AppError("conflict", "This draft was already handled");
  await audit(ctx.workspaceId, { type: "user", userId: ctx.userId }, "draft.approve", id, { platforms: caps.allowed, over_cap: caps.over });

  const result = await publishApproved(ctx.workspaceId, id, caps.allowed, mode, scheduledAt);
  if (caps.over.length) result.note += ` Over today's cap (not sent): ${caps.over.join(", ")}.`;
  result.keptAsDraft = caps.over;
  await db.draft.update({ where: { id }, data: { publishResult: result.note.slice(0, 1000) } });
  return result;
}

const istFmt = (d: Date) => d.toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) + " IST";

async function publishApproved(workspaceId: string, id: string, platforms: string[], mode: PublishMode, scheduledAt: Date | null): Promise<ApproveResult> {
  const d = await db.draft.findUniqueOrThrow({ where: { id } });
  if (config.publishDryRun) {
    await audit(workspaceId, { type: "system" }, "draft.publish_dry_run", id, { platforms, mode, scheduledAt: scheduledAt?.toISOString() });
    const when = mode === "scheduled" && scheduledAt ? ` (would be scheduled for ${istFmt(scheduledAt)})` : mode === "now" ? " (would share now)" : "";
    return { status: "approved", note: `Approved. Dry run: not sent to Buffer (PUBLISH_DRY_RUN is on)${when}. Use the Open in app buttons to post from your phone.`, published: [], keptAsDraft: [] };
  }
  let token: string | null = null;
  try { token = await bufferToken(workspaceId); } catch (e) {
    return { status: "approved", note: `Approved, but Buffer sign-in needs renewing (${e instanceof Error ? e.message : "error"}). Reconnect Buffer in Settings → Integrations, or use the Open in app buttons.`, published: [], keptAsDraft: [] };
  }
  if (!token) return { status: "approved", note: "Approved. Buffer isn't connected, so use the Open in app buttons to post from your phone.", published: [], keptAsDraft: [] };
  const ok: string[] = [], ids: string[] = [], errs: string[] = [];
  for (const p of platforms) {
    try {
      const post = await publish(token, p, d.text, { mode, dueAt: scheduledAt, channelId: await channelPickFor(workspaceId, p) });
      ok.push(p); ids.push(post.id);
      await recordEvent(workspaceId, "publish");
    } catch (e) {
      errs.push(`${p}: ${e instanceof Error ? e.message : "failed"}`);
    }
  }
  const status = ok.length ? "published" : "failed";
  await db.draft.update({ where: { id }, data: { status, bufferPostIds: JSON.stringify(ids) } });
  await audit(workspaceId, { type: "system" }, status === "published" ? "draft.published" : "draft.publish_failed", id, { ok, errs });
  const note = (ok.length ? `Sent to Buffer (${mode === "now" ? "sharing now" : mode === "scheduled" && scheduledAt ? `scheduled for ${istFmt(scheduledAt)}` : "added to the queue"}): ${ok.join(", ")}.` : "Buffer didn't take it.") + (errs.length ? ` Problems: ${errs.join("; ")}` : "");
  return { status, note, published: ok, keptAsDraft: [] };
}

export async function setPaused(ctx: Ctx, paused: boolean) {
  if (!paused && ctx.role !== "owner") throw new AppError("forbidden", "Only the owner can resume");
  if (paused && !can(ctx, "member")) throw new AppError("forbidden", "Not allowed");
  await db.workspace.update({ where: { id: ctx.workspaceId }, data: { paused } });
  await audit(ctx.workspaceId, { type: "user", userId: ctx.userId }, paused ? "workspace.pause" : "workspace.resume");
}
