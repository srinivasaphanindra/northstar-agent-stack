import { describe, it, expect, beforeEach } from "vitest";
import { db } from "@/core/db";
import { createUser } from "@/core/auth";
import { requireMember, createInvite, acceptInvite, changeRole, removeMember } from "@/core/tenancy";
import { proposeDraft, getDraft, approveDraft, listDrafts } from "@/core/approvals";
import { getConversation, newWebConversation, listMessages } from "@/core/chat";
import { saveIntegration, listIntegrations } from "@/core/integrations";
import { runAgentReply } from "@/core/agent";
import { resetDb, makeTenant } from "./helpers";

describe("tenant isolation", () => {
  beforeEach(resetDb);

  it("a user can't enter a workspace they don't belong to", async () => {
    const a = await makeTenant("A");
    const b = await makeTenant("B");
    await expect(requireMember(a.user.id, b.ws.id)).rejects.toThrow(/not found/);
  });

  it("drafts, conversations and integrations are invisible across tenants", async () => {
    const a = await makeTenant("A");
    const b = await makeTenant("B");
    const d = await proposeDraft(b.ws.id, { text: "B's private draft", platforms: ["linkedin"] });
    if (!d.ok) throw new Error("draft blocked");
    await expect(getDraft(a.ctx, d.draftId)).rejects.toThrow(/not found/);
    await expect(approveDraft(a.ctx, d.draftId)).rejects.toThrow(/not found/);
    expect(await listDrafts(a.ctx)).toHaveLength(0);
    expect((await db.draft.findUniqueOrThrow({ where: { id: d.draftId } })).status).toBe("pending");

    const conv = await newWebConversation(b.ctx);
    await expect(getConversation(a.ctx, conv.id)).rejects.toThrow(/not found/);
    await expect(listMessages(a.ctx, conv.id)).rejects.toThrow(/not found/);

    await saveIntegration(b.ctx, "buffer", "buffer-secret-token-9999");
    expect(await listIntegrations(a.ctx)).toHaveLength(0);
  });

  it("the worker refuses a job that mixes one tenant's id with another's conversation", async () => {
    const a = await makeTenant("A");
    const b = await makeTenant("B");
    const conv = await newWebConversation(b.ctx);
    const m = await db.message.create({ data: { workspaceId: b.ws.id, conversationId: conv.id, role: "user", content: "hi" } });
    await expect(runAgentReply(a.ws.id, conv.id, m.id)).rejects.toThrow(/not found/);
  });

  it("roles: member can't approve or change integrations; invites bind to the invited email", async () => {
    const a = await makeTenant("A");
    const mUser = await createUser({ email: "member@test.local", password: "a-long-password-1" });
    const { token } = await createInvite(a.ctx, "member@test.local", "member");
    const other = await createUser({ email: "other@test.local", password: "a-long-password-1" });
    await expect(acceptInvite(other.id, token)).rejects.toThrow(/different email/);
    await acceptInvite(mUser.id, token);
    await expect(acceptInvite(mUser.id, token)).rejects.toThrow(/not valid/);
    const mctx = await requireMember(mUser.id, a.ws.id);
    expect(mctx.role).toBe("member");
    await expect(requireMember(mUser.id, a.ws.id, "admin")).rejects.toThrow(/admin role/);
    const d = await proposeDraft(a.ws.id, { text: "hello world", platforms: ["x"] });
    if (!d.ok) throw new Error("blocked");
    await expect(approveDraft(mctx, d.draftId)).rejects.toThrow(/owners and admins/);
    await expect(saveIntegration(mctx, "buffer", "x".repeat(20))).rejects.toThrow(/owners and admins/);
    await expect(createInvite(mctx, "z@test.local", "admin")).rejects.toThrow();
    const mem = await db.member.findFirstOrThrow({ where: { userId: mUser.id } });
    await changeRole(a.ctx, mem.id, "admin");
    expect((await requireMember(mUser.id, a.ws.id, "admin")).role).toBe("admin");
    const ownerRow = await db.member.findFirstOrThrow({ where: { userId: a.user.id } });
    const actx = await requireMember(mUser.id, a.ws.id, "admin");
    await expect(removeMember(actx, ownerRow.id)).rejects.toThrow(/owner can't be removed/);
  });
});
