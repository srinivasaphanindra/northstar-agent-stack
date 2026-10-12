// Buffer OAuth (PKCE), token refresh, channel picker, post now / scheduled. Fake Buffer via a fetch stub; nothing leaves the box.
import crypto from "node:crypto";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { db } from "@/core/db";
import { pkcePair, startBufferOAuth, finishBufferOAuth, bufferToken, refreshBufferChannels, saveChannelPicks, bufferView } from "@/core/buffer-oauth";
import { postInput } from "@/core/buffer";
import { proposeDraft, approveDraft, checkSchedule, parseIstLocal } from "@/core/approvals";
import { saveIntegration } from "@/core/integrations";
import { resetDb, makeTenant } from "./helpers";

const ENV = { BUFFER_CLIENT_ID: "test-client", BUFFER_OAUTH_TOKEN_URL: "https://auth.buffer.test/token", BUFFER_OAUTH_AUTHORIZE_URL: "https://auth.buffer.test/auth", BUFFER_API_URL: "https://api.buffer.test" };
type Call = { url: string; body: string };

function fakeBuffer(opts: { tokenOk?: boolean } = {}) {
  const calls: Call[] = [];
  let n = 0;
  vi.stubGlobal("fetch", vi.fn(async (u: string, init: RequestInit = {}) => {
    const url = String(u), body = String(init.body ?? "");
    calls.push({ url, body });
    if (url === ENV.BUFFER_OAUTH_TOKEN_URL) {
      if (opts.tokenOk === false) return Response.json({ error: "invalid_grant", error_description: "bad code" }, { status: 400 });
      n++;
      return Response.json({ access_token: `access-${n}-xxxxxxxx`, refresh_token: `refresh-${n}`, expires_in: 3600, token_type: "Bearer" });
    }
    const q = JSON.parse(body || "{}").query as string;
    if (q?.includes("organizations")) return Response.json({ data: { account: { organizations: [{ id: "o1", name: "Org" }] } } });
    if (q?.includes("channels(")) return Response.json({ data: { channels: [
      { id: "li-personal", service: "linkedin", name: "Me on LinkedIn" },
      { id: "li-page", service: "linkedin", name: "Clinic page" },
      { id: "x1", service: "twitter", name: "@clinic", isDisconnected: true },
    ] } });
    if (q?.includes("createPost")) return Response.json({ data: { createPost: { post: { id: "p1", status: "scheduled", dueAt: null } } } });
    return Response.json({ errors: [{ message: "unexpected" }] });
  }));
  return calls;
}

