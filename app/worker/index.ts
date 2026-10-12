// Northstar worker: runs the DB job queue (agent replies, draft notifications) and the channel pollers
// (Telegram long polling, Slack polling). Start: `npm run worker`. Safe to run several (jobs are claimed atomically),
// but run ONE for channel polling (WORKER_CHANNELS=0 on extra workers) so updates aren't fetched twice.
import { db } from "../src/core/db";
import { config } from "../src/core/config";
import { claimNext, complete, fail, WORKER_ID } from "../src/core/jobs";
import { runAgentReply } from "../src/core/agent";
import { deliverReply, notifyDraft } from "../src/core/channels";
import { pollTelegramOnce } from "../src/core/channels/telegram";
import { pollSlackOnce } from "../src/core/channels/slack";
import { safeJson } from "../src/core/integrations";

let stopping = false;
const log = (...a: unknown[]) => console.log(new Date().toISOString(), "[worker]", ...a);

export async function processJob(job: { id: string; type: string; payload: string; workspaceId: string | null; attempts: number; maxAttempts: number }) {
  const p = safeJson<Record<string, string>>(job.payload, {});
  try {
    if (job.type === "agent.reply") {
      const reply = await runAgentReply(job.workspaceId!, p.conversationId, p.messageId);
      await deliverReply(job.workspaceId!, p.conversationId, reply.content).catch((e) => log("deliver failed", e.message));
    } else if (job.type === "channel.notify_draft") {
      await notifyDraft(job.workspaceId!, p.draftId);
    } else throw new Error(`unknown job type ${job.type}`);
    await complete(job.id);
  } catch (e) {
    log("job failed", job.type, job.id, e instanceof Error ? e.message : e);
    await fail(job.id, e, job.attempts, job.maxAttempts);
    if (job.type === "agent.reply" && job.attempts >= job.maxAttempts && job.workspaceId) {
      await db.message.create({ data: { workspaceId: job.workspaceId, conversationId: p.conversationId, role: "assistant", content: "Sorry, I couldn't answer that just now (the AI service didn't respond). Please try again in a minute.", meta: JSON.stringify({ error: true }) } }).catch(() => {});
    }
  }
}

async function jobLoop() {
  while (!stopping) {
    const job = await claimNext().catch((e) => { log("claim error", e.message); return null; });
    if (job) await processJob(job);
    else await new Promise((r) => setTimeout(r, config.workerPollMs));
  }
}

// ---- channel pollers ----
const tgLoops = new Map<string, boolean>();
const slackNext = new Map<string, number>();

async function telegramLoop(workspaceId: string) {
  tgLoops.set(workspaceId, true);
  log("telegram poller on", workspaceId);
  let backoff = 1000;
  while (!stopping && tgLoops.get(workspaceId)) {
    try { await pollTelegramOnce(workspaceId, 25); backoff = 1000; }
    catch (e) { log("telegram", workspaceId, e instanceof Error ? e.message : e); await new Promise((r) => setTimeout(r, backoff)); backoff = Math.min(backoff * 2, 60_000); }
  }
  tgLoops.delete(workspaceId);
}

async function channelScan() {
  const binds = await db.channelBinding.findMany({ where: { status: { in: ["active", "pending"] }, channel: { in: ["telegram", "slack"] } } });
  const tgWanted = new Set<string>();
  for (const b of binds) {
    if (b.channel === "telegram" && config.telegramEnabled) {
      tgWanted.add(b.workspaceId);
      if (!tgLoops.has(b.workspaceId)) void telegramLoop(b.workspaceId);
    }
    if (b.channel === "slack" && b.status === "active" && (slackNext.get(b.workspaceId) ?? 0) <= Date.now()) {
      slackNext.set(b.workspaceId, Date.now() + config.slackPollSeconds * 1000);
      pollSlackOnce(b.workspaceId).catch((e) => log("slack", b.workspaceId, e instanceof Error ? e.message : e));
    }
  }
  for (const id of tgLoops.keys()) if (!tgWanted.has(id)) tgLoops.set(id, false);
}

async function main() {
  log("start", WORKER_ID, "llm mode", config.llmMode, "publish dry run", config.publishDryRun);
  process.on("SIGTERM", () => { stopping = true; });
  process.on("SIGINT", () => { stopping = true; });
  const scan = async () => { while (!stopping) { await channelScan().catch((e) => log("scan", e.message)); await new Promise((r) => setTimeout(r, 10_000)); } };
  await Promise.all([jobLoop(), process.env.WORKER_CHANNELS === "0" || !config.channelsEnabled ? Promise.resolve() : scan()]);
  await db.$disconnect();
  log("stopped");
}

if (process.argv[1]?.endsWith("index.ts") && !process.env.VITEST) void main();
