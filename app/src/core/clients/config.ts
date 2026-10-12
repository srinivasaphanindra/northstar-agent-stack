import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import YAML from "yaml";
import type { PlanCaps } from "./caps";

export type Env = Record<string, string | undefined>;
export type ClientDef = { key: string; name: string; plan: string; start_date: string; sources: Record<string, unknown> };
export type ClientsConfig = { plans: Record<string, { ai_budget_usd_month: number; warmup_weeks: number; caps: PlanCaps }>; clients: ClientDef[] };

/** Expand ${VAR:-default} (env wins) and a leading ~ (HOME). */
export function expand(v: string, env: Env = process.env): string {
  const s = v.replace(/\$\{([A-Z0-9_]+)(?::-([^}]*))?\}/g, (_, k: string, d?: string) => env[k] || d || "");
  return s.startsWith("~/") ? path.join(env.HOME || os.homedir(), s.slice(2)) : s;
}

export function configPath(env: Env = process.env) {
  return env.CLIENTS_CONFIG_PATH || path.resolve(process.cwd(), "config/clients.yaml");
}

export function loadClientsConfig(file = configPath(), env: Env = process.env): ClientsConfig {
  const raw = YAML.parse(fs.readFileSync(file, "utf8")) as ClientsConfig;
  const walk = (o: unknown): unknown =>
    typeof o === "string" ? expand(o, env) : Array.isArray(o) ? o.map(walk) : o && typeof o === "object" ? Object.fromEntries(Object.entries(o).map(([k, v]) => [k, walk(v)])) : o;
  return { plans: raw.plans ?? {}, clients: (raw.clients ?? []).map((c) => ({ ...c, start_date: String(c.start_date), sources: walk(c.sources ?? {}) as Record<string, unknown> })) };
}

export const readIfExists = (p: unknown): string | null => {
  if (typeof p !== "string" || !p) return null;
  try { return fs.readFileSync(p, "utf8"); } catch { return null; }
};

/** KEY=value env file -> map. Values are never logged by callers. */
export function readEnvFile(p: unknown): Record<string, string> {
  const t = readIfExists(p);
  if (!t) return {};
  const out: Record<string, string> = {};
  for (const l of t.split(/\r?\n/)) {
    const m = l.match(/^\s*(?:export\s+)?([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
    if (m) out[m[1]] = m[2].replace(/^["']|["']$/g, "");
  }
  return out;
}