describe("buffer oauth + channels + scheduling", () => {
  beforeEach(async () => { await resetDb(); Object.assign(process.env, ENV); process.env.PUBLISH_DRY_RUN = "true"; });
  afterEach(() => { vi.unstubAllGlobals(); for (const k of Object.keys(ENV)) delete process.env[k]; process.env.PUBLISH_DRY_RUN = "true"; });

  it("PKCE pair: S256 challenge of the verifier", () => {
    const { verifier, challenge } = pkcePair();
    expect(verifier).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(challenge).toBe(crypto.createHash("sha256").update(verifier).digest("base64url"));
  });

  it("is off without BUFFER_CLIENT_ID", async () => {
    delete process.env.BUFFER_CLIENT_ID;
    const t = await makeTenant();
    await expect(startBufferOAuth(t.ctx)).rejects.toThrow(/isn't set up/);
  });

  it("start -> callback: state checked, code + verifier exchanged, tokens stored encrypted, state single-use", async () => {
    const t = await makeTenant();
    const url = new URL(await startBufferOAuth(t.ctx));
    expect(url.origin + url.pathname).toBe(ENV.BUFFER_OAUTH_AUTHORIZE_URL);
    const q = url.searchParams;
    expect(q.get("client_id")).toBe("test-client");
    expect(q.get("code_challenge_method")).toBe("S256");
    expect(q.get("scope")).toContain("offline_access");
    expect(q.get("redirect_uri")).toMatch(/\/api\/buffer\/oauth\/callback$/);

    const calls = fakeBuffer();
    await expect(finishBufferOAuth(t.ctx, "wrong-state", "code1")).rejects.toThrow(/expired or doesn't match/);
    expect(calls).toHaveLength(0);

    const url2 = new URL(await startBufferOAuth(t.ctx));
    const state = url2.searchParams.get("state")!;
    await finishBufferOAuth(t.ctx, state, "code1");
    const sent = new URLSearchParams(calls[0].body);
    expect(sent.get("grant_type")).toBe("authorization_code");
    expect(sent.get("code")).toBe("code1");
    expect(crypto.createHash("sha256").update(sent.get("code_verifier")!).digest("base64url")).toBe(url2.searchParams.get("code_challenge"));

    const row = await db.integration.findUniqueOrThrow({ where: { workspaceId_provider: { workspaceId: t.ws.id, provider: "buffer" } } });
    expect(row.status).toBe("ok");
    expect(row.secretEnc).not.toContain("access-1");
    expect(row.config).not.toContain("refresh-1");
    expect(row.config).not.toContain("oauthState");
    expect(await bufferToken(t.ws.id)).toBe("access-1-xxxxxxxx");
    await expect(finishBufferOAuth(t.ctx, state, "code1")).rejects.toThrow(/expired or doesn't match/);
  });

  it("refreshes an expiring token once, even for parallel callers; a pasted personal key replaces OAuth", async () => {
    const t = await makeTenant();
    const state = new URL(await startBufferOAuth(t.ctx)).searchParams.get("state")!;
    const calls = fakeBuffer();
    await finishBufferOAuth(t.ctx, state, "c");
    const row = await db.integration.findUniqueOrThrow({ where: { workspaceId_provider: { workspaceId: t.ws.id, provider: "buffer" } } });
    await db.integration.update({ where: { id: row.id }, data: { config: JSON.stringify({ ...JSON.parse(row.config), expiresAt: Date.now() - 1 }) } });
    const [a, b] = await Promise.all([bufferToken(t.ws.id), bufferToken(t.ws.id)]);
    expect(a).toBe("access-2-xxxxxxxx");
    expect(b).toBe("access-2-xxxxxxxx");
    const refreshes = calls.filter((c) => new URLSearchParams(c.body).get("grant_type") === "refresh_token");
    expect(refreshes).toHaveLength(1);
    expect(new URLSearchParams(refreshes[0].body).get("refresh_token")).toBe("refresh-1");

    await saveIntegration(t.ctx, "buffer", "personal-key-1234");
    expect((await bufferView(t.ws.id)).authMode).toBe("key");
    expect(await bufferToken(t.ws.id)).toBe("personal-key-1234");
  });

  it("channel picker: lists channels, only accepts a channel of the right service", async () => {
    const t = await makeTenant();
    await saveIntegration(t.ctx, "buffer", "personal-key-1234");
    fakeBuffer();
    const ch = await refreshBufferChannels(t.ctx);
    expect(ch.map((c) => c.id)).toEqual(["li-personal", "li-page", "x1"]);
    await expect(saveChannelPicks(t.ctx, { x: "li-page" })).rejects.toThrow(/isn't a x channel/);
    await saveChannelPicks(t.ctx, { linkedin: "li-page", facebook: "" });
    expect((await bufferView(t.ws.id)).picks).toEqual({ linkedin: "li-page" });
  });

  it("live publish (fake Buffer) uses the picked channel and customScheduled + dueAt", async () => {
    process.env.PUBLISH_DRY_RUN = "false";
    const t = await makeTenant();
    await saveIntegration(t.ctx, "buffer", "personal-key-1234");
    const calls = fakeBuffer();
    await refreshBufferChannels(t.ctx);
    await saveChannelPicks(t.ctx, { linkedin: "li-page" });
    const p = await proposeDraft(t.ws.id, { text: "Posture tip: stand up every 30 minutes.", platforms: ["linkedin"] });
    if (!p.ok) throw new Error(p.reason);
    const at = new Date(Date.now() + 3 * 3600_000);
    const r = await approveDraft(t.ctx, p.draftId, { mode: "scheduled", scheduledAt: at });
    expect(r.status).toBe("published");
    expect(r.note).toMatch(/scheduled for .* IST/);
    const mut = calls.find((c) => c.body.includes("createPost"))!;
    const input = JSON.parse(mut.body).variables.input;
    expect(input).toMatchObject({ channelId: "li-page", mode: "customScheduled", dueAt: at.toISOString() });
    const d = await db.draft.findUniqueOrThrow({ where: { id: p.draftId } });
    expect(d.mode).toBe("scheduled");
    expect(d.scheduledAt?.toISOString()).toBe(at.toISOString());
  });

  it("dry run + scheduled: nothing sent, the note says when it would go", async () => {
    const t = await makeTenant();
    const f = vi.fn(); vi.stubGlobal("fetch", f);
    const p = await proposeDraft(t.ws.id, { text: "Open on Sunday too.", platforms: ["facebook"] });
    if (!p.ok) throw new Error(p.reason);
    const r = await approveDraft(t.ctx, p.draftId, { mode: "scheduled", scheduledAt: parseIstLocal("2099-01-01T10:30") ?? undefined }).catch((e) => e);
    expect(String(r.message ?? r)).toMatch(/within the next 180 days/);
    const ok = await approveDraft(t.ctx, p.draftId, { mode: "scheduled", scheduledAt: new Date(Date.now() + 86400_000) });
    expect(ok.note).toMatch(/Dry run.*would be scheduled for/);
    expect(f).not.toHaveBeenCalled();
  });

  it("post-now / queue / scheduled inputs and schedule validation", () => {
    expect(postInput("c", "t", { mode: "now" }).mode).toBe("shareNow");
    expect(postInput("c", "t", { mode: "queue" }).mode).toBe("addToQueue");
    expect(() => postInput("c", "t", { mode: "scheduled" })).toThrow(/needs a time/);
    const now = new Date("2026-10-12T00:00:00Z");
    expect(() => checkSchedule("scheduled", new Date("2026-10-12T00:02:00Z"), now)).toThrow(/5 minutes/);
    expect(checkSchedule("queue", null, now)).toBeNull();
    expect(parseIstLocal("2026-10-12T18:30")?.toISOString()).toBe("2026-10-12T13:00:00.000Z");
    expect(parseIstLocal("garbage")).toBeNull();
  });
});
