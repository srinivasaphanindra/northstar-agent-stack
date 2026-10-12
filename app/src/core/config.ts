// All runtime configuration comes from the environment (.env locally, systemd EnvironmentFile / Docker env in prod).
import path from "node:path";

function str(name: string, def = ""): string {
  const v = process.env[name];
  return v === undefined || v === "" ? def : v;
}
function num(name: string, def: number): number {
  const v = Number(process.env[name]);
  return Number.isFinite(v) && process.env[name] !== "" && process.env[name] !== undefined ? v : def;
}
function bool(name: string, def: boolean): boolean {
  const v = process.env[name];
  if (v === undefined || v === "") return def;
  return ["1", "true", "yes", "on"].includes(v.toLowerCase());
}

export const config = {
  get appUrl() { return str("APP_URL", "http://127.0.0.1:3100"); },
  get masterKey() { return str("APP_MASTER_KEY"); },
  get adminEmails() { return str("ADMIN_EMAILS").split(",").map((s) => s.trim().toLowerCase()).filter(Boolean); },
  get secureCookies() { return bool("SECURE_COOKIES", str("APP_URL").startsWith("https://")); },
  get sessionDays() { return num("SESSION_DAYS", 14); },
  get allowSignup() { return bool("ALLOW_SIGNUP", true); },

  // AI
  get openrouterBase() { return str("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"); },
  get platformOpenrouterKey() { return str("OPENROUTER_API_KEY"); },
  get defaultModel() { return str("DEFAULT_MODEL", "google/gemini-3.1-flash-lite"); },
  // mock = deterministic offline agent (used when no key is available, and in tests)
  get llmMode() { return str("LLM_MODE", "auto") as "auto" | "mock" | "live"; },
  get estInPerM() { return num("EST_USD_PER_M_INPUT", 0.25); },
  get estOutPerM() { return num("EST_USD_PER_M_OUTPUT", 1.5); },

  // Publishing safety: when true, an approved draft is never sent to Buffer (recorded as a dry run).
  get publishDryRun() { return bool("PUBLISH_DRY_RUN", true); },
  get bufferApiUrl() { return str("BUFFER_API_URL", "https://api.buffer.com"); },
  // Buffer OAuth 2.0 (Authorization Code + PKCE). Off until BUFFER_CLIENT_ID is set (needs a registered Buffer app client).
  get bufferClientId() { return str("BUFFER_CLIENT_ID"); },
  get bufferClientSecret() { return str("BUFFER_CLIENT_SECRET"); },
  get bufferAuthUrl() { return str("BUFFER_OAUTH_AUTHORIZE_URL", "https://auth.buffer.com/auth"); },
  get bufferTokenUrl() { return str("BUFFER_OAUTH_TOKEN_URL", "https://auth.buffer.com/token"); },
  get bufferRedirectUri() { return str("BUFFER_OAUTH_REDIRECT_URI", `${this.appUrl}/api/buffer/oauth/callback`); },
  get bufferScopes() { return str("BUFFER_OAUTH_SCOPES", "posts:write posts:read account:read offline_access"); },
  get bufferOauthEnabled() { return Boolean(this.bufferClientId); },
  /** Model for the approve-time AI content check (falls back to DEFAULT_MODEL). */
  get checkModel() { return str("CONTENT_CHECK_MODEL", this.defaultModel); },

  // Guardrails template (yaml). Local: ~/agency/product/guardrails-default.yaml; prod: the copy in config/.
  get guardrailsPath() { return str("GUARDRAILS_TEMPLATE_PATH", path.join(process.cwd(), "config", "guardrails-default.yaml")); },

  // Manual payments
  get upiId() { return str("PAYMENT_UPI_ID", "northstar@upi-PLACEHOLDER"); },
  get upiPayee() { return str("PAYMENT_UPI_NAME", "Northstar Agents"); },
  get upiQrImage() { return str("PAYMENT_UPI_QR_IMAGE"); }, // optional: path/URL to his own QR image; else generated
  /** Empty / PLACEHOLDER = hide Gumroad in UI until a real product URL is set. */
  get gumroadUrl() {
    const v = str("PAYMENT_GUMROAD_URL");
    if (!v || /PLACEHOLDER/i.test(v)) return "";
    return v;
  },
  get gumroadEnabled() { return Boolean(this.gumroadUrl); },

  // Channels
  get telegramApi() { return str("TELEGRAM_API_URL", "https://api.telegram.org"); },
  get slackApi() { return str("SLACK_API_URL", "https://slack.com/api"); },
  get slackPollSeconds() { return num("SLACK_POLL_SECONDS", 60); },
  get channelsEnabled() { return bool("CHANNEL_WORKERS", true); },
  /** Feature flag: hide Telegram in onboarding/settings when false; code + worker poller stay. */
  get telegramEnabled() { return bool("CHANNEL_TELEGRAM", true); },
  get workerPollMs() { return num("WORKER_POLL_MS", 1500); },

  /** Platform-wide hard stop on OpenRouter spend across all workspaces (IST day). $0 = disabled. */
  get platformDailyAiCapUsd() { return num("PLATFORM_DAILY_AI_CAP_USD", 3); },
};

export const PLANS = {
  trial: { id: "trial", name: "Trial", inr: 0, byokInr: 0, days: 7, draftsPerMonth: 5, seats: 1, aiCapUsd: 1, blurb: "7 days, 5 drafts, nothing published without approval" },
  solo: { id: "solo", name: "Self-serve Solo", inr: 1999, byokInr: 1499, days: 30, draftsPerMonth: 40, seats: 1, aiCapUsd: 10, blurb: "1 workspace, up to 3 Buffer channels, 40 drafts/mo, web + Slack chat" },
  pro: { id: "pro", name: "Self-serve Pro", inr: 3999, byokInr: 2999, days: 30, draftsPerMonth: 120, seats: 3, aiCapUsd: 20, blurb: "120 drafts/mo, 3 team seats, weekly report, priority help" },
} as const;
export type PlanId = keyof typeof PLANS;
export function planOf(id: string) { return (PLANS as Record<string, (typeof PLANS)[PlanId]>)[id] ?? PLANS.trial; }
