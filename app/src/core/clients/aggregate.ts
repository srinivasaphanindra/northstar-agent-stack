// Aggregation over collected activity rows (pure, testable).
export type Row = { platform: string; kind: string; day: string; count: number; impressions?: number | null };

const DAY = 86_400_000;
/** The last n IST days ending at `today`, oldest first. */
export function lastDays(today: string, n: number): string[] {
  const end = Date.parse(`${today}T00:00:00Z`);
  return Array.from({ length: n }, (_, i) => new Date(end - (n - 1 - i) * DAY).toISOString().slice(0, 10));
}

export const COUNTED = ["post", "reply", "comment"] as const;
export type Series = Record<string, Record<string, number[]>>; // platform -> kind -> per-day counts

/** Per-day counts per platform and kind over the window (posts, replies, comments). */
export function perDay(rows: Row[], days: string[], platforms = ["x", "linkedin", "facebook"]): Series {
  const idx = new Map(days.map((d, i) => [d, i]));
  const s: Series = {};
  for (const p of platforms) { s[p] = {}; for (const k of COUNTED) s[p][k] = days.map(() => 0); }
  for (const r of rows) {
    const i = idx.get(r.day);
    if (i === undefined || !s[r.platform] || !(COUNTED as readonly string[]).includes(r.kind)) continue;
    s[r.platform][r.kind][i] += r.count ?? 1;
  }
  return s;
}

export type Totals = { dryRuns: number; posts: number; replies: number; comments: number; approved: number; rejected: number; edited: number; inbound: number; impressions: number | null; failed: number };
export function totals(rows: Row[], days: string[]): Totals {
  const set = new Set(days);
  const t: Totals = { dryRuns: 0, posts: 0, replies: 0, comments: 0, approved: 0, rejected: 0, edited: 0, inbound: 0, impressions: null, failed: 0 };
  for (const r of rows) {
    if (!set.has(r.day)) continue;
    const c = r.count ?? 1;
    if (r.kind === "post") t.posts += c;
    else if (r.kind === "reply") t.replies += c;
    else if (r.kind === "comment") t.comments += c;
    else if (r.kind === "approved") t.approved += c;
    else if (r.kind === "rejected") t.rejected += c;
    else if (r.kind === "edited") t.edited += c;
    else if (r.kind === "inbound") t.inbound += c;
    else if (r.kind === "publish_failed") t.failed += c;
    else if (r.kind === "dry_run") t.dryRuns += c;
    if (typeof r.impressions === "number") t.impressions = (t.impressions ?? 0) + r.impressions;
  }
  return t;
}

/** Today's counts for one platform. */
export function todayCounts(rows: Row[], today: string, platform: string) {
  const r = rows.filter((x) => x.day === today && x.platform === platform);
  const sum = (k: string) => r.filter((x) => x.kind === k).reduce((a, x) => a + (x.count ?? 1), 0);
  return { posts: sum("post"), replies: sum("reply"), comments: sum("comment") };
}

/** Posts in the Monday-start week containing `today` (for posts-per-week caps). */
export function weekPosts(rows: Row[], monday: string, sunday: string, platform: string) {
  return rows.filter((x) => x.platform === platform && x.kind === "post" && x.day >= monday && x.day <= sunday).reduce((a, x) => a + (x.count ?? 1), 0);
}
