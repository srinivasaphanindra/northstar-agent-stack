import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { db } from "@/core/db";
import { startOnboarding, answerOnboarding, QUESTIONS } from "@/core/onboarding";
import { choosePlan, markPaid } from "@/core/billing";
import { createUser } from "@/core/auth";
import { saveIntegration } from "@/core/integrations";
import { createTelegramLink } from "@/core/channels";
import { pollTelegramOnce } from "@/core/channels/telegram";
import { spendStatus, platformSpendStatus, recordLlm } from "@/core/usage";
import { runAgentReply } from "@/core/agent";
import { newWebConversation, sendUserMessage } from "@/core/chat";
import { resolveRules } from "@/core/guardrails";
import { resetDb, makeTenant } from "./helpers";

describe("flows", () => {
  beforeEach(resetDb);
  afterEach(() => vi.unstubAllGlobals());

  it("onboarding interview: 10 questions, skip, fix, finish -> brain notes", async () => {
    const t = await makeTenant("Onb");
    await startOnboarding(t.ctx);
    for (let i = 0; i < QUESTIONS.length; i++) await answerOnboarding(t.ctx, i === 7 ? "skip" : `answer ${i + 1}`);
    let s = await db.onboardingSession.findUniqueOrThrow({ where: { workspaceId: t.ws.id } });
    expect(s.status).toBe("review");
    await answerOnboarding(t.ctx, "2: Hyderabad, Kukatpally");
    await answerOnboarding(t.ctx, "my password: hunter2hunter2");
    const r = await answerOnboarding(t.ctx, "looks good");
    expect(r.done).toBe(true);
    s = await db.onboardingSession.findUniqueOrThrow({ where: { workspaceId: t.ws.id } });
    expect(s.status).toBe("done");
    const notes = await db.brainNote.findMany({ where: { workspaceId: t.ws.id } });
    expect(notes).toHaveLength(9);
    expect(notes.find((n) => n.section === "location")?.content).toBe("Hyderabad, Kukatpally");
    expect(JSON.stringify(notes)).not.toContain("hunter2");
    expect((await db.workspace.findUniqueOrThrow({ where: { id: t.ws.id } })).onboardingStep).toBe("integrations");
  });

  it("manual payment: plan -> pending invoice -> admin marks paid -> active", async () => {
    const t = await makeTenant("Pay", { active: false });
    const inv = await choosePlan(t.ctx, "solo", false);
    expect(inv?.amountInr).toBe(1999);
    expect((await db.workspace.findUniqueOrThrow({ where: { id: t.ws.id } })).status).toBe("pending_payment");
    await expect(markPaid(t.user.id, inv!.id, "upi", "ref")).rejects.toThrow(/Admins only/);
    const admin = await createUser({ email: "admin@test.local", password: "a-long-password-1" });
    await markPaid(admin.id, inv!.id, "upi", "UPI-REF-1");
    const ws = await db.workspace.findUniqueOrThrow({ where: { id: t.ws.id } });
    expect(ws.status).toBe("active");
    expect(ws.plan).toBe("solo");
    expect(await db.auditLog.count({ where: { action: "billing.marked_paid" } })).toBe(1);
    const byok = await choosePlan(t.ctx, "pro", true);
    expect(byok?.amountInr).toBe(2999);
  });

  it("spend caps: hard stop answers with a fixed message and skips the model", async () => {
    const t = await makeTenant("Spend");
    await recordLlm(t.ws.id, { model: "m", keyMode: "platform", inputTokens: 0, outputTokens: 0, costUsd: 5 }); // trial cap $1 -> 500%
    const s = await spendStatus(t.ws.id);
    expect(s.level).toBe("stop");
    const conv = await newWebConversation(t.ctx);
    const m = await sendUserMessage(t.ctx, conv.id, "draft a post");
    const r = await runAgentReply(t.ws.id, conv.id, m.id);
    expect(r.content).toMatch(/budget is used up/);
    expect(await db.draft.count({ where: { workspaceId: t.ws.id } })).toBe(0);
  });

  it("platform daily AI cap: hard stop across workspaces on the platform key", async () => {
    process.env.PLATFORM_DAILY_AI_CAP_USD = "3";
    const a = await makeTenant("PlatA");
    const b = await makeTenant("PlatB");
    // Pro plan AI cap ($20) so the workspace monthly budget does not fire before the platform daily cap.
    await db.workspace.update({ where: { id: a.ws.id }, data: { plan: "pro", aiKeyMode: "platform" } });
    await db.workspace.update({ where: { id: b.ws.id }, data: { plan: "pro", aiKeyMode: "platform" } });
    await recordLlm(a.ws.id, { model: "m", keyMode: "platform", inputTokens: 0, outputTokens: 0, costUsd: 2.5 });
    expect((await platformSpendStatus()).level).toBe("ok");
    expect((await spendStatus(a.ws.id)).level).not.toBe("stop");
    await recordLlm(b.ws.id, { model: "m", keyMode: "platform", inputTokens: 0, outputTokens: 0, costUsd: 0.6 });
    const plat = await platformSpendStatus();
    expect(plat.level).toBe("stop");
    expect(plat.used).toBeGreaterThanOrEqual(3);
    const conv = await newWebConversation(a.ctx);
    const m = await sendUserMessage(a.ctx, conv.id, "hello");
    const r = await runAgentReply(a.ws.id, conv.id, m.id);
    expect(r.content).toMatch(/shared AI budget for today/);
    expect(await db.auditLog.count({ where: { action: "platform.daily_ai_cap" } })).toBeGreaterThanOrEqual(1);
  });

  it("pasted secrets in chat are not sent to the model", async () => {
    const t = await makeTenant("Sec");
    const conv = await newWebConversation(t.ctx);
    const m = await sendUserMessage(t.ctx, conv.id, "here is my key sk-or-v1-abcdefghijklmnopqrstuvwxyz123456");
    const r = await runAgentReply(t.ws.id, conv.id, m.id);
    expect(r.content).toMatch(/password or key/);
    expect(await db.usageEvent.count({ where: { workspaceId: t.ws.id, kind: "llm" } })).toBe(0);
  });

  it("guardrails resolve defaults -> tier -> overrides from the yaml template", () => {
    expect(resolveRules("trial", {}).platforms.x.posts_per_day).toBe(3);
    expect(resolveRules("pro", {}).spend.monthly_ai_budget_usd).toBe(20);
    expect(resolveRules("trial", { content: { max_hashtags: 1 } }).content.max_hashtags).toBe(1);
    expect(resolveRules("trial", { approvals: { auto_post_routine: true } }).approvals.auto_post_routine).toBe(false);
  });

  it("telegram: /start code binds the chat, messages queue an agent reply, strangers are ignored", async () => {
    const t = await makeTenant("Tg");
    await saveIntegration(t.ctx, "telegram", "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi");
    const { code } = await createTelegramLink(t.ctx);
    const sent: { chat_id: string; text: string }[] = [];
    let updates: unknown[] = [
      { update_id: 10, message: { message_id: 1, chat: { id: 555, type: "private" }, text: `/start ${code}` } },
      { update_id: 11, message: { message_id: 2, chat: { id: 555, type: "private" }, text: "draft an x post about chai" } },
      { update_id: 12, message: { message_id: 3, chat: { id: 999, type: "private" }, text: "hello?" } },
    ];
    vi.stubGlobal("fetch", vi.fn(async (u: string, init: RequestInit) => {
      const body = JSON.parse(String(init.body ?? "{}"));
      if (u.endsWith("/getUpdates")) { const r = updates; updates = []; return Response.json({ ok: true, result: r }); }
      if (u.endsWith("/sendMessage")) { sent.push(body); return Response.json({ ok: true, result: {} }); }
      return Response.json({ ok: true, result: true });
    }));
    expect(await pollTelegramOnce(t.ws.id, 0)).toBe(3);
    const b = await db.channelBinding.findFirstOrThrow({ where: { workspaceId: t.ws.id, channel: "telegram" } });
    expect(b.status).toBe("active");
    expect(b.externalId).toBe("555");
    expect(b.cursor).toBe("13");
    expect(sent.find((s) => String(s.chat_id) === "999")?.text).toMatch(/isn't linked/);
    const conv = await db.conversation.findFirstOrThrow({ where: { workspaceId: t.ws.id, channel: "telegram" } });
    expect(await db.message.count({ where: { conversationId: conv.id, role: "user" } })).toBe(1);
    expect(await db.job.count({ where: { workspaceId: t.ws.id, type: "agent.reply" } })).toBe(1);
  });
});
