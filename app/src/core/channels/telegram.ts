// Telegram connector: long polling with getUpdates (no inbound URL needed), one bot token per tenant.
// A chat is bound to a workspace with a one-time /start <code> link; only that private chat is answered.
import { db } from "../db";
import { config } from "../config";
import { audit } from "../audit";
import { getSecret, safeJson } from "../integrations";
import { addIncoming, upsertConversation } from "../chat";
import { requireMember } from "../tenancy";
import { approveDraft, rejectDraft } from "../approvals";
import { handoffLinks } from "../handoff";

type TgUpdate = {
  update_id: number;
  message?: { message_id: number; chat: { id: number; type: string }; from?: { id: number; first_name?: string }; text?: string };
  callback_query?: { id: string; from: { id: number }; message?: { chat: { id: number }; message_id: number }; data?: string };
};

async function api<T>(token: string, method: string, body: Record<string, unknown>, timeoutMs = 35_000): Promise<T> {
  const r = await fetch(`${config.telegramApi}/bot${token}/${method}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: AbortSignal.timeout(timeoutMs) });
  const j = (await r.json().catch(() => ({}))) as { ok?: boolean; result?: T; description?: string };
  if (!j.ok) throw new Error(`telegram ${method}: ${j.description ?? r.status}`);
  return j.result as T;
}

export async function sendTelegram(workspaceId: string, chatId: string, text: string, replyMarkup?: unknown) {
  const s = await getSecret(workspaceId, "telegram");
  if (!s) return;
  await api(s.secret, "sendMessage", { chat_id: chatId, text: text.slice(0, 4000), ...(replyMarkup ? { reply_markup: replyMarkup } : {}) }, 15_000);
}

/** One getUpdates round for one workspace. longPollSec=0 for tests. */
export async function pollTelegramOnce(workspaceId: string, longPollSec = 25) {
  const s = await getSecret(workspaceId, "telegram");
  if (!s) return 0;
  const binding = await db.channelBinding.findUnique({ where: { workspaceId_channel: { workspaceId, channel: "telegram" } } });
  if (!binding || binding.status === "disabled") return 0;
  const offset = Number(binding.cursor || 0);
  const updates = await api<TgUpdate[]>(s.secret, "getUpdates", { offset: offset || undefined, timeout: longPollSec, allowed_updates: ["message", "callback_query"] }, (longPollSec + 10) * 1000);
  let next = offset;
  for (const u of updates) {
    next = Math.max(next, u.update_id + 1);
    try { await handleUpdate(workspaceId, s.secret, u); } catch (e) { console.error("[telegram]", workspaceId, e instanceof Error ? e.message : e); }
  }
  if (next !== offset) await db.channelBinding.update({ where: { id: binding.id }, data: { cursor: String(next) } });
  return updates.length;
}

async function handleUpdate(workspaceId: string, token: string, u: TgUpdate) {
  const b = await db.channelBinding.findUniqueOrThrow({ where: { workspaceId_channel: { workspaceId, channel: "telegram" } } });
  if (u.callback_query) {
    const cq = u.callback_query;
    const chatId = String(cq.message?.chat.id ?? "");
    if (b.status !== "active" || chatId !== b.externalId || !b.boundUserId) {
      await api(token, "answerCallbackQuery", { callback_query_id: cq.id, text: "This chat isn't linked." }, 10_000);
      return;
    }
    const [op, draftId] = (cq.data ?? "").split(":");
    let msg = "";
    try {
      const ctx = await requireMember(b.boundUserId, workspaceId, "admin");
      if (op === "ap") msg = (await approveDraft(ctx, draftId)).note;
      else if (op === "rj") { await rejectDraft(ctx, draftId); msg = "Rejected. Nothing was posted."; }
    } catch (e) {
      msg = e instanceof Error ? e.message : "Couldn't do that";
    }
    await api(token, "answerCallbackQuery", { callback_query_id: cq.id, text: msg.slice(0, 180) }, 10_000);
    if (msg) await api(token, "sendMessage", { chat_id: chatId, text: msg }, 10_000);
    return;
  }
  const m = u.message;
  if (!m?.text) return;
  const chatId = String(m.chat.id);
  const start = m.text.match(/^\/start(?:\s+(\S+))?/);
  if (start) {
    if (m.chat.type !== "private") return;
    if (b.status === "pending" && b.linkCode && start[1] === b.linkCode) {
      await db.channelBinding.update({ where: { id: b.id }, data: { status: "active", externalId: chatId, linkCode: "" } });
      await audit(workspaceId, { type: "system" }, "channel.telegram_linked", chatId);
      await api(token, "sendMessage", { chat_id: chatId, text: "Connected ✅ You can chat with your Northstar assistant here. Drafts will arrive with Approve / Reject buttons." }, 10_000);
    } else if (b.status === "active" && b.externalId === chatId) {
      await api(token, "sendMessage", { chat_id: chatId, text: "You're already connected. Just type what you need." }, 10_000);
    } else {
      await api(token, "sendMessage", { chat_id: chatId, text: "This link isn't valid. Create a new one in the dashboard: Settings → Channels." }, 10_000);
    }
    return;
  }
  if (b.status !== "active" || b.externalId !== chatId) {
    await api(token, "sendMessage", { chat_id: chatId, text: "This chat isn't linked to a Northstar workspace." }, 10_000);
    return;
  }
  const conv = await upsertConversation(workspaceId, "telegram", chatId, "Telegram");
  await addIncoming(workspaceId, conv.id, m.text.slice(0, 4000), b.boundUserId, { telegramMessageId: m.message_id });
}

export function telegramDraftMarkup(draftId: string, text: string) {
  const links = handoffLinks(text).filter((l) => l.id === "x" || l.id === "whatsapp");
  return {
    inline_keyboard: [
      [{ text: "✅ Approve", callback_data: `ap:${draftId}` }, { text: "❌ Reject", callback_data: `rj:${draftId}` }],
      links.map((l) => ({ text: l.label, url: l.url! })),
      [{ text: "Edit in dashboard", url: `${config.appUrl}/app/approvals#${draftId}` }],
    ],
  };
}

export function telegramUsername(cfg: string) {
  return safeJson<{ username?: string }>(cfg, {}).username ?? "";
}
