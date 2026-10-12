import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { beforeEach, describe, expect, it } from "vitest";
import { db } from "@/core/db";
import { createUser } from "@/core/auth";
import { fromIst, istDay, parseClientAudit, parseFollowers, parseHelperUsage, parseSpendLog, parseSyncLog, parseXInbound, parseXLedger, platformOfUrl, profileOf } from "@/core/clients/parsers";
import { capsFor, usage, warmupWeek, weekOf } from "@/core/clients/caps";
import { lastDays, perDay, todayCounts, totals, weekPosts } from "@/core/clients/aggregate";
import { expand, loadClientsConfig } from "@/core/clients/config";
import { collectAll } from "@/core/clients/collector";
import { assertPlatformAdmin, clientDetail, clientsOverview } from "@/core/clients/dashboard";
import { resetDb } from "./helpers";

const REPO_CFG = path.resolve(__dirname, "../config/clients.yaml");

describe("parsers", () => {
  it("IST helpers", () => {
    expect(istDay(new Date("2026-10-11T19:00:00Z"))).toBe("2026-10-12"); // 00:30 IST
    expect(fromIst("2026-10-12", "09:05")!.toISOString()).toBe("2026-10-12T03:35:00.000Z");
    expect(fromIst("bad", "09:05")).toBeNull();
  });
  it("x ledger: counts POSTED/ALREADY, skips junk and failures", () => {
    const t = [
      "2026-10-08T14:10:30+0530\toriginal\tSEED(r3 click, confirmed ALREADY 14:45)\thttps://x.com/acct/status/1\t-",
      "2026-10-08T16:19:09+0530\treply\tPOSTED\thttps://x.com/acct/status/2\t99",
      "2026-10-08T16:20:00+0530\treply\tFAILED\thttps://x.com/acct/status/3\t98",
      "garbage line",
      "",
    ].join("\n");
    const a = parseXLedger(t);
    expect(a.map((x) => x.kind)).toEqual(["post", "reply"]);
    expect(a[1].occurredAt.toISOString()).toBe("2026-10-08T10:49:09.000Z");
    expect(a[1].detail).toBe("reply to 99");
  });
  it("x inbound log: header only -> none; rows parsed", () => {
    expect(parseXInbound("date\ttime\ttype\tauthor\turl\taction\n")).toEqual([]);
    const a = parseXInbound("date\ttime\ttype\tauthor\turl\taction\n2026-10-12\t10:00\tmention\tsomeone\thttps://x.com/s/1\treplied");
    expect(a).toHaveLength(1);
    expect(a[0].kind).toBe("inbound");
  });
  it("sync log: bot entries classified; other actors ignored", () => {
    const bots = { "X Bot": "x", "LinkedIn Bot": "linkedin", "Facebook Bot": "facebook" } as const;
    const t = [
      "2026-10-11 09:41 IST | LinkedIn Bot | Morning post (proof theme): https://www.linkedin.com/feed/update/urn:li:activity:1/ . Comments: none",
      "2026-10-11 11:31 IST | Facebook Bot | Posted original (tip): https://www.facebook.com/page/posts/2 . Birthdays wished",
      "2026-10-11 12:00 IST | LinkedIn Bot | Inbound check: no new comments. 0 replies.",
      "2026-10-11 13:00 IST | Facebook Bot | Inbound check: 2 replies sent to new comments.",
      "2026-10-11 14:00 IST | Facebook Bot | Commented on https://www.facebook.com/someone/posts/9 (value tip)",
      "2026-10-12 12:20 IST | LinkedIn Bot | Viral plan warm-up: wk1 4 posts/wk + 2 comments/day",
      "2026-10-12 11:19 IST | X Bot | X original: https://x.com/acct/status/5 . Today: 1 original.",
      "2026-10-12 11:20 IST | Chief | posted something https://www.linkedin.com/feed/update/x",
      "## heading",
    ].join("\n");
    const a = parseSyncLog(t, { ...bots });
    expect(a.map((x) => `${x.platform}:${x.kind}:${x.count}`)).toEqual([
      "linkedin:post:1", "facebook:post:1", "linkedin:inbound:0", "facebook:inbound:2", "facebook:comment:1", "linkedin:note:1", "x:note:1",
    ]);
  });
  it("client audit log: approvals, replies, publish results; actor ids dropped", () => {
    const t = [
      "ts_ist\tevent\tby\tref\toutcome\tdetail",
      "2026-10-12 10:26:35\tdecide\tU0000000001\t1.1\tapprove\tengage",
      "2026-10-12 12:33:53\tdecide\tU0000000001\t1.2\tdiscard\tengage",
      "2026-10-12 12:34:52\tdecide\tU0000000001\t1.3\tedit\tpost",
      "2026-10-12 10:28:28\tengage_run\tworker\t1.1\tdone\treply https://x.com/a/status/7 DONE",
      "2026-10-12 10:26:45\tpublish_result\tclient1-state\t1.4\tfailed\ttwitter:fail:draft",
      "2026-10-12 09:00:20\tbrowser_task\tbatch-read\t\tallowed\thttps://x.com/home",
    ].join("\n");
    const a = parseClientAudit(t);
    expect(a.map((x) => `${x.platform}:${x.kind}`)).toEqual(["other:approved", "other:rejected", "other:edited", "x:reply", "x:publish_failed"]);
    expect(a.some((x) => x.detail.includes("U0000000001"))).toBe(false);
  });
  it("client0 dry-run rows are not counted as posts", () => {
    const t = [
      "ts_ist\tevent\tby\tref\toutcome\tdetail",
      "2026-10-12 17:40:40\tpublish\tU0000000001\t1.5\tissued\taddToQueue x",
      "2026-10-12 17:40:40\tpublish_result\tclient0-state\t1.5\tdry_run\tPUBLISH_DRY_RUN: addToQueue x; nothing sent to Buffer/X/LinkedIn/Facebook",
      "2026-10-12 17:41:00\tengage\tU0000000001\t1.6\tdry_run\treply https://x.com/a/status/9; nothing queued or sent",
    ].join("\n");
    const a = parseClientAudit(t);
    expect(a.map((x) => `${x.platform}:${x.kind}`)).toEqual(["x:dry_run", "x:dry_run"]);
    const rows = a.map((x) => ({ platform: x.platform, kind: x.kind, day: istDay(x.occurredAt), count: 1 }));
    expect(totals(rows, ["2026-10-12"])).toMatchObject({ posts: 0, replies: 0, dryRuns: 2 });
  });
  it("followers, spend log, helper summary, profiles", () => {
    expect(parseFollowers("date\ttime\tx\tfacebook\tlinkedin\tyoutube\n2026-10-08\t15:30\tnot visible\tnot visible\t\t\n2026-10-08\t23:45\t112\tnot visible\t1,204\t")).toEqual([
      { day: "2026-10-08", platform: "x", value: 112 }, { day: "2026-10-08", platform: "linkedin", value: 1204 },
    ]);
    expect(parseFollowers("")).toEqual([]);
    expect(parseSpendLog("OpenRouter spend: today $7.66, this week $16.47, Hermes key total $16.47 of $50 limit.\nOpenRouter spend: today $1.00, this week $2.00, Hermes key total $20.30 of $50 limit.")).toEqual({ today: 1, week: 2, total: 20.3 });
    expect(parseSpendLog("nothing")).toBeNull();
    expect(parseHelperUsage("Plan Viral (x). X: replies 1/12, posts 0/6; LinkedIn: approval-only comments 0/2, posts 0/1; Facebook: approval-only comments 3/4, posts 0/1. Allowed")).toEqual([
      { platform: "x", kind: "replies", used: 1, cap: 12 }, { platform: "x", kind: "posts", used: 0, cap: 6 },
      { platform: "linkedin", kind: "comments", used: 0, cap: 2 }, { platform: "linkedin", kind: "posts", used: 0, cap: 1 },
      { platform: "facebook", kind: "comments", used: 3, cap: 4 }, { platform: "facebook", kind: "posts", used: 0, cap: 1 },
    ]);
    expect(profileOf("x", "@someone")).toEqual({ handle: "@someone", url: "https://x.com/someone" });
    expect(profileOf("facebook", "https://www.facebook.com/page.name/")).toEqual({ handle: "page.name", url: "https://www.facebook.com/page.name/" });
    expect(profileOf("facebook", "My Page")).toEqual({ handle: "My Page", url: "" });
    expect(profileOf("linkedin", "")).toBeNull();
    expect(platformOfUrl("https://twitter.com/a")).toBe("x");
  });
});

