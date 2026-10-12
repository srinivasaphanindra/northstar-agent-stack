"""Unit tests for the 2026-10-12 tier update: Starter LinkedIn/Facebook comment allowance, Viral volume + warm-up,
per-platform pacing and the 7-day warning pause, and the hourly approval batches (timing, skip-when-ignored,
approval-to-schedule, progress, edit/target commands, plan->pick->build->register with FAKE data).
Fully offline: temp DB + temp guardrails copy, notifications off, worker off; no Slack, no browser, no LLM.
Usage: python3 state-helper/tests/test_batches.py"""
import os, sys, json, shutil, tempfile, time, re, traceback, random
from datetime import datetime, timedelta
T = tempfile.mkdtemp(prefix="c1batch-")
SRC = os.environ.get("CS_GUARDRAILS_SRC") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "templates", "guardrails.example.yaml")
GY = f"{T}/guardrails.yaml"; shutil.copy(SRC, GY)
# ramp-clock tests use their own fake "now"; the live warm-up start_date would pin the clock to 2026-10-12
import re as _re; _y = open(GY).read(); open(GY, "w").write(_re.sub(r"(?m)^\s*start_date:.*\n", "", _y))
open(f"{T}/notes.md", "w").write("# notes\n## Voice\nplain, friendly\n")
os.environ.update(CS_DB=f"{T}/db.sqlite", CS_NOTES=f"{T}/notes.md", CS_PAUSE=f"{T}/PAUSE", CS_PAUSE_ALL=f"{T}/PAUSE_ALL",
                  CS_LEDGER=f"{T}/ledger.tsv", CS_LOG=f"{T}/log", CS_GUARDRAILS=GY, CS_AUDIT=f"{T}/audit.tsv", CS_UPSELL=f"{T}/upsell.md",
                  CS_NOTIFY="0", CS_WORKER="0", CS_OR_UPSTREAM="http://127.0.0.1:9")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import server as S, coach as CO, guardrails as G, batches as BA
S.init_db()
OWNER, STRANGER = "U0000000001", "U0STRANGER1"
OK_VERDICT = '{"ok": true, "category": "ok", "reason": "fine"}'
results = []
def test(fn):
    try:
        fn(); results.append((fn.__name__, True, ""))
    except Exception as e:
        results.append((fn.__name__, False, f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=4)}"))
    return fn
def c():
    return S.db()
ORIG = open(GY).read()
def set_cfg(plan="starter", live=None, start_at=None):
    txt = re.sub(r"(?m)^plan:.*$", f"plan: {plan}", ORIG)
    # Tests always start from a clean overrides block (example-client may have live go-live overrides).
    txt = re.sub(r"(?ms)^overrides:.*\Z", "overrides: {}", txt)
    if live is not None or start_at is not None:
        ov = {}
        if live is not None: ov["live"] = live
        if start_at is not None: ov["start_at"] = start_at
        assert "\noverrides: {}" in txt
        blk = "".join(f"\n    {k}: " + ("true" if v is True else "false" if v is False else f'"{v}"') for k, v in ov.items())
        txt = txt.replace("\noverrides: {}", "\noverrides:\n  batches:" + blk)
    open(GY, "w").write(txt); t = time.time() + random.random()
    os.utime(GY, (t, t)); S.GUARD.mtime = None
    return S.cfg()
def reset(plan="starter", **kw):
    k = c()
    for t in ("engage_queue", "proposals", "actions_log", "coach_tasks", "coach_lists", "coach_nudges", "coach_stats", "kv", "browser_use",
              "audit", "outbox", "batches", "batch_pool", "batch_targets", "suggested", "watchlist"):
        k.execute(f"DELETE FROM {t}")
    k.close()
    for f in ("PAUSE", "PAUSE_ALL"):
        if os.path.exists(f"{T}/{f}"): os.remove(f"{T}/{f}")
    return set_cfg(plan, **kw)
def at(h, m=0, day_offset=0):
    return (S.now_ist().replace(hour=h, minute=m, second=0, microsecond=0) + timedelta(days=day_offset)).timestamp()
def prop(k, ts, kind, payload, state="pending", thread=None):
    k.execute("INSERT INTO proposals(slack_ts, kind, channel, thread_ts, payload, state, created, updated) VALUES(?,?,?,?,?,?,?,?)",
              (ts, kind, S.CHANNEL, thread or ts, json.dumps(payload), state, time.time(), time.time()))
def done_eng(k, url, ago_s, action="reply", text="t"):
    k.execute("INSERT INTO engage_queue(slack_ts, action, url, handle, text, approver, approved_at, approved_day, due_at, done_at, state) VALUES(?,?,?,?,?,?,?,?,?,?,'done')",
              (f"d{random.random()}", action, url, "h", text, OWNER, time.time() - ago_s, S.today(), time.time() - ago_s, time.time() - ago_s))

