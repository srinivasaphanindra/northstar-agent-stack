// Warm-up caps math. Ramp tables come from config/clients.yaml (product numbers, not personal data).
export type PlatformCaps = { posts_per_day?: number[]; posts_per_week?: number[]; replies_per_day?: number[]; comments_per_day?: number[] };
export type PlanCaps = Record<string, PlatformCaps>;

const DAY = 86_400_000;
const dayNum = (d: string) => Date.parse(`${d}T00:00:00Z`) / DAY;

/** Warm-up week index (0-based) of an IST day; before the start counts as week 0, after the warm-up holds the last week. */
export function warmupWeek(startDate: string, day: string, weeks = 4): number {
  const diff = Math.floor(dayNum(day) - dayNum(startDate));
  if (!Number.isFinite(diff) || diff < 0) return 0;
  return Math.min(Math.floor(diff / 7), weeks - 1);
}
const at = (arr: number[] | undefined, i: number) => (arr && arr.length ? arr[Math.min(i, arr.length - 1)] : undefined);

export type DayCaps = { posts?: number; postsPerWeek?: number; replies?: number; comments?: number };
export function capsFor(plan: PlanCaps, platform: string, startDate: string, day: string, weeks = 4): DayCaps {
  const c = plan[platform];
  if (!c) return {};
  const w = warmupWeek(startDate, day, weeks);
  return { posts: at(c.posts_per_day, w), postsPerWeek: at(c.posts_per_week, w), replies: at(c.replies_per_day, w), comments: at(c.comments_per_day, w) };
}

/** Monday-start ISO week bounds of an IST day: [monday, sunday]. */
export function weekOf(day: string): [string, string] {
  const n = dayNum(day);
  const dow = (new Date(n * DAY).getUTCDay() + 6) % 7;
  const iso = (x: number) => new Date(x * DAY).toISOString().slice(0, 10);
  return [iso(n - dow), iso(n - dow + 6)];
}

export type Usage = { used: number; cap?: number; pct: number | null; over: boolean; label: string };
export function usage(used: number, cap?: number): Usage {
  if (cap === undefined) return { used, cap, pct: null, over: false, label: `${used}` };
  const pct = cap > 0 ? Math.round((used / cap) * 100) : used > 0 ? 100 : 0;
  return { used, cap, pct, over: used > cap, label: `${used}/${cap}` };
}
