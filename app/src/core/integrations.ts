// Per-tenant credentials (encrypted) + "test connection" where the provider has an API.
import { db } from "./db";
import { config } from "./config";
import { AppError } from "./errors";
import { audit } from "./audit";
import { can, type Ctx } from "./tenancy";
import { encryptForWorkspace, decryptForWorkspace, last4 } from "./crypto";

export const PROVIDERS = {
  openrouter: { label: "OpenRouter (your own AI key)", secretLabel: "API key", test: true },
  buffer: { label: "Buffer", secretLabel: "Personal API key (or use Connect with Buffer)", test: true },
  hootsuite: { label: "Hootsuite", secretLabel: "Access token", test: true },
  gohighlevel: { label: "GoHighLevel", secretLabel: "Private integration token", test: true },
  generic: { label: "Other tool (API key / webhook)", secretLabel: "API key", test: true },
  telegram: { label: "Telegram bot", secretLabel: "Bot token from @BotFather", test: true },
  slack: { label: "Slack bot", secretLabel: "Bot token (xoxb-...)", test: true },
} as const;
export type Provider = keyof typeof PROVIDERS;
export const isProvider = (p: string): p is Provider => p in PROVIDERS;

export function safeJson<T = Record<string, unknown>>(s: string | null | undefined, def: T): T {
  try { return s ? (JSON.parse(s) as T) : def; } catch { return def; }
}

async function wsKey(workspaceId: string) {
  const ws = await db.workspace.findUniqueOrThrow({ where: { id: workspaceId }, select: { dataKeyEnc: true } });
  return ws.dataKeyEnc;
}

/** Public view: never includes the secret, only the last 4 characters. */
export async function listIntegrations(ctx: Ctx) {
  const rows = await db.integration.findMany({ where: { workspaceId: ctx.workspaceId } });
  return rows.map((r) => ({
    id: r.id, provider: r.provider, last4: r.last4, hasSecret: !!r.secretEnc, config: safeJson(r.config, {}),
    status: r.status, lastTestAt: r.lastTestAt, lastTestNote: r.lastTestNote, updatedAt: r.updatedAt,
  }));
}

export async function saveIntegration(ctx: Ctx, provider: string, secret: string | null, cfg: Record<string, string> = {}) {
  if (!can(ctx, "admin")) throw new AppError("forbidden", "Only owners and admins can change integrations");
  if (!isProvider(provider)) throw new AppError("invalid", "Unknown integration");
  const clean: Record<string, string> = {};
  for (const [k, v] of Object.entries(cfg)) if (typeof v === "string") clean[k.slice(0, 40)] = v.trim().slice(0, 500);
  if (provider === "generic" && clean.baseUrl) assertPublicHttps(clean.baseUrl);
  if (provider === "generic" && clean.webhookUrl) assertPublicHttps(clean.webhookUrl);
  const existing = await db.integration.findUnique({ where: { workspaceId_provider: { workspaceId: ctx.workspaceId, provider } } });
  const data: { config: string; secretEnc?: string; last4?: string; status: string } = {
    config: JSON.stringify({ ...safeJson(existing?.config, {}), ...clean }),
    status: "saved",
  };
  const s = (secret ?? "").trim();
  if (s) {
    if (s.length > 4000) throw new AppError("invalid", "That key is too long");
    data.secretEnc = encryptForWorkspace(ctx.workspaceId, await wsKey(ctx.workspaceId), `integration:${provider}`, s);
    data.last4 = last4(s);
    if (provider === "buffer") {
      // A pasted personal key replaces any OAuth tokens.
      const c = safeJson<Record<string, unknown>>(data.config, {});
      c.authMode = "key"; delete c.refreshEnc; delete c.expiresAt;
      data.config = JSON.stringify(c);
    }
  }
  const row = await db.integration.upsert({
    where: { workspaceId_provider: { workspaceId: ctx.workspaceId, provider } },
    create: { workspaceId: ctx.workspaceId, provider, secretEnc: data.secretEnc ?? "", last4: data.last4 ?? "", config: data.config, status: "saved" },
    update: data,
  });
  await audit(ctx.workspaceId, { type: "user", userId: ctx.userId }, s ? "integration.secret_saved" : "integration.config_saved", provider, { last4: row.last4 });
  return row;
}

