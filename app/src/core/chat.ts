// Conversations + messages (web, Telegram, Slack all land here).
import { db } from "./db";
import { AppError } from "./errors";
import { randomToken } from "./crypto";
import { enqueue } from "./jobs";
import { recordEvent } from "./usage";
import type { Ctx } from "./tenancy";

export async function listConversations(ctx: Ctx) {
  return db.conversation.findMany({ where: { workspaceId: ctx.workspaceId }, orderBy: { updatedAt: "desc" }, take: 50 });
}

export async function getConversation(ctx: Ctx, id: string) {
  const c = await db.conversation.findFirst({ where: { id, workspaceId: ctx.workspaceId } });
  if (!c) throw new AppError("not_found", "Conversation not found");
  return c;
}

export async function newWebConversation(ctx: Ctx, title = "New chat") {
  return db.conversation.create({ data: { workspaceId: ctx.workspaceId, channel: "web", externalId: randomToken(9), title } });
}

export async function upsertConversation(workspaceId: string, channel: string, externalId: string, title: string) {
  return db.conversation.upsert({
    where: { workspaceId_channel_externalId: { workspaceId, channel, externalId } },
    create: { workspaceId, channel, externalId, title },
    update: {},
  });
}

export async function listMessages(ctx: Ctx, conversationId: string, take = 100) {
  await getConversation(ctx, conversationId);
  const rows = await db.message.findMany({ where: { workspaceId: ctx.workspaceId, conversationId, role: { in: ["user", "assistant"] } }, orderBy: { createdAt: "desc" }, take });
  return rows.reverse();
}

/** Saves the user's message and queues the agent reply (worker). */
export async function sendUserMessage(ctx: Ctx, conversationId: string, text: string) {
  const conv = await getConversation(ctx, conversationId);
  const t = text.trim().slice(0, 4000);
  if (!t) throw new AppError("invalid", "Empty message");
  const m = await addIncoming(ctx.workspaceId, conv.id, t, ctx.userId);
  if (conv.title === "New chat") await db.conversation.update({ where: { id: conv.id }, data: { title: t.slice(0, 40) } });
  return m;
}

export async function addIncoming(workspaceId: string, conversationId: string, text: string, authorUserId: string | null, meta: Record<string, unknown> = {}) {
  const m = await db.message.create({ data: { workspaceId, conversationId, role: "user", content: text, authorUserId, meta: JSON.stringify(meta) } });
  await db.conversation.update({ where: { id: conversationId }, data: { updatedAt: new Date() } });
  await recordEvent(workspaceId, "message");
  await enqueue("agent.reply", { conversationId, messageId: m.id }, workspaceId);
  return m;
}
