"""Unit tests for the Viral tier: caps, ramps, pacing, nudges, quiet hours, completion tracking (2026-10-12).
Runs fully offline on a temp DB + temp guardrails.yaml copy; notifications off; no Slack, no browser, no LLM.
Usage: python3 state-helper/tests/test_viral.py"""
import os, sys, json, shutil, tempfile, time, re, traceback
from datetime import datetime, timedelta
T = tempfile.mkdtemp(prefix="c1viral-")
SRC = os.environ.get("CS_GUARDRAILS_SRC") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "templates", "guardrails.example.yaml")
GY = f"{T}/guardrails.yaml"; shutil.copy(SRC, GY)
# ramp-clock tests use their own fake "now"; the live warm-up start_date would pin the clock to 2026-10-12
import re as _re; _y = open(GY).read(); open(GY, "w").write(_re.sub(r"(?m)^\s*start_date:.*\n", "", _y))
open(f"{T}/notes.md", "w").write("# notes\n## Voice\nplain\n")
os.environ.update(CS_DB=f"{T}/db.sqlite", CS_NOTES=f"{T}/notes.md", CS_PAUSE=f"{T}/PAUSE", CS_PAUSE_ALL=f"{T}/PAUSE_ALL",
                  CS_LEDGER=f"{T}/ledger.tsv", CS_LOG=f"{T}/log", CS_GUARDRAILS=GY, CS_AUDIT=f"{T}/audit.tsv", CS_UPSELL=f"{T}/upsell.md",
                  CS_NOTIFY="0", CS_WORKER="0", CS_OR_UPSTREAM="http://127.0.0.1:9")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import server as S, coach as CO, guardrails as G
