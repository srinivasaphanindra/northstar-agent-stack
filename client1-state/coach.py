"""Warm-up ramps + manual-action coach for the client bot (2026-10-12). Stdlib only, NO database here:
pure functions that server.py (and tests/test_viral.py) call. Times are epoch seconds; IST for anything shown to people.

Ramp: a new account or a new tier starts at step 0 (week 1). Every `step_days` with no warning -> +1 step (up to the last
step). Any warning (captcha, lock, 'unusual activity', limit notice, reported by the worker/drafter/coach or 'report warning x')
drops `drop_steps_on_warning` steps and restarts the clock. Automated caps and the manual budget both follow the step.

Coach: twice a day an 'Action list' of manual tasks for the client (drafts + 'Open in app' links), ✅ done / ⏭ skipped,
reminders before a time-sensitive task's window closes, a friendly note when completion stays low, celebrations, weekly score.
Max `max_nudges_per_day` nudges, nothing in quiet hours.
"""
import json, re, time, urllib.parse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
PLAT = {"x": "X", "linkedin": "LinkedIn", "facebook": "Facebook"}

# ------------------------------------------------------------------ ramps
def ramp_new(now=None):
    return {"step": 0, "since": float(now or time.time()), "last_warning": 0.0, "warnings": 0}

def ramp_steps_len(ramp_cfg):
    n = 1
    for p in (ramp_cfg.get("steps") or {}).values():
        for lst in (p or {}).values():
            if isinstance(lst, list):
                n = max(n, len(lst))
    return max(n, len(ramp_cfg.get("manual_pct") or []) or 1)

def ramp_eval(state, ramp_cfg, now=None):
    """Step up once per full step_days without a warning. Returns (state, changed)."""
    now = float(now or time.time())
    st = dict(state or ramp_new(now))
    if not ramp_cfg.get("enabled"):
        return st, False
    period = float(ramp_cfg.get("step_days", 7)) * 86400
    top = ramp_steps_len(ramp_cfg) - 1
    changed = False
    while st["step"] < top and now - st["since"] >= period:
        st["step"] += 1
        st["since"] += period
        changed = True
    if st["step"] >= top and now - st["since"] >= period:
        st["since"] = now - (now - st["since"]) % period   # keep the clock bounded at the top step
    return st, changed

def ramp_warn(state, ramp_cfg, now=None):
    now = float(now or time.time())
    st = dict(state or ramp_new(now))
    st["step"] = max(0, int(st.get("step", 0)) - int(ramp_cfg.get("drop_steps_on_warning", 1) or 1))
    st["since"], st["last_warning"] = now, now
    st["warnings"] = int(st.get("warnings", 0)) + 1
    return st

def _pick(lst, step):
    if not isinstance(lst, list) or not lst:
        return None
    return lst[min(max(int(step), 0), len(lst) - 1)]

def ramp_cap(eff, platform, metric, step):
    """Today's automated cap: the ramp value for this step, never above the tier ceiling in platforms.<p>.<metric>."""
    ceiling = int(((eff.get("platforms") or {}).get(platform) or {}).get(metric, 0) or 0)
    r = eff.get("ramp") or {}
    if not r.get("enabled"):
        return ceiling
    v = _pick(((r.get("steps") or {}).get(platform) or {}).get(metric), step)
    return ceiling if v is None else min(int(v), ceiling)

def manual_budget(eff, platform, step):
    """The client's own daily action budget for this step (manual_pct of the full budget, at least 1 where the budget is > 0)."""
    full = dict(((eff.get("manual_budget") or {}).get(platform)) or {})
    r = eff.get("ramp") or {}
    pct = _pick(r.get("manual_pct"), step) if r.get("enabled") else 100
    pct = 100 if pct is None else float(pct)
    out = {}
    for k, v in full.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            out[k] = v
        elif k in ("per_hour",):
            out[k] = int(v)
        else:
            out[k] = 0 if v <= 0 else max(1, int(v * pct / 100.0))
    return out

def step_label(eff, step):
    r = eff.get("ramp") or {}
    if not r.get("enabled"):
        return ""
    n = ramp_steps_len(r)
    return f"warm-up week {min(step + 1, n)} of {n}" if step < n - 1 else "fully warmed up"

# ------------------------------------------------------------------ time helpers
def hm(s):
    h, m = str(s).split(":")
    return int(h) * 60 + int(m)

