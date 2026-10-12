// Guardrails: the yaml template (defaults -> tiers[plan] -> per-workspace overrides from the DB, deep merge)
// plus the checks that are enforced in code before anything is published.
import fs from "node:fs";
import YAML from "yaml";
import { db } from "./db";
import { config } from "./config";
import { AppError } from "./errors";
import { audit } from "./audit";
import { can, type Ctx } from "./tenancy";
import { safeJson } from "./integrations";

export type Rules = {
  platforms: Record<string, { engage_per_day: number; posts_per_day: number }>;
  engagement: { allowed_actions: string[]; never: string[]; min_gap_minutes: number };
  content: { regex_check: boolean; llm_check: boolean; max_hashtags: number; allow_politics: boolean; allow_religion: boolean; numbers_must_be_in_notes: boolean };
  approvals: { auto_post_routine: boolean; always_need_approval: string[] };
  spend: { monthly_ai_budget_usd: number; warn_pct: number; pause_nonessential_pct: number; hard_stop_pct: number };
  access: { browser_domains: string[]; client_website: string };
  scope: { in_scope: string };
};

// Used when the yaml is missing or broken (same values as the approved template).
export const BUILTIN: Rules = {
  platforms: { x: { engage_per_day: 5, posts_per_day: 3 }, linkedin: { engage_per_day: 3, posts_per_day: 2 }, facebook: { engage_per_day: 5, posts_per_day: 3 } },
  engagement: { allowed_actions: ["reply", "like", "repost"], never: ["follow", "unfollow", "dm", "mass_like", "identical_comment", "hashtag_spam"], min_gap_minutes: 7 },
  content: { regex_check: true, llm_check: true, max_hashtags: 2, allow_politics: false, allow_religion: false, numbers_must_be_in_notes: true },
  approvals: { auto_post_routine: false, always_need_approval: ["reply", "comment", "dm", "sensitive"] },
  spend: { monthly_ai_budget_usd: 10, warn_pct: 80, pause_nonessential_pct: 100, hard_stop_pct: 150 },
  access: { browser_domains: ["x.com", "twitter.com", "linkedin.com", "facebook.com", "buffer.com"], client_website: "" },
  scope: { in_scope: "social media and online presence" },
};

// App plans -> yaml tier names
const TIER_FOR_PLAN: Record<string, string> = { trial: "trial", solo: "starter", pro: "growth" };

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => !!v && typeof v === "object" && !Array.isArray(v);
export function deepMerge<T>(base: T, over: unknown): T {
  if (!isObj(over)) return base;
  const out: Obj = { ...(base as Obj) };
  for (const [k, v] of Object.entries(over)) out[k] = isObj(v) && isObj(out[k]) ? deepMerge(out[k], v) : v;
  return out as T;
}

let cache: { mtime: number; path: string; doc: Obj } | null = null;
export function loadTemplate(): { doc: Obj; source: string } {
  const p = config.guardrailsPath;
  try {
    const st = fs.statSync(p);
    if (!cache || cache.mtime !== st.mtimeMs || cache.path !== p) {
      const doc = YAML.parse(fs.readFileSync(p, "utf8")) as Obj;
      if (!isObj(doc) || !isObj(doc.defaults)) throw new Error("no defaults block");
      cache = { mtime: st.mtimeMs, path: p, doc };
    }
    return { doc: cache.doc, source: p };
  } catch {
    return { doc: { defaults: BUILTIN, tiers: {} }, source: "built-in defaults (template unreadable)" };
  }
}

export function resolveRules(plan: string, overrides: unknown): Rules {
  const { doc } = loadTemplate();
  let r = deepMerge(BUILTIN, doc.defaults);
  const tier = isObj(doc.tiers) ? (doc.tiers as Obj)[TIER_FOR_PLAN[plan] ?? plan] : undefined;
  r = deepMerge(r, tier);
  r = deepMerge(r, overrides);
  // Hard floor: the app has no auto-post path, so approvals are always required in phase 1.
  r.approvals.auto_post_routine = false;
  return r;
}

export async function rulesFor(workspaceId: string): Promise<Rules> {
  const ws = await db.workspace.findUniqueOrThrow({ where: { id: workspaceId }, select: { plan: true } });
  const g = await db.guardrailSettings.findUnique({ where: { workspaceId } });
  return resolveRules(ws.plan, safeJson(g?.overrides, {}));
}

export async function getOverrides(ctx: Ctx) {
  const g = await db.guardrailSettings.findUnique({ where: { workspaceId: ctx.workspaceId } });
  return safeJson<Obj>(g?.overrides, {});
}

const num = (v: unknown, min: number, max: number) => {
  const n = Number(v);
  if (!Number.isFinite(n) || n < min || n > max) throw new AppError("invalid", `Value must be between ${min} and ${max}`);
  return n;
};