describe("caps math", () => {
  const cfg = loadClientsConfig(REPO_CFG, {});
  const plan = cfg.plans.viral.caps;
  it("warm-up week index", () => {
    expect(warmupWeek("2026-10-12", "2026-10-11")).toBe(0);
    expect(warmupWeek("2026-10-12", "2026-10-12")).toBe(0);
    expect(warmupWeek("2026-10-12", "2026-10-18")).toBe(0);
    expect(warmupWeek("2026-10-12", "2026-10-19")).toBe(1);
    expect(warmupWeek("2026-10-12", "2026-11-01")).toBe(2);
    expect(warmupWeek("2026-10-12", "2026-11-02")).toBe(3);
    expect(warmupWeek("2026-10-12", "2027-03-01")).toBe(3);
  });
  it("X ramps 4/8 -> 15/30 over 4 weeks", () => {
    expect(capsFor(plan, "x", "2026-10-12", "2026-10-12")).toMatchObject({ posts: 4, replies: 8 });
    expect(capsFor(plan, "x", "2026-10-12", "2026-10-20")).toMatchObject({ posts: 8, replies: 15 });
    expect(capsFor(plan, "x", "2026-10-12", "2026-10-27")).toMatchObject({ posts: 11, replies: 23 });
    expect(capsFor(plan, "x", "2026-10-12", "2026-12-25")).toMatchObject({ posts: 15, replies: 30 });
    expect(capsFor(plan, "linkedin", "2026-10-12", "2026-10-12")).toMatchObject({ postsPerWeek: 4, comments: 2 });
    expect(capsFor(plan, "facebook", "2026-10-12", "2026-11-03")).toMatchObject({ posts: 3, comments: 9 });
    expect(capsFor(plan, "tiktok", "2026-10-12", "2026-10-12")).toEqual({});
    expect(cfg.plans.viral.ai_budget_usd_month).toBe(35);
  });
  it("usage and weeks", () => {
    expect(usage(5, 4)).toMatchObject({ over: true, pct: 125, label: "5/4" });
    expect(usage(0, 0)).toMatchObject({ pct: 0, over: false });
    expect(usage(3)).toMatchObject({ pct: null, label: "3" });
    expect(weekOf("2026-10-12")).toEqual(["2026-10-12", "2026-10-18"]);
    expect(weekOf("2026-10-18")).toEqual(["2026-10-12", "2026-10-18"]);
  });
  it("config expands env and ~ without hardcoded handles", () => {
    expect(expand("${NOPE:-~/a/b}", { HOME: "/h" })).toBe("/h/a/b");
    expect(expand("${P:-x}", { P: "/y" })).toBe("/y");
    const raw = fs.readFileSync(REPO_CFG, "utf8");
    expect(raw).not.toMatch(/x\.com\/|linkedin\.com\/in\/|facebook\.com\//);
  });
});

describe("aggregation", () => {
  const days = lastDays("2026-10-12", 7);
  const rows = [
    { platform: "x", kind: "post", day: "2026-10-12", count: 1 },
    { platform: "x", kind: "reply", day: "2026-10-12", count: 1 },
    { platform: "x", kind: "reply", day: "2026-10-10", count: 1 },
    { platform: "linkedin", kind: "post", day: "2026-10-11", count: 1 },
    { platform: "facebook", kind: "inbound", day: "2026-10-12", count: 2 },
    { platform: "other", kind: "approved", day: "2026-10-12", count: 1 },
    { platform: "other", kind: "rejected", day: "2026-10-12", count: 1 },
    { platform: "x", kind: "post", day: "2026-09-01", count: 1, impressions: 50 },
    { platform: "facebook", kind: "post", day: "2026-10-09", count: 1, impressions: 40 },
  ];
  it("windows", () => {
    expect(days).toEqual(["2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09", "2026-10-10", "2026-10-11", "2026-10-12"]);
    expect(lastDays("2026-10-12", 30)).toHaveLength(30);
    expect(lastDays("2026-03-01", 2)).toEqual(["2026-02-28", "2026-03-01"]);
  });
  it("per day per platform", () => {
    const s = perDay(rows, days);
    expect(s.x.reply).toEqual([0, 0, 0, 0, 1, 0, 1]);
    expect(s.x.post).toEqual([0, 0, 0, 0, 0, 0, 1]);
    expect(s.linkedin.post[5]).toBe(1);
    expect(s.facebook.comment.every((v) => v === 0)).toBe(true);
  });
  it("totals and today", () => {
    expect(totals(rows, days)).toMatchObject({ posts: 3, replies: 2, comments: 0, inbound: 2, approved: 1, rejected: 1, impressions: 40 });
    expect(totals([], days).impressions).toBeNull();
    expect(todayCounts(rows, "2026-10-12", "x")).toEqual({ posts: 1, replies: 1, comments: 0 });
    expect(weekPosts(rows, "2026-10-06", "2026-10-12", "linkedin")).toBe(1);
  });
});

describe("collector + admin-only access", () => {
  let dir: string;
  let cfgFile: string;
  beforeEach(async () => {
    await resetDb();
    dir = fs.mkdtempSync(path.join(os.tmpdir(), "ns-clients-"));
    const w = (n: string, t: string) => fs.writeFileSync(path.join(dir, n), t);
    w("brand.yaml", 'handles:\n  x: "@acct"\n  facebook: "https://www.facebook.com/acct.page/"\n');
    w("landing.html", '<a href="https://www.linkedin.com/in/acct-li/">in</a>');
    w("ledger.tsv", "2026-10-12T10:00:00+0530\toriginal\tPOSTED\thttps://x.com/acct/status/1\t-\n2026-10-12T11:00:00+0530\treply\tPOSTED\thttps://x.com/acct/status/2\t5\n");
    w("sync.md", "2026-10-12 10:02 IST | LinkedIn Bot | Morning post: https://www.linkedin.com/feed/update/urn:li:activity:9/ .\n");
    w("followers.tsv", "date\ttime\tx\tfacebook\tlinkedin\tyoutube\n2026-10-11\t10:00\t100\t\t\t\n2026-10-12\t10:00\t112\t\t\t\n");
    w("g.yaml", 'coach:\n  handles: {x: "c1x", linkedin: "c1li", facebook: "C1 Page"}\n');
    w("audit.tsv", "ts_ist\tevent\tby\tref\toutcome\tdetail\n2026-10-12 10:26:35\tdecide\tU1\t1.1\tapprove\tengage\n2026-10-12 10:28:28\tengage_run\tworker\t1.1\tdone\treply https://x.com/b/status/7\n");
    w("spend.log", "OpenRouter spend: today $1.00, this week $2.00, Hermes key total $20.30 of $50 limit.\n");
    w("n8n.env", "N8N_URL=http://n8n.test\nN8N_API_KEY=test-key\n");
    const repo = fs.readFileSync(REPO_CFG, "utf8").split("\nclients:")[0];
    cfgFile = path.join(dir, "clients.yaml");
    fs.writeFileSync(cfgFile, `${repo}
clients:
  - key: client0
    name: "Client 0"
    plan: viral
    start_date: "2026-10-12"
    sources:
      brand_yaml: ${dir}/brand.yaml
      landing_html: ${dir}/landing.html
      x_ledger: ${dir}/ledger.tsv
      x_inbound: ${dir}/missing.tsv
      sync_log: ${dir}/sync.md
      sync_log_bots: { "LinkedIn Bot": linkedin }
      followers_tsv: ${dir}/followers.tsv
      ai_spend_log: ${dir}/spend.log
      buffer_key_file: ${dir}/no-buffer.env
      state_helper_url: http://helper0.test
  - key: client1
    name: "Client 1"
    plan: viral
    start_date: "2026-10-12"
    sources:
      guardrails_yaml: ${dir}/g.yaml
      audit_log: ${dir}/audit.tsv
      state_helper_url: http://helper.test
      n8n_inbound_workflow: "Client1 bot"
`);
    process.env.CLIENTS_CONFIG_PATH = cfgFile;
  });

  const calls: { url: string; method: string }[] = [];
  const fakeFetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, method: init?.method ?? "GET" });
    const j = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { "content-type": "application/json" } });
    if (url === "http://helper.test/health" || url === "http://helper0.test/health") return j({ ok: true });
    if (url === "http://helper0.test/guard/limits") return j({ summary: "X: replies 2/8, posts 0/4", spend: { spent: 0.5, budget: 35 } });
    if (url === "http://helper0.test/engage/status") return j({ approved_today: 0, cap: 8 });
    if (url === "http://helper.test/guard/limits") return j({ summary: "X: replies 3/12, posts 0/6; LinkedIn: approval-only comments 0/2, posts 0/1", spend: { spent: 0.15, budget: 35 } });
    if (url === "http://helper.test/engage/status") return j({ approved_today: 1, cap: 12 });
    if (url.startsWith("http://n8n.test/api/v1/workflows")) return j({ data: [{ id: "wf1", name: "Client1 bot" }] });
    if (url.startsWith("http://n8n.test/api/v1/executions")) return j({ data: [{ id: "11", status: "success", startedAt: new Date().toISOString() }, { id: "12", status: "error", startedAt: new Date().toISOString() }], nextCursor: null });
    return new Response("no", { status: 404 });
  }) as typeof fetch;

  it("collects read-only, idempotently, and skips missing sources", async () => {
    calls.length = 0;
    const env = { ...process.env, HOME: dir, N8N_ENV_FILE: path.join(dir, "n8n.env"), N8N_API_KEY: "" };
    const r1 = await collectAll({ fetch: fakeFetch, env, configFile: cfgFile, now: new Date("2026-10-12T08:00:00Z") });
    await collectAll({ fetch: fakeFetch, env, configFile: cfgFile, now: new Date("2026-10-12T08:00:00Z") });
    expect(calls.every((c) => c.method === "GET")).toBe(true);
    expect(calls.some((c) => /x\.com|twitter|api\.buffer/.test(c.url))).toBe(false);
    expect(calls.some((c) => /\/tick/.test(c.url))).toBe(false);
    const n0 = r1!.client0;
    expect(n0.find((n) => n.source === "x_inbound")!.info).toMatch(/skipped/);
    expect(n0.find((n) => n.source === "buffer")!.info).toMatch(/skipped/);
    expect(await db.clientActivity.count({ where: { clientKey: "client0" } })).toBe(3); // no duplicates after 2 runs
    expect(await db.clientActivity.count({ where: { clientKey: "client1", kind: "inbound" } })).toBe(1);
    const prof = await db.clientProfile.findMany({ where: { clientKey: "client0" }, orderBy: { platform: "asc" } });
    expect(prof.map((p) => `${p.platform}:${p.handle}`)).toEqual(["facebook:acct.page", "linkedin:acct-li", "x:@acct"]);
    expect(await db.collectorRun.count()).toBe(2);
  });

  it("dashboard is admin-only and computes caps", async () => {
    const env = { ...process.env, HOME: dir, N8N_ENV_FILE: path.join(dir, "n8n.env"), N8N_API_KEY: "" };
    await collectAll({ fetch: fakeFetch, env, configFile: cfgFile, now: new Date("2026-10-12T08:00:00Z") });
    const member = await createUser({ email: `m-${Date.now()}@test.local`, password: "correct horse battery" });
    const admin = await createUser({ email: "admin@test.local", password: "correct horse battery" });
    expect(member.isPlatformAdmin).toBe(false);
    expect(admin.isPlatformAdmin).toBe(true);
    expect(() => assertPlatformAdmin(member)).toThrow(/admins only/);
    expect(() => assertPlatformAdmin(null)).toThrow();
    await expect(clientsOverview(member)).rejects.toThrow(/admins only/);
    await expect(clientDetail(member, "client0")).rejects.toThrow(/admins only/);
    await expect(clientDetail(admin, "nobody")).rejects.toThrow(/No such client/);
    const now = new Date("2026-10-12T08:00:00Z");
    const o = await clientsOverview(admin, now);
    const c0x = o.clients.find((c) => c.key === "client0")!.caps.find((p) => p.platform === "x")!;
    expect(c0x.posts.label).toBe("1/4");
    const c1x = o.clients.find((c) => c.key === "client1")!.caps.find((p) => p.platform === "x")!;
    expect(c1x.replies!.label).toBe("3/8"); // helper count (3) beats the audit log (1)
    const d = await clientDetail(admin, "client1", now);
    expect(d.spend).toEqual({ spent: 0.15, budget: 35, pct: 0 });
    expect(d.t7).toMatchObject({ approved: 1, inbound: 1, replies: 1 });
    const d0 = await clientDetail(admin, "client0", now);
    expect(d0.followers.map((f) => f.value)).toEqual([100, 112]);
    expect(d0.spend.spent).toBe(0.5); // client0's own helper meter beats the shared spend log ($20.30)
    expect(c0x.replies!.label).toBe("2/8"); // helper count (2) beats the ledger (1)
    expect(d0.series7.linkedin.post[6]).toBe(1);
  });
});
