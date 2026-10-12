// Buffer OAuth 2.0 (Authorization Code + PKCE) per workspace, plus the channel picker.
// Off until BUFFER_CLIENT_ID is set (Northstar's registered Buffer app client). The personal API key path still works.
// Token store: access token = the integration secret; refresh token encrypted in config (Buffer rotates it on refresh).
import crypto from "node:crypto";
import { db } from "./db";
import { config } from "./config";
import { AppError } from "./errors";
import { audit } from "./audit";
import { can, type Ctx } from "./tenancy";
import { encryptForWorkspace, decryptForWorkspace, last4 } from "./crypto";
import { safeJson } from "./integrations";
import { listChannels, BUFFER_SERVICE, type BufferChannel } from "./buffer";

const b64url = (b: Buffer) => b.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
export function pkcePair() {
  const verifier = b64url(crypto.randomBytes(32));
  const challenge = b64url(crypto.createHash("sha256").update(verifier).digest());
  return { verifier, challenge };
}

type BufCfg = {
  authMode?: "oauth" | "key"; refreshEnc?: string; expiresAt?: number;
  oauthState?: string; oauthVerifierEnc?: string; oauthStartedAt?: number;
  channels?: BufferChannel[]; channelsAt?: number; picks?: Record<string, string>;
  [k: string]: unknown;
};
const STATE_TTL_MS = 15 * 60_000;

async function row(workspaceId: string) {
  const ws = await db.workspace.findUniqueOrThrow({ where: { id: workspaceId }, select: { dataKeyEnc: true } });
  const r = await db.integration.findUnique({ where: { workspaceId_provider: { workspaceId, provider: "buffer" } } });
  return { dk: ws.dataKeyEnc, r, cfg: safeJson<BufCfg>(r?.config, {}) };
}
async function writeCfg(workspaceId: string, cfg: BufCfg, extra: { secretEnc?: string; last4?: string; status?: string } = {}) {
  const data = { config: JSON.stringify(cfg), ...extra };
  await db.integration.upsert({
    where: { workspaceId_provider: { workspaceId, provider: "buffer" } },
    create: { workspaceId, provider: "buffer", secretEnc: extra.secretEnc ?? "", last4: extra.last4 ?? "", status: extra.status ?? "saved", config: data.config },
    update: data,
  });
}

/** Step 1: returns the Buffer authorize URL; keeps state + encrypted PKCE verifier on the workspace. */
export async function startBufferOAuth(ctx: Ctx): Promise<string> {
  if (!can(ctx, "admin")) throw new AppError("forbidden", "Only owners and admins can connect Buffer");
  if (!config.bufferOauthEnabled) throw new AppError("invalid", "Buffer one-click connect isn't set up on this server yet (BUFFER_CLIENT_ID). Use a personal API key for now.");
  const { dk, cfg } = await row(ctx.workspaceId);
  const { verifier, challenge } = pkcePair();
  const state = b64url(crypto.randomBytes(18));
  cfg.oauthState = state;
  cfg.oauthVerifierEnc = encryptForWorkspace(ctx.workspaceId, dk, "buffer:pkce", verifier);
  cfg.oauthStartedAt = Date.now();
  await writeCfg(ctx.workspaceId, cfg);
  await audit(ctx.workspaceId, { type: "user", userId: ctx.userId }, "buffer.oauth_start");
  const u = new URL(config.bufferAuthUrl);
  u.search = new URLSearchParams({
    response_type: "code", client_id: config.bufferClientId, redirect_uri: config.bufferRedirectUri, scope: config.bufferScopes,
    state, code_challenge: challenge, code_challenge_method: "S256",
  }).toString();
  return u.toString();
}