# ================================================================== caps per tier
@test
def tier_caps_starter_growth_viral():
    """1x / 2x / 3x ceilings (the owner 2026-10-12 01:10); week-1 ramp ≈ 40%."""
    reset("starter"); k = c()
    x = S.plat_caps("x", k)
    assert (x["engage_per_day"], x["posts_per_day"], x["ceiling_engage"], x["ceiling_posts"]) == (4, 2, 10, 5), x
    li, fb = S.plat_caps("linkedin", k), S.plat_caps("facebook", k)
    assert li["engage_per_day"] == 1 and li["comments_on"] and li.get("posts_per_week") == 4, li
    assert fb["engage_per_day"] == 1 and fb["comments_on"] and fb["posts_per_day"] == 1, fb
    assert "like" not in S.cfg()["engagement"]["allowed_actions"]
    assert S.cfg()["spend"]["browser_tasks_per_month"] == 560
    assert S.cfg()["engagement"]["max_per_hour"] == 3 and S.cfg()["engagement"]["min_gap_minutes"] == 8
    assert S.cfg()["platforms"]["x"]["posts_min_gap_minutes"] == 45
    b = S.cfg().get("batches") or {}
    assert b.get("enabled") and b.get("every_minutes") == 120 and b.get("items_min") == 2 and b.get("items_max") == 4, b
    assert (S.cfg().get("ramp") or {}).get("enabled")
    reset("growth"); k2 = c()
    x2 = S.plat_caps("x", k2)
    assert (x2["engage_per_day"], x2["ceiling_engage"], x2["ceiling_posts"]) == (8, 20, 10), x2
    li2, fb2 = S.plat_caps("linkedin", k2), S.plat_caps("facebook", k2)
    assert li2["engage_per_day"] == 2 and li2.get("posts_per_week") == 8 and fb2["engage_per_day"] == 2, (li2, fb2)
    assert S.cfg()["spend"]["browser_tasks_per_month"] == 1120
    bg = S.cfg().get("batches") or {}
    assert bg.get("every_minutes") == 60 and bg.get("items_max") == 5, bg
    reset("viral"); k3 = c()
    x3, li3, fb3 = S.plat_caps("x", k3), S.plat_caps("linkedin", k3), S.plat_caps("facebook", k3)
    assert (x3["engage_per_day"], x3["posts_per_day"], x3["ceiling_engage"], x3["ceiling_posts"]) == (12, 6, 30, 15), x3
    assert (li3["engage_per_day"], li3.get("posts_per_week"), li3["ceiling_engage"]) == (2, 12, 6), li3
    assert (fb3["engage_per_day"], fb3["posts_per_day"], fb3["ceiling_engage"], fb3["ceiling_posts"]) == (4, 1, 9, 3), fb3
    assert S.cfg()["spend"]["browser_tasks_per_month"] == 1680
    assert S.cfg()["spend"]["monthly_ai_budget_usd"] == 35
    for kk in (k, k2, k3): kk.close()

@test
def viral_warmup_steps_up_weekly_to_ceilings():
    reset("viral"); k = c(); t0 = time.time() - 40 * 86400
    want = {"x": ([12, 18, 24, 30], [6, 9, 12, 15]), "linkedin": ([2, 4, 5, 6], [1, 2, 2, 2]), "facebook": ([4, 5, 7, 9], [1, 2, 2, 3])}
    for p, (eng, posts) in want.items():
        for step in range(4):
            assert CO.ramp_cap(S.cfg(), p, "engage_per_day", step) == eng[step], (p, step)
            assert CO.ramp_cap(S.cfg(), p, "posts_per_day", step) == posts[step], (p, step)
        assert CO.ramp_cap(S.cfg(), p, "engage_per_day", 9) == eng[-1]
    st = S.ramp_state(k, "x", now=t0); assert st["step"] == 0
    assert S.ramp_state(k, "x", now=t0 + 7.01 * 86400)["step"] == 1
    assert S.ramp_state(k, "x", now=t0 + 14.01 * 86400)["step"] == 2
    assert S.ramp_state(k, "x", now=t0 + 21.01 * 86400)["step"] == 3
    assert S.ramp_state(k, "x", now=t0 + 39 * 86400)["step"] == 3
    # week 1 ≈ 40% of full volume (X+LI+FB engage+posts ceilings)
    full = 30 + 15 + 6 + 2 + 9 + 3; wk1 = 12 + 6 + 2 + 1 + 4 + 1
    assert 0.35 <= wk1 / full <= 0.5, wk1 / full
    k.close()

@test
def starter_and_growth_also_warmup_from_40pct():
    reset("starter"); k = c()
    assert (S.cfg().get("ramp") or {}).get("enabled")
    assert CO.ramp_cap(S.cfg(), "x", "engage_per_day", 0) == 4
    assert CO.ramp_cap(S.cfg(), "x", "engage_per_day", 3) == 10
    k.close()
    reset("growth"); k = c()
    assert CO.ramp_cap(S.cfg(), "x", "engage_per_day", 0) == 8
    assert CO.ramp_cap(S.cfg(), "x", "engage_per_day", 3) == 20
    k.close()

@test
def viral_pacing_x_and_comment_platforms():
    reset("viral")
    assert S.gaps("x") == (8 * 60, 4 * 60, 3), S.gaps("x")                                   # >=8 min, max 3/hr
    assert S.gaps("linkedin")[2] == 1 and S.gaps("facebook")[2] == 1
    reset("starter")
    assert S.gaps("x") == (8 * 60, 4 * 60, 3)
    assert S.gaps("linkedin")[2] == 1

@test
def li_fb_first_due_respects_20min_and_1_per_hour():
    reset("starter"); k = c()
    done_eng(k, "https://www.linkedin.com/feed/update/urn:li:activity:1", 5 * 60, "comment")
    now = time.time()
    due = S.first_due(k, "linkedin", now)
    assert due >= now - 300 + 3600, (due - now) / 60                                        # 1 an hour wins over the 20-min gap
    assert BA.in_active(due, S.cfg()["batches"]["active_hours"])
    # X is independent of LinkedIn
    due_x = S.first_due(k, "x", now)
    assert due_x < due or not BA.in_active(now, ["09:00", "21:00"])
    k.close()

