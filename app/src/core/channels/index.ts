// Channel bindings + delivery of agent replies and draft previews to Telegram / Slack.
import { db } from "../db";
import { AppError } from "../errors";
import { audit } from "../audit";
import { can, type Ctx } from "../tenancy";
import { randomToken } from "../crypto";
import { getSecret, safeJson } from "../integrations";
import { sendTelegram, telegramDraftMarkup } from "./telegram";
import { sendSlack } from "./slack";

export async function listBindings(ctx: Ctx) {
  return db.channelBinding.findMany({ where: { workspaceId: ctx.workspaceId } });
}

export async function createTelegramLink(ctx: Ctx) {
  if (!can(ctx, "admin")) throw new AppError("forbidden", "Only owners and admins can connect channels");
  const s = await getSecret(ctx.workspaceId, "telegram");
  if (!s) throw new AppError("invalid", "Save your Telegram bot token first (Integrations)");
  const code = randomToken(12).replace(/[^A-Za-z0-9]/g, "").slice(0, 16);
  await db.channelBinding.upsert({
    where: { workspaceId_channel: { workspaceId: ctx.workspaceId, channel: "telegram" } },
    create: { workspaceId: ctx.workspaceId, channel: "telegram", linkCode: code, boundUserId: ctx.userId, status: "pending" },
    update: { linkCode: code, boundUserId: ctx.userId, status: "pending", externalId: "" },
  });
  await audit(ctx.workspaceId, { type: "user", userId: ctx.userId }, "channel.telegram_link_created");
  const row = await db.integration.findUnique({ where: { workspaceId_provider: { workspaceId: ctx.workspaceId, provider: "telegram" } } });
  const username = safeJson<{ username?: string }>(row?.config, {}).username;
  return { code, url: username ? `https://t.me/${username}?start=${code}` : null };
}

export async function connectSlackChannel(ctx: Ctx, channelId: string) {
  if (!can(ctx, "admin")) throw new AppError("forbidden", "Only owners and admins can connect channels");
  if (!/^[CG][A-Z0-9]{6,}$/.test(channelId.trim())) throw new AppError("invalid", "Slack channel id looks like C0123ABCD");
  if (!(await getSecret(ctx.workspaceId, "slack"))) throw new AppError("invalid", "Save your Slack bot token first (Integrations)");
  await db.channelBinding.upsert({
    where: { workspaceId_channel: { workspaceId: ctx.workspaceId, channel: "slack" } },
    create: { workspaceId: ctx.workspaceId, channel: "slack", externalId: channelId.trim(), boundUserId: ctx.userId, status: "active", cursor: JSON.stringify({ lastTs: String(Date.now() / 1000) }) },
    update: { externalId: channelId.trim(), boundUserId: ctx.userId, status: "active", cursor: JSON.stringify({ lastTs: String(Date.now() / 1000) }) },
  });
  await audit(ctx.workspaceId, { type: "user", userId: ctx.userId }, "channel.slack_connected", channelId);
}

export async function disconnectChannel(ctx: Ctx, channel: string) {
  if (!can(ctx, "admin")) throw new AppError("forbidden", "Only owners and admins can change channels");
  if (channel === "web") throw new AppError("invalid", "Web chat is always on");
  await db.channelBinding.updateMany({ where: { workspaceId: ctx.workspaceId, channel }, data: { status: "disabled" } });
  await audit(ctx.workspaceId, { type: "user", userId: ctx.userId }, "channel.disconnected", channel);
}

/** Called by the worker after the agent replied in a Telegram/Slack conversation. */
export async function deliverReply(workspaceId: string, conversationId: string, text: string) {
  const c = await db.conversation.findFirst({ where: { id: conversationId, workspaceId } });
  if (!c) return;
  if (c.channel === "telegram") await sendTelegram(workspaceId, c.externalId, text);
  if (c.channel === "slack") {
    const [channel, thread] = c.externalId.split(":");
    await sendSlack(workspaceId, channel, text, thread);
  }
}

/** Mirrors a new draft into every active chat channel (approvals always also show in the dashboard). */
export async function notifyDraft(workspaceId: string, draftId: string) {
  const d = await db.draft.findFirst({ where: { id: draftId, workspaceId } });
  if (!d || d.status !== "pending") return;
  const plats = safeJson<string[]>(d.platforms, []).join(", ");
  const binds = await db.channelBinding.findMany({ where: { workspaceId, status: "active", channel: { in: ["telegram", "slack"] } } });
  for (const b of binds) {
    if (b.channel === "telegram" && b.externalId) {
      await sendTelegram(workspaceId, b.externalId, `New draft for ${plats}:\n\n${d.text}`, telegramDraftMarkup(d.id, d.text));
    }
    if (b.channel === "slack" && b.externalId) {
      const code = d.id.slice(-6);
      await sendSlack(workspaceId, b.externalId, `New draft for ${plats}:\n>${d.text.replace(/\n/g, "\n>")}\nReply \`approve ${code}\` or \`reject ${code}\`, or open the Approvals inbox.`);
    }
  }
}