type TokenResp = { access_token?: string; refresh_token?: string; expires_in?: number; error?: string; error_description?: string };
async function tokenCall(params: Record<string, string>): Promise<TokenResp & { access_token: string }> {
  const body = new URLSearchParams({ client_id: config.bufferClientId, ...(config.bufferClientSecret ? { client_secret: config.bufferClientSecret } : {}), ...params });
  const r = await fetch(config.bufferTokenUrl, { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" }, body, signal: AbortSignal.timeout(15_000) });
  const j = (await r.json().catch(() => ({}))) as TokenResp;
  if (!r.ok || !j.access_token) throw new AppError("invalid", `Buffer sign-in failed: ${j.error_description ?? j.error ?? `HTTP ${r.status}`}`);
  return j as TokenResp & { access_token: string };
}

async function storeTokens(workspaceId: string, dk: string, cfg: BufCfg, t: TokenResp & { access_token: string }) {
  cfg.authMode = "oauth";
  if (t.refresh_token) cfg.refreshEnc = encryptForWorkspace(workspaceId, dk, "integration:buffer:refresh", t.refresh_token);
  cfg.expiresAt = Date.now() + (t.expires_in ?? 3600) * 1000;
  await writeCfg(workspaceId, cfg, { secretEnc: encryptForWorkspace(workspaceId, dk, "integration:buffer", t.access_token), last4: last4(t.access_token), status: "ok" });
}

/** Step 2 (callback): checks state, swaps the code (+ verifier) for tokens and stores them encrypted. */
export async function finishBufferOAuth(ctx: Ctx, state: string, code: string) {
  if (!can(ctx, "admin")) throw new AppError("forbidden", "Only owners and admins can connect Buffer");
  const { dk, cfg } = await row(ctx.workspaceId);
  const fresh = cfg.oauthStartedAt && Date.now() - cfg.oauthStartedAt < STATE_TTL_MS;
  const sameState = !!cfg.oauthState && !!state && cfg.oauthState.length === state.length && crypto.timingSafeEqual(Buffer.from(cfg.oauthState), Buffer.from(state));
  if (!sameState || !fresh || !cfg.oauthVerifierEnc) throw new AppError("invalid", "This Buffer sign-in link expired or doesn't match. Start again from Integrations.");
  const verifier = decryptForWorkspace(ctx.workspaceId, dk, "buffer:pkce", cfg.oauthVerifierEnc);
  delete cfg.oauthState; delete cfg.oauthVerifierEnc; delete cfg.oauthStartedAt;
  await writeCfg(ctx.workspaceId, cfg); // one-time: the state can't be replayed even if the exchange fails
  const t = await tokenCall({ grant_type: "authorization_code", code, redirect_uri: config.bufferRedirectUri, code_verifier: verifier });
  await storeTokens(ctx.workspaceId, dk, cfg, t);
  await audit(ctx.workspaceId, { type: "user", userId: ctx.userId }, "buffer.oauth_connected");
}

const refreshing = new Map<string, Promise<string>>();
/** Access token for publishing: personal key as is; OAuth token refreshed (single refresher per workspace) when close to expiry. */
export async function bufferToken(workspaceId: string): Promise<string | null> {
  const { dk, r, cfg } = await row(workspaceId);
  if (!r || !r.secretEnc || r.status === "disabled") return null;
  const access = decryptForWorkspace(workspaceId, dk, "integration:buffer", r.secretEnc);
  if (cfg.authMode !== "oauth" || !cfg.expiresAt || cfg.expiresAt - 60_000 > Date.now()) return access;
  if (!cfg.refreshEnc) return null;
  let p = refreshing.get(workspaceId);
  if (!p) {
    p = (async () => {
      try {
        const rt = decryptForWorkspace(workspaceId, dk, "integration:buffer:refresh", cfg.refreshEnc!);
        const t = await tokenCall({ grant_type: "refresh_token", refresh_token: rt });
        await storeTokens(workspaceId, dk, cfg, t);
        await audit(workspaceId, { type: "system" }, "buffer.token_refreshed");
        return t.access_token;
      } finally { refreshing.delete(workspaceId); }
    })();
    refreshing.set(workspaceId, p);
  }
  return p;
}

/** Reads the Buffer channels once and caches them (id, service, name) for the picker. */
export async function refreshBufferChannels(ctx: Ctx) {
  if (!can(ctx, "admin")) throw new AppError("forbidden", "Only owners and admins can change integrations");
  const token = await bufferToken(ctx.workspaceId);
  if (!token) throw new AppError("invalid", "Connect Buffer first");
  let chans: BufferChannel[];
  try { chans = await listChannels(token); } catch (e) { throw new AppError("invalid", `Couldn't read Buffer channels: ${e instanceof Error ? e.message : "error"}`); }
  const { cfg } = await row(ctx.workspaceId);
  cfg.channels = chans.map((c) => ({ id: c.id, service: c.service, name: c.name, isDisconnected: !!c.isDisconnected, isLocked: !!c.isLocked }));
  cfg.channelsAt = Date.now();
  await writeCfg(ctx.workspaceId, cfg);
  return cfg.channels;
}

/** Saves which Buffer channel each platform posts to ("" = first usable channel). Only ids from the cached list. */
export async function saveChannelPicks(ctx: Ctx, picks: Record<string, string>) {
  if (!can(ctx, "admin")) throw new AppError("forbidden", "Only owners and admins can change integrations");
  const { cfg } = await row(ctx.workspaceId);
  const known = cfg.channels ?? [];
  const out: Record<string, string> = {};
  for (const [p, id] of Object.entries(picks)) {
    if (!(p in BUFFER_SERVICE) || !id) continue;
    const c = known.find((x) => x.id === id);
    if (!c || c.service !== BUFFER_SERVICE[p]) throw new AppError("invalid", `That channel isn't a ${p} channel`);
    out[p] = id;
  }
  cfg.picks = out;
  await writeCfg(ctx.workspaceId, cfg);
  await audit(ctx.workspaceId, { type: "user", userId: ctx.userId }, "buffer.channels_picked", "", out);
}

export async function bufferView(workspaceId: string) {
  const { cfg, r } = await row(workspaceId);
  return { connected: !!r?.secretEnc, authMode: cfg.authMode ?? (r?.secretEnc ? "key" : undefined), channels: cfg.channels ?? [], picks: cfg.picks ?? {}, expiresAt: cfg.expiresAt };
}
export async function channelPickFor(workspaceId: string, platform: string) {
  return (await bufferView(workspaceId)).picks[platform] ?? null;
}
