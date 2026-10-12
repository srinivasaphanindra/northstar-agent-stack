import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { db } from "@/core/db";
import { proposeDraft, approveDraft, rejectDraft, editDraft, setPaused } from "@/core/approvals";
import { saveIntegration, saveIntegration as save } from "@/core/integrations";
import { saveOverrides } from "@/core/guardrails";
import { TOOL_NAMES, runAgentReply } from "@/core/agent";
import { newWebConversation, sendUserMessage } from "@/core/chat";
import { claimNext } from "@/core/jobs";
import { handoffLinks } from "@/core/handoff";
import { resetDb, makeTenant } from "./helpers";

const ok = async (p: ReturnType<typeof proposeDraft>) => { const r = await p; if (!r.ok) throw new Error(r.reason); return r; };

describe("approvals", () => {
  beforeEach(async () => { await resetDb(); process.env.PUBLISH_DRY_RUN = "true"; });
  afterEach(() => { vi.unstubAllGlobals(); process.env.PUBLISH_DRY_RUN = "true"; });

  it("the agent has no approve/publish tool", () => {
    expect(TOOL_NAMES.sort()).toEqual(["list_pending_drafts", "propose_post", "save_note"]);
  });

  it("agent chat -> draft pending -> nothing sent to Buffer until a human approves", async () => {
    const t = await makeTenant("Salon");
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const conv = await newWebConversation(t.ctx);
    const m = await sendUserMessage(t.ctx, conv.id, "draft a linkedin post about our weekend hair spa offer");
    const job = await claimNext(["agent.reply"]);
    expect(job?.type).toBe("agent.reply");
    const reply = await runAgentReply(t.ws.id, conv.id, m.id);
    expect(reply.content).toMatch(/Approvals inbox/);
    const drafts = await db.draft.findMany({ where: { workspaceId: t.ws.id } });
    expect(drafts).toHaveLength(1);
    expect(drafts[0].status).toBe("pending");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("approve publishes once via Buffer (live mode, fake Buffer), second approve is refused", async () => {
    process.env.PUBLISH_DRY_RUN = "false";
    const t = await makeTenant("Clinic");
    await saveIntegration(t.ctx, "buffer", "buf-test-token-0000");
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (_u: string, init: RequestInit) => {
      const q = JSON.parse(String(init.body)).query as string;
      calls.push(q.split(/[({]/)[0].trim() || q.slice(0, 20));
      if (q.includes("organizations")) return Response.json({ data: { account: { organizations: [{ id: "o1", name: "x" }] } } });
      if (q.includes("channels(")) return Response.json({ data: { channels: [{ id: "c1", service: "linkedin", name: "li" }, { id: "c2", service: "twitter", name: "x" }] } });
      if (q.includes("createPost")) return Response.json({ data: { createPost: { post: { id: "p1", status: "buffer", dueAt: null } } } });
      return Response.json({ errors: [{ message: "unexpected" }] });
    }));
    const d = await ok(proposeDraft(t.ws.id, { text: "Tip: drink water before your physio session.", platforms: ["linkedin"] }));
    const r = await approveDraft(t.ctx, d.draftId);
    expect(r.status).toBe("published");
    expect(r.published).toEqual(["linkedin"]);
    expect(calls.filter((c) => c.startsWith("mutation"))).toHaveLength(1);
    await expect(approveDraft(t.ctx, d.draftId)).rejects.toThrow(/already handled/);
    expect(calls.filter((c) => c.startsWith("mutation"))).toHaveLength(1);
    const audit = await db.auditLog.findMany({ where: { workspaceId: t.ws.id, action: { startsWith: "draft." } } });
    expect(audit.map((a) => a.action)).toEqual(expect.arrayContaining(["draft.create", "draft.approve", "draft.published"]));
  });

  it("concurrent approvals publish only once", async () => {
    const t = await makeTenant("Race");
    const d = await ok(proposeDraft(t.ws.id, { text: "One post only", platforms: ["x"] }));
    const res = await Promise.allSettled([approveDraft(t.ctx, d.draftId), approveDraft(t.ctx, d.draftId), approveDraft(t.ctx, d.draftId)]);
    expect(res.filter((r) => r.status === "fulfilled")).toHaveLength(1);
  });

  it("dry run (default) approves without calling Buffer; reject and edit work", async () => {
    const t = await makeTenant("Dry");
    await saveIntegration(t.ctx, "buffer", "buf-test-token-0000");
    const f = vi.fn(); vi.stubGlobal("fetch", f);
    const d1 = await ok(proposeDraft(t.ws.id, { text: "First", platforms: ["x"] }));
    const r = await approveDraft(t.ctx, d1.draftId);
    expect(r.status).toBe("approved");
    expect(r.note).toMatch(/Dry run/);
    expect(f).not.toHaveBeenCalled();
    const d2 = await ok(proposeDraft(t.ws.id, { text: "Second", platforms: ["x"] }));
    await editDraft(t.ctx, d2.draftId, "Second, edited");
    expect((await db.draft.findUniqueOrThrow({ where: { id: d2.draftId } })).text).toBe("Second, edited");
    await rejectDraft(t.ctx, d2.draftId);
    await expect(approveDraft(t.ctx, d2.draftId)).rejects.toThrow(/already handled/);
  });

  it("guardrails: content check at propose and at approve, daily cap, pause, unpaid plan", async () => {
    const t = await makeTenant("Guard");
    const bad = await proposeDraft(t.ws.id, { text: "Great day #a #b #c #d", platforms: ["x"] });
    expect(bad.ok).toBe(false);
    const fake = await proposeDraft(t.ws.id, { text: "Get 5 star reviews from happy clients, guaranteed results", platforms: ["x"] });
    expect(fake.ok).toBe(false);
    if (!fake.ok) expect(fake.alternative.length).toBeGreaterThan(5);
    // edit at approval time is re-checked
    const d = await ok(proposeDraft(t.ws.id, { text: "Clean text", platforms: ["x"] }));
    await expect(approveDraft(t.ctx, d.draftId, { text: "now with #a #b #c" })).rejects.toThrow(/hashtags/);
    // cap: x posts_per_day = 1
    await saveOverrides(t.ctx, { x_posts: "1", x_engage: "5", max_hashtags: "2", monthly_ai_budget_usd: "10" });
    await approveDraft(t.ctx, d.draftId);
    const d2 = await ok(proposeDraft(t.ws.id, { text: "Another one", platforms: ["x"] }));
    await expect(approveDraft(t.ctx, d2.draftId)).rejects.toThrow(/cap/);
    // pause
    await setPaused(t.ctx, true);
    const p = await proposeDraft(t.ws.id, { text: "x", platforms: ["linkedin"] });
    expect(p.ok).toBe(false);
    await setPaused(t.ctx, false);
    // unpaid workspace can't publish
    const u = await makeTenant("Unpaid", { active: false });
    const d3 = await ok(proposeDraft(u.ws.id, { text: "hello", platforms: ["linkedin"] }));
    await expect(approveDraft(u.ctx, d3.draftId)).rejects.toThrow(/payment/);
  });

  it("handoff links prefill text where the platform allows it", () => {
    const l = Object.fromEntries(handoffLinks("Hi & welcome #1").map((h) => [h.id, h]));
    expect(l.x.url).toBe("https://x.com/intent/post?text=Hi%20%26%20welcome%20%231");
    expect(l.whatsapp.url).toBe("https://wa.me/?text=Hi%20%26%20welcome%20%231");
    expect(l.linkedin.copyFirst).toBe(true);
    expect(l.facebook.copyFirst).toBe(true);
    expect(handoffLinks("t", { url: "https://a.in" }).find((h) => h.id === "linkedin")?.url).toBe("https://www.linkedin.com/sharing/share-offsite/?url=https%3A%2F%2Fa.in");
    void save;
  });
});