def in_quiet(dt, quiet):
    """quiet = ["22:00", "08:00"] (IST, wraps midnight)."""
    if not quiet or len(quiet) != 2:
        return False
    a, b = hm(quiet[0]), hm(quiet[1])
    t = dt.astimezone(IST).hour * 60 + dt.astimezone(IST).minute
    return (a <= t or t < b) if a > b else (a <= t < b)

def ist_day(ts):
    return datetime.fromtimestamp(ts, IST).strftime("%Y-%m-%d")

def hhmm(ts):
    return datetime.fromtimestamp(ts, IST).strftime("%H:%M")

def parse_age(age, now):
    """'3h', '45m', '2 hours', '1d', 'now' -> posted_at epoch; unknown -> None."""
    a = str(age or "").strip().lower()
    if not a:
        return None
    if a in ("now", "just now"):
        return now
    m = re.match(r"^(\d+)\s*(s|sec|secs|seconds?|m|min|mins|minutes?|h|hr|hrs|hours?|d|days?)\b", a)
    if not m:
        return None
    n, u = int(m.group(1)), m.group(2)[0]
    return now - n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[u]

def fmt_age(sec):
    sec = max(0, int(sec))
    if sec < 3600:
        return f"{max(1, sec // 60)} min"
    h = sec / 3600.0
    return f"{h:.0f}h" if h >= 2 else f"{h:.1f}h".replace(".0h", "h")

# ------------------------------------------------------------------ open-in-app links (all public, documented or widely used share URLs)
def status_id(url):
    m = re.search(r"/status(?:es)?/(\d+)", str(url or ""))
    return m.group(1) if m else ""

def x_reply_link(post_url, text):
    sid = status_id(post_url)
    q = {"in_reply_to": sid, "text": text} if sid else {"text": text}
    return "https://x.com/intent/post?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote)

def x_quote_link(post_url, text):
    return "https://x.com/intent/post?" + urllib.parse.urlencode({"text": text, "url": post_url}, quote_via=urllib.parse.quote)

def li_share_link(url):
    return "https://www.linkedin.com/sharing/share-offsite/?" + urllib.parse.urlencode({"url": url})

def li_search_link(topic):
    return "https://www.linkedin.com/search/results/content/?" + urllib.parse.urlencode({"keywords": topic, "sortBy": '"date_posted"'})

def fb_share_link(url):
    return "https://www.facebook.com/sharer/sharer.php?" + urllib.parse.urlencode({"u": url})

def fb_search_link(topic):
    return "https://www.facebook.com/search/posts/?" + urllib.parse.urlencode({"q": topic})

def x_search_link(query, tab="top"):
    return "https://x.com/search?" + urllib.parse.urlencode({"q": query, "src": "typed_query", "f": tab})

# ------------------------------------------------------------------ task kinds
KINDS = {
    "reply_trend":     {"platform": "x", "label": "Reply to a trending post", "budget": "replies", "units": 1, "timed": True,
                        "why": "early replies on fresh, busy posts are where new people find you"},
    "creator_comment": {"platform": "x", "label": "Comment early on a creator's post", "budget": "replies", "units": 1, "timed": True,
                        "why": "the first 30-60 minutes of a creator's post get most of the eyes"},
    "quote":           {"platform": "x", "label": "Quote post with your take", "budget": "quotes", "units": 1, "timed": False,
                        "why": "a quote puts your view in front of your followers and theirs"},
    "join_thread":     {"platform": "x", "label": "Join a busy thread", "budget": "replies", "units": 1, "timed": True,
                        "why": "threads with lots of replies are live conversations; a useful reply gets read"},
    "answer_own":      {"platform": "x", "label": "Answer replies on your own post", "budget": "replies", "units": 1, "timed": False,
                        "why": "answering the people who reply to you is the cheapest growth there is"},
    "li_comment":      {"platform": "linkedin", "label": "Comment on fresh LinkedIn posts", "budget": "comments", "units": 3, "timed": True,
                        "why": "LinkedIn shows early, thoughtful comments to the author's network"},
    "fb_comment":      {"platform": "facebook", "label": "Comment on Facebook posts in your niche", "budget": "comments", "units": 3, "timed": True,
                        "why": "helpful comments in niche Pages and groups bring profile visits"},
}
TIMED_NOTE = {
    "reply_trend": "replies after about {w} get little reach",
    "creator_comment": "comments after the first {w} rarely get seen",
    "join_thread": "the thread goes quiet after about {w}",
    "li_comment": "LinkedIn posts get most comments in the first {w}",
    "fb_comment": "Facebook posts cool off after about {w}",
}