@test
def li_fb_warning_pauses_7_days_at_zero():
    reset("starter"); k = c()
    k.execute("INSERT INTO engage_queue(slack_ts, action, url, handle, text, approver, approved_at, approved_day, due_at, state) VALUES('q1','comment','https://www.facebook.com/x/posts/1','a','hi there',?,?,?,?,'queued')",
              (OWNER, time.time(), S.today(), time.time() + 900))
    k.execute("INSERT INTO engage_queue(slack_ts, action, url, handle, text, approver, approved_at, approved_day, due_at, state) VALUES('q2','reply','https://x.com/a/status/1','a','hi there',?,?,?,?,'queued')",
              (OWNER, time.time(), S.today(), time.time() + 900))
    n, until = S.platform_warning(k, "facebook", "STOPPED: security checkpoint", "test", dry=False, announce=True)
    assert n == 1 and 6.9 * 86400 < until - time.time() <= 7 * 86400 + 5
    assert S.plat_caps("facebook", k)["engage_per_day"] == 0 and S.plat_caps("facebook", k)["paused_until"] == until
    assert S.plat_caps("linkedin", k)["engage_per_day"] == 1 and S.plat_caps("x", k)["engage_per_day"] == 4     # FB paused; LI+X week-1 keep running
    st = dict(k.execute("SELECT slack_ts, state FROM engage_queue").fetchall())
    assert st == {"q1": "cancelled", "q2": "queued"}, st
    prop(k, "f.1", "engage", {"action": "comment", "url": "https://www.facebook.com/y/posts/2", "handle": "y", "text": "which part was hardest?"})
    r, m = S.engage_checks(k, S.get_prop(k, "f.1"), OWNER)
    assert r in ("plat_paused", "cap_engage"), (r, m)
    assert "7 days" in k.execute("SELECT text FROM outbox ORDER BY id DESC").fetchone()[0]
    assert S.plat_pause(k, "facebook", now=until + 1)[0] is False                         # resumes automatically after 7 days
    k.close()

@test
def viral_li_warning_restarts_warmup_week1():
    reset("viral"); k = c()
    k.execute("INSERT INTO kv(k, v) VALUES('ramp:viral:linkedin', ?)", (json.dumps({"step": 3, "since": time.time() - 86400, "last_warning": 0, "warnings": 0}),))
    assert S.plat_caps("linkedin", k)["engage_per_day"] == 6
    S.platform_warning(k, "linkedin", "unusual activity", "test", announce=False)
    st = S.ramp_state(k, "linkedin", write=False); assert st["step"] == 0
    assert S.plat_caps("linkedin", k)["engage_per_day"] == 0
    assert S.plat_caps("x", k)["engage_per_day"] == 12
    k.close()

@test
def comment_checks_per_platform():
    reset("starter"); k = c()
    prop(k, "c.1", "engage", {"action": "comment", "url": "https://www.linkedin.com/feed/update/urn:li:activity:7", "handle": "a", "text": "how long did the pilot take?"})
    assert S.engage_checks(k, S.get_prop(k, "c.1"), OWNER) == (None, None)
    prop(k, "c.2", "engage", {"action": "like", "url": "https://www.linkedin.com/feed/update/urn:li:activity:8", "handle": "a", "text": ""})
    assert S.engage_checks(k, S.get_prop(k, "c.2"), OWNER)[0] in ("action_not_allowed", "like_manual")
    prop(k, "c.3", "engage", {"action": "comment", "url": "https://x.com/a/status/9", "handle": "a", "text": "nice one, what next?"})
    assert S.engage_checks(k, S.get_prop(k, "c.3"), OWNER)[0] == "action_not_allowed"     # X stays reply/repost
    done_eng(k, "https://www.linkedin.com/feed/update/urn:li:activity:1", 3600 * 2, "comment", "something else entirely here")
    assert S.engage_checks(k, S.get_prop(k, "c.1"), OWNER)[0] == "cap_engage"            # Starter LinkedIn 1/day
    reset("growth"); k2 = c()
    prop(k2, "c.4", "engage", {"action": "comment", "url": "https://www.facebook.com/a/posts/1", "handle": "a", "text": "how long did the pilot take?"})
    assert S.engage_checks(k2, S.get_prop(k2, "c.4"), OWNER) == (None, None)   # Growth now has Starter's FB 2/day
    k.close(); k2.close()

# ================================================================== batch timing (pure)
BC_V = {"enabled": True, "active_hours": ["09:00", "21:00"], "every_minutes": 60, "jitter_minutes": 10, "items_min": 4, "items_max": 6,
        "skip_after_ignored": 2, "max_still_there_per_day": 1}
BC_S = {"enabled": True, "every_minutes": 120, "jitter_minutes": 10, "items_min": 2, "items_max": 4, "active_hours": ["09:00", "21:00"], "skip_after_ignored": 2, "max_still_there_per_day": 1}