/** Saves the editable subset. Clients can tighten or loosen within safe bounds; "never" lists are not editable. */
export async function saveOverrides(ctx: Ctx, form: Record<string, string>) {
  if (!can(ctx, "admin")) throw new AppError("forbidden", "Only owners and admins can change guardrails");
  const o: Obj = { platforms: {}, content: {}, spend: {}, access: {} };
  for (const p of ["x", "linkedin", "facebook"]) {
    if (form[`${p}_posts`] !== undefined) (o.platforms as Obj)[p] = { posts_per_day: num(form[`${p}_posts`], 0, 10), engage_per_day: num(form[`${p}_engage`] ?? 0, 0, 10) };
  }
  if (form.max_hashtags !== undefined) (o.content as Obj).max_hashtags = num(form.max_hashtags, 0, 5);
  (o.content as Obj).allow_politics = form.allow_politics === "on";
  (o.content as Obj).allow_religion = form.allow_religion === "on";
  if (form.monthly_ai_budget_usd !== undefined) (o.spend as Obj).monthly_ai_budget_usd = num(form.monthly_ai_budget_usd, 0, 500);
  if (form.client_website !== undefined) (o.access as Obj).client_website = String(form.client_website).trim().replace(/^https?:\/\//, "").replace(/\/.*$/, "").slice(0, 100);
  await db.guardrailSettings.upsert({ where: { workspaceId: ctx.workspaceId }, create: { workspaceId: ctx.workspaceId, overrides: JSON.stringify(o) }, update: { overrides: JSON.stringify(o) } });
  await audit(ctx.workspaceId, { type: "user", userId: ctx.userId }, "guardrails.update", "", o);
}

// ---- content checks (free regex; runs at propose time and again right before publishing) ----
const FAKE_REVIEW = /(5[- ]?star|five[- ]star)\s+(review|rating)s?|\b(write|post|generate)\b.{0,30}\breviews?\b|best\s+(clinic|salon|doctor|service)\s+in\s+\w+\s*(guaranteed|100%)|\bguaranteed\s+(results|cure|returns)\b|\b100%\s+(cure|guaranteed|safe)\b/i;
const MED_FIN = /\b(cures?|miracle|risk[- ]free returns|double your money)\b/i;
const POLITICS = /\b(election|bjp|congress party|modi|rahul gandhi|vote for|political party|brs|tdp|ysrcp)\b/i;
const RELIGION = /\b(hindu|muslim|christian|temple|mosque|church|religion|religious)\b/i;

export function checkContent(text: string, rules: Rules): { ok: true } | { ok: false; reason: string; alternative: string } {
  if (!rules.content.regex_check) return { ok: true };
  const tags = text.match(/(^|\s)#[\p{L}\p{N}_]+/gu)?.length ?? 0;
  if (tags > rules.content.max_hashtags)
    return { ok: false, reason: `It has ${tags} hashtags; your limit is ${rules.content.max_hashtags}. Hashtag stacks look like spam and reach fewer people.`, alternative: `Keep the ${rules.content.max_hashtags} most relevant hashtags.` };
  if (FAKE_REVIEW.test(text))
    return { ok: false, reason: "It reads like a fake review or a guaranteed-results claim, which platforms remove and which can break consumer law.", alternative: "Share a real customer quote (with their OK) or explain what you actually do." };
  if (MED_FIN.test(text))
    return { ok: false, reason: "It makes a medical or money promise we can't back up.", alternative: "Describe the service and invite people to ask questions instead." };
  if (!rules.content.allow_politics && POLITICS.test(text))
    return { ok: false, reason: "Political topics are switched off for this workspace.", alternative: "Keep the post about your business, or switch politics on in Guardrails." };
  if (!rules.content.allow_religion && RELIGION.test(text))
    return { ok: false, reason: "Religious topics are switched off for this workspace.", alternative: "Keep the post neutral, or switch religion on in Guardrails." };
  return { ok: true };
}

/** Start of the current day in IST (UTC+5:30), as a UTC Date. */
export function istDayStart(now = new Date()): Date {
  const off = 330 * 60_000;
  const ist = new Date(now.getTime() + off);
  ist.setUTCHours(0, 0, 0, 0);
  return new Date(ist.getTime() - off);
}

/** Platforms that are still under today's post cap. */
export async function postCapCheck(workspaceId: string, platforms: string[], rules: Rules) {
  const since = istDayStart();
  const today = await db.draft.findMany({ where: { workspaceId, status: { in: ["published", "approved"] }, decidedAt: { gte: since } }, select: { platforms: true } });
  const counts: Record<string, number> = {};
  for (const d of today) for (const p of safeJson<string[]>(d.platforms, [])) counts[p] = (counts[p] ?? 0) + 1;
  const allowed: string[] = [];
  const over: string[] = [];
  for (const p of platforms) {
    const cap = rules.platforms[p]?.posts_per_day ?? 3;
    ((counts[p] ?? 0) < cap ? allowed : over).push(p);
  }
  return { allowed, over, counts };
}

export function rulesSummary(r: Rules) {
  const caps = Object.entries(r.platforms).map(([p, v]) => `${p} ${v.posts_per_day} posts/day`).join(", ");
  return `Every public action needs a human approval. Daily caps: ${caps}. Max ${r.content.max_hashtags} hashtags. Politics ${r.content.allow_politics ? "allowed" : "off"}, religion ${r.content.allow_religion ? "allowed" : "off"}. Never: ${r.engagement.never.join(", ")}. Monthly AI budget $${r.spend.monthly_ai_budget_usd}. Stay on: ${r.scope.in_scope}.`;
}
