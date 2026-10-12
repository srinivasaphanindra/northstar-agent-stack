// View models for /admin/clients. Admin-only: every entry point takes the user and checks isPlatformAdmin.
import { db } from "../db";
import { AppError } from "../errors";
import { loadClientsConfig, type ClientDef } from "./config";
import { capsFor, usage, weekOf } from "./caps";
import { lastDays, perDay, todayCounts, totals, weekPosts, type Row } from "./aggregate";
import { istDay } from "./parsers";

export function assertPlatformAdmin(user: { isPlatformAdmin?: boolean } | null | undefined) {
  if (!user?.isPlatformAdmin) throw new AppError("forbidden", "Platform admins only.");
}

const PLATFORMS = ["x", "linkedin", "facebook"] as const;

async function rowsFor(clientKey: string, since: string): Promise<Row[]> {
  return db.clientActivity.findMany({ where: { clientKey, day: { gte: since } }, select: { platform: true, kind: true, day: true, count: true, impressions: true } });
}
async function latestMetric(clientKey: string, platform: string, metric: string, day?: string) {
  return db.clientMetric.findFirst({ where: { clientKey, platform, metric, ...(day ? { day } : {}) }, orderBy: [{ day: "desc" }, { updatedAt: "desc" }] });
}

export function todayCaps(cfg: ReturnType<typeof loadClientsConfig>, c: ClientDef, rows: Row[], today: string, helper: Record<string, number> = {}) {
  const plan = cfg.plans[c.plan];
  const [mon, sun] = weekOf(today);
  return PLATFORMS.map((p) => {
    const cap = capsFor(plan?.caps ?? {}, p, c.start_date, today, plan?.warmup_weeks ?? 4);
    const t = todayCounts(rows, today, p);
    // the client's own state helper counts approvals it enforces; take whichever is higher so nothing is under-reported
    const posts = Math.max(t.posts, helper[`${p}:posts`] ?? 0);
    const replies = Math.max(t.replies, helper[`${p}:replies`] ?? 0);
    const comments = Math.max(t.comments, helper[`${p}:comments`] ?? 0);
    return {
      platform: p,
      posts: cap.postsPerWeek !== undefined ? { ...usage(weekPosts(rows, mon, sun, p), cap.postsPerWeek), label: `${weekPosts(rows, mon, sun, p)}/${cap.postsPerWeek} this week` } : usage(posts, cap.posts),
      replies: cap.replies !== undefined ? usage(replies, cap.replies) : null,
      comments: cap.comments !== undefined ? usage(comments, cap.comments) : null,
    };
  });
}

async function helperToday(clientKey: string, today: string) {
  const ms = await db.clientMetric.findMany({ where: { clientKey, day: today, source: "state_helper", metric: { in: ["helper_posts", "helper_replies", "helper_comments"] } } });
  return Object.fromEntries(ms.map((m) => [`${m.platform}:${m.metric.replace("helper_", "")}`, m.value]));
}

export async function clientsOverview(user: { isPlatformAdmin?: boolean }, now = new Date()) {
  assertPlatformAdmin(user);
  const cfg = loadClientsConfig();
  const today = istDay(now);
  const lastRun = await db.collectorRun.findFirst({ orderBy: { startedAt: "desc" } });
  const clients = await Promise.all(cfg.clients.map(async (c) => {
    const rows = await rowsFor(c.key, lastDays(today, 7)[0]);
    const profiles = await db.clientProfile.findMany({ where: { clientKey: c.key }, orderBy: { platform: "asc" } });
    return { key: c.key, name: c.name, plan: c.plan, start: c.start_date, profiles, caps: todayCaps(cfg, c, rows, today, await helperToday(c.key, today)) };
  }));
  return { today, lastRun, clients };
}

export async function clientDetail(user: { isPlatformAdmin?: boolean }, key: string, now = new Date()) {
  assertPlatformAdmin(user);
  const cfg = loadClientsConfig();
  const c = cfg.clients.find((x) => x.key === key);
  if (!c) throw new AppError("not_found", "No such client.");
  const today = istDay(now);
  const d7 = lastDays(today, 7);
  const d30 = lastDays(today, 30);
  const rows = await rowsFor(c.key, d30[0]);
  const plan = cfg.plans[c.plan];
  const spend = (await latestMetric(c.key, "all", "ai_spend_usd"))?.value ?? null;
  const budget = (await latestMetric(c.key, "all", "ai_budget_usd"))?.value ?? plan?.ai_budget_usd_month ?? 35;
  const imps = await db.clientMetric.findMany({ where: { clientKey: c.key, metric: "impressions", day: { gte: d30[0] } } });
  const sumImp = (days: string[]) => { const s = new Set(days); const v = imps.filter((m) => s.has(m.day)); return v.length ? v.reduce((a, m) => a + m.value, 0) : null; };
  const followers = await db.clientMetric.findMany({ where: { clientKey: c.key, metric: "followers" }, orderBy: { day: "asc" } });
  const recent = await db.clientActivity.findMany({ where: { clientKey: c.key, kind: { not: "note" } }, orderBy: { occurredAt: "desc" }, take: 25 });
  const t7 = totals(rows, d7), t30 = totals(rows, d30);
  return {
    client: { key: c.key, name: c.name, plan: c.plan, start: c.start_date },
    today,
    profiles: await db.clientProfile.findMany({ where: { clientKey: c.key }, orderBy: { platform: "asc" } }),
    caps: todayCaps(cfg, c, rows, today, await helperToday(c.key, today)),
    d7, d30,
    series7: perDay(rows, d7), series30: perDay(rows, d30),
    t7: { ...t7, impressions: t7.impressions ?? sumImp(d7) }, t30: { ...t30, impressions: t30.impressions ?? sumImp(d30) },
    spend: { spent: spend, budget, pct: spend === null ? null : Math.round((spend / budget) * 100) },
    followers: followers.map((f) => ({ day: f.day, platform: f.platform, value: f.value })),
    recent: recent.map((r) => ({ at: r.occurredAt, platform: r.platform, kind: r.kind, url: r.url, detail: r.detail, count: r.count })),
  };
}