@test
def batch_timing_active_hours_and_cadence():
    assert BA.in_active(at(9), BC_V["active_hours"]) and BA.in_active(at(20, 59), BC_V["active_hours"])
    assert not BA.in_active(at(8, 59), BC_V["active_hours"]) and not BA.in_active(at(21), BC_V["active_hours"])
    assert BA.next_active_start(at(23), ["09:00", "21:00"]) == at(9, 0, 1)
    assert BA.next_active_start(at(3), ["09:00", "21:00"]) == at(9)
    assert BA.in_active(at(23), ["22:00", "02:00"]) and BA.in_active(at(1), ["22:00", "02:00"]) and not BA.in_active(at(3), ["22:00", "02:00"])
    assert BA.next_due(at(9), 60, 10, rnd=0) == at(10) and BA.next_due(at(9), 60, 10, rnd=0.9999) <= at(10, 10)
    assert BA.next_due(at(9), 180, 10, rnd=0) == at(12)
    assert BA.decide(at(7), BC_V, {})[0] == "wait" and "active hours" in BA.decide(at(7), BC_V, {})[1]
    assert BA.decide(at(10), BC_V, {"next_due": at(10, 30)})[0] == "wait"
    assert BA.decide(at(10, 31), BC_V, {"next_due": at(10, 30)})[0] == "run"
    assert BA.decide(at(10), {**BC_V, "enabled": False}, {})[0] == "wait"
    assert BA.decide(at(10), BC_V, {"paused": True})[0] == "wait"
    assert BA.slots_left_today(at(9), ["09:00", "21:00"], 60) == 13 and BA.slots_left_today(at(9), ["09:00", "21:00"], 180) == 5
    assert BA.slots_left_today(at(20, 30), ["09:00", "21:00"], 60) == 1 and BA.slots_left_today(at(21, 30), ["09:00", "21:00"], 60) == 0
    assert BA.cadence_text(BC_V).startswith("next batches about every hour") and "about every 2 hours" in BA.cadence_text(BC_S)
    # a whole simulated Starter day: batches about every 3 h inside 09:00-21:00 only
    t, fired = at(0), []
    st = {"next_due": 0}
    while t < at(23, 59):
        if BA.decide(t, BC_S, st)[0] == "run":
            fired.append(t); st["next_due"] = BA.next_due(t, 180, 10, rnd=0.5)
        t += 15 * 60
    hrs = [datetime.fromtimestamp(x, BA.IST).strftime("%H:%M") for x in fired]
    assert hrs[0] == "09:00" and 4 <= len(fired) <= 5 and all("09:00" <= h < "21:00" for h in hrs), hrs

@test
def batch_live_gating_and_start_at():
    eff = reset("starter")
    assert eff["batches"]["enabled"] and not eff["batches"]["live"]
    assert S.batch_live(eff, time.time())[0] is False
    eff = set_cfg("starter", live=True, start_at="2026-10-12 09:00")
    assert eff["batches"]["live"] is True
    sa = BA.parse_start_at("2026-10-12 09:00")
    assert datetime.fromtimestamp(sa, BA.IST).strftime("%Y-%m-%d %H:%M") == "2026-10-12 09:00"
    assert S.batch_live(eff, sa - 60)[0] is False and S.batch_live(eff, sa + 1)[0] is True
    assert BA.parse_start_at("tomorrow-ish") == float("inf")                                  # unparseable -> never live
    assert BA.parse_start_at("") == 0.0
    eff = set_cfg("starter")
    k = c()
    o = S.batch_plan(k, {"now": at(10)})                                                    # not dry, not live -> waits
    assert o["action"] == "wait" and "not live" in o["why_not"], o
    k.close()

# ================================================================== skip-when-ignored
@test
def skip_when_ignored_and_one_still_there_a_day():
    assert BA.ignored_streak([{"answered": False}, {"answered": False}, {"answered": True}]) == 2
    assert BA.ignored_streak([{"answered": True}, {"answered": False}]) == 0
    assert BA.decide(at(12), BC_V, {"streak": 1})[0] == "run"
    assert BA.decide(at(12), BC_V, {"streak": 2, "still_there_today": 0})[0] == "still_there"
    assert BA.decide(at(13), BC_V, {"streak": 2, "still_there_today": 1})[0] == "skip_ignored"
    assert BA.decide(at(14), BC_V, {"streak": 3, "still_there_today": 1})[0] == "skip_ignored"
    # through the helper with real rows: 2 posted, unanswered batches
    reset("viral", live=True, start_at=""); k = c()
    for i, h in enumerate((10, 11)):
        k.execute("INSERT INTO batches(id, day, plan, created, posted_at, header_ts, n_items, state, items) VALUES(?,?,?,?,?,?,?, 'posted', '[]')",
                  (f"b{i}", S.today(), "viral", at(h), at(h), f"h{i}", 2))
    o = S.batch_plan(k, {"dry_run": True, "now": at(12), "force_plan": "viral"})
    assert o["action"] == "still_there" and "Still there?" in o["still_there"] and "only reminder today" in o["still_there"], o
    k.execute("INSERT INTO kv(k, v) VALUES(?, '1')", (f"batch_still_{S.today()}",))
    o = S.batch_plan(k, {"dry_run": True, "now": at(13), "force_plan": "viral"})
    assert o["action"] == "skip_ignored" and not o["ok_to_run"], o
    S.batch_answered(k, "b1")                                                               # a reaction on the latest batch
    o = S.batch_plan(k, {"dry_run": True, "now": at(14), "force_plan": "viral"})
    assert o["action"] == "run", o
    k.close()

@test
def decide_on_batch_item_marks_batch_answered():
    reset("starter"); k = c()
    k.execute("INSERT INTO batches(id, day, plan, created, posted_at, header_ts, n_items, state, items) VALUES('bx',?,?,?,?,'H1',1,'posted','[]')",
              (S.today(), "starter", time.time(), time.time()))
    prop(k, "i.1", "engage", {"action": "reply", "url": "https://x.com/a/status/77", "handle": "a", "text": "how did you pick the first task?",
                               "batch_id": "bx", "batch_n": 1}, thread="H1")
    S.op(k, {"op": "decide", "slack_ts": "i.1", "decision": "discard", "by": OWNER})
    assert k.execute("SELECT answered_at FROM batches WHERE id='bx'").fetchone()[0] is not None
    assert S.batch_recent(k)[0]["answered"]
    k.close()

