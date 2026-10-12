"""Hourly approval batches (2026-10-12). Stdlib only, NO database here: pure functions that server.py and
tests/test_batches.py call. Times are epoch seconds; IST for anything shown to people.

During the client's active hours (default 09:00-21:00 IST) about once every `every_minutes` (Viral 60, Starter 180) the bot
posts ONE Slack message with a small batch (Viral 2-4 items, Starter 1-2) of ready drafts: posts, X replies and
LinkedIn/Facebook comments with target links. Each item sits in the batch thread with ✅ / ✏️ / ❌. Approved items go into
the paced schedule queue (caps, gaps, hourly limits, active hours); posts go to Buffer's queue (its good times), engagement
is done by the browser agent. A batch is skipped when the last `skip_after_ignored` batches got no reaction, with at most
`max_still_there_per_day` gentle 'still there?' a day.
"""
import math, random, re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
PLAT = {"x": "X", "linkedin": "LinkedIn", "facebook": "Facebook"}

def hm(s):
    h, m = str(s).split(":")
    return int(h) * 60 + int(m)

def ist(ts):
    return datetime.fromtimestamp(float(ts), IST)

def in_active(ts, hours):
    """hours = ["09:00", "21:00"] IST (start inclusive, end exclusive; may wrap midnight)."""
    if not hours or len(hours) != 2:
        return True
    a, b = hm(hours[0]), hm(hours[1]); d = ist(ts); t = d.hour * 60 + d.minute
    return (a <= t < b) if a < b else (t >= a or t < b)

def next_active_start(ts, hours):
    """The next moment >= ts inside the active window (ts itself if already inside)."""
    if in_active(ts, hours):
        return float(ts)
    a = hm(hours[0]); d = ist(ts)
    start = d.replace(hour=a // 60, minute=a % 60, second=0, microsecond=0)
    if start.timestamp() <= ts:
        start += timedelta(days=1)
    return start.timestamp()

def parse_start_at(s):
    s = str(s or "").strip()
    if not s:
        return 0.0
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=IST).timestamp()
    except ValueError:
        return float("inf")      # unparseable -> never live (fail safe)

def next_due(last_ts, every_min, jitter_min, rnd=None):
    """Next batch time after a batch (or a skipped slot) at last_ts: every_min + random 0..jitter_min."""
    r = rnd if rnd is not None else random.random()
    return float(last_ts) + every_min * 60 + int(r * (jitter_min * 60 + 1))

