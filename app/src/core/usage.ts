// Usage + spend per workspace, with caps (warn / pause extras / hard stop) from the guardrails spend block.
import { db } from "./db";
import { config, planOf } from "./config";
import { rulesFor } from "./guardrails";

export function monthStart(now = new Date()) {
  const off = 330 * 60_000; // months are counted in IST
  const ist = new Date(now.getTime() + off);
  return new Date(Date.UTC(ist.getUTCFullYear(), ist.getUTCMonth(), 1) - off);
}


export function dayStart(now = new Date()) {
  const off = 330 * 60_000; // IST day boundary
  const ist = new Date(now.getTime() + off);
  return new Date(Date.UTC(ist.getUTCFullYear(), ist.getUTCMonth(), ist.getUTCDate()) - off);
}

/** Platform-key LLM spend across all workspaces since IST midnight. */
export async function platformDaySpend(now = new Date()) {
  const since = dayStart(now);
  const agg = await db.usageEvent.aggregate({
    where: { kind: "llm", keyMode: "platform", createdAt: { gte: since } },
    _sum: { costUsd: true },
    _count: true,
  });
  return { since, used: agg._sum.costUsd ?? 0, calls: agg._count };
}

export type PlatformSpendLevel = "ok" | "stop";

/** Hard stop when platform-wide daily OpenRouter spend hits PLATFORM_DAILY_AI_CAP_USD. */
export async function platformSpendStatus(now = new Date()) {
  const cap = config.platformDailyAiCapUsd;
  const { since, used, calls } = await platformDaySpend(now);
  const pct = cap > 0 ? (used / cap) * 100 : 0;
  const level: PlatformSpendLevel = cap > 0 && used >= cap ? "stop" : "ok";
  return { cap, used, pct, level, calls, since };
}

export async function recordLlm(workspaceId: string, u: { model: string; keyMode: string; inputTokens: number; outputTokens: number; costUsd?: number | null }) {
  const cost = u.costUsd ?? (u.inputTokens * config.estInPerM + u.outputTokens * config.estOutPerM) / 1_000_000;
  await db.usageEvent.create({ data: { workspaceId, kind: "llm", model: u.model, keyMode: u.keyMode, inputTokens: u.inputTokens, outputTokens: u.outputTokens, costUsd: cost } });
  return cost;
}

export async function recordEvent(workspaceId: string, kind: "message" | "publish") {
  await db.usageEvent.create({ data: { workspaceId, kind } });
}

export async function monthUsage(workspaceId: string) {
  const since = monthStart();
  const rows = await db.usageEvent.groupBy({ by: ["kind", "keyMode"], where: { workspaceId, createdAt: { gte: since } }, _sum: { costUsd: true, inputTokens: true, outputTokens: true }, _count: true });
  let platformCost = 0, byokCost = 0, llmCalls = 0, messages = 0, publishes = 0, tokens = 0;
  for (const r of rows) {
    if (r.kind === "llm") {
      llmCalls += r._count;
      tokens += (r._sum.inputTokens ?? 0) + (r._sum.outputTokens ?? 0);
      if (r.keyMode === "byok") byokCost += r._sum.costUsd ?? 0; else platformCost += r._sum.costUsd ?? 0;
    } else if (r.kind === "message") messages += r._count;
    else if (r.kind === "publish") publishes += r._count;
  }
  const drafts = await db.draft.count({ where: { workspaceId, createdAt: { gte: since } } });
  return { since, platformCost, byokCost, totalCost: platformCost + byokCost, llmCalls, tokens, messages, publishes, drafts };
}

export type SpendLevel = "ok" | "warn" | "pause" | "stop";

/** Cap = min(guardrails monthly budget, plan cap when on the platform key). BYOK spend counts against the guardrail budget only. */
export async function spendStatus(workspaceId: string) {
  const ws = await db.workspace.findUniqueOrThrow({ where: { id: workspaceId } });
  const rules = await rulesFor(workspaceId);
  const u = await monthUsage(workspaceId);
  const budget = rules.spend.monthly_ai_budget_usd;
  const cap = ws.aiKeyMode === "platform" ? Math.min(budget, planOf(ws.plan).aiCapUsd) : budget;
  const pct = cap > 0 ? (u.totalCost / cap) * 100 : 100;
  let level: SpendLevel = "ok";
  if (pct >= rules.spend.hard_stop_pct) level = "stop";
  else if (pct >= rules.spend.pause_nonessential_pct) level = "pause";
  else if (pct >= rules.spend.warn_pct) level = "warn";
  const draftCap = planOf(ws.plan).draftsPerMonth;
  return { cap, pct, level, used: u.totalCost, usage: u, draftCap, draftsLeft: Math.max(0, draftCap - u.drafts) };
}