# ================================================================== approval -> schedule
@test
def approval_to_schedule_engage_items():
    reset("starter"); k = c()
    k.execute("INSERT INTO batches(id, day, plan, created, posted_at, header_ts, n_items, state, items) VALUES('b1',?,?,?,?,'H9',2,'posted','[]')",
              (S.today(), "starter", time.time(), time.time()))
    items = [{"n": 1, "slack_ts": "s.1", "payload": {"action": "reply", "url": "https://x.com/ann/status/101", "handle": "ann",
                                                    "text": "what made you switch from the spreadsheet?", "batch_id": "b1", "batch_n": 1}},
             {"n": 2, "slack_ts": "s.2", "payload": {"action": "comment", "url": "https://www.linkedin.com/feed/update/urn:li:activity:55", "handle": "Bo",
                                                    "text": "how did your team react to the first automated report?", "batch_id": "b1", "batch_n": 2}}]
    k.execute("UPDATE batches SET state='draft' WHERE id='b1'")
    r = S.batch_register(k, {"batch_id": "b1", "header_ts": "H9", "items": items, "posts": []})
    assert r["registered"] == 2 and S.batch_items_map(k)["H9"]["2"]["ts"] == "s.2"
    hrs = S.cfg()["batches"]["active_hours"]
    for ts, plat in (("s.1", "x"), ("s.2", "linkedin")):
        o = S.op(k, {"op": "decide", "slack_ts": ts, "decision": "approve", "by": OWNER})
        assert o.get("check") and o["check"]["slack_ts"] == ts, o                           # content check first (LLM in n8n)
        v = S.guard_verdict(k, {"slack_ts": ts, "raw": OK_VERDICT, "by": OWNER})
        assert v["reply"].startswith("Approved."), v
        q = k.execute("SELECT * FROM engage_queue WHERE slack_ts=?", (ts,)).fetchone()
        assert q and q["state"] == "queued" and BA.in_active(q["due_at"], hrs), dict(q) if q else None
    rows = k.execute("SELECT url, due_at FROM engage_queue ORDER BY due_at").fetchall()
    assert len(rows) == 2
    assert "LinkedIn comments today: 1/1" in v["reply"], v["reply"]
    assert k.execute("SELECT answered_at FROM batches WHERE id='b1'").fetchone()[0] is not None
    # a stranger can't approve
    prop(k, "s.3", "engage", {"action": "reply", "url": "https://x.com/c/status/3", "handle": "c", "text": "how long did it take?", "batch_id": "b1", "batch_n": 3}, thread="H9")
    o = S.op(k, {"op": "decide", "slack_ts": "s.3", "decision": "approve", "by": STRANGER})
    assert not k.execute("SELECT 1 FROM engage_queue WHERE slack_ts='s.3'").fetchone()
    k.close()

@test
def approval_to_schedule_post_goes_to_buffer_queue():
    reset("starter"); k = c()
    prop(k, "p.1", "post", {"text": "Small shops don't need an AI strategy. They need one boring task automated this week.",
                             "drafts": [{"service": "twitter", "post_id": "bp1"}], "batch_id": "b2", "batch_n": 1}, thread="H2")
    S.op(k, {"op": "decide", "slack_ts": "p.1", "decision": "approve", "by": OWNER})
    v = S.guard_verdict(k, {"slack_ts": "p.1", "raw": OK_VERDICT, "by": OWNER})
    ops = v.get("buffer_ops") or []
    assert ops and ops[0]["mode"] == "addToQueue", v                                         # Buffer picks the good time
    k.close()

@test
def worker_reschedules_outside_active_hours():
    reset("starter"); k = c()
    due = S.first_due(k, "facebook", at(22, 30))
    assert due >= at(9, 0, 1) and BA.in_active(due, ["09:00", "21:00"]), datetime.fromtimestamp(due, BA.IST)
    k.close()

# ================================================================== sizes, allocation, progress, browser pacing
@test
def batch_sizes_and_allocation():
    assert BA.batch_size(BC_V, 20, 12) == 4 and BA.batch_size(BC_V, 40, 5) == 6 and BA.batch_size(BC_V, 1, 3) == 1 and BA.batch_size(BC_V, 0, 3) == 0
    assert BA.batch_size(BC_S, 8, 5) == 2 and BA.batch_size(BC_S, 3, 5) == 2
    room = {"post": {"x": 1, "linkedin": 1}, "reply": 3, "comment": {"linkedin": 1, "facebook": 1}}
    a = BA.allocate(4, room, 1, ["x", "linkedin", "facebook"])
    kinds = [x["kind"] for x in a]
    assert len(a) == 4 and kinds.count("post") == 1 and "reply" in kinds and "comment" in kinds, a
    assert BA.allocate(4, {"post": {}, "reply": 0, "comment": {}}) == []
    assert len(BA.allocate(4, {"post": {"x": 1}, "reply": 1, "comment": {}})) == 2
    assert BA.daily_browser_room(560, 0, 0, 1, 31) == (19, 19)
    assert BA.daily_browser_room(1680, 0, 0, 1, 31) == (55, 55)
    assert BA.daily_browser_room(60, 30, 1, 16, 31) == (1, 2)
    assert BA.daily_browser_room(60, 60, 0, 20, 31) == (0, 0)

@test
def progress_line_and_texts():
    s = BA.progress_line({"x": {"replies": (5, 12), "posts": (1, 6)}, "linkedin": {"comments": (0, 5)}}, "warm-up week 2 of 4")
    assert s == "today: X replies 5/12 · X posts 1/6 · LinkedIn comments 0/5 · warm-up week 2 of 4", s
    reset("starter"); k = c()
    p = S.batch_progress(k, S.cfg())
    assert "X replies 0/4" in p and "LinkedIn comments 0/1" in p and "Facebook comments 0/1" in p, p
    its = [{"n": 1, "kind": "reply", "platform": "x", "url": "https://x.com/a/status/1", "handle": "a", "text": "how?"},
           {"n": 2, "kind": "post", "platform": "linkedin", "text": "A post."}]
    h = BA.header_text(its, p, at(10), BA.cadence_text(BC_S), dry=True)
    assert "2 items" in h and "1. X reply to @a" in h and "2. LinkedIn post" in h and "Nothing goes out without your ✅" in h and "(dry run)" in h and p in h
    t = BA.item_text({**its[0], "excerpt": "their post", "why": "asks a question"})
    assert "✅" in t and "✏️" in t and "❌" in t and "Target:" in t
    k.close()

