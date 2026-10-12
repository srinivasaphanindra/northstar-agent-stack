// Approve-time content check: "numbers must be in notes" + the AI classifier (fake OpenRouter via a fetch stub).
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { db } from "@/core/db";
import { proposeDraft, approveDraft } from "@/core/approvals";
import { addNote } from "@/core/notes";
import { madeUpNumber, parseVerdict } from "@/core/content-check";
import { resetDb, makeTenant } from "./helpers";

const propose = async (ws: string, text: string) => { const r = await proposeDraft(ws, { text, platforms: ["linkedin"] }); if (!r.ok) throw new Error(r.reason); return r.draftId; };
function fakeLlm(content: string | (() => never)) {
  const f = vi.fn(async (url: string) => {
    if (!String(url).includes("/chat/completions")) return Response.json({ errors: [{ message: "unexpected" }] }, { status: 500 });
    if (typeof content === "function") content();
    return Response.json({ choices: [{ message: { content } }], usage: { prompt_tokens: 300, completion_tokens: 20, cost: 0.0001 } });
  });
  vi.stubGlobal("fetch", f);
  return f;
}

describe("content check: pure parts", () => {
  it("finds numbers that aren't in the notes (port of the n8n rule)", () => {
    expect(madeUpNumber("We helped 500 customers last year", "")).toBe("500 customers");
    expect(madeUpNumber("Flat 20% off this weekend", "- weekend offer: 20% off")).toBeNull();
    expect(madeUpNumber("Hair spa now ₹1,499", "- price list: hair spa ₹1499")).toBeNull();
    expect(madeUpNumber("3 tips for better sleep", "")).toBeNull(); // plain counts are fine
    expect(madeUpNumber("Grow 10x faster", "")).toBe("10x");
  });
  it("parses classifier JSON, even with extra words around it", () => {
    expect(parseVerdict('Sure: {"ok": false, "category": "medical", "reason": "cure claim", "fix": "drop it"}')).toEqual({ ok: false, category: "medical", reason: "cure claim", fix: "drop it" });
    expect(parseVerdict("no json")).toBeNull();
    expect(parseVerdict('{"category":"ok"}')).toBeNull();
  });
});

describe("content check at approve time", () => {
  beforeEach(async () => { await resetDb(); process.env.PUBLISH_DRY_RUN = "true"; process.env.LLM_MODE = "mock"; delete process.env.OPENROUTER_API_KEY; });
  afterEach(() => { vi.unstubAllGlobals(); process.env.LLM_MODE = "mock"; delete process.env.OPENROUTER_API_KEY; });

  it("blocks a number that isn't in the notes, passes once the note exists", async () => {
    const t = await makeTenant("Clinic");
    const id = await propose(t.ws.id, "Over 500 patients trust our physio team.");
    await expect(approveDraft(t.ctx, id)).rejects.toThrow(/can't find in your notes \(500 patients\)/);
    const d = await db.draft.findUniqueOrThrow({ where: { id } });
    expect(d.status).toBe("pending");
    expect(d.guardNote).toMatch(/500 patients/);
    await addNote(t.ws.id, "business", "We have treated over 500 patients since 2019.", "test", { type: "user", userId: t.user.id });
    const r = await approveDraft(t.ctx, id);
    expect(r.status).toBe("approved");
  });

  it("numbers rule can be switched off in the yaml (rules.content.numbers_must_be_in_notes)", async () => {
    const t = await makeTenant("Clinic2");
    await db.guardrailSettings.create({ data: { workspaceId: t.ws.id, overrides: JSON.stringify({ content: { numbers_must_be_in_notes: false } }) } });
    const id = await propose(t.ws.id, "Over 500 patients trust our physio team.");
    expect((await approveDraft(t.ctx, id)).status).toBe("approved");
  });

  it("mock mode: no AI call, skip is recorded in the audit log", async () => {
    const t = await makeTenant("Salon");
    const f = vi.fn(); vi.stubGlobal("fetch", f);
    const id = await propose(t.ws.id, "Weekend hair spa slots are open. Message us to book.");
    await approveDraft(t.ctx, id);
    expect(f).not.toHaveBeenCalled();
    const a = await db.auditLog.findFirstOrThrow({ where: { workspaceId: t.ws.id, action: "draft.check_passed" } });
    expect(a.detail).toMatch(/skipped/);
  });

  it("live AI says no -> stays pending with the reason; AI says ok -> approved", async () => {
    process.env.LLM_MODE = "live"; process.env.OPENROUTER_API_KEY = "test-openrouter-key-not-real";
    const t = await makeTenant("Gym");
    const id = await propose(t.ws.id, "Our trainers fix back pain for good.");
    const f = fakeLlm('{"ok": false, "category": "medical", "reason": "Promises a medical outcome.", "fix": "Say what the session includes instead."}');
    await expect(approveDraft(t.ctx, id)).rejects.toThrow(/AI check \(medical\): Promises a medical outcome\. Say what the session includes instead\./);
    expect(f).toHaveBeenCalledTimes(1);
    const body = JSON.parse(String((f.mock.calls[0] as unknown as [string, RequestInit])[1].body));
    expect(body.messages[0].content).toContain("CONTENT SAFETY CHECK v1");
    expect((await db.draft.findUniqueOrThrow({ where: { id } })).status).toBe("pending");

    fakeLlm('{"ok": true, "category": "ok", "reason": "", "fix": ""}');
    expect((await approveDraft(t.ctx, id)).status).toBe("approved");
    const usage = await db.usageEvent.findMany({ where: { workspaceId: t.ws.id } });
    expect(usage.length).toBeGreaterThan(0);
  });

  it("AI errors fail open (human is approving), with the skip recorded", async () => {
    process.env.LLM_MODE = "live"; process.env.OPENROUTER_API_KEY = "test-openrouter-key-not-real";
    const t = await makeTenant("Bakery");
    const id = await propose(t.ws.id, "Fresh bread every morning from 7am.");
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ error: { message: "upstream down" } }, { status: 502 })));
    expect((await approveDraft(t.ctx, id)).status).toBe("approved");
    const a = await db.auditLog.findFirstOrThrow({ where: { workspaceId: t.ws.id, action: "draft.check_passed" } });
    expect(a.detail).toMatch(/AI check unavailable/);
  });
});