def slots_left_today(now, hours, every_min):
    """How many batch slots are still possible today inside the active window (including one now)."""
    if not hours or len(hours) != 2:
        return max(1, int((24 * 60) // every_min))
    d = ist(now); t = d.hour * 60 + d.minute
    a, b = hm(hours[0]), hm(hours[1])
    end = b if a < b else b + 24 * 60
    if t < a and a < b:
        t = a
    return max(1, int(math.floor(max(end - t, 0) / every_min)) + 1) if t < end else 0

def ignored_streak(batches):
    """batches: newest first, each {answered: bool}. Number of most recent consecutive batches with no reaction."""
    n = 0
    for b in batches:
        if b.get("answered"):
            break
        n += 1
    return n

def decide(now, bc, state):
    """Pure batch-timing decision.
    bc = batches config; state = {last_batch_ts, next_due, streak, still_there_today, live_ok, paused}
    Returns (action, reason): action in run | wait | skip_ignored | still_there."""
    if not bc.get("enabled"):
        return "wait", "batches are off on this plan"
    if state.get("paused"):
        return "wait", "batches paused ('resume batches' turns them back on)"
    if not state.get("live_ok", True):
        return "wait", state.get("live_reason") or "batches not live yet"
    hours = bc.get("active_hours") or ["09:00", "21:00"]
    if not in_active(now, hours):
        return "wait", f"outside active hours ({'-'.join(hours)} IST)"
    nd = float(state.get("next_due") or 0)
    if nd and now < nd:
        return "wait", f"next batch due at {ist(nd):%H:%M} IST"
    if state.get("streak", 0) >= int(bc.get("skip_after_ignored", 2) or 2):
        if state.get("still_there_today", 0) < int(bc.get("max_still_there_per_day", 1)):
            return "still_there", f"the last {state['streak']} batches got no reaction"
        return "skip_ignored", f"the last {state['streak']} batches got no reaction (already asked 'still there?' today)"
    return "run", ""

def batch_size(bc, remaining_total, slots_left):
    """Spread what is left today over the remaining slots: between items_min and items_max (0 if nothing left)."""
    lo, hi = int(bc.get("items_min", 2)), int(bc.get("items_max", 4))
    if remaining_total <= 0:
        return 0
    per = int(math.ceil(remaining_total / max(slots_left, 1)))
    return max(min(lo, remaining_total), min(hi, max(per, lo), remaining_total))

def allocate(size, room, max_posts=1, post_order=None):
    """room = {'post': {'x': n, ...}, 'reply': n, 'comment': {'linkedin': n, 'facebook': n}} -> list of slots.
    Round-robin: one post at most (max_posts), then replies and comments alternating, so a batch is a mix."""
    out = []
    posts = dict(room.get("post") or {}); comments = dict(room.get("comment") or {}); replies = int(room.get("reply") or 0)
    order = [p for p in (post_order or ["x", "linkedin", "facebook"]) if posts.get(p, 0) > 0]
    n_posts = 0
    while len(out) < size:
        progressed = False
        if replies > 0 and len(out) < size:
            out.append({"kind": "reply", "platform": "x"}); replies -= 1; progressed = True
        for p in ("linkedin", "facebook"):
            if comments.get(p, 0) > 0 and len(out) < size:
                out.append({"kind": "comment", "platform": p}); comments[p] -= 1; progressed = True
        if n_posts < max_posts and order and len(out) < size:
            p = order.pop(0); out.append({"kind": "post", "platform": p}); n_posts += 1; progressed = True
        if not progressed:
            break
    return out

def daily_browser_room(cap, used_before_today, used_today, day_of_month, days_in_month):
    """Pace the monthly browser allowance: today may use (left at the start of today) / (days left incl. today)."""
    left_start = max(cap - used_before_today, 0)
    days_left = max(days_in_month - day_of_month + 1, 1)
    allow = int(math.ceil(left_start / days_left)) if left_start else 0
    return max(allow - used_today, 0), allow

def progress_line(prog, step_txt=""):
    """prog = {'x': {'replies': (done, cap), 'posts': (done, cap)}, 'linkedin': {'comments': ..., 'posts': ...}, ...}"""
    parts = []
    for p in ("x", "linkedin", "facebook"):
        for k in ("replies", "comments", "posts"):
            v = (prog.get(p) or {}).get(k)
            if v and v[1]:
                parts.append(f"{PLAT[p]} {k} {v[0]}/{v[1]}")
    s = "today: " + (" · ".join(parts) if parts else "nothing scheduled yet")
    return s + (f" · {step_txt}" if step_txt else "")

def short(s, n=110):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"

LABEL = {"reply": "reply", "comment": "comment", "post": "post"}

def item_title(it):
    p = PLAT.get(it["platform"], it["platform"])
    if it["kind"] == "reply":
        return f"{p} reply to @{it.get('handle') or 'post'}"
    if it["kind"] == "comment":
        return f"{p} comment"
    return f"{p} post"

def header_text(items, progress, now, cadence_txt, dry=False):
    lines = [f"*Drafts for your ✅* · {len(items)} item{'s' if len(items) != 1 else ''} · {ist(now):%H:%M} IST"]
    for it in items:
        tgt = f" on <{it['url']}|this post>" if it.get("url") and it["kind"] != "post" else ""
        lines.append(f"{it['n']}. {item_title(it)}{tgt}: “{short(it['text'])}”")
    lines.append("Each item is in the thread with ✅ approve · ✏️ edit · ❌ skip (or type `1 ✅`, `2 ❌`, `approve all`, `3: new wording`). "
                 "Approved items go into a paced queue (caps, gaps, hourly limits); posts go to Buffer's next good slot. Nothing goes out without your ✅.")
    lines.append(f"_{progress}_ · _{cadence_txt}_" + ("\n_(dry run)_" if dry else ""))
    return "\n".join(lines)

def item_text(it):
    head = f"*{it['n']} · {item_title(it)}*"
    lines = [head]
    if it.get("excerpt"):
        lines.append(f"> {short(it['excerpt'], 220)}")
    if it.get("url") and it["kind"] != "post":
        lines.append(f"Target: <{it['url']}|open the post>")
    lines.append(f"*Draft:* {it['text']}")
    if it.get("why"):
        lines.append(f"_Why:_ {short(it['why'], 120)}")
    lines.append("React ✅ to do exactly this once (paced, by the browser agent) · ✏️ edit (then type `" + str(it['n']) + ": new wording`) · ❌ skip")
    return "\n".join(lines)

def still_there_text(streak, cadence_txt):
    return (f":wave: Still there? The last {streak} draft batches got no reaction, so I'm holding the next ones (nothing went out). "
            f"No rush: react to any item above, or say `next batch` and I'll pick up again ({cadence_txt}). "
            f"This is the only reminder today.")

def cadence_text(bc):
    m = int(bc.get("every_minutes", 60))
    every = "about every hour" if m <= 60 else f"about every {m // 60} hours" if m % 60 == 0 else f"about every {m} min"
    return f"next batches {every}, {'-'.join(bc.get('active_hours') or ['09:00', '21:00'])} IST"

BATCH_CMD_VERB_OK = r"(?:approve|ok|yes|✅|:white_check_mark:|:heavy_check_mark:)"
BATCH_CMD_VERB_NO = r"(?:skip|discard|no|❌|:x:)"

def parse_batch_text(text):
    """Typed commands in a batch thread (mirrors the poller's JS). Returns (action, numbers|'all', new_text) or None.
    action: approve | discard | edit."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    low = t.lower()
    m = re.match(r"^(\d)\s*[:.)]\s*(.{3,})$", t)
    if m:
        return ("edit", [int(m.group(1))], m.group(2).strip())
    if re.match(rf"^({BATCH_CMD_VERB_OK}\s+all|all\s+{BATCH_CMD_VERB_OK})\s*[!.]*$", low):
        return ("approve", "all", "")
    if re.match(rf"^({BATCH_CMD_VERB_NO}\s+all|all\s+{BATCH_CMD_VERB_NO})\s*[!.]*$", low):
        return ("discard", "all", "")
    nums = r"((?:\d\s*(?:,|and|&|\s)\s*)*\d)"
    for verb, act in ((BATCH_CMD_VERB_OK, "approve"), (BATCH_CMD_VERB_NO, "discard")):
        m = re.match(rf"^{verb}\s*{nums}\s*[!.]*$", low) or re.match(rf"^{nums}\s*{verb}\s*[!.]*$", low)
        if m:
            return (act, sorted({int(x) for x in re.findall(r"\d", m.group(1))}), "")
    return None
