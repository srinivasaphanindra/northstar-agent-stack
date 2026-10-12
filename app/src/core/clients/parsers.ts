// Pure parsers for the read-only Clients collector. No I/O here: callers pass file text / JSON in.
// Every parser tolerates junk lines and returns [] / null rather than throwing.

export type Platform = "x" | "linkedin" | "facebook" | "other";
export type Kind = "post" | "reply" | "comment" | "inbound" | "approved" | "rejected" | "edited" | "publish_failed" | "dry_run" | "note";
export type Activity = {
  platform: Platform; kind: Kind; occurredAt: Date; url: string; detail: string;
  count: number; impressions?: number | null; source: string; sourceKey: string;
};

const IST_MS = 330 * 60_000;
/** YYYY-MM-DD of a Date in IST. */
export function istDay(d: Date): string { return new Date(d.getTime() + IST_MS).toISOString().slice(0, 10); }
/** "2026-10-12" + "11:19[:05]" in IST -> Date. */
export function fromIst(date: string, time = "00:00"): Date | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !/^\d{1,2}:\d{2}(:\d{2})?$/.test(time)) return null;
  const t = time.length <= 5 ? `${time.padStart(5, "0")}:00` : time.padStart(8, "0");
  const d = new Date(`${date}T${t}+05:30`);
  return isNaN(d.getTime()) ? null : d;
}
function parseIso(ts: string): Date | null {
  const norm = ts.trim().replace(/([+-]\d{2})(\d{2})$/, "$1:$2");
  const d = new Date(norm);
  return isNaN(d.getTime()) ? null : d;
}
const lines = (text: string) => text.split(/\r?\n/).filter((l) => l.trim() !== "");
const firstUrl = (s: string) => (s.match(/https?:\/\/[^\s<>"')]+/) ?? [""])[0].replace(/[.,;]+$/, "");
export function platformOfUrl(url: string): Platform {
  if (/(^|\/\/|\.)(x|twitter)\.com\//.test(url)) return "x";
  if (/linkedin\.com\//.test(url)) return "linkedin";
  if (/facebook\.com\/|fb\.com\//.test(url)) return "facebook";
  return "other";
}

/** ~/agency/state/x-post-ledger.tsv: ts \t original|reply \t status \t url \t parent_id (no header). */
export function parseXLedger(text: string): Activity[] {
  const out: Activity[] = [];
  for (const l of lines(text)) {
    const [ts, kind, status = "", url = "", parent = ""] = l.split("\t");
    const at = parseIso(ts ?? "");
    if (!at || (kind !== "original" && kind !== "reply")) continue;
    if (!/POSTED|ALREADY/i.test(status)) continue; // failed / skipped attempts are not activity
    out.push({ platform: "x", kind: kind === "reply" ? "reply" : "post", occurredAt: at, url: url.trim(), detail: parent && parent !== "-" ? `reply to ${parent.trim()}` : "", count: 1, source: "x_ledger", sourceKey: url.trim() || `${ts}|${kind}` });
  }
  return out;
}

/** ~/agency/state/x-inbound-log.tsv: header `date time type author url action`. */
export function parseXInbound(text: string): Activity[] {
  const out: Activity[] = [];
  for (const l of lines(text)) {
    const [date, time, type = "", author = "", url = "", action = ""] = l.split("\t");
    if (date === "date") continue;
    const at = fromIst(date ?? "", time ?? "");
    if (!at) continue;
    out.push({ platform: "x", kind: "inbound", occurredAt: at, url: url.trim(), detail: [type, author, action].filter(Boolean).join(" · "), count: 1, source: "x_inbound", sourceKey: `${date} ${time}|${url || author}|${type}` });
  }
  return out;
}

/** sync-log.md lines `YYYY-MM-DD HH:MM IST | Actor | request | result | refs`, only the given bots. */
export function parseSyncLog(text: string, bots: Record<string, Platform>, countOnly: Platform[] = ["linkedin", "facebook"]): Activity[] {
  const out: Activity[] = [];
  for (const l of lines(text)) {
    const m = l.match(/^(\d{4}-\d{2}-\d{2}) (\d{1,2}:\d{2}) IST \| ([^|]+?) \| (.*)$/);
    if (!m) continue;
    const platform = bots[m[3].trim()];
    if (!platform) continue;
    const at = fromIst(m[1], m[2]);
    if (!at) continue;
    const body = m[4].trim();
    const url = firstUrl(body);
    const onPlatform = url !== "" && platformOfUrl(url) === platform;
    let kind: Kind = "note";
    let count = 1;
    if (countOnly.includes(platform)) {
      const inbound = body.match(/^Inbound check\b.*?(\d+) repl(?:y|ies)/i);
      if (/^Inbound check/i.test(body)) { kind = "inbound"; count = inbound ? Number(inbound[1]) : 0; }
      else if (onPlatform && /^(?:Commented|Comment)\b/i.test(body)) kind = "comment";
      else if (onPlatform && /\b(posted|post|original)\b/i.test(body) && !/^(HOLD|Hold)/.test(body)) kind = "post";
    }
    out.push({ platform, kind, occurredAt: at, url, detail: body.slice(0, 280), count, source: "sync_log", sourceKey: `${m[1]} ${m[2]}|${m[3].trim()}|${body.slice(0, 60)}` });
  }
  return out;
}

/** client1 audit-log.tsv: header `ts_ist event by ref outcome detail`. Actor ids are not kept. */
export function parseClientAudit(text: string): Activity[] {
  const out: Activity[] = [];
  for (const l of lines(text)) {
    const [ts = "", event = "", , ref = "", outcome = "", detail = ""] = l.split("\t");
    if (ts === "ts_ist") continue;
    const [d, t] = ts.split(" ");
    const at = fromIst(d ?? "", t ?? "");
    if (!at) continue;
    const url = firstUrl(detail);
    const base = { occurredAt: at, url, detail: `${event} ${outcome}`.trim(), count: 1, source: "audit_log", sourceKey: `${ts}|${event}|${ref}|${outcome}` };
    if (event === "decide") {
      const kind: Kind = outcome === "approve" ? "approved" : outcome === "edit" ? "edited" : outcome === "discard" || outcome === "reject" ? "rejected" : "note";
      out.push({ ...base, platform: "other", kind, detail: `${outcome} ${detail}`.trim() });
    } else if (event === "engage_run" && /^done/.test(outcome)) {
      const kind: Kind = /^\s*(?:backfilled[^:]*:\s*)?(?:comment)/i.test(detail) ? "comment" : "reply";
      const p = url ? platformOfUrl(url) : "x";
      out.push({ ...base, platform: p, kind: p === "x" ? "reply" : kind === "reply" ? "comment" : kind, detail: detail.slice(0, 200) });
    } else if (event === "publish_result") {
      const p = /twitter|^x:|\b(?:addToQueue|shareNow) x\b/i.test(detail) ? "x" : /linkedin/i.test(detail) ? "linkedin" : /facebook/i.test(detail) ? "facebook" : "other";
      const kind: Kind = /dry.?run/i.test(outcome) ? "dry_run" : /fail|error/i.test(outcome) ? "publish_failed" : "post";
      out.push({ ...base, platform: p, kind, detail: `${outcome} ${detail}`.trim().slice(0, 200) });
    } else if (event === "engage" && /dry.?run/i.test(outcome)) {
      const p = url ? platformOfUrl(url) : "x";
      out.push({ ...base, platform: p, kind: "dry_run", detail: `engage ${detail}`.slice(0, 200) });
    }
  }
  return out;
}

/** followers.tsv: header `date time x facebook linkedin youtube`; non-numbers ("not visible") are skipped. */
export function parseFollowers(text: string): { day: string; platform: Platform; value: number }[] {
  const ls = lines(text);
  if (!ls.length) return [];
  const head = ls[0].split("\t").map((h) => h.trim());
  const out = new Map<string, { day: string; platform: Platform; value: number }>();
  for (const l of ls.slice(1)) {
    const cols = l.split("\t");
    const day = cols[0];
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day ?? "")) continue;
    head.forEach((h, i) => {
      if (h !== "x" && h !== "facebook" && h !== "linkedin") return;
      const v = Number((cols[i] ?? "").replace(/,/g, "").trim());
      if ((cols[i] ?? "").trim() !== "" && Number.isFinite(v)) out.set(`${day}|${h}`, { day, platform: h, value: v }); // last row of a day wins
    });
  }
  return [...out.values()];
}

/** Latest "OpenRouter spend: today $x, this week $y, ... key total $z ..." line. */
export function parseSpendLog(text: string): { today: number; week: number; total: number } | null {
  const ls = lines(text).reverse();
  for (const l of ls) {
    const m = l.match(/today \$([\d.]+), this week \$([\d.]+), .*?total \$([\d.]+)/);
    if (m) return { today: Number(m[1]), week: Number(m[2]), total: Number(m[3]) };
  }
  return null;
}

/** client1-state /guard/limits summary: "X: replies 1/12, posts 0/6; LinkedIn: approval-only comments 0/2, posts 0/1; ..." */
export function parseHelperUsage(summary: string): { platform: Platform; kind: "posts" | "replies" | "comments"; used: number; cap: number }[] {
  const out: { platform: Platform; kind: "posts" | "replies" | "comments"; used: number; cap: number }[] = [];
  const names: Record<string, Platform> = { x: "x", linkedin: "linkedin", facebook: "facebook" };
  for (const seg of summary.split(";")) {
    const m = seg.match(/(?:^|\.\s*|\s)(X|LinkedIn|Facebook):\s*(.*)$/);
    if (!m) continue;
    const platform = names[m[1].toLowerCase()];
    for (const part of m[2].split(",")) {
      const k = part.match(/\b(replies|posts|comments)\s+(\d+)\/(\d+)/);
      if (k) out.push({ platform, kind: k[1] as "posts" | "replies" | "comments", used: Number(k[2]), cap: Number(k[3]) });
    }
  }
  return out;
}

/** Minimal yaml-free handle extraction is not needed: callers use the yaml lib. Normalise a handle to {handle,url}. */
export function profileOf(platform: Platform | "website", raw: string): { handle: string; url: string } | null {
  const v = (raw ?? "").trim();
  if (!v) return null;
  if (/^https?:\/\//.test(v)) {
    const seg = v.replace(/\/+$/, "").split("/").pop() ?? v;
    return { handle: platform === "website" ? v.replace(/^https?:\/\//, "").replace(/\/$/, "") : seg, url: v };
  }
  const h = v.replace(/^@/, "");
  if (platform === "x") return { handle: `@${h}`, url: `https://x.com/${h}` };
  if (platform === "linkedin") return { handle: h, url: `https://www.linkedin.com/in/${h}/` };
  if (platform === "facebook") return { handle: h, url: /\s/.test(h) ? "" : `https://www.facebook.com/${h}` };
  return { handle: h, url: "" };
}