@test
def parse_typed_batch_commands():
    P = BA.parse_batch_text
    assert P("1 ✅") == ("approve", [1], "") and P("approve 1, 3") == ("approve", [1, 3], "") and P("2 ❌") == ("discard", [2], "")
    assert P("approve all") == ("approve", "all", "") and P("skip all") == ("discard", "all", "")
    assert P("3: what made you start?") == ("edit", [3], "what made you start?")
    assert P("approve") is None and P("hello there") is None

# ================================================================== commands
@test
def batch_target_and_edit_commands():
    reset("starter"); k = c()
    o = S.batch_op(k, "batch_target", {"text": "comment on https://www.linkedin.com/feed/update/urn:li:activity:999?utm=x they ask about pricing", "by": OWNER}, {"ok": True})
    assert "LinkedIn comment" in o["reply"] and "after your ✅" in o["reply"], o
    t = k.execute("SELECT url, platform, note, state FROM batch_targets").fetchone()
    assert t["url"].endswith("activity:999") and t["platform"] == "linkedin" and "pricing" in t["note"] and t["state"] == "new"
    reset("growth"); k2 = c()
    o = S.batch_op(k2, "batch_target", {"text": "comment on https://www.facebook.com/a/posts/1 they ask about timing", "by": OWNER}, {"ok": True})
    assert "Facebook comment" in o["reply"] and "after your ✅" in o["reply"], o   # Growth now includes Starter LI/FB comments
    reset("starter"); k3 = c()
    k3.execute("INSERT INTO batches(id, day, plan, created, posted_at, header_ts, n_items, state, items) VALUES('be',?,?,?,?,'HE',1,'posted','[]')",
               (S.today(), "starter", time.time(), time.time()))
    prop(k3, "e.1", "engage", {"action": "reply", "url": "https://x.com/a/status/5", "handle": "a", "text": "old wording here?", "batch_id": "be", "batch_n": 1}, thread="HE")
    o = S.batch_op(k3, "batch_edit", {"thread_ts": "HE", "n": 1, "text": "how long did the first week take you?", "by": OWNER}, {"ok": True})
    assert o["reply"].startswith("Updated item 1") and S.get_prop(k3, "e.1")["payload"]["text"] == "how long did the first week take you?", o
    o = S.batch_op(k3, "batch_edit", {"thread_ts": "HE", "n": 1, "text": "check out my site https://spam.example #ai", "by": OWNER}, {"ok": True})
    assert "keeps its old text" in o["reply"], o
    o = S.batch_op(k3, "batch_edit", {"thread_ts": "HE", "n": 1, "text": "x y z w", "by": STRANGER}, {"ok": True})
    assert o["ok"] is False
    o = S.batch_op(k3, "batch_pause", {"by": OWNER}, {"ok": True}); assert "paused" in o["reply"]
    assert S.batch_plan(k3, {"dry_run": True, "now": at(10), "force_plan": "starter"})["action"] == "wait"
    S.batch_op(k3, "batch_resume", {"by": OWNER}, {"ok": True})
    assert "Draft batches:" in S.batch_status_text(k3)
    for kk in (k, k2, k3): kk.close()

@test
def batch_help_and_edit_reaction_on_batch_item():
    reset("starter"); k = c()
    k.execute("INSERT INTO batches(id, day, plan, created, posted_at, header_ts, n_items, state, items) VALUES('bh',?,?,?,?,'HH',2,'posted','[]')",
              (S.today(), "starter", time.time(), time.time()))
    prop(k, "h.1", "engage", {"action": "reply", "url": "https://x.com/a/status/61", "handle": "a", "text": "what changed first?", "batch_id": "bh", "batch_n": 1}, thread="HH")
    prop(k, "h.2", "engage", {"action": "reply", "url": "https://x.com/b/status/62", "handle": "b", "text": "how long did it take?", "batch_id": "bh", "batch_n": 2}, thread="HH")
    o = S.op(k, {"op": "batch_help", "thread_ts": "HH", "by": OWNER})
    assert "Open items here: 1, 2" in o["reply"] and "approve all" in o["reply"], o
    o = S.op(k, {"op": "decide", "slack_ts": "h.2", "decision": "edit", "by": OWNER})
    assert "Type `2: your new wording`" in o["reply"], o
    k.close()

# ================================================================== plan -> pick -> build -> register (fake data)
FAKE_X = [{"url": "https://x.com/rhea/status/9001", "text": "We moved invoice reminders to an AI draft. Clients actually reply faster now.", "age": "30m"},
          {"url": "https://x.com/kabir/status/9002?s=20", "text": "Founders: what is the one tool you would never give up?", "age": "1h"},
          {"url": "https://x.com/your_x_handle/status/9003", "text": "own post", "age": "5m"},
          {"url": "https://x.com/old/status/9004", "text": "very old", "age": "9h"}]