export async function deleteIntegration(ctx: Ctx, provider: string) {
  if (!can(ctx, "admin")) throw new AppError("forbidden", "Only owners and admins can change integrations");
  await db.integration.deleteMany({ where: { workspaceId: ctx.workspaceId, provider } });
  if (provider === "openrouter") await db.workspace.update({ where: { id: ctx.workspaceId }, data: { aiKeyMode: "platform" } });
  await audit(ctx.workspaceId, { type: "user", userId: ctx.userId }, "integration.removed", provider);
}

/** Server-side only (worker / connectors). Takes a workspace id, never a user-supplied row id. */
export async function getSecret(workspaceId: string, provider: Provider): Promise<{ secret: string; config: Record<string, string> } | null> {
  const row = await db.integration.findUnique({ where: { workspaceId_provider: { workspaceId, provider } } });
  if (!row || !row.secretEnc || row.status === "disabled") return null;
  const secret = decryptForWorkspace(workspaceId, await wsKey(workspaceId), `integration:${provider}`, row.secretEnc);
  return { secret, config: safeJson(row.config, {}) };
}

export function assertPublicHttps(url: string) {
  let u: URL;
  try { u = new URL(url); } catch { throw new AppError("invalid", "Not a valid URL"); }
  if (u.protocol !== "https:") throw new AppError("invalid", "Only https:// URLs are allowed");
  const h = u.hostname.toLowerCase();
  if (h === "localhost" || h.endsWith(".local") || h.endsWith(".internal") || /^(127\.|10\.|192\.168\.|169\.254\.|0\.|172\.(1[6-9]|2\d|3[01])\.)/.test(h) || h === "[::1]" || /^\[?(fc|fd|fe80)/.test(h)) {
    throw new AppError("invalid", "Private or local addresses are not allowed");
  }
  return u;
}

type TestResult = { ok: boolean; note: string };

async function http(url: string, init: RequestInit, timeoutMs = 10000) {
  const ctl = AbortSignal.timeout(timeoutMs);
  return fetch(url, { ...init, signal: ctl, redirect: "manual" });
}

export async function testProvider(provider: Provider, secret: string, cfg: Record<string, string>): Promise<TestResult> {
  try {
    switch (provider) {
      case "openrouter": {
        const r = await http(`${config.openrouterBase}/key`, { headers: { Authorization: `Bearer ${secret}` } });
        if (!r.ok) return { ok: false, note: `OpenRouter said ${r.status}` };
        const j = (await r.json()) as { data?: { limit?: number | null; usage?: number } };
        return { ok: true, note: `Key works. Used so far: $${(j.data?.usage ?? 0).toFixed(2)}${j.data?.limit ? ` of $${j.data.limit}` : ""}` };
      }
      case "buffer": {
        const r = await http(config.bufferApiUrl, { method: "POST", headers: { Authorization: `Bearer ${secret}`, "Content-Type": "application/json" }, body: JSON.stringify({ query: "query { account { id email } }" }) });
        const j = (await r.json().catch(() => ({}))) as { data?: { account?: { email?: string } }; errors?: { message: string }[] };
        if (!r.ok || j.errors?.length || !j.data?.account) return { ok: false, note: j.errors?.[0]?.message ?? `Buffer said ${r.status}` };
        return { ok: true, note: `Connected to Buffer account ${j.data.account.email ?? ""}`.trim() };
      }
      case "hootsuite": {
        const r = await http("https://platform.hootsuite.com/v1/me", { headers: { Authorization: `Bearer ${secret}` } });
        return r.ok ? { ok: true, note: "Hootsuite token works" } : { ok: false, note: `Hootsuite said ${r.status}` };
      }
      case "gohighlevel": {
        if (!cfg.locationId) return { ok: false, note: "Add your GoHighLevel location id to test the token" };
        const r = await http(`https://services.leadconnectorhq.com/locations/${encodeURIComponent(cfg.locationId)}`, { headers: { Authorization: `Bearer ${secret}`, Version: "2021-07-28", Accept: "application/json" } });
        return r.ok ? { ok: true, note: "GoHighLevel token works for this location" } : { ok: false, note: `GoHighLevel said ${r.status}` };
      }
      case "generic": {
        if (!cfg.baseUrl) return { ok: true, note: "Saved. Add a base URL to test it." };
        const u = assertPublicHttps(new URL(cfg.testPath || "/", cfg.baseUrl).toString());
        const header = cfg.headerName || "Authorization";
        const value = header.toLowerCase() === "authorization" ? `Bearer ${secret}` : secret;
        const r = await http(u.toString(), { headers: { [header]: value } });
        return r.status < 400 ? { ok: true, note: `${u.hostname} answered ${r.status}` } : { ok: false, note: `${u.hostname} answered ${r.status}` };
      }
      case "telegram": {
        const r = await http(`${config.telegramApi}/bot${secret}/getMe`, {});
        const j = (await r.json().catch(() => ({}))) as { ok?: boolean; result?: { username?: string }; description?: string };
        return j.ok ? { ok: true, note: `Bot @${j.result?.username} is ready` } : { ok: false, note: j.description ?? `Telegram said ${r.status}` };
      }
      case "slack": {
        const r = await http(`${config.slackApi}/auth.test`, { method: "POST", headers: { Authorization: `Bearer ${secret}` } });
        const j = (await r.json().catch(() => ({}))) as { ok?: boolean; team?: string; user?: string; error?: string };
        return j.ok ? { ok: true, note: `Signed in to ${j.team} as ${j.user}` } : { ok: false, note: `Slack: ${j.error ?? r.status}` };
      }
    }
  } catch (e) {
    return { ok: false, note: e instanceof Error ? `Could not reach the service (${e.message.slice(0, 80)})` : "Could not reach the service" };
  }
}

export async function testIntegration(ctx: Ctx, provider: string): Promise<TestResult> {
  if (!can(ctx, "admin")) throw new AppError("forbidden", "Only owners and admins can test integrations");
  if (!isProvider(provider)) throw new AppError("invalid", "Unknown integration");
  const s = await getSecret(ctx.workspaceId, provider);
  if (!s) return { ok: false, note: "Nothing saved yet" };
  const res = await testProvider(provider, s.secret, s.config);
  const extra: Record<string, string> = {};
  const bot = provider === "telegram" && res.ok ? res.note.match(/@(\w+)/) : null;
  if (bot) extra.username = bot[1];
  await db.integration.update({
    where: { workspaceId_provider: { workspaceId: ctx.workspaceId, provider } },
    data: { status: res.ok ? "ok" : "error", lastTestAt: new Date(), lastTestNote: res.note.slice(0, 300), ...(bot ? { config: JSON.stringify({ ...s.config, ...extra }) } : {}) },
  });
  await audit(ctx.workspaceId, { type: "user", userId: ctx.userId }, "integration.test", provider, { ok: res.ok });
  return res;
}

export async function setAiKeyMode(ctx: Ctx, mode: "platform" | "byok") {
  if (!can(ctx, "admin")) throw new AppError("forbidden", "Only owners and admins can change the AI key");
  if (mode === "byok") {
    const s = await getSecret(ctx.workspaceId, "openrouter");
    if (!s) throw new AppError("invalid", "Save your OpenRouter key first");
  }
  await db.workspace.update({ where: { id: ctx.workspaceId }, data: { aiKeyMode: mode } });
  await audit(ctx.workspaceId, { type: "user", userId: ctx.userId }, "ai.key_mode", mode);
}