def _lc(s):
    return s[:1].lower() + s[1:] if s else s

def window_text(minutes):
    return f"{minutes // 60}h" if minutes % 60 == 0 and minutes >= 60 else f"{minutes} min"

def open_link(kind, platform, url, draft):
    if platform == "x":
        if kind == "quote":
            return x_quote_link(url, draft or "")
        if kind in ("reply_trend", "creator_comment", "join_thread") and status_id(url):
            return x_reply_link(url, draft or "")
        return url
    return url

# ------------------------------------------------------------------ messages (warm, short, never naggy)
def list_header(slot, n_tasks, budget_line, step_txt, dry=False):
    when = "morning" if slot == "morning" else ("evening" if slot == "evening" else "extra")
    mins = max(5, n_tasks * 3)
    return (f"*Your {when} action list* · {n_tasks} task{'s' if n_tasks != 1 else ''} · about {mins} min\n"
            f"These are things only you can do: real replies and comments from you grow the account faster and more safely than any bot. "
            f"Each task has ready drafts and an *Open* link. React ✅ when done or ⏭ to skip, no pressure.\n"
            f"_Today's room: {budget_line}{(' · ' + step_txt) if step_txt else ''}_" + ("\n_(dry run)_" if dry else ""))

def task_message(t, i, n):
    k = KINDS.get(t["kind"], {})
    head = f"*{i}/{n} · {PLAT.get(t['platform'], t['platform'])} · {k.get('label', t['kind'])}*"
    if t.get("deadline"):
        head += f"  ⏱ best before {hhmm(t['deadline'])} IST"
    lines = [head]
    if t.get("excerpt"):
        who = f"@{t['handle']}" if t.get("handle") else "Post"
        age = f" ({t['age_txt']} old)" if t.get("age_txt") else ""
        lines.append(f"> {who}{age}: {t['excerpt']}")
    elif t.get("title"):
        lines.append(t["title"])
    for j, d in enumerate(t.get("drafts") or []):
        lines.append(f"*Draft {'AB'[j] if j < 2 else j + 1}:* {d}")
    if t.get("tip"):
        lines.append(f"_Tip:_ {t['tip']}")
    links = []
    if t.get("open_url"):
        links.append(f"<{t['open_url']}|Open in {PLAT.get(t['platform'], 'app')}{' with draft A' if t.get('drafts') and t['open_url'] != t.get('url') else ''}>")
    if t.get("url") and t.get("url") != t.get("open_url"):
        links.append(f"<{t['url']}|open the post>" if t["platform"] == "x" else f"<{t['url']}|open>")
    if t.get("share_url"):
        links.append(f"<{t['share_url']}|share>")
    if links:
        lines.append(" · ".join(links))
    lines.append(f"_Why: {k.get('why', '')}._ React ✅ done · ⏭ skip")
    return "\n".join(lines)

def reminder_text(tasks, now):
    """One friendly message for 1+ time-sensitive tasks whose window is about to close."""
    if len(tasks) == 1:
        t = tasks[0]
        w = window_text(int(t.get("window_min") or 180))
        note = TIMED_NOTE.get(t["kind"], "the window closes soon").format(w=w)
        what = {"reply_trend": "this trend", "creator_comment": "this creator's post", "join_thread": "this thread",
                "li_comment": "the LinkedIn comments", "fb_comment": "the Facebook comments"}.get(t["kind"], "this task")
        when = (f"{what} is ~{fmt_age(now - t['posted_at'])} old" if t.get("posted_at")
                else f"the window for {what} closes at {hhmm(t['deadline'])} IST")
        link = t.get("open_url") or t.get("url")
        return (f":hourglass_flowing_sand: Quick one if you have 2 minutes: {when}; {note}."
                + (f" <{link}|Open it with the draft>." if link else "")
                + " React ✅ on the task when done, or ⏭ if today's not the day. Totally fine either way.")
    items = "; ".join(f"{_lc(KINDS.get(t['kind'], {}).get('label', t['kind']))} (until {hhmm(t['deadline'])})" for t in tasks[:4])
    return (f":hourglass_flowing_sand: {len(tasks)} time-sensitive tasks close soon: {items}. Fresh posts reward early replies, later ones get little reach. "
            f"The drafts are ready in the list; ✅ or ⏭ each one, whatever fits your day.")

