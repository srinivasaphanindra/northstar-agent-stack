// Slack connector (phase 1): per-tenant bot token, polls one channel (like the the example client bot) because the
// box has no inbound URL. Phase 2: Events API + "Add to Slack" OAuth on the VPS.
// Polls conversations.history + replies of recently active threads. Approvals by typed
// `approve <code>` / `reject <code>` from the Slack users listed as approvers.
import { db } from "../db";
import { config } from "../config";
import { getSecret, safeJson } from "../integrations";
import { addIncoming, upsertConversation } from "../chat";
import { requireMember } from "../tenancy";
import { approveDraft, rejectDraft } from "../approvals";

type SlackMsg = { ts: string; thread_ts?: string; user?: string; text?: string; bot_id?: string; app_id?: string; subtype?: string };
type Cursor = { lastTs?: string; threads?: Record<string, string> };

async function api<T>(token: string, method: string, params: Record<string, string | number | undefined>): Promise<T> {
  const body = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined) body.set(k, String(v));
  const r = await fetch(`${config.slackApi}/${method}`, { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/x-www-form-urlencoded" }, body, signal: AbortSignal.timeout(15_000) });
  const j = (await r.json().catch(() => ({}))) as T & { ok?: boolean; error?: string };
  if (!j.ok) throw new Error(`slack ${method}: ${j.error ?? r.status}`);
  return j;
}

export async function sendSlack(workspaceId: string, channel: string, text: string, threadTs?: string) {
  const s = await getSecret(workspaceId, "slack");
  if (!s) return;
  await api(s.secret, "chat.postMessage", { channel, text: text.slice(0, 3900), thread_ts: threadTs });
}

const isHuman = (m: SlackMsg) => !m.bot_id && !m.app_id && !!m.user && (!m.subtype || m.subtype === "thread_broadcast" || m.subtype === "file_share");

export async function pollSlackOnce(workspaceId: string) {
  const s = await getSecret(workspaceId, "slack");
  if (!s) return 0;
  const b = await db.channelBinding.findUnique({ where: { workspaceId_channel: { workspaceId, channel: "slack" } } });
  if (!b || b.status !== "active" || !b.externalId) return 0;
  const cur = safeJson<Cursor>(b.cursor, {});
  const oldest = cur.lastTs || String(Date.now() / 1000);
  const hist = await api<{ messages: SlackMsg[] }>(s.secret, "conversations.history", { channel: b.externalId, oldest, limit: 15 });
  const fresh: SlackMsg[] = hist.messages.filter((m) => m.ts > oldest);
  const threads = cur.threads ?? {};
  const cutoff = Date.now() / 1000 - 48 * 3600;
  for (const [t, seen] of Object.entries(threads).slice(-5)) {
    if (Number(t) < cutoff) { delete threads[t]; continue; }
    const rep = await api<{ messages: SlackMsg[] }>(s.secret, "conversations.replies", { channel: b.externalId, ts: t, oldest: seen, limit: 15 });
    for (const m of rep.messages) if (m.ts > seen && m.ts !== t) { fresh.push(m); threads[t] = m.ts > threads[t] ? m.ts : threads[t]; }
  }
  let lastTs = oldest;
  const approvers = String(s.config.approvers ?? "").split(/[,\s]+/).filter(Boolean);
  let n = 0;
  for (const m of fresh.sort((a, z) => (a.ts < z.ts ? -1 : 1))) {
    if (!m.thread_ts || m.thread_ts === m.ts) lastTs = m.ts > lastTs ? m.ts : lastTs;
    if (!isHuman(m) || !m.text) continue;
    n++;
    const root = m.thread_ts ?? m.ts;
    threads[root] = m.ts > (threads[root] ?? "0") ? m.ts : threads[root];
    const cmd = m.text.trim().match(/^(approve|reject)\s+([a-z0-9]{6,})/i);
    if (cmd) {
      let reply = "";
      if (!approvers.includes(m.user!) || !b.boundUserId) reply = "Only the approvers set in Northstar can approve here. You can also approve in the dashboard.";
      else {
        try {
          const ctx = await requireMember(b.boundUserId, workspaceId, "admin");
          const d = await db.draft.findFirst({ where: { workspaceId, status: "pending", id: { endsWith: cmd[2].toLowerCase() } } });
          if (!d) reply = "I can't find a pending draft with that code.";
          else if (cmd[1].toLowerCase() === "approve") reply = (await approveDraft(ctx, d.id)).note;
          else { await rejectDraft(ctx, d.id); reply = "Rejected. Nothing was posted."; }
        } catch (e) { reply = e instanceof Error ? e.message : "Couldn't do that"; }
      }
      await api(s.secret, "chat.postMessage", { channel: b.externalId, text: reply, thread_ts: root });
      continue;
    }
    const conv = await upsertConversation(workspaceId, "slack", `${b.externalId}:${root}`, "Slack thread");
    await addIncoming(workspaceId, conv.id, m.text.slice(0, 4000), b.boundUserId, { slackTs: m.ts, slackUser: m.user });
  }
  await db.channelBinding.update({ where: { id: b.id }, data: { cursor: JSON.stringify({ lastTs, threads }) } });
  return n;
}