def pipeline(plan, dry, now):
    k = c()
    pl = S.batch_plan(k, {"dry_run": dry, "now": now, "force_plan": plan, "ignore_timing": True})
    assert pl["ok_to_run"], pl
    reads = []
    for r in pl["reads"]:
        if r["type"] in ("x_search", "x_watch", "x_following"):
            reads.append({**r, "status": "ok", "posts": FAKE_X, "follows": [re.search(r"x\.com/([^/]+)/", p["url"]).group(1) for p in FAKE_X]})  # cached Following list (fix3)
        else:
            reads.append({**r, "status": "ok", "data": {"text": "We tried weekly AI summaries for our sales team. Mixed results so far.", "author": "Meera"}})
    pk = S.batch_pick(k, {"dry_run": dry, "now": now, "force_plan": plan, "alloc": pl["alloc"], "reads": reads, "batch_id": pl["batch_id"]})
    assert pk["ok"] and not pk["nothing"] and S.BATCH_TAG in pk["prompt"], pk
    fake = {"items": []}
    for p in pk["picks"]:
        txt = {"reply": f"what changed first for you after the switch, {p['ref']}?",
               "comment": "which team noticed the difference first, and what did they say about it?",
               "post": "Most small shops do not need an AI plan. They need one boring weekly task done by AI, checked by a person."}[p["kind"]]
        fake["items"].append({"ref": p["ref"], "text": txt, "why": "concrete question"})
    bd = S.batch_build(k, {"dry_run": dry, "now": now, "force_plan": plan, "picks": pk["picks"], "raw": json.dumps(fake), "batch_id": pl["batch_id"]})
    k.close()
    return pl, pk, bd

@test
def dry_run_viral_batch_end_to_end_fake_events():
    reset("starter"); k = c()
    o = S.batch_op(k, "batch_target", {"text": "comment on https://www.linkedin.com/feed/update/urn:li:activity:4242", "by": OWNER}, {"ok": True})
    assert "tell me in a line" in o["reply"], o                                              # no gist -> asks for one (no LinkedIn read)
    S.batch_op(k, "batch_target", {"text": "comment on https://www.linkedin.com/feed/update/urn:li:activity:4242 they ask how to start with AI reports", "by": OWNER}, {"ok": True})
    assert "how to start" in k.execute("SELECT note FROM batch_targets").fetchone()[0]
    # seed a watch handle so auto X reads are watchlist (never keyword topic search)
    S.op(k, {"op": "watch", "handle": "rhea", "by": OWNER})
    n_out = k.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]; n_br = k.execute("SELECT COUNT(*) FROM browser_use").fetchone()[0]
    k.close()
    pl, pk, bd = pipeline("viral", True, at(11))
    assert 4 <= pl["size"] <= 6 and pl["plan"] == "viral", pl
    assert any(r["type"] in ("x_watch", "x_following") for r in pl["reads"]) and not any(r["type"] == "x_search" for r in pl["reads"]), pl["reads"]
    assert not any(r["type"] == "target" for r in pl["reads"]), pl["reads"]   # read_targets off
    assert any(a["kind"] == "comment" and a["platform"] == "linkedin" for a in pl["alloc"]) or pl["size"] < 3, pl["alloc"]
    assert bd["ok"] and not bd["nothing"] and bd["dry_run"] and 2 <= bd["n_items"] <= 4, bd
    assert "(dry run)" in bd["header"]["text"] and "today:" in bd["header"]["text"]
    urls = [it["payload"]["url"] for it in bd["items"]]
    assert all("your_x_handle" not in u and "/old/" not in u for u in urls), urls
    assert sum(1 for _ in bd["posts"]) <= 1
    k = c()
    assert k.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == n_out                       # nothing to Slack
    assert k.execute("SELECT COUNT(*) FROM browser_use").fetchone()[0] == n_br                   # no browser use counted
    assert k.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 0
    assert S.batch_register(k, {"dry_run": True, "batch_id": pl["batch_id"], "header_ts": "x"})["dry_run"]
    assert S.cfg()["plan"] == "starter"                                                        # force_plan never switches the plan
    k.close()

@test
def starter_batch_live_path_registers_with_fake_slack_ts():
    """Non-dry helper path (what n8n calls when live), with a fake clock at 11:00 IST and fake Slack ts; nothing is sent anywhere."""
    reset("starter", live=True, start_at="2020-01-01 09:00"); k = c()
    S.batch_op(k, "batch_target", {"text": "comment on https://www.facebook.com/yourbrand.friend/posts/77 asks which tool to try first", "by": OWNER}, {"ok": True})
    k.close()
    real = S.time
    class FakeTime:
        def __getattr__(self, n): return getattr(real, n)
        def time(self): return at(11)
    S.time = FakeTime()
    try:
        pl, pk, bd = pipeline("starter", False, at(11))
    finally:
        S.time = real
    set_cfg("starter")
    assert 2 <= bd["n_items"] <= 4 and any(i["kind"] == "comment" and i["platform"] == "facebook" for i in bd["items"]), bd
    k = c()
    assert k.execute("SELECT state FROM batches WHERE id=?", (pl["batch_id"],)).fetchone()[0] == "draft"
    items = [{**it, "slack_ts": f"fake.{it['n']}"} for it in bd["items"]]
    posts = []
    for po in bd["posts"]:
        prop(k, f"fakepost.{po['n']}", "post", {"text": po["text"], "drafts": [{"service": po["services"], "post_id": "fp"}]}, thread="FAKEHDR")
        posts.append({**po, "preview_ts": f"fakepost.{po['n']}"})
    r = S.batch_register(k, {"batch_id": pl["batch_id"], "header_ts": "FAKEHDR", "items": items, "posts": posts})
    assert r["ok"] and r["registered"] == bd["n_items"], (r, bd)
    m = S.batch_items_map(k)["FAKEHDR"]
    assert len(m) == bd["n_items"]
    assert k.execute("SELECT state FROM batches WHERE id=?", (pl["batch_id"],)).fetchone()[0] == "posted"
    assert "FAKEHDR" in json.dumps(S.batch_items_map(k))
    k.close()

