// Read-only Clients collector (v0.3). Runs every 15 min from the worker (CLIENTS_COLLECTOR=true) or `npm run collect`.
// Reads local files, the client1-state helper (GET, read-only endpoints only), n8n's executions API (GET) and,
// only when a key file already exists, Buffer's GraphQL API with read queries. It never calls a platform write API
// and makes no X API calls. Every source is optional: a missing file or a failed request is recorded and skipped.
import YAML from "yaml";
import { db } from "../db";
import { loadClientsConfig, readEnvFile, readIfExists, type ClientDef } from "./config";
import { istDay, parseClientAudit, parseFollowers, parseHelperUsage, parseSpendLog, parseSyncLog, parseXInbound, parseXLedger, profileOf, type Activity, type Platform } from "./parsers";

type Note = { source: string; ok: boolean; rows?: number; info?: string };
type Fetch = typeof fetch;

async function getJson(f: Fetch, url: string, headers: Record<string, string> = {}, ms = 6000) {
  const r = await f(url, { method: "GET", headers, signal: AbortSignal.timeout(ms) });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function saveActivities(clientKey: string, acts: Activity[]) {
  for (const a of acts) {
    const sourceKey = `${clientKey}|${a.sourceKey}`.slice(0, 500);
    const data = { clientKey, platform: a.platform, kind: a.kind, occurredAt: a.occurredAt, day: istDay(a.occurredAt), url: a.url, detail: a.detail, count: a.count, impressions: a.impressions ?? null };
    await db.clientActivity.upsert({ where: { source_sourceKey: { source: a.source, sourceKey } }, create: { ...data, source: a.source, sourceKey }, update: data });
  }
  return acts.length;
}
async function metric(clientKey: string, platform: string, name: string, day: string, value: number, source: string) {
  await db.clientMetric.upsert({
    where: { clientKey_platform_metric_day_source: { clientKey, platform, metric: name, day, source } },
    create: { clientKey, platform, metric: name, day, value, source }, update: { value },
  });
}
async function profile(clientKey: string, platform: string, p: { handle: string; url: string } | null) {
  if (!p) return;
  await db.clientProfile.upsert({ where: { clientKey_platform: { clientKey, platform } }, create: { clientKey, platform, ...p }, update: p });
}

function findKey(o: unknown, key: string): unknown {
  if (!o || typeof o !== "object") return undefined;
  if (key in (o as Record<string, unknown>)) return (o as Record<string, unknown>)[key];
  for (const v of Object.values(o as Record<string, unknown>)) { const r = findKey(v, key); if (r !== undefined) return r; }
  return undefined;
}

async function step(notes: Note[], source: string, fn: () => Promise<number | string | null>) {
  try {
    const r = await fn();
    notes.push(r === null ? { source, ok: true, info: "skipped (not configured / missing)" } : typeof r === "number" ? { source, ok: true, rows: r } : { source, ok: true, info: r });
  } catch (e) {
    notes.push({ source, ok: false, info: (e instanceof Error ? e.message : String(e)).slice(0, 160) });
  }
}

export async function collectClient(c: ClientDef, opts: { fetch?: Fetch; now?: Date; env?: Record<string, string | undefined> } = {}): Promise<Note[]> {
  const f = opts.fetch ?? fetch;
  const env = opts.env ?? process.env;
  const today = istDay(opts.now ?? new Date());
  const s = c.sources;
  const notes: Note[] = [];

  // ---- profiles (read from existing config, never stored in the repo) ----
  await step(notes, "profiles", async () => {
    let n = 0;
    const brand = readIfExists(s.brand_yaml);
    if (brand) {
      const h = (YAML.parse(brand)?.handles ?? {}) as Record<string, string>;
      for (const p of ["x", "facebook", "linkedin", "website"] as const) if (h[p]) { await profile(c.key, p, profileOf(p, h[p])); n++; }
    }
    const landing = readIfExists(s.landing_html);
    if (landing) {
      const li = landing.match(/https:\/\/(?:www\.)?linkedin\.com\/in\/[A-Za-z0-9_-]+\/?/);
      if (li && !(await db.clientProfile.findUnique({ where: { clientKey_platform: { clientKey: c.key, platform: "linkedin" } } })) ) { await profile(c.key, "linkedin", profileOf("linkedin", li[0])); n++; }
    }
    const gy = readIfExists(s.guardrails_yaml);
    if (gy) {
      const h = (findKey(YAML.parse(gy), "handles") ?? {}) as Record<string, string>;
      for (const p of ["x", "linkedin", "facebook"] as const) if (h[p]) { await profile(c.key, p, profileOf(p, h[p])); n++; }
    }
    return brand || landing || gy ? n : null;
  });

  // ---- local logs ----
  await step(notes, "x_ledger", async () => { const t = readIfExists(s.x_ledger); return t === null ? null : saveActivities(c.key, parseXLedger(t)); });
  await step(notes, "x_inbound", async () => { const t = readIfExists(s.x_inbound); return t === null ? null : saveActivities(c.key, parseXInbound(t)); });
  await step(notes, "sync_log", async () => {
    const t = readIfExists(s.sync_log);
    if (t === null) return null;
    return saveActivities(c.key, parseSyncLog(t, (s.sync_log_bots ?? {}) as Record<string, Platform>));
  });
  await step(notes, "audit_log", async () => { const t = readIfExists(s.audit_log); return t === null ? null : saveActivities(c.key, parseClientAudit(t)); });
  await step(notes, "followers", async () => {
    const t = readIfExists(s.followers_tsv);
    if (t === null) return null;
    const rows = parseFollowers(t);
    for (const r of rows) await metric(c.key, r.platform, "followers", r.day, r.value, "followers_tsv");
    return rows.length;
  });
  await step(notes, "ai_spend_log", async () => {
    const t = readIfExists(s.ai_spend_log);
    const p = t ? parseSpendLog(t) : null;
    if (!p) return null;
    await metric(c.key, "all", "ai_spend_usd", today, p.total, "spend_log");
    return 1;
  });

  // ---- client1-state helper: read-only GET endpoints only (/health, /guard/limits, /engage/status) ----
  if (typeof s.state_helper_url === "string" && s.state_helper_url) {
    const base = s.state_helper_url.replace(/\/$/, "");
    await step(notes, "state_helper", async () => {
      await getJson(f, `${base}/health`, {}, 3000);
      const lim = (await getJson(f, `${base}/guard/limits`, {}, 4000)) as { summary?: string; spend?: { spent?: number; budget?: number } };
      let n = 0;
      for (const u of parseHelperUsage(String(lim.summary ?? ""))) { await metric(c.key, u.platform, `helper_${u.kind}`, today, u.used, "state_helper"); await metric(c.key, u.platform, `helper_${u.kind}_cap`, today, u.cap, "state_helper"); n++; }
      if (typeof lim.spend?.spent === "number") { await metric(c.key, "all", "ai_spend_usd", today, lim.spend.spent, "state_helper"); n++; }
      if (typeof lim.spend?.budget === "number") { await metric(c.key, "all", "ai_budget_usd", today, lim.spend.budget, "state_helper"); n++; }
      const eng = (await getJson(f, `${base}/engage/status`, {}, 4000).catch(() => null)) as { approved_today?: number; cap?: number } | null;
      if (eng && typeof eng.approved_today === "number") { await metric(c.key, "x", "helper_engage_approved", today, eng.approved_today, "state_helper"); n++; }
      return n;
    });
  }

  // ---- n8n executions (read API) = inbound messages handled by the client's bot workflow ----
  if (typeof s.n8n_inbound_workflow === "string" && s.n8n_inbound_workflow) {
    await step(notes, "n8n", async () => {
      const k = readEnvFile(env.N8N_ENV_FILE || `${env.HOME}/apps/n8n/owner.env`);
      const key = env.N8N_API_KEY || k.N8N_API_KEY; // gitleaks:allow (reads a key file at run time; no secret here)
      if (!key) return null;
      const base = (env.N8N_API_URL || k.N8N_URL || "http://127.0.0.1:5678").replace(/\/$/, "");
      const h = { "X-N8N-API-KEY": key, accept: "application/json" };
      const wfs = (await getJson(f, `${base}/api/v1/workflows?limit=250`, h)) as { data: { id: string; name: string }[] };
      const wf = wfs.data.find((w) => w.name === s.n8n_inbound_workflow);
      if (!wf) return "workflow not found";
      const acts: Activity[] = [];
      let cursor = "";
      for (let page = 0; page < 4; page++) {
        const r = (await getJson(f, `${base}/api/v1/executions?workflowId=${wf.id}&limit=250${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`, h)) as { data: { id: string; status: string; startedAt: string }[]; nextCursor?: string | null };
        for (const e of r.data) {
          const at = new Date(e.startedAt);
          if (isNaN(at.getTime())) continue;
          if (Date.now() - at.getTime() > 31 * 86_400_000) { cursor = ""; break; }
          if (e.status === "success") acts.push({ platform: "other", kind: "inbound", occurredAt: at, url: "", detail: `n8n ${s.n8n_inbound_workflow} #${e.id}`, count: 1, source: "n8n", sourceKey: e.id });
        }
        cursor = r.nextCursor ?? "";
        if (!cursor) break;
      }
      return saveActivities(c.key, acts);
    });
  }

  // ---- Buffer analytics: only when a key file already exists; read queries only ----
  if (typeof s.buffer_key_file === "string" && s.buffer_key_file) {
    await step(notes, "buffer", async () => {
      const key = readEnvFile(s.buffer_key_file).BUFFER_API_KEY;
      if (!key) return null;
      const q = async (query: string) => {
        if (!/^\s*(query\b|\{)/.test(query) || /\bmutation\b/.test(query)) throw new Error("read queries only");
        const r = await f(env.BUFFER_API_URL || "https://api.buffer.com", { method: "POST", headers: { Authorization: `Bearer ${key}`, "content-type": "application/json" }, body: JSON.stringify({ query }), signal: AbortSignal.timeout(10_000) });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const j = (await r.json()) as { data?: unknown; errors?: { message: string }[] };
        if (j.errors?.length) throw new Error(j.errors[0].message.slice(0, 120));
        return j.data as Record<string, unknown>;
      };
      const acc = (await q("{ account { organizations { id } } }")) as { account: { organizations: { id: string }[] } };
      const byDay = new Map<string, number>();
      for (const o of acc.account.organizations.slice(0, 3)) {
        if (!/^[a-f0-9]{24}$/i.test(o.id)) continue;
        const d = (await q(`query { posts(first: 100, input: { organizationId: "${o.id}", filter: { status: [sent] } }) { edges { node { channelService sentAt metrics { type value } } } } }`)) as { posts: { edges: { node: { channelService: string; sentAt: string; metrics: { type: string; value: number }[] | null } }[] } };
        for (const { node } of d.posts.edges) {
          const imp = node.metrics?.find((m) => m.type === "impressions")?.value;
          if (typeof imp !== "number" || !node.sentAt) continue;
          const p = node.channelService === "twitter" ? "x" : node.channelService;
          const k = `${istDay(new Date(node.sentAt))}|${p}`;
          byDay.set(k, (byDay.get(k) ?? 0) + imp);
        }
      }
      for (const [k, v] of byDay) { const [day, p] = k.split("|"); await metric(c.key, p, "impressions", day, v, "buffer"); }
      return byDay.size;
    });
  }
  return notes;
}

let running = false;
export async function collectAll(opts: { fetch?: Fetch; now?: Date; env?: Record<string, string | undefined>; configFile?: string } = {}) {
  if (running) return null;
  running = true;
  const run = await db.collectorRun.create({ data: {} });
  const all: Record<string, Note[]> = {};
  try {
    const cfg = loadClientsConfig(opts.configFile, opts.env);
    for (const c of cfg.clients) all[c.key] = await collectClient(c, opts);
    await db.collectorRun.update({ where: { id: run.id }, data: { finishedAt: new Date(), ok: Object.values(all).flat().every((n) => n.ok), notes: JSON.stringify(all) } });
  } catch (e) {
    await db.collectorRun.update({ where: { id: run.id }, data: { finishedAt: new Date(), ok: false, notes: JSON.stringify({ error: e instanceof Error ? e.message : String(e) }) } });
  } finally {
    running = false;
  }
  return all;
}