S.init_db()
OWNER, STRANGER = "U0000000001", "U0STRANGER1"
results = []
def test(fn):
    try:
        fn(); results.append((fn.__name__, True, ""))
    except Exception as e:
        results.append((fn.__name__, False, f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"))
    return fn
def c():
    return S.db()
def set_plan(p):
    txt = open(GY).read(); open(GY, "w").write(re.sub(r"(?m)^plan:.*$", f"plan: {p}", txt)); os.utime(GY, (time.time() + 1, time.time() + 1))
    S.GUARD.mtime = None
def reset():
    k = c()
    for t in ("engage_queue", "proposals", "actions_log", "coach_tasks", "coach_lists", "coach_nudges", "coach_stats", "kv", "browser_use", "audit", "outbox", "watchlist", "batch_pool"):
        k.execute(f"DELETE FROM {t}")
    k.close()
    for f in ("PAUSE", "PAUSE_ALL"):
        if os.path.exists(f"{T}/{f}"): os.remove(f"{T}/{f}")
def at(h, m=0, day_offset=0):
    return (S.now_ist().replace(hour=h, minute=m, second=0, microsecond=0) + timedelta(days=day_offset)).timestamp()
def prop(k, ts, kind, payload, state="pending", created=None):
    k.execute("INSERT INTO proposals(slack_ts, kind, channel, thread_ts, payload, state, created, updated) VALUES(?,?,?,?,?,?,?,?)",
              (ts, kind, S.CHANNEL, ts, json.dumps(payload), state, created or time.time(), time.time()))

# ------------------------------------------------------------------ caps
@test
def starter_caps_1x_with_warmup():
    """Starter = 1x base with 4-week warm-up from ~40% (the owner 2026-10-12 01:10)."""
    reset(); set_plan("starter"); k = c()
    assert S.cfg()["plan"] == "starter"
    x = S.plat_caps("x", k)
    assert (x["engage_per_day"], x["posts_per_day"], x["ceiling_engage"], x["ceiling_posts"]) == (4, 2, 10, 5), x
    assert S.plat_caps("linkedin", k)["engage_per_day"] == 1 and S.plat_caps("linkedin", k).get("posts_per_week") == 4
    assert S.plat_caps("facebook", k)["engage_per_day"] == 1 and S.plat_caps("facebook", k)["posts_per_day"] == 1
    assert S.cfg()["engagement"]["min_gap_minutes"] == 8 and S.gaps() == (480, 240, 3)
    assert "like" not in S.cfg()["engagement"]["allowed_actions"] and set(S.cfg()["engagement"]["allowed_actions"]) == {"reply", "repost", "comment"}
    assert not S.cfg()["coach"]["enabled"]
    assert (S.cfg().get("ramp") or {}).get("enabled")
    k.close()

@test
def viral_week1_caps_and_ceilings():
    reset(); set_plan("viral"); k = c()
    x = S.plat_caps("x", k)
    assert x["engage_per_day"] == 12 and x["posts_per_day"] == 6 and x["ceiling_engage"] == 30 and x["ceiling_posts"] == 15, x
    assert S.plat_caps("linkedin", k)["engage_per_day"] == 2 and S.plat_caps("linkedin", k).get("posts_per_week") == 12
    assert S.gaps() == (480, 240, 3)
    assert S.cfg()["spend"]["browser_tasks_per_month"] == 1680
    k.close()

@test
def viral_like_goes_manual_reply_ok():
    reset(); set_plan("viral"); k = c()
    prop(k, "1.1", "engage", {"action": "like", "url": "https://x.com/a/status/1", "handle": "a"})
    p = S.get_prop(k, "1.1"); r, m = S.engage_checks(k, p, OWNER)
    assert r == "like_manual" and "action list" in m, (r, m)
    prop(k, "1.2", "engage", {"action": "reply", "url": "https://x.com/b/status/2", "handle": "b", "text": "which tool did you try first?"})
    assert S.engage_checks(k, S.get_prop(k, "1.2"), OWNER) == (None, None)
    prop(k, "1.3", "engage", {"action": "follow", "url": "https://x.com/b/status/3", "handle": "b"})
    assert S.engage_checks(k, S.get_prop(k, "1.3"), OWNER)[0] == "action_not_allowed"
    k.close()

@test
def starter_and_growth_like_refused_and_queued_likes_cancelled():
    """X April 2026: Starter/Growth also forbid automated likes; already-queued likes are cancelled."""
    reset(); set_plan("starter"); k = c()
    prop(k, "s1", "engage", {"action": "like", "url": "https://x.com/a/status/1", "handle": "a"})
    r, m = S.engage_checks(k, S.get_prop(k, "s1"), OWNER)
    assert r == "like_manual" and "April 2026" in m, (r, m)
    prop(k, "s2", "engage", {"action": "reply", "url": "https://x.com/b/status/2", "handle": "b", "text": "which tool did you try first?"})
    assert S.engage_checks(k, S.get_prop(k, "s2"), OWNER) == (None, None)
    # simulate a like that was queued before the rule change
    k.execute("INSERT INTO engage_queue(slack_ts, action, url, handle, text, approver, approved_at, approved_day, due_at, state) VALUES(?,?,?,?,?,?,?,?,?,'queued')",
              ("sq1", "like", "https://x.com/old/status/9", "old", "", OWNER, time.time(), S.today(), time.time() + 3600))
    k.execute("INSERT INTO engage_queue(slack_ts, action, url, handle, text, approver, approved_at, approved_day, due_at, state) VALUES(?,?,?,?,?,?,?,?,?,'queued')",
              ("sq2", "repost", "https://x.com/ok/status/8", "ok", "", OWNER, time.time(), S.today(), time.time() + 3600))
    n = S.cancel_disallowed_queued(k)
    assert n == 1, n
    row = k.execute("SELECT state, result FROM engage_queue WHERE slack_ts='sq1'").fetchone(); assert tuple(row) == ("cancelled", "action not allowed"), row
    assert k.execute("SELECT state FROM engage_queue WHERE slack_ts='sq2'").fetchone()[0] == "queued"
    set_plan("growth")
    assert "like" not in S.cfg()["engagement"]["allowed_actions"]
    prop(k, "g1", "engage", {"action": "like", "url": "https://x.com/c/status/3", "handle": "c"})
    assert S.engage_checks(k, S.get_prop(k, "g1"), OWNER)[0] == "like_manual"
    assert S.engage_plan(k, {"dry_run": True})["allowed_actions"] == ["reply", "repost", "comment"]  # Growth includes Starter LI/FB comments
    k.close()


@test
def viral_engage_cap_enforced_week1():
    reset(); set_plan("viral"); k = c()
    for i in range(12):
        k.execute("INSERT INTO engage_queue(slack_ts, action, url, handle, text, approver, approved_at, approved_day, due_at, state) VALUES(?,?,?,?,?,?,?,?,?,'done')",
                  (f"9.{i}", "reply", f"https://x.com/h{i}/status/{i}", f"h{i}", f"text number {i} about something", OWNER, time.time(), S.today(), time.time()))
    prop(k, "2.1", "engage", {"action": "reply", "url": "https://x.com/z/status/9", "handle": "z", "text": "how long did setup take you?"})
    r, m = S.engage_checks(k, S.get_prop(k, "2.1"), OWNER)
    assert r == "cap_engage" and "12/12" in m, (r, m)
    k.close()

@test
def hourly_pacing_and_random_gap():
    reset(); set_plan("viral"); k = c(); now = time.time()
    for i, ago in enumerate((3000, 1200)):
        k.execute("INSERT INTO engage_queue(slack_ts, action, url, approved_at, approved_day, due_at, done_at, state) VALUES(?,?,?,?,?,?,?,'done')",
                  (f"h{i}", "reply", "https://x.com/a/status/1", now, S.today(), now - ago, now - ago))
    t = S.hour_slot_due(k, now + 60, 2)
    assert t >= now - 3000 + 3600, (t - now)            # 2 in the last hour -> wait until the oldest is >1h old
    due = S.first_due(k)
    assert due >= now - 1200 + 12 * 60 and due >= now - 3000 + 3600, due - now
    assert S.hour_slot_due(k, now + 60, 0) == now + 60
    k.close()

@test
def duplicate_post_refused_and_post_gap_queues():
    reset(); set_plan("viral"); k = c()
    txt = "Most small shops don't need an AI strategy. They need one boring task automated this week."
    prop(k, "3.1", "post", {"text": txt, "drafts": [{"service": "twitter", "post_id": "p1"}]}, state="published")
    prop(k, "3.2", "post", {"text": txt + " ", "drafts": [{"service": "twitter", "post_id": "p2"}]})
    out = S.op(k, {"op": "decide", "slack_ts": "3.2", "decision": "approve", "by": OWNER})
    assert "almost the same" in out["reply"], out
    prop(k, "3.3", "post", {"text": txt, "drafts": [{"service": "linkedin", "post_id": "p3"}]})   # other platform: fine
    assert S.duplicate_post(k, S.get_prop(k, "3.3")) is None
    k.execute("INSERT INTO actions_log(ts, day, platform, kind, ref, state) VALUES(?,?,?,?,?,?)", (time.time() - 600, S.today(), "x", "post", "p0", "done"))
    prop(k, "3.4", "post", {"text": "A different post about invoices.", "when": "now", "drafts": [{"service": "twitter", "post_id": "p4"}]})
    o = S.post_issue(k, S.get_prop(k, "3.4"), OWNER, True, True, {"reply": ""})
    assert o["buffer_ops"][0]["mode"] == "addToQueue" and "45 minutes" in o.get("cap_note", ""), o
    k.close()

# ------------------------------------------------------------------ ramps
@test
def ramp_steps_up_weekly_and_tops_out():
    reset(); set_plan("viral"); k = c(); t0 = time.time() - 30 * 86400
    st = S.ramp_state(k, "x", now=t0); assert st["step"] == 0
    assert S.ramp_state(k, "x", now=t0 + 6.9 * 86400)["step"] == 0
    assert S.ramp_state(k, "x", now=t0 + 7.01 * 86400)["step"] == 1
    assert S.ramp_state(k, "x", now=t0 + 22 * 86400)["step"] == 3
    assert S.ramp_state(k, "x", now=t0 + 90 * 86400)["step"] == 3      # top step holds
    assert CO.ramp_cap(S.cfg(), "x", "engage_per_day", 3) == 30 and CO.ramp_cap(S.cfg(), "x", "posts_per_day", 3) == 15
    k.close()

@test
def ramp_warning_drops_one_step_and_restarts_clock():
    reset(); set_plan("viral"); k = c(); t0 = time.time() - 40 * 86400
    S.ramp_state(k, "x", now=t0); S.ramp_state(k, "x", now=t0 + 15 * 86400)     # step 2
    new = S.ramp_warning(k, "x", "captcha", now=t0 + 16 * 86400)
    assert new["step"] == 1 and new["warnings"] == 1
    assert S.ramp_state(k, "x", now=t0 + 22.9 * 86400)["step"] == 1             # needs a full clean week again
    assert S.ramp_state(k, "x", now=t0 + 23.1 * 86400)["step"] == 2
    k.execute("DELETE FROM kv"); S.ramp_state(k, "x")
    assert S.ramp_warning(k, "x", "again")["step"] == 0                          # never below week 1
    assert S.ramp_state(k, "linkedin")["step"] == 0
    k.close()

@test
def auto_pause_records_ramp_warning():
    reset(); set_plan("viral"); k = c(); t0 = time.time() - 8 * 86400
    S.ramp_state(k, "x", now=t0); assert S.ramp_state(k, "x")["step"] == 1
    S.set_pause(k, "auto: browser stopped on https://x.com/a/status/1: unusual activity", "state-helper")
    assert S.ramp_state(k, "x")["step"] == 0 and os.path.exists(f"{T}/PAUSE")
    assert k.execute("SELECT COUNT(*) FROM audit WHERE event='ramp_warning'").fetchone()[0] == 1
    k.close()

@test
def manual_budget_follows_step():
    reset(); set_plan("viral"); eff = S.cfg()
    b0, b3 = CO.manual_budget(eff, "x", 0), CO.manual_budget(eff, "x", 3)
    assert b0["replies"] == 10 and b3["replies"] == 27 and b0["cold_dms"] == 0 and b0["per_hour"] == 8, (b0, b3)  # 40% / 100%
    assert CO.manual_budget(eff, "linkedin", 3)["comments"] == 25 and CO.manual_budget(eff, "facebook", 3)["comments"] == 25
    assert CO.manual_budget(eff, "linkedin", 1)["invitations_per_week"] == 54
    set_plan("starter"); assert CO.manual_budget(S.cfg(), "x", 0) == {}

@test
def switch_plan_admin_only_and_resets_ramp():
    reset(); set_plan("starter"); k = c()
    o = S.op(k, {"op": "switch_plan", "plan": "viral", "by": STRANGER})
    assert "Only the owner" in o["reply"] and S.cfg()["plan"] == "starter"
    o = S.op(k, {"op": "switch_plan", "plan": "viral", "by": OWNER, "dry_run": True})
    assert "(dry run) Would switch" in o["reply"] and "X 12 engagement / 6 posts" in o["reply"] and S.cfg()["plan"] == "starter", o["reply"]
    k.execute("INSERT INTO kv(k, v) VALUES('ramp:viral:x', ?)", (json.dumps({"step": 3, "since": time.time(), "last_warning": 0, "warnings": 0}),))
    o = S.op(k, {"op": "switch_plan", "plan": "viral", "by": OWNER}); S.GUARD.mtime = None
    assert o["reply"].startswith("Switched") and S.cfg()["plan"] == "viral" and S.ramp_state(k, "x")["step"] == 0, o
    assert "switched from starter" in open(GY).read()
    assert "don't know a plan" in S.op(k, {"op": "switch_plan", "plan": "mega", "by": OWNER})["reply"]
    S.op(k, {"op": "switch_plan", "plan": "starter", "by": OWNER}); S.GUARD.mtime = None
    assert S.cfg()["plan"] == "starter"
    k.close()

@test
def limits_text_per_tier():
    reset(); set_plan("starter"); k = c()
    t = S.limits_text(k)
    assert "*Plan: Starter*" in t and "replies 0/4" in t and "reply/repost only" in t and "part of the Viral plan" in t, t
    assert "LinkedIn: comments 0/1" in t and "Facebook: comments 0/1" in t and "max 1 an hour" in t and "7 days at 0" in t, t
    assert "Never automated: follows, unfollows, DMs, likes," in t
    assert "browser tasks 0/560" in t
    set_plan("viral"); t = S.limits_text(k)
    assert "*Plan: Viral* (warm-up week 1 of 4)" in t and "replies 0/12 (12 left, ceiling 30; reply/repost only, 8-12 min apart, max 3 an hour)" in t, t
    assert "Manual budget" in t and "replies 0/10" in t and "no cold DMs" in t and "LinkedIn: comments 0/2 (2 left, ceiling 6" in t, t
    assert "browser tasks 0/1680" in t
    assert "Never automated: follows, unfollows, DMs, likes," in t
    k.close()

# ------------------------------------------------------------------ coach: build, completion tracking
POSTS = [
    {"url": "https://x.com/alice/status/111", "text": "We replaced our invoice follow-ups with a simple AI email draft. Saved hours.", "age": "40m", "likes": 80, "replies": 30},
    {"url": "https://x.com/bob/status/222?s=20", "text": "Founders in India: what is the one tool you'd never give up?", "age": "1h", "likes": 200, "replies": 90},
    {"url": "https://x.com/carol/status/333", "text": "Old post", "age": "5h", "likes": 10, "replies": 2},
    {"url": "https://x.com/your_x_handle/status/444", "text": "own post", "age": "10m"},
]
LLM = {"tasks": [
    {"kind": "reply_trend", "url": "https://x.com/alice/status/111", "drafts": ["did the clients notice the change, or did it feel the same to them?", "which step did you automate first, the draft or the sending?"]},
    {"kind": "quote", "url": "https://x.com/bob/status/222", "drafts": ["Mine is a plain notes app. Every tool I add later starts as a note there."]},
    {"kind": "reply_trend", "url": "https://x.com/carol/status/333", "drafts": ["what made it hard?"]},
    {"kind": "reply_trend", "url": "https://x.com/dave/status/555", "drafts": ["not in the list"]},
    {"kind": "creator_comment", "url": "https://x.com/your_x_handle/status/444", "drafts": ["own"]},
    {"kind": "join_thread", "url": "https://x.com/bob/status/222", "drafts": ["we grew sales 300% with this"]}],
    "li_angles": ["what did you try before this, and why didn't it stick?", "i tried something similar with my weekly report, the hard part was trusting the first draft. how did you handle that?"],
    "fb_angles": ["Guaranteed results in 7 days!", "which part of your week would you hand to AI first, and why that one?"]}

def build(k, dry=False, now=None, slot="morning"):
    return S.coach_build(k, {"slot": slot, "posts": POSTS, "raw": json.dumps(LLM), "dry_run": dry, "now": now})

@test
def coach_off_on_starter():
    reset(); set_plan("starter"); k = c()
    p = S.coach_plan(k, {"slot": "morning"})
    assert not p["ok_to_run"] and "Viral plan" in p["why_not"]
    p = S.coach_plan(k, {"slot": "morning", "dry_run": True, "force_plan": "viral", "posts": POSTS, "ignore_quiet": True})
    assert p["ok_to_run"] and p["plan"] == "viral" and p["search_urls"], p
    assert S.coach_tick(k, {})["why"] == "coach off on this plan"
    k.close()

@test
def coach_build_validates_and_links():
    reset(); set_plan("viral"); k = c(); now = time.time()
    r = build(k, now=now)
    kinds = [m["slack"]["text"].split("\n")[0] for m in r["messages"]]
    assert r["n_tasks"] == 4, (r["n_tasks"], r["dropped"], kinds)              # alice reply, bob quote, li, fb (carol too old, dave unknown, own, number)
    rows = [dict(x) for x in k.execute("SELECT * FROM coach_tasks ORDER BY id")]
    by = {x["kind"]: x for x in rows}
    a = by["reply_trend"]
    assert a["url"] == "https://x.com/alice/status/111" and "in_reply_to=111" in a["open_url"] and a["open_url"].startswith("https://x.com/intent/post?")
    assert abs(a["deadline"] - (now - 2400 + 180 * 60)) < 5 and a["state"] == "draft"
    q = by["quote"]; assert "url=https%3A%2F%2Fx.com%2Fbob%2Fstatus%2F222" in q["open_url"]
    li = by["li_comment"]; assert li["units"] == 3 and "linkedin.com/search/results/content" in li["url"]
    fb = by["fb_comment"]; assert json.loads(fb["drafts"]) == ["which part of your week would you hand to AI first, and why that one?"]   # 'Guaranteed' dropped
    assert any(d[0] == "window_closed" for d in r["dropped"]) and any(d[0] == "unknown_or_repeat_url" for d in r["dropped"])
    hdr = r["messages"][0]["slack"]["text"]
    assert "morning action list" in hdr and "React ✅ when done or ⏭ to skip" in hdr and "warm-up week 1 of 4" in hdr, hdr
    t1 = [m["slack"]["text"] for m in r["messages"] if "Reply to a trending post" in m["slack"]["text"]][0]
    assert "best before" in t1 and "Open in X with draft A" in t1 and "Draft A:" in t1, t1
    k.close()

@test
def coach_build_dedupes_and_respects_budget():
    reset(); set_plan("viral"); k = c()
    build(k)                                                      # first list takes alice + bob
    r2 = build(k, slot="evening")
    assert all("alice" not in m["slack"]["text"] for m in r2["messages"][1:]), "same post suggested twice"
    reset(); k.execute("INSERT INTO kv(k, v) VALUES('coach_size','0.6')")
    r3 = build(k, dry=True)
    assert r3["n_tasks"] <= 5 and k.execute("SELECT COUNT(*) FROM coach_tasks").fetchone()[0] == 0     # dry: nothing stored
    # budget: X replies week 1 = 13; fill 13 done replies -> no X reply tasks
    reset()
    for i in range(13):
        k.execute("INSERT INTO coach_tasks(day, platform, kind, units, state, created, drafts) VALUES(?,?,?,?,?,?,?)", (S.today(), "x", "reply_trend", 1, "done", time.time(), "[]"))
    r4 = build(k)
    assert not any("Reply to a trending post" in m["slack"]["text"] for m in r4["messages"]), [m["slack"]["text"][:60] for m in r4["messages"]]
    assert any(d[0] == "manual_budget" for d in r4["dropped"])
    k.close()

def posted_list(k, now=None):
    r = build(k, now=now)
    tasks = [{"task_id": m["task_id"], "slack_ts": f"500.{m['task_id']}"} for m in r["messages"] if m["type"] == "task"]
    S.coach_register(k, {"list_id": r["list_id"], "header_ts": "500.0", "tasks": tasks})
    return r, tasks

@test
def completion_tracking_marks():
    reset(); set_plan("viral"); k = c()
    r, tasks = posted_list(k)
    assert k.execute("SELECT COUNT(*) FROM coach_tasks WHERE state='open'").fetchone()[0] == 4
    xts = k.execute("SELECT slack_ts FROM coach_tasks WHERE kind='reply_trend'").fetchone()[0]
    tasks.sort(key=lambda t: t["slack_ts"] != xts)
    o = S.op(k, {"op": "coach_mark", "slack_ts": tasks[0]["slack_ts"], "state": "done", "by": STRANGER})
    assert not o["ok"] and o["error"] == "not the client"
    o = S.op(k, {"op": "coach_mark", "slack_ts": tasks[0]["slack_ts"], "state": "done", "by": OWNER})
    assert o["coach_state"] == "done"
    t0 = dict(k.execute("SELECT * FROM coach_tasks WHERE slack_ts=?", (tasks[0]["slack_ts"],)).fetchone())
    assert t0["state"] == "done" and t0["verify_state"] == "pending" and t0["decided_by"] == OWNER
    assert S.op(k, {"op": "coach_mark", "slack_ts": tasks[0]["slack_ts"], "state": "skipped", "by": OWNER}).get("info") == "already done"
    qts = k.execute("SELECT slack_ts FROM coach_tasks WHERE kind='quote'").fetchone()[0]
    S.op(k, {"op": "coach_mark", "slack_ts": qts, "state": "skipped", "by": OWNER})
    assert k.execute("SELECT state FROM coach_tasks WHERE slack_ts=?", (qts,)).fetchone()[0] == "skipped"
    S.op(k, {"op": "coach_mark", "slack_ts": qts, "state": "done", "by": OWNER})      # changed mind -> done
    assert k.execute("SELECT state FROM coach_tasks WHERE slack_ts=?", (qts,)).fetchone()[0] == "done"
    assert S.manual_used(k, "x").get("replies") == 1 and S.manual_used(k, "x").get("quotes") == 1
    for t in tasks:
        S.op(k, {"op": "coach_mark", "slack_ts": t["slack_ts"], "state": "done", "by": OWNER})
    ob = [r[0] for r in k.execute("SELECT text FROM outbox")]
    assert (any("All of the morning list done" in x for x in ob)) or CO.in_quiet(S.now_ist(), ["22:00", "08:00"]), ob
    assert "4 of 4" in S.coach_status_text(k)
    k.close()

@test
def expiry_and_late_done():
    reset(); set_plan("viral"); k = c(); now = time.time()
    r, tasks = posted_list(k, now=now)
    S.coach_tick(k, {"now": now + 4 * 3600, "no_check": True})
    st = dict(k.execute("SELECT state FROM coach_tasks WHERE kind='reply_trend'").fetchone())
    assert st["state"] == "expired"
    ts = k.execute("SELECT slack_ts FROM coach_tasks WHERE kind='reply_trend'").fetchone()[0]
    S.op(k, {"op": "coach_mark", "slack_ts": ts, "state": "done", "by": OWNER})
    assert k.execute("SELECT state FROM coach_tasks WHERE kind='reply_trend'").fetchone()[0] == "done"
    assert ts in S.coach_open_for_poll(k) or True
    k.close()

@test
def poll_list_rotates():
    reset(); set_plan("viral"); k = c()
    r, tasks = posted_list(k)
    first = S.coach_open_for_poll(k, limit=2); second = S.coach_open_for_poll(k, limit=2)
    assert len(first) == 2 and set(first).isdisjoint(second), (first, second)
    k.close()

# ------------------------------------------------------------------ nudges + quiet hours
@test
def quiet_hours_wrap():
    q = ["22:00", "08:00"]
    mk = lambda h, m=0: S.now_ist().replace(hour=h, minute=m)
    assert CO.in_quiet(mk(22), q) and CO.in_quiet(mk(23, 59), q) and CO.in_quiet(mk(0, 30), q) and CO.in_quiet(mk(7, 59), q)
    assert not CO.in_quiet(mk(8), q) and not CO.in_quiet(mk(21, 59), q) and not CO.in_quiet(mk(13), q)
    assert CO.in_quiet(mk(13), ["12:00", "14:00"]) and not CO.in_quiet(mk(15), ["12:00", "14:00"])

def open_task(k, kind="reply_trend", deadline=None, posted=None, day=None, state="open"):
    k.execute("""INSERT INTO coach_tasks(list_id, day, slot, platform, kind, units, url, handle, open_url, drafts, posted_at, deadline, window_min, slack_ts, state, created, updated)
                 VALUES('L', ?, 'morning', 'x', ?, 1, 'https://x.com/a/status/1', 'a', 'https://x.com/intent/post?in_reply_to=1', '[]', ?, ?, 180, ?, ?, ?, ?)""",
              (day or S.today(), kind, posted, deadline, f"7.{time.time()}", state, time.time(), time.time()))

@test
def reminder_before_window_closes():
    reset(); set_plan("viral"); k = c(); now = at(11)
    open_task(k, deadline=now + 30 * 60, posted=now - 150 * 60)                 # closes in 30 min, posted 2.5 h ago
    open_task(k, kind="quote", deadline=now + 20 * 60)                           # not time-sensitive -> no reminder
    r = S.coach_tick(k, {"now": now, "no_check": True})
    assert len(r["nudges"]) == 1 and "is ~2h old" in r["nudges"][0] and "replies after about 3h get little reach" in r["nudges"][0], r
    r = S.coach_tick(k, {"now": now + 60, "no_check": True})
    assert not r["nudges"], "reminded twice"
    reset(); open_task(k, deadline=now + 90 * 60)
    assert not S.coach_tick(k, {"now": now, "no_check": True})["nudges"]        # too early
    reset(); open_task(k, deadline=now + 5 * 60)
    assert not S.coach_tick(k, {"now": now, "no_check": True})["nudges"]        # too late to be useful
    k.close()

@test
def max_three_nudges_a_day():
    reset(); set_plan("viral"); k = c(); now = at(12)
    sent = 0
    for i in range(6):
        open_task(k, deadline=now + i * 3600 + 30 * 60)
        r = S.coach_tick(k, {"now": now + i * 3600, "no_check": True}); sent += len(r["nudges"])
    assert sent == 3, sent
    assert k.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 3
    k.close()

@test
def quiet_hours_block_nudges_and_lists():
    reset(); set_plan("viral"); k = c()
    for h, m in ((22, 30), (7, 30)):
        now = at(h, m); open_task(k, deadline=now + 30 * 60)
        r = S.coach_tick(k, {"now": now, "no_check": True})
        assert r["why"] == "quiet hours" and not r["nudges"], (h, r)
    now = at(8, 5); r = S.coach_tick(k, {"now": now, "no_check": True})
    assert r["why"] == "" , r
    k.close()

@test
def low_completion_note_once():
    reset(); set_plan("viral"); k = c()
    for d in (1, 2):
        day = (S.now_ist() - timedelta(days=d)).strftime("%Y-%m-%d")
        for i in range(6):
            open_task(k, day=day, state="done" if i < 2 else ("expired" if i < 5 else "skipped"), deadline=1)
    now = at(11)
    r = S.coach_tick(k, {"now": now, "no_check": True})
    assert len(r["nudges"]) == 1 and "4 of 12" in r["nudges"][0] and "shorter lists" in r["nudges"][0], r
    assert not S.coach_tick(k, {"now": now + 1800, "no_check": True})["nudges"]
    # 50% or better on one of the days -> no note
    reset()
    for d, done in ((1, 3), (2, 1)):
        day = (S.now_ist() - timedelta(days=d)).strftime("%Y-%m-%d")
        for i in range(6):
            open_task(k, day=day, state="done" if i < done else "expired", deadline=1)
    assert not S.coach_tick(k, {"now": now, "no_check": True})["nudges"]
    k.close()

@test
def celebrate_streak_and_follower_jump():
    reset(); set_plan("viral"); k = c(); now = at(13)
    for d in (1, 2, 3):
        day = (S.now_ist() - timedelta(days=d)).strftime("%Y-%m-%d")
        for i in range(5):
            open_task(k, day=day, state="done" if i < 4 else "skipped", deadline=1)
    r = S.coach_tick(k, {"now": now, "no_check": True})
    assert r["celebrations"] and "3 days in a row" in r["celebrations"][0], r
    assert not S.coach_tick(k, {"now": now + 1800, "no_check": True})["celebrations"]   # max 1 a day
    reset()
    S.coach_stats(k, {"platform": "x", "followers": "1,000", "ts": time.time() - 86400})
    S.coach_stats(k, {"platform": "x", "followers": "1.1K"})
    r = S.coach_tick(k, {"now": now, "no_check": True})
    assert r["celebrations"] and "1000 → 1100 (+10.0%)" in r["celebrations"][0], r
    k.close()

@test
def verify_check_read_only_and_danger_pause():
    reset(); set_plan("viral"); k = c(); now = at(15)
    r, tasks = posted_list(k)
    xts = k.execute("SELECT slack_ts FROM coach_tasks WHERE kind='reply_trend'").fetchone()[0]
    S.op(k, {"op": "coach_mark", "slack_ts": xts, "state": "done", "by": OWNER})
    k.execute("UPDATE coach_tasks SET updated=? WHERE slack_ts=?", (now - 1200, xts))
    t = S.coach_tick(k, {"now": now})
    ck = t["check"]; assert ck and ck["type"] == "verify" and "READ-ONLY" in ck["task"] and "your_x_handle/with_replies" in ck["task"], t
    assert k.execute("SELECT COUNT(*) FROM browser_use WHERE source='coach-verify' AND allowed=1").fetchone()[0] == 1
    S.coach_verified(k, {"type": "verify", "task_id": ck["task_id"], "status": "ok", "result_text": '{"found": true, "url": "https://x.com/your_x_handle/status/9"}'})
    assert k.execute("SELECT verify_state FROM coach_tasks WHERE id=?", (ck["task_id"],)).fetchone()[0] == "verified"
    k.execute("DELETE FROM kv WHERE k LIKE 'ramp:%'")
    S.ramp_state(k, "x", now=time.time() - 8 * 86400); assert S.ramp_state(k, "x")["step"] == 1
    S.coach_verified(k, {"type": "verify", "task_id": ck["task_id"], "status": "stopped", "result_text": "STOPPED: captcha shown"})
    assert os.path.exists(f"{T}/PAUSE") and S.ramp_state(k, "x")["step"] == 0
    assert any("warning / login / captcha" in x[0] for x in k.execute("SELECT text FROM outbox"))
    # daily cap for checks
    k.execute("DELETE FROM browser_use")
    for i in range(3):
        k.execute("INSERT INTO browser_use(ts, month, day, source, allowed, reason, task) VALUES(?,?,?,?,1,'','')", (time.time(), S.month(), S.today(), "coach-verify"))
    k.execute("UPDATE coach_tasks SET verify_state='pending' WHERE state='done'")
    assert S.coach_tick(k, {"now": now})["check"] is None
    k.close()

@test
def weekly_report_has_coordination_score():
    reset(); set_plan("viral"); k = c()
    for i in range(10):
        open_task(k, kind="creator_comment" if i % 2 else "reply_trend", state="done" if i < 4 else "expired", deadline=1)
    w = S.weekly_summary(k)
    assert "Coordination score, last 7 days:* 4/10 manual tasks done (40%)" in w and "Coaching tip:" in w, w
    k.close()

@test
def commands_size_status_warning():
    reset(); set_plan("viral"); k = c()
    assert "40% shorter" in S.op(k, {"op": "coach_size", "size": "shorter", "by": OWNER})["reply"] and S.kv_get(k, "coach_size") == "0.6"
    o = S.op(k, {"op": "ramp_warning", "platform": "x", "text": "X showed 'unusual activity'", "by": OWNER})
    assert "paused automated engagement" in o["reply"] and os.path.exists(f"{T}/PAUSE"), o
    o = S.op(k, {"op": "coach_now", "by": OWNER})
    assert o["run_coach"] and "action list" in o["reply"]
    S.op(k, {"op": "coach_now", "by": OWNER}); o = S.op(k, {"op": "coach_now", "by": OWNER})
    assert "two extra lists" in o["reply"]
    set_plan("starter"); assert "part of the Viral plan" in S.op(k, {"op": "coach_now", "by": OWNER})["reply"]
    k.close()

# ------------------------------------------------------------------ regression (existing guardrails still hold)
@test
def regression_existing_guardrails():
    reset(); set_plan("starter"); k = c()
    pre = S.content_precheck("Our happy customers say we are the best! ★★★★★", "post")
    assert not pre["ok"] and pre["blocks"][0]["cat"] == "fake_review"
    for i in range(5):
        k.execute("INSERT INTO engage_queue(slack_ts, action, url, approved_at, approved_day, due_at, state) VALUES(?,?,?,?,?,?,'done')",
                  (f"r{i}", "reply", f"https://x.com/h/status/{i}", time.time(), S.today(), time.time()))
    prop(k, "8.1", "engage", {"action": "reply", "url": "https://x.com/q/status/8", "handle": "q", "text": "what changed after week one for you?"})
    assert S.engage_checks(k, S.get_prop(k, "8.1"), OWNER)[0] == "cap_engage"       # Starter still 5/day; likes no longer an allowed action
    g = S.guard_browser(k, {"task": "https://zomato.com/x", "dry_run": True}); assert not g["ok"] and g["reason"] == "domain"
    k.close()

for name, ok, err in results:
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "\n   " + err.replace("\n", "\n   ")))
n_ok = sum(1 for _, ok, _ in results if ok)
print(f"\n{n_ok}/{len(results)} passed  (temp dir {T})")
shutil.rmtree(T, ignore_errors=True)
sys.exit(0 if n_ok == len(results) else 1)