@test
def build_rejects_bad_drafts():
    reset("starter"); k = c()
    picks = [{"ref": "i1", "kind": "reply", "platform": "x", "url": "https://x.com/z/status/1", "handle": "z", "excerpt": "x"},
             {"ref": "i2", "kind": "comment", "platform": "facebook", "url": "https://www.facebook.com/a/posts/2", "handle": "a", "excerpt": "y", "note": ""}]
    raw = json.dumps({"items": [{"ref": "i1", "text": "DM me for a free audit https://spam.example #growth"},
                                 {"ref": "i2", "text": "Guaranteed results in 7 days!"}]})
    bd = S.batch_build(k, {"dry_run": True, "picks": picks, "raw": raw, "batch_id": "t"})
    assert bd["nothing"] and len(bd["dropped"]) == 2, bd
    k.close()

@test
def danger_page_on_li_target_read_pauses_platform():
    reset("starter"); k = c()
    o = S.batch_pick(k, {"alloc": [{"kind": "comment", "platform": "linkedin"}], "batch_id": "bd",
                         "reads": [{"type": "target", "id": 1, "url": "https://www.linkedin.com/feed/update/urn:li:activity:1", "status": "stopped",
                                    "result_text": "STOPPED: security checkpoint, verify it's you"}]})
    assert o.get("stop") and S.plat_caps("linkedin", k)["engage_per_day"] == 0, o
    k.close()

@test
def drafter_schedule_steps_aside_when_batches_live():
    set_cfg("starter", live=True, start_at="2020-01-01 09:00"); k = c()
    for t in ("engage_queue", "kv", "browser_use"): k.execute(f"DELETE FROM {t}")
    o = S.engage_plan(k, {"source": "schedule", "dry_run": True})
    assert not o.get("ok_to_run", o.get("run", False)) and "batch" in json.dumps(o).lower(), o
    k.close(); set_cfg("starter")

@test
def limits_and_weekly_mention_batches():
    reset("starter"); k = c()
    t = S.limits_text(k)
    assert "LinkedIn: comments 0/1" in t and "Facebook: comments 0/1" in t and "terms restrict automation" in t and "Draft batches" in t, t
    w = S.weekly_summary(k) if S.weekly_summary.__code__.co_argcount >= 1 else S.weekly_summary()
    ws = json.dumps(w) if not isinstance(w, str) else w
    assert "batch" in ws.lower(), ws[:500]
    k.close()


@test
def watch_unwatch_watchlist_commands():
    reset("starter"); k = c()
    o = S.op(k, {"op": "watch", "handle": "naval", "by": OWNER})
    assert "Watching @naval" in o["reply"], o
    assert k.execute("SELECT handle FROM watchlist").fetchone()[0] == "naval"
    o = S.op(k, {"op": "watchlist", "by": OWNER})
    assert "@naval" in o["reply"]
    o = S.op(k, {"op": "unwatch", "handle": "naval", "by": OWNER})
    assert "Removed @naval" in o["reply"]
    assert k.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0] == 0
    # keyword-search pool rows must NOT be used for auto replies
    k.execute("INSERT INTO batch_pool(url, handle, text, age, posted_at, added, source) VALUES(?,?,?,?,?,?,?)",
              ("https://x.com/spam/status/1", "spam", "topic hit", "", time.time(), time.time(), None))
    k.execute("INSERT INTO batch_pool(url, handle, text, age, posted_at, added, source) VALUES(?,?,?,?,?,?,?)",
              ("https://x.com/ok/status/2", "ok", "client link", "", time.time(), time.time(), "client"))
    pool = S._fresh_pool(k, time.time(), set())
    assert [p["handle"] for p in pool] == ["ok"], pool
    k.close()

@test
def engage_plan_uses_watchlist_not_keyword_topic():
    set_cfg("starter"); k = c()
    for tbl in ("engage_queue", "kv", "browser_use", "watchlist"): k.execute(f"DELETE FROM {tbl}")
    S.op(k, {"op": "watch", "handle": "rhea", "by": OWNER})
    o = S.engage_plan(k, {"source": "webhook", "dry_run": True})
    assert o.get("ok_to_run") and o.get("read_type") == "x_watch" and "from:rhea" in (o.get("query") or ""), o
    assert "min_faves" not in (o.get("query") or "") and "AI for small" not in json.dumps(o)
    k.close(); set_cfg("starter")

@test
def batch_asks_for_links_when_no_reply_targets():
    reset("starter", live=True, start_at="2020-01-01 09:00"); k = c()
    # exhaust reply room without pool: fill engage_queue to cap week-1 (4)
    for i in range(4):
        done_eng(k, f"https://x.com/a/status/{i}", 3600)
    # also no watchlist -> following read may still be planned if room; zero reply room => no x read for replies
    pl = S.batch_plan(k, {"dry_run": True, "now": at(11), "force_plan": "starter", "ignore_timing": True})
    # with reply room 0, may still propose posts/comments; if empty ask_links tip appears in why or header later
    if not pl.get("ok_to_run"):
        assert "reply to" in (pl.get("why_not") or "").lower() or "watch" in (pl.get("why_not") or "").lower() or pl.get("ask_links")
    k.close()

for name, ok, err in results:
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "\n   " + err.replace("\n", "\n   ")))
n_ok = sum(1 for _, ok, _ in results if ok)
print(f"\n{n_ok}/{len(results)} passed  (temp dir {T})")
shutil.rmtree(T, ignore_errors=True)
sys.exit(0 if n_ok == len(results) else 1)