def low_completion_text(days, missed_kinds):
    tot_s = sum(d["suggested"] for d in days); tot_d = sum(d["done"] for d in days)
    miss = " and ".join(_lc(KINDS.get(k, {}).get("label", k)) for k in missed_kinds[:2]) or "the early replies"
    return (f":seedling: Checking in: over the last {len(days)} days {tot_d} of {tot_s} suggested tasks got done. Totally normal on busy days. "
            f"What you're missing in plain words: {miss} are what put your name in front of new people, and those windows are short. "
            f"Even 3 tasks a day keeps the momentum. If the lists feel long, say 'shorter lists' and I'll trim them.")

def streak_text(n):
    return f":fire: {n} days in a row with most of your action list done. That consistency is exactly what the algorithms reward. Nice work!"

def win_text(platform, before, after, pct):
    return (f":tada: Nice jump on {PLAT.get(platform, platform)}: followers {before} → {after} (+{pct:.1f}%) since the last check. "
            f"Your replies and comments are working. Keep going!")

def list_done_text(slot):
    return f":white_check_mark: All of the {slot} list done. Great work, that's the stuff that compounds."

# ------------------------------------------------------------------ nudge decisions (pure)
def due_reminders(tasks, now, remind_before_min):
    """Open, timed, not yet reminded tasks whose window closes within remind_before (and at least 10 min left)."""
    out = []
    for t in tasks:
        if t.get("state") != "open" or t.get("nudged") or not t.get("deadline"):
            continue
        if not KINDS.get(t["kind"], {}).get("timed"):
            continue
        left = t["deadline"] - now
        if 10 * 60 <= left <= remind_before_min * 60:
            out.append(t)
    return sorted(out, key=lambda t: t["deadline"])

def day_rates(rows):
    """rows: [{day, suggested, done}] -> with pct."""
    return [{**r, "pct": (100.0 * r["done"] / r["suggested"]) if r["suggested"] else None} for r in rows]

def low_completion(days, pct_floor, n_days):
    """days: last full days, newest first. True when the last n_days all had tasks and were below pct_floor."""
    d = [x for x in days[:n_days]]
    return len(d) == n_days and all(x["suggested"] > 0 and x["pct"] is not None and x["pct"] < pct_floor for x in d)

def streak_len(days, pct_min):
    """days newest first (today may be partial and is NOT included by the caller)."""
    n = 0
    for x in days:
        if x["suggested"] > 0 and x["pct"] is not None and x["pct"] >= pct_min:
            n += 1
        else:
            break
    return n

def follower_win(prev, cur, jump_pct):
    if not prev or not cur or prev <= 0 or cur <= prev:
        return None
    pct = 100.0 * (cur - prev) / prev
    return pct if pct >= jump_pct else None

TIPS = {
    "reply_trend": "Do the trending-post replies first when the list arrives: they only work in the first 2-3 hours.",
    "creator_comment": "Turn on notifications for 3-5 creators in your niche so you can comment in their first 30 minutes.",
    "quote": "Quote posts work best with one clear opinion in the first line; one a day is plenty.",
    "join_thread": "In busy threads, reply to the top reply with a specific question; it gets read more than a new comment at the bottom.",
    "answer_own": "Answer every reply on your own posts within a few hours: it doubles the reach of the post.",
    "li_comment": "On LinkedIn, 3 thoughtful comments in the first hour of fresh posts beat 20 likes.",
    "fb_comment": "On Facebook, a helpful answer in a niche Page or group beats a generic 'great post'.",
}

def coordination(rows):
    """rows: [{kind, state}] for the week -> (done, suggested, pct, weakest_kind, tip)."""
    sug = [r for r in rows if r["state"] in ("open", "done", "skipped", "expired")]
    done = [r for r in sug if r["state"] == "done"]
    pct = (100.0 * len(done) / len(sug)) if sug else None
    by = {}
    for r in sug:
        s = by.setdefault(r["kind"], [0, 0]); s[1] += 1
        if r["state"] == "done": s[0] += 1
    cand = [(v[0] / v[1], -v[1], k) for k, v in by.items() if v[1] >= 2]
    weakest = sorted(cand)[0][2] if cand else None
    if pct is None:
        tip = "No action lists this week yet. When they start, doing just the first 3 tasks each morning makes the biggest difference."
    elif pct >= 80:
        tip = "Excellent week. Keep the same rhythm; quality of each reply matters more than adding more."
    else:
        tip = TIPS.get(weakest, "Start with the time-sensitive tasks (⏱) when a list arrives; the rest can wait.")
    return len(done), len(sug), pct, weakest, tip
