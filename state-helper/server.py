#!/usr/bin/env python3
"""state-helper: small local helper for the n8n 'Example Client bot' (127.0.0.1:8771, stdlib only).

Owns everything that must survive n8n restarts or be enforced outside the LLM:
  * chat memory per Slack thread (SQLite, last 20 turns)          GET /context, POST /memory
  * client notes file ~/agency/clients/example-client/notes.md            (read every turn; 'remember:' appends)
  * approval proposals (Buffer post previews + engagement ideas)   POST /proposals, GET /tick, POST /op, POST /op/finish
  * engagement trial (X @your_x_handle): kill switch file, daily cap 5, >=7 min random gaps, ledger TSV,
    and the ONLY code path that calls the browser agent with allow_write + X-Approved: yes (after a human approval).
n8n never gets write access to the browser itself; it can only hand an approved proposal to this queue.

Guardrails (2026-10-12, guardrails.py + ~/agency/templates/guardrails.example.yaml), enforced HERE in code:
  * per-platform daily caps (posts + engagement), allowed actions, copy-paste reply check
  * pre-publish content check: regex (free) + cheap LLM classifier verdict (run by n8n, judged here), fail closed
  * approvals only from configured approvers; 'pause everything' kill switch (PAUSE_ALL file), resume by owner only
  * spend: OpenRouter proxy /or/<lane>/v1/* meters every LLM call (cost from the response / generation id / token estimate),
    warns in-channel at 80 %, pauses non-essential AI at 100 %, hard stop at hard_stop_pct
  * browser tasks: domain allow-list + monthly cap (/guard/browser), counted for agent, drafter and worker
  * secrets never stored (memory scrub), out-of-scope asks logged to upsell-log.md + owner notified, audit log of every action

Viral tier (2026-10-12, coach.py): plans starter/growth/viral ('switch plan <name>', admins only), warm-up ramps per plan+platform
(start low, +1 step a week without warnings, -1 step on any warning), hourly pacing + configurable random gaps, no identical posts,
'shorter lists', manual-action coach: daily action lists (/coach/plan -> n8n reads X read-only + 1 LLM call -> /coach/build ->
Slack -> /coach/register), ✅/⏭ tracking (op coach_mark), reminders/low-completion notes/celebrations (/coach/tick, max 3 nudges a
day, quiet hours), read-only completion checks (/coach/verified), follower stats (/coach/stats), weekly coordination score. X auto replies: client links + watchlist + Following only (no keyword-search targets; X April 2026).

v3 (2026-10-12 00:43 IST, the owner): approval-only LinkedIn/Facebook comments by the browser agent (Starter 1/2 a day, Viral ramp to
5/8; 20+ min apart, max 1 an hour per platform; any warning/checkpoint -> that platform pauses 7 days at 0), Viral ceilings raised
(X 12 replies + 6 posts, LinkedIn 2 posts + 5 comments, Facebook 3 posts + 8 comments; 4-week warm-up from ~50 %), per-platform
pacing in the worker, and hourly approval batches (batches.py): /batch/plan -> n8n (read-only X search, 1 LLM call) -> /batch/build
-> ONE Slack message + items in its thread (✅/✏️/❌) -> /batch/register; skip when the last 2 batches were ignored, max one
'still there?' a day, active hours only, browser allowance paced per day.
"""
import json, os, random, re, sqlite3, subprocess, threading, time, urllib.request, urllib.parse, urllib.error
import guardrails as G
import coach as CO
import batches as BA
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
HOME = os.path.expanduser("~")
CLIENT_DIR = os.environ.get("CS_CLIENT_DIR", f"{HOME}/agency/clients/example-client")
DB = os.environ.get("CS_DB", f"{CLIENT_DIR}/bot-state.sqlite")
NOTES = os.environ.get("CS_NOTES", f"{CLIENT_DIR}/notes.md")
PAUSE = os.environ.get("CS_PAUSE", f"{HOME}/agency/state/PAUSE-example-client-engage")
LEDGER = os.environ.get("CS_LEDGER", f"{HOME}/agency/state/example-client-engage-ledger.tsv")
LOG = os.environ.get("CS_LOG", f"{HOME}/agency/logs/state-helper.log")
PAUSE_ALL = os.environ.get("CS_PAUSE_ALL", f"{HOME}/agency/state/PAUSE-example-client-all")   # NOT the agency-wide state/PAUSE_ALL
GUARD = G.Config(os.environ.get("CS_GUARDRAILS", f"{CLIENT_DIR}/guardrails.yaml"))
NOTIFY = os.environ.get("CS_NOTIFY", "1") == "1"          # owner notifications via ~/agency/bin/notify.sh
OR_UPSTREAM = os.environ.get("CS_OR_UPSTREAM", "https://openrouter.ai/api/v1")
BROWSER = os.environ.get("CS_BROWSER", "http://127.0.0.1:8770")
PORT = int(os.environ.get("CS_PORT", "8771"))
CHANNEL = "C0000000001"
OWNER = "U0000000001"                 # only the owner can approve / resume / remember
OWN_HANDLE = "your_x_handle"
DAILY_CAP = 5            # legacy default; real caps come from guardrails.yaml (platforms.<p>.engage_per_day)
MIN_GAP_S, EXTRA_GAP_S = 7 * 60, 8 * 60   # legacy defaults; live gaps come from guardrails.yaml engagement.min_gap_minutes/extra_gap_minutes (gaps())
ALLOWED_ACTIONS = {"reply", "like", "repost", "comment"}   # code universe; yaml allowed_actions is the live allow-list (no like per X April 2026 rules)
X_ACTIONS = {"reply", "like", "repost"}          # on X
COMMENT_PLATFORMS = ("linkedin", "facebook")     # approval-only comments (action "comment"), browser agent, tiny caps
MEMORY_TURNS = 20
TOPICS = [  # client niche (rotates per run)
    ("AI for small businesses", '("small business" OR "local business") AI'),
    ("founders in India", '(founder OR founders OR startup) India'),
    ("social media marketing", '"social media marketing" OR "content marketing" tips'),
]
LINT = "/workspace/social/reply-lint.py"
LOCK = threading.RLock()
WORKER_ON = os.environ.get("CS_WORKER", "1") == "1"


def now_ist():
    return datetime.now(IST)

def fmt(dt):
    return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M IST")

def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"[{now_ist():%Y-%m-%d %H:%M:%S} IST] {msg}\n")

def db():
    c = sqlite3.connect(DB, timeout=15, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c

def init_db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS memory(id INTEGER PRIMARY KEY, thread TEXT, role TEXT, text TEXT, ts REAL);
    CREATE INDEX IF NOT EXISTS memory_thread ON memory(thread, id);
    CREATE TABLE IF NOT EXISTS proposals(slack_ts TEXT PRIMARY KEY, kind TEXT, channel TEXT, thread_ts TEXT,
        payload TEXT, state TEXT, created REAL, updated REAL, decided_by TEXT, result TEXT);
    CREATE TABLE IF NOT EXISTS engage_queue(id INTEGER PRIMARY KEY, slack_ts TEXT UNIQUE, action TEXT, url TEXT,
        handle TEXT, text TEXT, approver TEXT, approved_at REAL, approved_day TEXT, due_at REAL, state TEXT,
        browser_id TEXT, result TEXT, done_at REAL);
    CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY, channel TEXT, thread_ts TEXT, text TEXT,
        created REAL, sent REAL);
    CREATE TABLE IF NOT EXISTS suggested(url TEXT PRIMARY KEY, handle TEXT, day TEXT, ts REAL);
    CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
    CREATE TABLE IF NOT EXISTS spend(id INTEGER PRIMARY KEY, ts REAL, month TEXT, day TEXT, lane TEXT, purpose TEXT, model TEXT,
        gen_id TEXT, prompt_tokens INTEGER, completion_tokens INTEGER, cost REAL, cost_source TEXT, status INTEGER);
    CREATE TABLE IF NOT EXISTS browser_use(id INTEGER PRIMARY KEY, ts REAL, month TEXT, day TEXT, source TEXT, allowed INTEGER,
        reason TEXT, task TEXT);
    CREATE TABLE IF NOT EXISTS actions_log(id INTEGER PRIMARY KEY, ts REAL, day TEXT, platform TEXT, kind TEXT, ref TEXT, state TEXT);
    CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, ts REAL, day TEXT, event TEXT, by_ TEXT, ref TEXT, outcome TEXT, detail TEXT);
    CREATE TABLE IF NOT EXISTS coach_lists(id TEXT PRIMARY KEY, day TEXT, slot TEXT, plan TEXT, created REAL, header_ts TEXT, n_tasks INTEGER, state TEXT);
    CREATE TABLE IF NOT EXISTS coach_tasks(id INTEGER PRIMARY KEY, list_id TEXT, day TEXT, slot TEXT, platform TEXT, kind TEXT, units INTEGER,
        url TEXT, handle TEXT, excerpt TEXT, open_url TEXT, drafts TEXT, posted_at REAL, deadline REAL, window_min INTEGER, slack_ts TEXT,
        state TEXT, created REAL, updated REAL, decided_by TEXT, nudged INTEGER DEFAULT 0, last_polled REAL DEFAULT 0,
        verify_state TEXT, verify_note TEXT);
    CREATE INDEX IF NOT EXISTS coach_tasks_day ON coach_tasks(day, state);
    CREATE TABLE IF NOT EXISTS coach_nudges(id INTEGER PRIMARY KEY, ts REAL, day TEXT, kind TEXT, ref TEXT, text TEXT);
    CREATE TABLE IF NOT EXISTS coach_stats(id INTEGER PRIMARY KEY, ts REAL, day TEXT, platform TEXT, followers INTEGER, impressions INTEGER,
        source TEXT, celebrated INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, day TEXT, plan TEXT, created REAL, posted_at REAL, header_ts TEXT, n_items INTEGER,
        state TEXT, answered_at REAL, items TEXT);
    CREATE TABLE IF NOT EXISTS batch_pool(url TEXT PRIMARY KEY, handle TEXT, text TEXT, age TEXT, posted_at REAL, added REAL, used_at REAL, source TEXT);
    CREATE TABLE IF NOT EXISTS batch_targets(id INTEGER PRIMARY KEY, url TEXT UNIQUE, platform TEXT, note TEXT, text TEXT, author TEXT,
        added REAL, by_ TEXT, state TEXT, used_at REAL);
    CREATE TABLE IF NOT EXISTS watchlist(handle TEXT PRIMARY KEY, added REAL, by_ TEXT);
    """)
    try:
        c.execute("ALTER TABLE batch_pool ADD COLUMN source TEXT")
    except Exception:
        pass
    c.close()

def j(row):
    return dict(row) if row is not None else None

# ---------------------------------------------------------------- notes / memory
def read_notes():
    try:
        return open(NOTES).read()
    except FileNotFoundError:
        return ""

SECRETISH = re.compile(r"(xox[abpe]-|sk-[A-Za-z0-9]{16,}|api[_ -]?key\s*[:=]|password\s*[:=]|token\s*[:=])", re.I)

def append_note(text, by):
    text = re.sub(r"\s+", " ", text).strip()[:600]
    if not text:
        return None, "Nothing to remember (empty)."
    if SECRETISH.search(text):
        return None, "Not saved: that looks like a password/key/token. Never put secrets in notes."
    line = f"- {now_ist():%Y-%m-%d %H:%M} IST ({'the owner' if by == OWNER else by}): {text}\n"
    with LOCK:
        cur = read_notes()
        if "## Remembered from Slack" not in cur:
            cur = cur.rstrip("\n") + "\n\n## Remembered from Slack\n"
            open(NOTES, "w").write(cur)
        with open(NOTES, "a") as f:
            f.write(line)
    log(f"notes append by {by}: {text[:80]}")
    return line, None

def get_history(c, thread, turns=MEMORY_TURNS):
    rows = c.execute("SELECT role, text, ts FROM memory WHERE thread=? ORDER BY id DESC LIMIT ?",
                     (thread, turns * 2)).fetchall()
    return [dict(r) for r in reversed(rows)]

def add_memory(c, thread, user_text, bot_text):
    t = time.time()
    user_text, bot_text = G.scrub(user_text or ""), G.scrub(bot_text or "")   # never store secrets
    if user_text:
        c.execute("INSERT INTO memory(thread, role, text, ts) VALUES(?,?,?,?)", (thread, "user", user_text[:4000], t))
    if bot_text:
        c.execute("INSERT INTO memory(thread, role, text, ts) VALUES(?,?,?,?)", (thread, "assistant", bot_text[:4000], t + 0.001))
    # keep last 20 turns (40 messages) per thread
    c.execute("""DELETE FROM memory WHERE thread=? AND id NOT IN
                 (SELECT id FROM memory WHERE thread=? ORDER BY id DESC LIMIT ?)""", (thread, thread, MEMORY_TURNS * 2))

OB_START, OB_END = "<!-- onboarding:start -->", "<!-- onboarding:end -->"

def write_onboarding(client, block, dry=False):
    """Replace (or insert) the onboarding block in ~/agency/clients/<client>/notes.md."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,40}", client or ""):
        raise ValueError("bad client slug")
    path = NOTES if client == "example-client" else f"{os.path.dirname(CLIENT_DIR)}/{client}/notes.md"
    cur = open(path).read() if os.path.exists(path) else f"# {client}: notes for the bot\n"
    blk = f"{OB_START}\n{block.strip()}\n{OB_END}\n"
    if OB_START in cur and OB_END in cur:
        new = cur[:cur.index(OB_START)] + blk + cur[cur.index(OB_END) + len(OB_END):].lstrip("\n")
    elif "## Remembered from Slack" in cur:
        i = cur.index("## Remembered from Slack"); new = cur[:i] + blk + "\n" + cur[i:]
    else:
        new = cur.rstrip("\n") + "\n\n" + blk
    if not dry:
        with LOCK:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "w").write(new)
        log(f"onboarding block written for {client} ({len(block)} chars)")
    return {"ok": True, "path": path, "dry_run": dry, "chars": len(new), "preview": new[-3000:] if dry else ""}

# ---------------------------------------------------------------- engagement status
def pause_info():
    if os.path.exists(PAUSE):
        try:
            return True, open(PAUSE).read().strip()[:400]
        except Exception:
            return True, "(pause file present)"
    return False, ""

def today():
    return now_ist().strftime("%Y-%m-%d")

def engage_status(c):
    paused, reason = pause_info()
    used = c.execute("SELECT COUNT(*) FROM engage_queue WHERE approved_day=? AND state IN ('queued','running','done','failed','stopped')",
                     (today(),)).fetchone()[0]
    queued = [dict(r) for r in c.execute("SELECT id, slack_ts, action, url, due_at FROM engage_queue WHERE state='queued' ORDER BY due_at")]
    for q in queued:
        q["due_ist"] = fmt(datetime.fromtimestamp(q["due_at"], IST))
    return {"paused": paused, "pause_reason": reason, "today": today(), "approved_today": used, "cap": int(plat_caps("x", c).get("engage_per_day", DAILY_CAP)),
            "queued": queued}

def set_pause(c, reason, by, dry=False):
    n = c.execute("SELECT COUNT(*) FROM engage_queue WHERE state='queued'").fetchone()[0]
    if not dry:
        with open(PAUSE, "w") as f:
            f.write(f"{now_ist():%Y-%m-%d %H:%M:%S} IST paused by {by}: {reason}\n")
        c.execute("UPDATE engage_queue SET state='cancelled', result='cancelled by pause' WHERE state='queued'")
        log(f"PAUSE by {by}: {reason} (cancelled {n} queued)")
        if str(reason).startswith("auto"):          # a platform warning seen by the worker/drafter/coach: warm-up drops one step
            ramp_warning(c, "x", reason, by)
    return n

def outbox_add(c, text, thread_ts="", channel=CHANNEL):
    c.execute("INSERT INTO outbox(channel, thread_ts, text, created) VALUES(?,?,?,?)", (channel, thread_ts or "", text[:3900], time.time()))

def ledger_write(row):
    cols = ["ts_ist", "day", "queue_id", "platform", "account", "action", "post_url", "text", "approved_by",
            "approved_at_ist", "slack_ts", "status", "browser_task_id", "result"]
    new = not os.path.exists(LEDGER)
    with open(LEDGER, "a") as f:
        if new:
            f.write("\t".join(cols) + "\n")
        f.write("\t".join(re.sub(r"[\t\r\n]+", " ", str(row.get(k, "")))[:500] for k in cols) + "\n")

# ---------------------------------------------------------------- guardrails core (2026-10-12)
def cfg():
    return GUARD.get()

def audit_path():
    return os.environ.get("CS_AUDIT") or (cfg().get("audit") or {}).get("log") or f"{CLIENT_DIR}/audit-log.tsv"

def upsell_path():
    return os.environ.get("CS_UPSELL") or (cfg().get("scope") or {}).get("upsell_log") or f"{CLIENT_DIR}/upsell-log.md"

def audit_ev(c, event, by="", ref="", outcome="", detail="", dry=False):
    """Every action / guard decision. Dry runs are not written (they are only returned)."""
    if dry:
        return
    try:
        c.execute("INSERT INTO audit(ts, day, event, by_, ref, outcome, detail) VALUES(?,?,?,?,?,?,?)",
                  (time.time(), today(), event, by, str(ref), outcome, G.scrub(str(detail))[:500]))
    except Exception as e:
        log(f"audit db error {e}")
    for attempt in range(3):
        try:
            G.audit(audit_path(), event, detail, by, ref, outcome)
            break
        except Exception as e:
            log(f"audit file error (try {attempt + 1}) {e}")
            time.sleep(0.2)
    else:
        try:   # never lose a row silently: spool it next to the DB and tell the owner
            with open(f"{CLIENT_DIR}/audit-spool.tsv", "a") as f:
                f.write(f"{now_ist():%Y-%m-%d %H:%M:%S}\t{event}\t{by}\t{ref}\t{outcome}\t{G.scrub(str(detail))[:400]}\n")
        except Exception:
            pass
        notify_owner(f"audit-log write failed for {event} {ref}")

OUTBOUND_EVENTS = ("engage_run",)

def audit_reconcile(c, path=None, dry=False):
    """Every finished outbound engage action must have an audit row. Append missing ones marked 'backfilled'."""
    path = path or audit_path()
    have = set()
    try:
        for line in open(G.expand(path)):
            f = line.rstrip("\n").split("\t")
            if len(f) >= 4 and f[1] in OUTBOUND_EVENTS: have.add(f[3])
    except FileNotFoundError:
        pass
    rows = c.execute("SELECT * FROM engage_queue WHERE state IN ('done','failed','stopped') AND done_at IS NOT NULL ORDER BY done_at").fetchall()
    added = []
    for q in rows:
        if q["slack_ts"] in have:
            continue
        ts = datetime.fromtimestamp(q["done_at"], IST).strftime("%Y-%m-%d %H:%M:%S")
        clean = lambda v: re.sub(r"[\t\r\n]+", " ", G.scrub(str(v)))[:400]
        row = [ts, "engage_run", "worker", q["slack_ts"], f"{q['state']} (backfilled)",
               clean(f"backfilled from engage_queue/n8n history: {q['action']} {q['url']} approved_by={q['approver']}")]
        added.append(row)
    if added and not dry:
        new = not os.path.exists(G.expand(path))
        with open(G.expand(path), "a") as f:
            if new: f.write("ts_ist\tevent\tby\tref\toutcome\tdetail\n")
            for r in added: f.write("\t".join(r) + "\n")
    return added

def notify_owner(text, dry=False):
    if dry or not NOTIFY:
        return False
    try:
        subprocess.Popen([f"{HOME}/agency/bin/notify.sh", "[example-client bot] " + G.scrub(text)[:600]],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception as e:
        log(f"notify error {e}")
        return False

def month():
    return now_ist().strftime("%Y-%m")

def is_approver(by):
    return by in (cfg()["approvals"].get("approvers") or [OWNER])

def pause_all_info():
    if os.path.exists(PAUSE_ALL):
        try:
            return True, open(PAUSE_ALL).read().strip()[:300]
        except Exception:
            return True, "(pause file present)"
    return False, ""

def spend_status(c):
    sp = cfg()["spend"]
    spent = c.execute("SELECT COALESCE(SUM(cost),0) FROM spend WHERE month=?", (month(),)).fetchone()[0] or 0.0
    budget = float(sp.get("monthly_ai_budget_usd") or 10)
    pct = 100.0 * spent / budget if budget > 0 else 0.0
    level = ("hard" if pct >= float(sp.get("hard_stop_pct", 150)) else "pause" if pct >= float(sp.get("pause_nonessential_pct", 100))
             else "warn" if pct >= float(sp.get("warn_pct", 80)) else "ok")
    return {"spent": round(spent, 4), "budget": budget, "pct": round(pct, 1), "level": level, "month": month()}

def browser_status(c):
    cap = int(cfg()["spend"].get("browser_tasks_per_month") or 60)
    used = c.execute("SELECT COUNT(*) FROM browser_use WHERE month=? AND allowed=1", (month(),)).fetchone()[0]
    return {"used": used, "cap": cap, "left": max(cap - used, 0)}

def spend_thresholds(c):
    """Called after each recorded LLM cost: one in-channel message per level per month."""
    s = spend_status(c)
    msgs = {
        "warn": (f":warning: Heads-up: {s['pct']:.0f}% of this month's AI budget is used (${s['spent']:.2f} of ${s['budget']:.2f}). "
                 f"Everything keeps working. At 100% I'll pause the extras (engagement ideas, research browsing) so we don't quietly go over. "
                 f"Type `limits` to see usage."),
        "pause": (f":pause_button: This month's AI budget is used up (${s['spent']:.2f} of ${s['budget']:.2f}). I've paused the non-essential AI: "
                  f"engagement ideas and research browsing. Chat, drafts, approvals and safety checks still work. "
                  f"To keep everything on, the owner can upgrade the plan or raise the budget in guardrails.yaml; it resets on the 1st."),
        "hard": (f":octagonal_sign: AI spend reached {s['pct']:.0f}% of the monthly budget, so I've stopped all AI replies until the 1st "
                 f"(safety checks still run). the owner can raise the budget or upgrade the plan to switch it back on."),
    }
    order = ["warn", "pause", "hard"]
    if s["level"] == "ok":
        return s
    for lvl in order[: order.index(s["level"]) + 1]:
        k = f"spend_{lvl}_{s['month']}"
        if not c.execute("SELECT 1 FROM kv WHERE k=?", (k,)).fetchone():
            c.execute("INSERT OR REPLACE INTO kv(k, v) VALUES(?,?)", (k, str(time.time())))
            if lvl == s["level"]:       # only the highest new level is announced
                outbox_add(c, msgs[lvl])
                audit_ev(c, "spend_" + lvl, "state-helper", s["month"], f"{s['pct']}%", f"${s['spent']:.2f} of ${s['budget']:.2f}")
                if lvl != "warn":
                    notify_owner(f"example-client AI spend {s['pct']:.0f}% of ${s['budget']:.0f} this month ({lvl}).")
    return s

def record_spend(row):
    c = db()
    try:
        with LOCK:
            c.execute("BEGIN IMMEDIATE")
            c.execute("""INSERT INTO spend(ts, month, day, lane, purpose, model, gen_id, prompt_tokens, completion_tokens, cost, cost_source, status)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (time.time(), month(), today(), row.get("lane"), row.get("purpose"), row.get("model"), row.get("gen_id"),
                       row.get("pt") or 0, row.get("ct") or 0, float(row.get("cost") or 0), row.get("src"), row.get("status") or 0))
            spend_thresholds(c)
            c.execute("COMMIT")
    except Exception as e:
        log(f"record_spend error {type(e).__name__}: {e}")
        try: c.execute("ROLLBACK")
        except Exception: pass
    finally:
        c.close()

def engage_used(c, platform):
    rows = c.execute("SELECT url FROM engage_queue WHERE approved_day=? AND state IN ('queued','running','done','failed','stopped')",
                     (today(),)).fetchall()
    return sum(1 for r in rows if G.platform_of(r[0]) == platform)

def posts_used(c, platform):
    return c.execute("SELECT COUNT(*) FROM actions_log WHERE day=? AND platform=? AND kind='post' AND state IN ('issued','done')",
                     (today(), platform)).fetchone()[0]

def posts_used_week(c, platform, now=None):
    """IST week Mon-Sun count of issued/done posts (for LinkedIn posts_per_week ceilings)."""
    d = datetime.fromtimestamp(float(now or time.time()), IST)
    start = (d - timedelta(days=d.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    days = [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    return c.execute(
        "SELECT COUNT(*) FROM actions_log WHERE platform=? AND kind='post' AND state IN ('issued','done') AND day IN (%s)"
        % ",".join("?" * 7), (platform, *days)).fetchone()[0]

def comments_on(eff, platform):
    return platform in COMMENT_PLATFORMS and bool((eff["platforms"].get(platform) or {}).get("comments_on"))

def plat_pause(c, platform, now=None):
    """LinkedIn/Facebook 7-day pause after a warning/checkpoint: (paused, until_ts, reason)."""
    r = c.execute("SELECT v FROM kv WHERE k=?", (f"plat_pause:{platform}",)).fetchone()
    if not r:
        return False, 0.0, ""
    try:
        d = json.loads(r[0])
    except Exception:
        return False, 0.0, ""
    until = float(d.get("until") or 0)
    return (float(now or time.time()) < until), until, str(d.get("reason") or "")

def plat_caps(platform, c=None, eff=None):
    """Today's automated caps. With a ramp (Viral) the cap is the warm-up step's value, never above the tier ceiling.
    LinkedIn/Facebook engagement is 0 unless comments_on, and 0 while the platform's 7-day warning pause runs.
    Pass the open connection c inside a POST transaction (a second connection would wait on the write lock)."""
    eff = eff or cfg()
    base = dict(eff["platforms"].get(platform) or {"engage_per_day": 0, "posts_per_day": 0})
    own = c is None
    if own:
        c = db()
    try:
        if (eff.get("ramp") or {}).get("enabled"):
            st = ramp_state(c, platform, eff)
            base["ceiling_engage"], base["ceiling_posts"] = int(base.get("engage_per_day", 0)), int(base.get("posts_per_day", 0))
            for m in ("engage_per_day", "posts_per_day"):
                base[m] = CO.ramp_cap(eff, platform, m, st["step"])
            base["step"] = st["step"]
        if platform in COMMENT_PLATFORMS:
            base["comments_on"] = comments_on(eff, platform)
            paused, until, why = plat_pause(c, platform)
            if paused:
                base["paused_until"], base["pause_reason"] = until, why
            if not base["comments_on"] or paused:
                base["engage_per_day"] = 0
        pw = int(base.get("posts_per_week") or 0)
        if pw:
            base["posts_per_week"] = pw
            used_w = posts_used_week(c, platform)
            base["posts_used_week"] = used_w
            # daily soft cap cannot exceed what's left this week
            left_w = max(pw - used_w, 0)
            base["posts_per_day"] = min(int(base.get("posts_per_day", 0) or 0), left_w)
    finally:
        if own:
            c.close()
    return base

# ---------------------------------------------------------------- warm-up ramps (2026-10-12)
def ramp_key(eff, platform):
    return f"ramp:{eff['plan']}:{platform}"

def ramp_state(c, platform, eff=None, now=None, write=True):
    """Ramp state for the current plan + platform; created at step 0 the first time it is needed (new account or new tier)."""
    eff = eff or cfg(); r = eff.get("ramp") or {}
    if not r.get("enabled"):
        return {"step": 0, "enabled": False}
    k = ramp_key(eff, platform)
    row = c.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    st = json.loads(row[0]) if row else None
    if st is None and r.get("start_date"):   # warm-up start date from guardrails (IST midnight)
        try:
            st = CO.ramp_new(datetime.strptime(str(r["start_date"]), "%Y-%m-%d").replace(tzinfo=IST).timestamp())
        except Exception:
            st = None
    st2, changed = CO.ramp_eval(st or CO.ramp_new(now), r, now)
    if write and (st is None or changed):
        c.execute("INSERT OR REPLACE INTO kv(k, v) VALUES(?,?)", (k, json.dumps(st2)))
        if changed:
            audit_ev(c, "ramp_up", "state-helper", platform, f"step {st2['step'] + 1}", f"plan {eff['plan']}: a week without warnings")
    return {**st2, "enabled": True}

def ramp_warning(c, platform, reason, by="state-helper", dry=False, eff=None, now=None):
    """Any warning (captcha, lock, 'unusual activity', limit notice) -> one step back and the weekly clock restarts."""
    eff = eff or cfg(); r = eff.get("ramp") or {}
    if not r.get("enabled"):
        audit_ev(c, "ramp_warning", by, platform, "no_ramp", str(reason)[:200], dry)
        return None
    st = ramp_state(c, platform, eff, now=now, write=not dry)
    new = CO.ramp_warn(st, r, now)
    new.pop("enabled", None)
    if not dry:
        c.execute("INSERT OR REPLACE INTO kv(k, v) VALUES(?,?)", (ramp_key(eff, platform), json.dumps(new)))
    audit_ev(c, "ramp_warning", by, platform, f"step {st['step'] + 1}->{new['step'] + 1}", str(reason)[:200], dry)
    return new

def gaps(platform="x", eff=None):
    """(min_gap_s, extra_gap_s, max_per_hour) for engagement on this platform. X: engagement.*; LinkedIn/Facebook: platforms.<p>.engage_*."""
    eff = eff or cfg()
    if platform in COMMENT_PLATFORMS:
        pc = eff["platforms"].get(platform) or {}
        return (int(float(pc.get("engage_min_gap_minutes", 20)) * 60), int(float(pc.get("engage_extra_gap_minutes", 10)) * 60),
                int(pc.get("engage_max_per_hour", 1) or 0))
    e = eff["engagement"]
    return int(float(e.get("min_gap_minutes", 7)) * 60), int(float(e.get("extra_gap_minutes", 8)) * 60), int(e.get("max_per_hour", 0) or 0)

def _plat_rows(c, platform, sql, args):
    return [r for r in c.execute(sql, args).fetchall() if G.platform_of(r["url"]) == platform]

def hour_slot_due(c, t, per_hour, platform="x"):
    """Earliest time >= t such that fewer than per_hour engagement actions on this platform fall in the hour before it (0 = no cap)."""
    if not per_hour:
        return t
    rows = [r["ts"] for r in _plat_rows(c, platform, "SELECT url, COALESCE(done_at, due_at) AS ts FROM engage_queue WHERE state IN "
                                        "('queued','running','done','failed') AND COALESCE(done_at, due_at) > ?", (t - 3600,))]
    rows.sort()
    while len([x for x in rows if t - 3600 < x <= t]) >= per_hour:
        inside = [x for x in rows if t - 3600 < x <= t]
        t = inside[0] + 3600 + random.randint(60, 600)
    return t

def guard_browser(c, b):
    """Gate for every browser task (agent research, drafter, approved actions). Counts allowed tasks per month."""
    dry, task, src = bool(b.get("dry_run")), str(b.get("task") or ""), str(b.get("source") or "agent")
    essential = bool(b.get("essential"))
    paused, reason = pause_all_info()
    bad, doms = G.check_task_urls(task, cfg())
    bs, ss = browser_status(c), spend_status(c)
    res = {"ok": True, "reason": "", "message": "", "browser": bs, "spend": ss, "allowed_domains": doms}
    if paused and src != "worker-approved":
        res.update(ok=False, reason="paused_all", message=G.msg("paused", reason=reason))
    elif bad:
        res.update(ok=False, reason="domain", message=G.msg("domain", bad=bad, doms=doms))
    elif bs["used"] >= bs["cap"]:
        res.update(ok=False, reason="browser_cap", message=G.msg("browser_cap", used=bs["used"], cap=bs["cap"]))
    elif ss["level"] in ("pause", "hard") and not essential:
        res.update(ok=False, reason="spend", message=G.msg("spend_pause", spent=ss["spent"], budget=ss["budget"]))
    if not dry:
        c.execute("INSERT INTO browser_use(ts, month, day, source, allowed, reason, task) VALUES(?,?,?,?,?,?,?)",
                  (time.time(), month(), today(), src, 1 if res["ok"] else 0, res["reason"], G.scrub(task)[:300]))
        if res["ok"]:
            res["browser"] = browser_status(c)
        audit_ev(c, "browser_task", src, "", "allowed" if res["ok"] else "refused:" + res["reason"], task[:200])
    return res

def content_precheck(text, kind="post"):
    blocks, hints = G.content_regex(text, kind, read_notes(), cfg())
    return {"ok": not blocks, "blocks": blocks, "hints": hints,
            "message": G.content_message(blocks, cfg()) if blocks else ""}

def manual_used(c, platform, day=None, states=("done",)):
    """Manual actions per budget key, from the client's ✅ on action-list tasks (actions outside the list are not seen)."""
    q = "SELECT kind, units FROM coach_tasks WHERE day=? AND platform=? AND state IN (%s)" % ",".join("?" * len(states))
    out = {}
    for k, u in c.execute(q, (day or today(), platform, *states)).fetchall():
        key = CO.KINDS.get(k, {}).get("budget", "replies") if not (k == "answer_own" and platform != "x") else "comments"
        out[key] = out.get(key, 0) + int(u or 1)
    return out

BUDGET_WORDS = {"replies": "replies", "quotes": "quote posts", "originals": "own posts", "likes": "likes", "follows": "follows",
                "cold_dms": "cold DMs", "comments": "comments", "reshares": "reshares", "shares": "shares",
                "invitations_per_week": "invitations a week"}

def manual_line(c, eff, p, step):
    bud = CO.manual_budget(eff, p, step)
    if not bud:
        return ""
    used = manual_used(c, p)
    parts = []
    for k, v in bud.items():
        if k == "per_hour":
            continue
        if k in ("likes", "follows", "invitations_per_week"):
            parts.append(f"{BUDGET_WORDS.get(k, k)} up to {v}")
        elif k == "cold_dms":
            parts.append("no cold DMs (answer people who write first)" if not v else f"cold DMs up to {v}")
        else:
            parts.append(f"{BUDGET_WORDS.get(k, k)} {used.get(k, 0)}/{v}")
    if bud.get("per_hour"):
        parts.append(f"max {bud['per_hour']} an hour")
    return f"• {G.PLATFORM_NAMES[p]}: " + " · ".join(parts)

def limits_text(c, eff=None):
    eff = eff or cfg()
    ramp_on = bool((eff.get("ramp") or {}).get("enabled"))
    st = {p: ramp_state(c, p, eff) for p in ("x", "linkedin", "facebook")} if ramp_on else {}
    sl = CO.step_label(eff, st["x"]["step"]) if ramp_on else ""
    lines = [f"*Plan: {eff.get('plan_label', eff.get('plan'))}*" + (f" ({sl})" if sl else "") + f" · today's limits ({now_ist():%a %d %b}, IST)"]
    e = eff["engagement"]; gap, extra, per_hour = int(e.get("min_gap_minutes", 7)), int(e.get("extra_gap_minutes", 8)), int(e.get("max_per_hour", 0) or 0)
    lines.append("*Automated (the bot does it, each public action still needs your ✅):*")
    for p in ("x", "linkedin", "facebook"):
        cap = plat_caps(p, c, eff); eu, pu = engage_used(c, p), posts_used(c, p)
        ce, cp = cap.get("engage_per_day", 0), cap.get("posts_per_day", 0)
        ceil_e = f", ceiling {cap['ceiling_engage']}" if ramp_on and cap.get("ceiling_engage", ce) != ce else ""
        ceil_p = f", ceiling {cap['ceiling_posts']}" if ramp_on and cap.get("ceiling_posts", cp) != cp else ""
        if p == "x" and ce:
            acts = "/".join(a for a in (e.get("allowed_actions") or []) if a in X_ACTIONS)
            eng = (f"replies {eu}/{ce} ({max(ce - eu, 0)} left{ceil_e}; {acts} only, {gap}{'-' + str(gap + extra) if extra else '+'} min apart"
                   + (f", max {per_hour} an hour" if per_hour else "") + ")")
        elif p in COMMENT_PLATFORMS and cap.get("paused_until"):
            eng = f"comments PAUSED until {fmt(datetime.fromtimestamp(cap['paused_until'], IST))} after a warning (0 a day)"
        elif p in COMMENT_PLATFORMS and cap.get("comments_on") and ce:
            g2, x2, h2 = gaps(p, eff)
            eng = (f"comments {eu}/{ce} ({max(ce - eu, 0)} left{ceil_e}; approval-only, {g2 // 60}+ min apart, max {h2} an hour)")
        elif not ce:
            eng = "engagement off (manual only)"
        else:
            eng = f"engagement 0/{ce} (not switched on yet)"
        lines.append(f"• {G.PLATFORM_NAMES[p]}: {eng} · posts {pu}/{cp} ({max(cp - pu, 0)} left{ceil_p})")
    if any(comments_on(eff, p) for p in COMMENT_PLATFORMS):
        lines.append("_LinkedIn and Facebook terms restrict automation, so comments there stay tiny and only happen after your ✅; "
                     "any warning or checkpoint pauses that platform for 7 days at 0._")
    bc = eff.get("batches") or {}
    if bc.get("enabled"):
        lines.append(f"*Draft batches:* {bc.get('items_min', 2)}-{bc.get('items_max', 4)} ready drafts per Slack message, {BA.cadence_text(bc)}; "
                     f"skipped after {bc.get('skip_after_ignored', 2)} batches without a reaction (one 'still there?' a day at most).")
    if (eff.get("coach") or {}).get("enabled") and eff.get("manual_budget"):
        lines.append("*Manual budget (your own actions today, from the action lists):*")
        for p in (eff["coach"].get("platforms") or []):
            ml = manual_line(c, eff, p, (st.get(p) or {}).get("step", 0))
            if ml:
                lines.append(ml)
        co = eff["coach"]
        lines.append(f"_About 90% of a realistic human pace, far below the platforms' hard limits. Lists at {' and '.join(co.get('list_times') or [])} IST, "
                     f"max {co.get('max_nudges_per_day', 3)} reminders a day, quiet {'-'.join(co.get('quiet_hours') or [])} IST._")
    else:
        lines.append("*Manual-action coach:* part of the Viral plan (daily action lists with drafts, reminders, weekly score).")
    ss, bs = spend_status(c), browser_status(c)
    lines.append(f"*This month:* AI spend ${ss['spent']:.2f} of ${ss['budget']:.2f} ({ss['pct']:.0f}%) · browser tasks {bs['used']}/{bs['cap']}"
                 + {"ok": "", "warn": " · over 80%, heads-up", "pause": " · extras paused (budget used up)", "hard": " · AI stopped (budget)"}[ss["level"]])
    pa, pr = pause_all_info(); pe, per = pause_info()
    lines.append("*Status:* " + (f"EVERYTHING PAUSED ({pr})" if pa else ("engagement paused (" + per + ")" if pe else "all running")))
    ap = eff["approvals"]
    lines.append("*Approvals:* every public post and reply needs a ✅" + (" (routine auto-posting is opted in)" if ap.get("auto_post_routine") else " (auto-posting off)"))
    lines.append("_Never automated: follows, unfollows, DMs, " + ("likes, " if "like" not in (e.get("allowed_actions") or []) else "mass likes, ")
                 + "copy-paste comments or posts, hashtag spam._")
    return "\n".join(lines)

def guard_summary(c):
    """Short version injected into the agent prompt every turn."""
    eff = cfg()
    ss, bs = spend_status(c), browser_status(c)
    pa, pr = pause_all_info(); pe, _ = pause_info()
    parts = []
    for p in ("x", "linkedin", "facebook"):
        cap = plat_caps(p, c)
        kind = "replies" if p == "x" else ("approval-only comments" if cap.get("comments_on") else "engagement")
        extra = "" if p == "x" else (" (paused after a warning)" if cap.get("paused_until") else ("" if cap.get("comments_on") else " (not switched on)"))
        parts.append(f"{G.PLATFORM_NAMES[p]}: {kind} {engage_used(c, p)}/{cap.get('engage_per_day')}{extra}, posts {posts_used(c, p)}/{cap.get('posts_per_day')}")
    co = eff.get("coach") or {}
    plan = (f"Plan {eff.get('plan_label')}" + (" (manual-action coach on: action lists 2x a day; growth comes from the client's own replies)" if co.get("enabled") else "")
            + (f" (draft batches for ✅ {BA.cadence_text(eff.get('batches') or {})}; 'comment on <link>' lines up a LinkedIn/Facebook comment)" if (eff.get("batches") or {}).get("enabled") else "") + ". ")
    return (plan + "; ".join(parts) + f". Allowed automated actions: {', '.join(eff['engagement'].get('allowed_actions') or [])}. "
            + f"AI spend {ss['pct']:.0f}% of ${ss['budget']:.0f} ({ss['level']}). Browser tasks {bs['used']}/{bs['cap']} this month. "
            + ("EVERYTHING PAUSED. " if pa else "") + ("Engagement paused. " if pe else "")
            + f"Allowed browser sites: {', '.join(G.allowed_domains(cfg()))}. "
            + f"Politics opt-in: {'yes' if cfg()['content'].get('allow_politics') else 'no'}; religion opt-in: {'yes' if cfg()['content'].get('allow_religion') else 'no'}.")

def weekly_summary(c, days=7):
    since = time.time() - days * 86400
    ev = {r[0]: r[1] for r in c.execute("SELECT event||':'||COALESCE(outcome,''), COUNT(*) FROM audit WHERE ts>=? GROUP BY 1", (since,))}
    cnt = lambda pre: sum(v for k, v in ev.items() if k.startswith(pre))
    refused = [(k, v) for k, v in ev.items() if k.split(":", 1)[1].startswith(("refused", "block", "skipped"))]
    posts = c.execute("SELECT platform, COUNT(*) FROM actions_log WHERE ts>=? AND kind='post' AND state IN ('issued','done') GROUP BY 1", (since,)).fetchall()
    eng = c.execute("SELECT action, state, COUNT(*) FROM engage_queue WHERE approved_at>=? GROUP BY 1,2", (since,)).fetchall()
    sp = c.execute("SELECT COALESCE(SUM(cost),0), COUNT(*) FROM spend WHERE ts>=?", (since,)).fetchone()
    ss, bs = spend_status(c), browser_status(c)
    lines = [f"*Safety and limits, last {days} days*",
             "• Posts sent to Buffer: " + (", ".join(f"{G.PLATFORM_NAMES.get(p, p)} {n}" for p, n in posts) or "none"),
             "• Engagement: " + (", ".join(f"{a} {s} {n}" for a, s, n in eng) or "none"),
             f"• Blocked or redirected by guardrails: {sum(v for _, v in refused)}"
             + (" (" + ", ".join(f"{k.split(':', 1)[0]} {k.split(':', 1)[1].split(':')[-1]} x{v}" for k, v in sorted(refused, key=lambda x: -x[1])[:8]) + ")" if refused else ""),
             f"• Content checks: {cnt('content_check:pass')} passed, {cnt('content_check:block')} blocked, {cnt('content_check:fail')} could not run",
             f"• Out-of-scope asks: {cnt('out_of_scope')} (logged in upsell-log.md) · pasted passwords caught: {cnt('secret_pasted')}",
             f"• Pauses: {cnt('pause_all')} pause-everything, {cnt('engage_pause')} engagement pauses, {cnt('resume_all')} resumes",
             f"• AI spend this week ${sp[0]:.2f} over {sp[1]} calls; month so far ${ss['spent']:.2f} of ${ss['budget']:.2f} ({ss['pct']:.0f}%) · browser tasks {bs['used']}/{bs['cap']}",
             f"• Platform warnings: {cnt('engage_autopause') + cnt('ramp_warning') or 'none'}"]
    bt = c.execute("SELECT COUNT(*), SUM(answered_at IS NOT NULL), SUM(n_items) FROM batches WHERE state='posted' AND posted_at>=?", (since,)).fetchone()
    if bt[0] or (cfg().get("batches") or {}).get("enabled"):
        its = {}
        for r in c.execute("SELECT state FROM proposals WHERE created>=? AND payload LIKE '%\"batch_id\"%'", (since,)):
            k = {"approved": "approved", "done": "approved", "published": "approved", "acting": "approved", "partial": "approved", "failed": "approved",
                 "stopped": "approved", "discarded": "skipped", "expired": "expired", "refused": "refused"}.get(r[0], "waiting")
            its[k] = its.get(k, 0) + 1
        lines.append(f"• Draft batches: {bt[0] or 0} sent ({bt[1] or 0} answered), {cnt('batch:skipped_ignored')} skipped as ignored, "
                     f"{cnt('batch:still_there')} 'still there?'; items: " + (", ".join(f"{v} {k}" for k, v in sorted(its.items())) or "none")
                     + f"; edits {cnt('batch_edit')}; LinkedIn/Facebook pauses after warnings: {cnt('platform_pause')}")
    rows = [dict(r) for r in c.execute("SELECT kind, state, verify_state FROM coach_tasks WHERE created>=? AND state<>'draft'", (since,))]
    if rows or (cfg().get("coach") or {}).get("enabled"):
        done, sug, pct, weak, tip = CO.coordination(rows)
        ver = sum(1 for r in rows if r.get("verify_state") == "verified")
        lines += [f"*Coordination score, last {days} days:* " + (f"{done}/{sug} manual tasks done ({pct:.0f}%)" if sug else "no action lists yet")
                  + (f", {ver} checked on the profile" if ver else ""),
                  f"• Coaching tip: {tip}"]
    return "\n".join(lines)

# ---------------------------------------------------------------- proposals / decisions
def get_prop(c, ts):
    r = c.execute("SELECT * FROM proposals WHERE slack_ts=?", (ts,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["payload"] = json.loads(d["payload"] or "{}")
    return d

def set_state(c, ts, state, by=None, result=None):
    c.execute("UPDATE proposals SET state=?, updated=?, decided_by=COALESCE(?, decided_by), result=COALESCE(?, result) WHERE slack_ts=?",
              (state, time.time(), by, json.dumps(result) if result is not None else None, ts))

def register(c, b):
    ts = str(b["slack_ts"])
    replaced = []
    if b.get("kind") == "post":
        for r in c.execute("SELECT slack_ts FROM proposals WHERE kind='post' AND thread_ts=? AND state='editing'", (b.get("thread_ts"),)).fetchall():
            p = get_prop(c, r["slack_ts"])
            replaced.append({"slack_ts": p["slack_ts"], "drafts": p["payload"].get("drafts", [])})
            set_state(c, p["slack_ts"], "replaced", result={"replaced_by": ts})
    c.execute("INSERT OR REPLACE INTO proposals(slack_ts, kind, channel, thread_ts, payload, state, created, updated) VALUES(?,?,?,?,?,?,?,?)",
              (ts, b.get("kind"), b.get("channel") or CHANNEL, b.get("thread_ts") or ts, json.dumps(b.get("payload") or {}), "pending", time.time(), time.time()))
    if b.get("kind") == "engage":
        p = b.get("payload") or {}
        p["handle"] = str(p.get("handle") or "").lstrip("@")
        c.execute("UPDATE proposals SET payload=? WHERE slack_ts=?", (json.dumps(p), ts))
        c.execute("INSERT OR IGNORE INTO suggested(url, handle, day, ts) VALUES(?,?,?,?)", (p.get("url"), (p.get("handle") or "").lower(), today(), time.time()))
    return {"ok": True, "replaced": replaced}

def active_hours(eff=None):
    """Approved actions run only inside the batch active hours when batches are on for the plan (None = any time)."""
    b = (eff or cfg()).get("batches") or {}
    return (b.get("active_hours") or ["09:00", "21:00"]) if b.get("enabled") else None

def first_due(c, platform="x", now=None):
    """Due time for a newly approved action: per-platform min gap + random extra, hourly cap, inside active hours."""
    gap, extra, per_hour = gaps(platform)
    now = float(now or time.time())
    rows = _plat_rows(c, platform, "SELECT url, COALESCE(done_at, due_at) AS ts FROM engage_queue WHERE state IN ('queued','running','done','failed')", ())
    last = max([r["ts"] for r in rows if r["ts"]] or [0])
    t = now + random.randint(45, 150)
    if last:
        t = max(t, last + gap + random.randint(0, extra))
    hrs = active_hours()
    for _ in range(6):
        if hrs and not BA.in_active(t, hrs):
            t = BA.next_active_start(t, hrs) + random.randint(60, 900)
        t2 = hour_slot_due(c, t, per_hour, platform)
        if t2 == t and (not hrs or BA.in_active(t, hrs)):
            break
        t = t2
    return t

def _post_drafts_split(c, p):
    ok, capped = [], []
    for d in p["payload"].get("drafts", []):
        pl = G.platform_of(d.get("service"))
        used, cap = posts_used(c, pl), int(plat_caps(pl, c).get("posts_per_day", 0))
        (capped if used >= cap else ok).append({**d, "platform": pl, "used": used, "cap": cap})
    return ok, capped

def _content_gate(c, p, kind, text, platform, by, dry, out, now=False):
    """Regex (free) then hand the LLM classifier to n8n. Returns True if the caller may issue directly (no LLM check)."""
    pre = content_precheck(text, kind)
    if not pre["ok"]:
        audit_ev(c, "content_check", by, p["slack_ts"], "block:regex:" + pre["blocks"][0]["cat"], text[:200], dry)
        out["reply"] = pre["message"] + " Nothing was published" + (
            "; the draft is still saved. React ✏️ (or reply with what to change) and I'll redo it, or ❌ to delete it." if kind == "post" else ".")
        if kind == "reply" and not dry:
            set_state(c, p["slack_ts"], "refused", by, {"reason": "content:" + pre["blocks"][0]["cat"]})
        return False
    if not cfg()["content"].get("llm_check", True):
        return True
    if not dry:
        set_state(c, p["slack_ts"], "checking", by, {"decision": "approve", "now": bool(now)})
    out["check"] = {"slack_ts": p["slack_ts"], "kind": kind, "now": bool(now), "by": by,
                    "prompt": G.classifier_prompt(text, kind, platform, read_notes(), pre["hints"], cfg())}
    out["reply"] = ""
    return False

def _media_urls(p, d):
    """Media for a draft: per-draft fields win over proposal-level ones (media_url / media_urls / image_url / assets)."""
    urls = []
    for src in (d, p.get("payload") or {}):
        for k in ("media_urls", "media"):
            v = src.get(k)
            if isinstance(v, list): urls += [str(x.get("url") if isinstance(x, dict) else x) for x in v if x]
            elif isinstance(v, str) and v: urls.append(v)
        for k in ("media_url", "image_url"):
            if src.get(k): urls.append(str(src[k]))
        if urls: break
    return [u for u in dict.fromkeys(urls) if u and u != "None"]

def edit_post_input(p, d, mode):
    """Buffer editPost replaces the post: it must carry text and/or media, otherwise Buffer answers
    'Post must have either text or media'. Keep id/mode/saveToDraft/schedulingType as before."""
    inp = {"id": d["post_id"], "mode": mode, "saveToDraft": False, "schedulingType": "automatic"}
    text = d.get("text") or (p.get("payload") or {}).get("text") or ""
    if text: inp["text"] = text
    assets = d.get("assets") or (p.get("payload") or {}).get("assets")
    if not assets:
        urls = _media_urls(p, d)
        assets = [{"image": {"url": u}} for u in urls] if urls else None
    if assets: inp["assets"] = assets
    if not text and not assets:
        raise ValueError(f"draft {d['post_id']} has neither text nor media; refusing to call Buffer editPost")
    return inp

def post_issue(c, p, by, now, dry, out):
    ok, capped = _post_drafts_split(c, p)
    if not ok:
        d = capped[0]
        out["reply"] = G.msg("cap_post", platform=d["platform"], used=d["used"], cap=d["cap"]) + " (Type `limits` to see today's numbers.)"
        audit_ev(c, "publish", by, p["slack_ts"], "refused:cap_post", ",".join(x["platform"] for x in capped), dry)
        if not dry: set_state(c, p["slack_ts"], "pending", by)
        return out
    mode = "shareNow" if (now or p["payload"].get("when") == "now") else "addToQueue"
    gap_note = ""
    if mode == "shareNow":
        for d in ok:
            g = int((cfg()["platforms"].get(d["platform"]) or {}).get("posts_min_gap_minutes", 0) or 0)
            last = c.execute("SELECT MAX(ts) FROM actions_log WHERE kind='post' AND platform=? AND state IN ('issued','done')", (d["platform"],)).fetchone()[0]
            if g and last and time.time() - last < g * 60:
                mode, gap_note = "addToQueue", G.msg("post_gap", platform=d["platform"], gap=g)
                break
    if not dry:
        set_state(c, p["slack_ts"], "acting", by, {"decision": "approve", "mode": mode,
                  "skipped_cap": [x["platform"] for x in capped]})
        for d in ok:
            c.execute("INSERT INTO actions_log(ts, day, platform, kind, ref, state) VALUES(?,?,?,?,?,?)",
                      (time.time(), today(), d["platform"], "post", d["post_id"], "issued"))
    audit_ev(c, "publish", by, p["slack_ts"], "issued", f"{mode} " + ",".join(d["platform"] for d in ok)
             + (f"; over cap, kept as draft: {','.join(x['platform'] for x in capped)}" if capped else ""), dry)
    out["buffer_ops"] = [{"op": "publish", "post_id": d["post_id"], "service": d.get("service"), "mode": mode,
                          "body": {"query": "mutation E($input: EditPostInput!) { editPost(input: $input) { ... on PostActionSuccess { post { id status dueAt externalLink } } ... on MutationError { message } } }",
                                   "variables": {"input": edit_post_input(p, d, mode)}}}
                         for d in ok]
    if capped:
        out["cap_note"] = " ".join(G.msg("cap_post", platform=x["platform"], used=x["used"], cap=x["cap"]) for x in capped[:1])
    if gap_note:
        out["cap_note"] = (out.get("cap_note", "") + " " + gap_note).strip()
        audit_ev(c, "publish_pacing", by, p["slack_ts"], "queued_not_now", gap_note[:120], dry)
    out["reply"] = ""
    return out

def duplicate_post(c, p):
    """No identical text: compare with posts published on the same platform(s) in the lookback window."""
    text = p["payload"].get("text") or ""
    lim = float(cfg()["content"].get("post_duplicate_similarity", 0.9) or 0)
    if not lim or not text:
        return None
    since = time.time() - float(cfg()["engagement"].get("duplicate_lookback_days", 30)) * 86400
    mine = {G.platform_of(d.get("service")) for d in p["payload"].get("drafts", [])}
    for r in c.execute("SELECT payload FROM proposals WHERE kind='post' AND state IN ('published','partial','acting') AND created>=? AND slack_ts<>?",
                       (since, p["slack_ts"])).fetchall():
        try:
            q = json.loads(r[0] or "{}")
        except Exception:
            continue
        if not mine & {G.platform_of(d.get("service")) for d in q.get("drafts", [])}:
            continue
        rr, _ = G.most_similar(text, [q.get("text") or ""])
        if rr >= lim:
            return sorted(mine & {G.platform_of(d.get("service")) for d in q.get("drafts", [])})[0]
    return None


def cancel_disallowed_queued(c):
    """Cancel any queued engagement whose action is no longer allowed on the current plan (e.g. likes after X April 2026 ban)."""
    acts = [a for a in (cfg()["engagement"].get("allowed_actions") or []) if a in ALLOWED_ACTIONS]
    if not acts:
        return 0
    ph = ",".join("?" * len(acts))
    rows = [dict(r) for r in c.execute(f"SELECT id, slack_ts, action, url FROM engage_queue WHERE state='queued' AND action NOT IN ({ph})", acts)]
    if not rows:
        return 0
    c.execute(f"UPDATE engage_queue SET state='cancelled', result='action not allowed' WHERE state='queued' AND action NOT IN ({ph})", acts)
    for r in rows:
        audit_ev(c, "engage_run", "guardrails", r["slack_ts"], "cancelled:action_not_allowed", f"{r['action']} {r['url']}")
        log(f"CANCEL disallowed queued {r['action']} id={r['id']}")
    return len(rows)

def engage_checks(c, p, by):
    """Deterministic engagement checks. Returns (reason, message) or (None, None)."""
    pl = p["payload"]; e = cfg()["engagement"]
    platform = G.platform_of(pl.get("url"))
    pa, par = pause_all_info()
    if pa: return "paused_all", G.msg("paused", reason=par)
    paused, reason = pause_info()
    if paused: return "paused", f"Not done: engagement is paused ({reason}). Say 'resume engagement' first (the owner only). Nothing was done."
    act = str(pl.get("action") or "")
    url = str(pl.get("url") or "")
    P_ = G.PLATFORM_NAMES.get(platform, platform)
    if act == "like" and act in ALLOWED_ACTIONS and act not in (e.get("allowed_actions") or []):
        return "like_manual", G.msg("like_manual")
    if act not in (e.get("allowed_actions") or []) or act not in ALLOWED_ACTIONS:
        return "action_not_allowed", G.msg("action_not_allowed", action=act or "that")
    if platform == "x":
        if act not in X_ACTIONS or "x.com/" not in url:
            return "action_not_allowed", G.msg("action_not_allowed", action=act or "that")
    elif platform in COMMENT_PLATFORMS:
        if act != "comment" or f"{platform}.com/" not in url:
            return "action_not_allowed", (f"On {P_} I only leave a single approved comment on a post (no likes, shares, follows or messages). "
                                          "Safe alternative: do that one yourself in the app.")
        if not comments_on(cfg(), platform):
            return "platform_off", (f"Automated comments on {P_} aren't part of the {cfg().get('plan_label')} plan. "
                                    "Safe alternative: I can draft the comment here and you post it yourself.")
        pz, until, why = plat_pause(c, platform)
        if pz:
            return "plat_paused", (f"{P_} comments are paused until {fmt(datetime.fromtimestamp(until, IST))} because {P_} showed a warning or checkpoint "
                                   f"({why[:120]}). Its terms restrict automation, so after any warning I stop for 7 days. "
                                   "Safe alternative: post the draft yourself in the app if it still fits.")
    else:
        return "platform_off", (f"Engagement on {P_} isn't switched on. Safe alternative: I can draft the comment here and you post it yourself.")
    used, cap = engage_used(c, platform), int(plat_caps(platform, c).get("engage_per_day", 0))
    if used >= cap: return "cap_engage", G.msg("cap_engage", platform=platform, used=used, cap=cap)
    bs = browser_status(c)
    if bs["used"] >= bs["cap"]: return "browser_cap", G.msg("browser_cap", used=bs["used"], cap=bs["cap"])
    if act in ("reply", "comment"):
        since = time.time() - float(e.get("duplicate_lookback_days", 30)) * 86400
        prev = [r[0] for r in c.execute("SELECT text FROM engage_queue WHERE action IN ('reply','comment') AND approved_at>=? AND text<>''", (since,))]
        r, _ = G.most_similar(pl.get("text") or "", prev)
        if r >= float(e.get("duplicate_similarity", 0.85)): return "duplicate", G.msg("duplicate")
    return None, None

def engage_issue(c, p, by, dry, out):
    reason, m = engage_checks(c, p, by)
    pl = p["payload"]
    if reason:
        if not dry: set_state(c, p["slack_ts"], "refused", by, {"reason": reason})
        audit_ev(c, "engage", by, p["slack_ts"], "refused:" + reason, f"{pl.get('action')} {pl.get('url')}", dry)
        out["reply"] = m; return out
    platform = G.platform_of(pl.get("url"))
    used, cap = engage_used(c, platform), int(plat_caps(platform, c).get("engage_per_day", 0))
    due = first_due(c, platform)
    if not dry:
        set_state(c, p["slack_ts"], "approved", by)
        c.execute("""INSERT INTO engage_queue(slack_ts, action, url, handle, text, approver, approved_at, approved_day, due_at, state)
                     VALUES(?,?,?,?,?,?,?,?,?, 'queued')""", (p["slack_ts"], pl["action"], pl["url"], pl.get("handle"), pl.get("text") or "", by, time.time(), today(), due))
    audit_ev(c, "engage", by, p["slack_ts"], "queued", f"{pl.get('action')} {pl.get('url')} due {fmt(datetime.fromtimestamp(due, IST))}", dry)
    d = datetime.fromtimestamp(due, IST)
    when = d.strftime("%H:%M") + ("" if d.strftime("%Y-%m-%d") == today() else d.strftime(" on %a %d %b"))
    what = "comments" if platform in COMMENT_PLATFORMS else "engagement"
    out["reply"] = (f"Approved. I'll {pl['action']} at about {when} IST (paced, one action only). "
                    f"{G.PLATFORM_NAMES.get(platform, platform)} {what} today: {used + 1}/{cap}.")
    return out

def guard_verdict(c, b):
    """Second half of the pre-publish check: n8n ran the classifier, the helper judges it (fail closed) and only then issues."""
    ts, dry, raw = str(b.get("slack_ts") or ""), bool(b.get("dry_run")), b.get("raw")
    by = b.get("by") or OWNER
    p = get_prop(c, ts)
    out = {"ok": True, "op": "verdict", "dry_run": dry, "thread_ts": p["thread_ts"] if p else "", "reply": "", "buffer_ops": [], "run_drafter": False}
    if not p:
        return {**out, "ok": False, "error": "unknown proposal"}
    if not is_approver(by):
        return {**out, "ok": False, "error": "not the approver"}
    if p["state"] != "checking" and not (dry and p["state"] in ("pending", "editing")):
        return {**out, "ok": False, "error": f"already handled ({p['state']})"}
    v = G.parse_verdict(raw)
    kind = "post" if p["kind"] == "post" else "reply"
    text = p["payload"].get("text") or ""
    if v is None and cfg()["content"].get("fail_closed", True):
        audit_ev(c, "content_check", by, ts, "fail", str(raw)[:200], dry)
        if not dry: set_state(c, ts, "pending", by)
        out["reply"] = G.msg("check_failed"); return out
    if v is not None and not v["ok"]:
        if v["category"] == "politics" and cfg()["content"].get("allow_politics") or v["category"] == "religion" and cfg()["content"].get("allow_religion"):
            v = {**v, "ok": True}
    if v is not None and not v["ok"]:
        audit_ev(c, "content_check", by, ts, "block:llm:" + v["category"], v["reason"], dry)
        if not dry: set_state(c, ts, "pending" if kind == "post" else "refused", by, {"reason": "content:" + v["category"]})
        out["reply"] = G.content_message(v, cfg()) + " Nothing was published" + (
            "; the draft is still saved. React ✏️ (or tell me what to change) and I'll redo it, or ❌ to delete it." if kind == "post" else ".")
        return out
    audit_ev(c, "content_check", by, ts, "pass", (v or {}).get("reason", "") , dry)
    if not dry: set_state(c, ts, "pending", by)
    p["state"] = "pending"
    if p["kind"] == "post":
        return post_issue(c, p, by, bool(b.get("now")), dry, out)
    return engage_issue(c, p, by, dry, out)

def pause_all(c, by, reason, dry):
    n_q = c.execute("SELECT COUNT(*) FROM engage_queue WHERE state='queued'").fetchone()[0]
    n_pend = c.execute("SELECT COUNT(*) FROM proposals WHERE state IN ('pending','editing','checking')").fetchone()[0]
    holds = []
    for r in c.execute("SELECT slack_ts, result FROM proposals WHERE kind='post' AND state IN ('published','partial')").fetchall():
        try:
            res = json.loads(r["result"] or "{}").get("results") or []
        except Exception:
            res = []
        for x in res:
            due = x.get("dueAt")
            if x.get("ok") and x.get("op") == "publish" and str(x.get("status") or "").lower() in ("scheduled", "buffer", "pending", "") and due:
                try:
                    if datetime.fromisoformat(due.replace("Z", "+00:00")).timestamp() > time.time() + 60:
                        holds.append({"slack_ts": r["slack_ts"], "post_id": x.get("post_id"), "service": x.get("service"),
                                      "body": {"query": "mutation H($input: EditPostInput!) { editPost(input: $input) { ... on PostActionSuccess { post { id status dueAt } } ... on MutationError { message } } }",
                                               "variables": {"input": {"id": x.get("post_id"), "saveToDraft": True}}}})
                except Exception:
                    pass
    if not dry:
        with open(PAUSE_ALL, "w") as f:
            f.write(f"{now_ist():%Y-%m-%d %H:%M:%S} IST paused by {by}: {reason}\n")
        set_pause(c, f"pause everything ({reason})", by)
        c.execute("UPDATE proposals SET state='pending', updated=? WHERE state='checking'", (time.time(),))
    audit_ev(c, "pause_all", by, "", "paused", f"{reason}; queued cancelled {n_q}; approvals on hold {n_pend}; buffer holds {len(holds)}", dry)
    return n_q, n_pend, holds

def op(c, b):
    """Deterministic actions (no LLM). Returns {reply, thread_ts, buffer_ops, run_drafter, check?, hold_ops?}."""
    o, by, dry = b.get("op"), b.get("by") or "", bool(b.get("dry_run"))
    thread = b.get("thread_ts") or ""
    out = {"ok": True, "op": o, "dry_run": dry, "thread_ts": thread, "reply": "", "buffer_ops": [], "run_drafter": False}
    if o == "remember":
        if by != OWNER:
            out["reply"] = "Only the owner can change the client notes. Tell him what to add and he can say 'remember: ...'."; return out
        if G.looks_secret(b.get("text", "")):
            out["reply"] = "Not saved: that looks like a password, key or token. Secrets never go into the notes."; return out
        if dry:
            out["reply"] = f"(dry run) would add to notes.md: {b.get('text','')[:200]}"; return out
        line, err = append_note(b.get("text", ""), by)
        audit_ev(c, "remember", by, "", "saved" if not err else "refused", b.get("text", "")[:200])
        out["reply"] = err or f"Noted. Added to client notes (notes.md):\n> {b.get('text','').strip()[:500]}"
        return out
    if o == "limits":
        out["reply"] = limits_text(c); audit_ev(c, "limits", by, "", "shown", "", dry); return out
    if o == "weekly_report":
        out["reply"] = weekly_summary(c) + "\n_(The full Monday report with post stats is still to be built; this is its safety section.)_"
        return out
    if o == "pause_all":
        n_q, n_pend, holds = pause_all(c, by, b.get("text") or f"<@{by}> said 'pause everything'", dry)
        out["hold_ops"] = holds
        out["reply"] = (f":pause_button: *Paused everything.* Nothing will be published, liked, commented or reposted, and no engagement ideas or "
                        f"research browsing will run. {n_q} queued engagement action(s) cancelled, {n_pend} approval(s) on hold"
                        + (f", {len(holds)} post(s) already in Buffer's queue are being moved back to drafts" if holds else "")
                        + ". Chat still works. Only the owner can say 'resume everything'.") + (" (dry run, nothing changed)" if dry else "")
        if not dry and by != OWNER:
            notify_owner(f"'pause everything' was said in #example-client by <@{by}>. Say 'resume everything' there when it's safe.")
        return out
    if o == "resume_all":
        if by not in (cfg()["audit"].get("resume_all_by") or [OWNER]):
            audit_ev(c, "resume_all", by, "", "refused:not_owner", "", dry)
            out["reply"] = "Only the owner can resume everything, so a pause can't be undone by accident. I've let him know you'd like it back on."
            if not dry: notify_owner(f"<@{by}> asked to 'resume everything' in #example-client. Only you can do it.")
            return out
        pa, pr = pause_all_info()
        if not pa:
            out["reply"] = "Nothing is paused with 'pause everything' right now." + (" (Engagement has its own pause: say 'engagement status'.)" if pause_info()[0] else ""); return out
        pe, per = pause_info()
        still = ""
        if not dry:
            os.remove(PAUSE_ALL)
            if pe and "pause everything" in per:
                os.remove(PAUSE); pe = False
        if pe and "pause everything" not in per:
            still = f" Engagement stays paused for its own reason ({per}); say 'resume engagement' when that's checked."
        audit_ev(c, "resume_all", by, "", "resumed", pr, dry)
        out["reply"] = (f":arrow_forward: Resumed everything (it was paused: {pr}). Approvals that were on hold can be approved again by replying 'approve' in their thread; "
                        f"posts moved back to drafts stay drafts until you approve them again.{still}") + (" (dry run)" if dry else "")
        return out
    if o == "secret":
        can_del = bool(cfg()["access"].get("slack_can_delete_user_messages"))
        audit_ev(c, "secret_pasted", by, b.get("ts", ""), "deleted" if can_del else "warned", "(content not stored)", dry)
        out["reply"] = G.msg("secret_deleted" if can_del else "secret")
        if can_del and not dry: out["delete_ts"] = b.get("ts")
        if not dry: notify_owner(f"A password-like message was posted in #example-client by <@{by}> (ts {b.get('ts','')}). "
                                 + ("Bot deleted it." if can_del else "Bot can't delete it (no scope): asked them to delete it and change the password."))
        return out
    if o == "signin_request":
        audit_ev(c, "signin_request", by, "", "notified", "", dry)
        if not dry: notify_owner(f"<@{by}> asked for a sign-in link in #example-client. Run: ~/agency/bin/signin-link.sh start example-client --minutes 30")
        out["reply"] = ("OK, I've asked the owner for a one-time sign-in link. You'll open it, log in yourself in a private browser window, "
                        "and close it. I never see or store the password.") + (" (dry run, not sent)" if dry else "")
        return out
    if o == "scope":
        ask, summ = str(b.get("text") or "")[:500], str(b.get("summary") or "")[:300]
        if not dry:
            G.upsell(upsell_path(), ask, summ, by)
            if cfg()["scope"].get("notify_owner", True):
                notify_owner(f"Out-of-scope ask in #example-client from <@{by}>: {summ or ask[:200]} (logged in clients/example-client/upsell-log.md)")
        audit_ev(c, "out_of_scope", by, b.get("ts", ""), "logged", summ or ask[:200], dry)
        out["reply"] = ""; out["logged"] = not dry; return out
    if o == "pause":
        n = set_pause(c, b.get("text") or "said 'pause engagement' in #example-client", by, dry)
        audit_ev(c, "engage_pause", by, "", "paused", b.get("text") or "", dry)
        out["reply"] = (f"Engagement paused. {n} queued action(s) cancelled. Nothing will be liked, commented or reposted, and no new suggestions run. "
                        "Say 'resume engagement' to restart.") + (" (dry run, not saved)" if dry else "")
        return out
    if o == "resume":
        if by != OWNER:
            out["reply"] = "Only the owner can resume engagement."; return out
        paused, reason = pause_info()
        if not paused:
            out["reply"] = "Engagement is not paused."; return out
        if pause_all_info()[0]:
            out["reply"] = "Everything is paused right now. Say 'resume everything' first."; return out
        if not dry:
            os.remove(PAUSE); log(f"RESUME by {by} (was: {reason})")
        audit_ev(c, "engage_resume", by, "", "resumed", reason, dry)
        out["reply"] = f"Engagement resumed. (It was paused: {reason})" + (" (dry run)" if dry else "")
        return out
    if o == "engage_status":
        s = engage_status(c)
        out["reply"] = (f"Engagement: {'PAUSED (' + s['pause_reason'] + ')' if s['paused'] else 'on'}. Approved today: "
                        f"{s['approved_today']}/{s['cap']}. Queued: " + (", ".join(f"{q['action']} at {q['due_ist'][11:]}" for q in s['queued']) or "none")
                        + ". Type `limits` for all platforms and spend.")
        return out
    if o == "engage_now":
        if by != OWNER:
            out["reply"] = "Only the owner can start an engagement round."; return out
        plan_ = engage_plan(c, {"dry_run": True})
        if not plan_["ok_to_run"]:
            out["reply"] = plan_["why_not"]; return out
        out["run_drafter"] = True
        out["reply"] = ("OK, looking at recent posts from your watchlist / accounts you follow (read-only — never keyword search). Suggestions in a few minutes. Tip: `watch @handle` or `reply to <link>` to add targets.")
        return out
    if o == "noop":
        return out
    if o in ("batch_edit", "batch_target", "batch_next", "batch_pause", "batch_resume", "batch_status", "batch_help"):
        return batch_op(c, o, b, out)
    if o in ("watch", "unwatch", "watchlist"):
        return watch_op(c, o, b, out)
    # ---- Viral plan / coach (2026-10-12)
    if o == "switch_plan":
        return switch_plan(c, b, out)
    if o == "coach_mark":
        return coach_mark(c, b, out)
    if o == "coach_status":
        out["reply"] = coach_status_text(c); return out
    if o == "coach_now":
        eff = cfg(); co = eff["coach"]
        if by not in set((co.get("client_users") or []) + (eff["approvals"].get("admins") or [OWNER])):
            out["reply"] = "Action lists are for the account owner. the owner can send one with 'action list'."; return out
        if not co.get("enabled"):
            out["reply"] = (f"The action-list coach is part of the Viral plan (this client is on {eff.get('plan_label')}). "
                            "Meanwhile I can suggest a few replies here: say 'suggest engagement'."); return out
        k = f"coach_extra_{today()}"; n = int(kv_get(c, k, "0") or 0)
        if n >= 2:
            out["reply"] = "You've had two extra lists today already; the next regular one comes at the usual time. Keeps the pace human."; return out
        if not dry:
            kv_set(c, k, n + 1)
        out["run_coach"] = not dry
        out["reply"] = "OK, putting together an action list for you (read-only look at X first, about 2 minutes)." + (" (dry run)" if dry else "")
        return out
    if o == "coach_size":
        f = "0.6" if b.get("size") == "shorter" else "1"
        if not dry:
            kv_set(c, "coach_size", f)
        audit_ev(c, "coach_size", by, "", f, "", dry)
        out["reply"] = ("Done, lists will be about 40% shorter from the next one. Say 'normal lists' to go back anytime."
                        if f != "1" else "Back to normal-length lists from the next one.") + (" (dry run)" if dry else "")
        return out
    if o == "ramp_warning":
        p = G.platform_of(b.get("platform") or "x")
        if p in COMMENT_PLATFORMS:
            n, until = platform_warning(c, p, b.get("text") or f"reported by {by} in Slack", by, dry, announce=False)
            out["reply"] = (f"Thanks for telling me. {G.PLATFORM_NAMES[p]} comments are paused for 7 days (until {fmt(datetime.fromtimestamp(until, IST))}, "
                            f"{n} queued cancelled) and drop to 0 a day; " + ("the warm-up restarts at week 1 after that. " if (cfg().get('ramp') or {}).get('enabled') else "")
                            + f"{G.PLATFORM_NAMES[p]}'s terms restrict automation, so we stay careful. Posts through Buffer and your own activity are not affected; "
                            "keep it light for a day or two." + (" (dry run)" if dry else ""))
            if not dry and by != OWNER:
                notify_owner(f"<@{by}> reported a {p} warning in #example-client: {str(b.get('text') or '')[:150]}")
            return out
        new = ramp_warning(c, p, b.get("text") or f"reported by {by} in Slack", by, dry)
        n = set_pause(c, f"warning on {G.PLATFORM_NAMES.get(p, p)} reported by <@{by}> in #example-client", by, dry) if p == "x" else 0
        out["reply"] = (f"Thanks for telling me. I've noted the {G.PLATFORM_NAMES.get(p, p)} warning"
                        + (f", stepped the warm-up back to week {new['step'] + 1}" if new else "")
                        + (f" and paused automated engagement ({n} queued cancelled)" if p == "x" else "")
                        + ". Please keep your own activity light for a day or two (a few replies, no bursts). "
                        + ("the owner can say 'resume engagement' when it looks fine." if p == "x" else "") + (" (dry run)" if dry else ""))
        if not dry and by != OWNER:
            notify_owner(f"<@{by}> reported a {p} warning in #example-client: {str(b.get('text') or '')[:150]}")
        return out
    if o != "decide":
        return {"ok": False, "error": f"unknown op {o}"}
    # ---- decisions on a proposal (reaction or typed reply)
    ts, decision = str(b.get("slack_ts") or ""), b.get("decision")
    p = get_prop(c, ts)
    if not p:
        return {**out, "ok": False, "reply": "", "error": "unknown proposal"}
    out["thread_ts"] = p["thread_ts"]
    if not is_approver(by):
        audit_ev(c, "decide", by, ts, "refused:not_approver", decision, dry)
        return {**out, "ok": False, "reply": "", "error": "not the approver"}
    st = p["state"]
    audit_ev(c, "decide", by, ts, decision, p["kind"], dry)
    if p["payload"].get("batch_id") and not dry:
        batch_answered(c, p["payload"]["batch_id"])
    if p["kind"] == "post":
        if decision == "edit":
            if st != "pending":
                return {**out, "ok": False, "error": f"state {st}"}
            if not dry: set_state(c, ts, "editing", by)
            out["reply"] = "What should I change? Reply in this thread and I'll make a new draft (the old one gets replaced)."
            return out
        if st not in ("pending", "editing"):
            return {**out, "ok": False, "error": f"already handled ({st})"}
        drafts = p["payload"].get("drafts", [])
        if decision == "approve":
            pa, par = pause_all_info()
            if pa:
                audit_ev(c, "publish", by, ts, "refused:paused_all", "", dry)
                out["reply"] = G.msg("paused", reason=par) + " After that, reply 'approve' in this thread."; return out
            dup = duplicate_post(c, p)
            if dup:
                audit_ev(c, "publish", by, ts, "refused:duplicate_post", dup, dry)
                out["reply"] = G.msg("duplicate_post", platform=dup) + " Nothing was published; the draft is still saved."
                return out
            ok, capped = _post_drafts_split(c, p)
            if not ok:
                return post_issue(c, p, by, bool(b.get("now")), dry, out)
            if _content_gate(c, p, "post", p["payload"].get("text") or "", ok[0]["platform"], by, dry, out, now=bool(b.get("now"))):
                return post_issue(c, p, by, bool(b.get("now")), dry, out)
            return out
        elif decision == "discard":
            if not dry: set_state(c, ts, "acting", by, {"decision": "discard"})
            out["buffer_ops"] = [{"op": "delete", "post_id": d["post_id"], "service": d.get("service"),
                                  "body": {"query": "mutation D($input: DeletePostInput!) { deletePost(input: $input) { ... on DeletePostSuccess { id } ... on VoidMutationError { message } } }",
                                           "variables": {"input": {"id": d["post_id"]}}}} for d in drafts]
        else:
            return {**out, "ok": False, "error": "bad decision"}
        if not out["buffer_ops"]:
            if not dry: set_state(c, ts, "failed", by, {"error": "no drafts"})
            out["reply"] = "That preview has no Buffer draft ids, nothing to do."
        return out
    if p["kind"] == "engage":
        if st != "pending":
            return {**out, "ok": False, "error": f"already handled ({st})"}
        pl = p["payload"]
        P_ = G.PLATFORM_NAMES.get(G.platform_of(pl.get("url")), "X")
        if decision == "discard":
            if not dry: set_state(c, ts, "discarded", by)
            out["reply"] = f"OK, skipped. Nothing done on {P_}."; return out
        if decision == "edit":
            if pl.get("batch_id"):
                out["reply"] = (f"Sure. Type `{pl.get('batch_n')}: your new wording` here in this thread and I'll swap it in (checks run again), "
                                f"then react ✅ on item {pl.get('batch_n')}.")
            else:
                out["reply"] = "Engagement ideas are ✅ (do exactly this once) or ❌ (skip). Ask me in the channel for a different wording."
            return out
        reason, m = engage_checks(c, p, by)
        if reason:
            return engage_issue(c, p, by, dry, out)
        if pl.get("action") in ("reply", "comment"):
            if not _content_gate(c, p, "reply", pl.get("text") or "", G.platform_of(pl.get("url")), by, dry, out):
                return out
        return engage_issue(c, p, by, dry, out)
    return {**out, "ok": False, "error": "bad kind"}

def op_finish(c, b):
    ts = str(b.get("slack_ts"))
    p = get_prop(c, ts)
    if not p:
        return {"ok": False, "error": "unknown proposal"}
    res = b.get("results") or []
    decision = (json.loads(p["result"] or "{}") if isinstance(p["result"], str) else {}).get("decision", "approve")
    lines, okc = [], 0
    for r in res:
        svc = {"twitter": "X", "linkedin": "LinkedIn", "facebook": "Facebook"}.get(r.get("service"), r.get("service") or "")
        if r.get("ok"):
            okc += 1
            if r.get("op") == "delete":
                lines.append(f"• {svc}: draft {r.get('post_id')} deleted")
            else:
                st, due, link = r.get("status"), r.get("dueAt"), r.get("externalLink")
                when = ""
                if due:
                    try:
                        when = " for " + fmt(datetime.fromisoformat(due.replace("Z", "+00:00")))
                    except Exception:
                        when = f" for {due}"
                lines.append(f"• {svc}: {st or 'ok'}{when}" + (f" <{link}|view post>" if link else f" (Buffer post {r.get('post_id')})"))
        else:
            lines.append(f"• {svc}: FAILED ({str(r.get('error'))[:200]}), post {r.get('post_id')}")
    if decision == "discard":
        state = "discarded" if okc == len(res) else "failed"
        head = "Discarded." if state == "discarded" else "Discard partly failed:"
    else:
        state = "published" if okc == len(res) else ("partial" if okc else "failed")
        head = {"published": "Done, sent to Buffer:", "partial": "Partly done:", "failed": "Publishing failed, nothing went out:"}[state]
    if not b.get("dry_run"):
        set_state(c, ts, state, result={"decision": decision, "results": res})
        for r in res:
            if r.get("op") == "publish":
                c.execute("UPDATE actions_log SET state=? WHERE kind='post' AND ref=? AND state='issued'", ("done" if r.get("ok") else "failed", r.get("post_id")))
        audit_ev(c, "publish_result" if decision != "discard" else "discard_result", "state-helper", ts, state,
                 "; ".join(f"{r.get('service')}:{'ok' if r.get('ok') else 'fail'}:{r.get('status') or r.get('error') or ''}" for r in res))
    skipped = (json.loads(p["result"] or "{}") if isinstance(p["result"], str) else {}).get("skipped_cap") or []
    if skipped and decision != "discard":
        lines.append("• Kept as draft (today's post limit reached): " + ", ".join(G.PLATFORM_NAMES.get(x, x) for x in skipped)
                     + ". Approve again tomorrow with 'approve' in this thread.")
    return {"ok": True, "thread_ts": p["thread_ts"], "reply": head + "\n" + "\n".join(lines), "state": state}

def hold_done(c, b):
    """After 'pause everything': n8n moved queued Buffer posts back to drafts; record + tell the channel (via outbox)."""
    res, dry = b.get("results") or [], bool(b.get("dry_run"))
    okc = sum(1 for r in res if r.get("ok"))
    if not dry:
        for r in res:
            if r.get("ok"):
                c.execute("UPDATE proposals SET state='pending', updated=? WHERE slack_ts=?", (time.time(), r.get("slack_ts")))
                c.execute("UPDATE actions_log SET state='held' WHERE kind='post' AND ref=?", (r.get("post_id"),))
        audit_ev(c, "pause_all_buffer_hold", "state-helper", "", f"{okc}/{len(res)}", json.dumps(res)[:300])
        txt = (f"Pause everything: {okc} of {len(res)} post(s) in Buffer's queue moved back to drafts."
               + ("" if okc == len(res) else " :warning: Some could not be moved; please pause the queue in Buffer yourself (Publish > the channel > Pause queue)."))
        outbox_add(c, txt)
    return {"ok": True, "held": okc, "total": len(res)}

# ---------------------------------------------------------------- engagement drafting helpers

# ---------------------------------------------------------------- X watchlist (auto reply targets; not keyword search)
def watchlist_get(c):
    return [r[0] for r in c.execute("SELECT handle FROM watchlist ORDER BY added").fetchall()]

def watchlist_add(c, handle, by, dry=False):
    h = str(handle or "").lstrip("@").lower()
    if not re.fullmatch(r"[a-z0-9_]{1,15}", h):
        return None, "That doesn't look like an X handle (letters, numbers, underscore, max 15)."
    if h == OWN_HANDLE:
        return None, "That's your own account — no need to watch it."
    if not dry:
        c.execute("INSERT OR IGNORE INTO watchlist(handle, added, by_) VALUES(?,?,?)", (h, time.time(), by))
    return h, None

def watchlist_remove(c, handle, dry=False):
    h = str(handle or "").lstrip("@").lower()
    if not re.fullmatch(r"[a-z0-9_]{1,15}", h):
        return None, "That doesn't look like an X handle."
    if not dry:
        c.execute("DELETE FROM watchlist WHERE handle=?", (h,))
    return h, None

def x_follows_cached(c):
    """Accounts @your_x_handle follows, as cached by the existing read flow (kv x_follows; JSON list). No extra X API calls."""
    try:
        v = json.loads(kv_get(c, "x_follows", "[]") or "[]")
    except Exception:
        v = []
    return [str(h).lstrip("@").lower() for h in v if re.fullmatch(r"@?[A-Za-z0-9_]{1,15}", str(h))]

def x_allowed_targets(c):
    """Reply targets: client watchlist + followed accounts only. Empty set -> propose nothing."""
    return {h.lower() for h in watchlist_get(c)} | set(x_follows_cached(c))

def filter_reply_candidates(c, cands, key="handle"):
    allowed = x_allowed_targets(c)
    return [x for x in cands if str(x.get(key) or "").lstrip("@").lower() in allowed]

def x_targets_onboarding(c, dry=False):
    """Onboarding check: the client must name their target accounts before any X reply is proposed."""
    if x_allowed_targets(c):
        return True
    if not dry and kv_get(c, "onboard_x_targets_asked") != today():
        kv_set(c, "onboard_x_targets_asked", today())
        outbox_add(c, "Before I suggest any X replies: which accounts do you want to engage with? Send `watch @handle` for each "
                      "(customers, peers, creators in your niche). I only reply to accounts on that list or accounts you follow.", "")
        audit_ev(c, "onboarding", "batches", "x_targets", "asked", "reply proposals blocked until target accounts are set")
    return False

def auto_x_read_spec(c, now=None):
    """Allowed auto-target reads: watchlist (from:handles) or the Following home timeline. Never keyword/topic search."""
    wl = watchlist_get(c)[:8]
    if wl:
        q = "(" + " OR ".join(f"from:{h}" for h in wl) + ") -filter:replies"
        return {"type": "x_watch", "url": CO.x_search_link(q, "live"), "handles": wl, "topic": "watchlist", "query": q}
    return {"type": "x_following", "url": "https://x.com/home", "handles": [], "topic": "following", "query": ""}

def ask_links_tip():
    return ("No auto-reply targets lined up yet. Send `reply to <x link>`, or `watch @handle` for accounts whose recent posts I can draft replies to "
            "(or accounts you follow — I read Following, never keyword search). Say `watchlist` to see who's on it.")

def engage_plan(c, b):
    """X engagement ideas: targets from watchlist or Following only (X bans automated replies based on keyword searches)."""
    cancel_disallowed_queued(c)
    s = engage_status(c)
    pa, par = pause_all_info()
    ss, bs = spend_status(c), browser_status(c)
    cap = int(plat_caps("x", c).get("engage_per_day", 0))
    why = ""
    if pa: why = G.msg("paused", reason=par)
    elif s["paused"]: why = f"Engagement is paused ({s['pause_reason']}). Say 'resume engagement' first."
    elif ss["level"] in ("pause", "hard"): why = G.msg("spend_pause", spent=ss["spent"], budget=ss["budget"])
    elif bs["used"] >= bs["cap"]: why = G.msg("browser_cap", used=bs["used"], cap=bs["cap"])
    elif engage_used(c, "x") >= cap: why = G.msg("cap_engage", platform="x", used=engage_used(c, "x"), cap=cap)
    elif b.get("source") == "schedule" and (cfg().get("batches") or {}).get("enabled") and batch_live(cfg(), time.time())[0]:
        why = "Scheduled engagement ideas now come inside the hourly draft batches (one Slack message instead of several)."
    spec = auto_x_read_spec(c)
    name, query, url = spec["topic"], spec.get("query") or "", spec["url"]
    ok = not why
    if ok and not b.get("dry_run"):
        g = guard_browser(c, {"task": url, "source": "drafter"})
        if not g["ok"]:
            ok, why = False, g["message"]
    if not ok:
        audit_ev(c, "engage_round", "drafter", "", "skipped", why[:200], bool(b.get("dry_run")))
    gap, extra, per_hour = gaps()
    return {**s, "cap": cap, "topic": name, "query": query, "search_url": url, "read_type": spec["type"],
            "watchlist": watchlist_get(c), "ok_to_run": ok, "why_not": why,
            "allowed_actions": [a for a in (cfg()["engagement"].get("allowed_actions") or []) if a in ALLOWED_ACTIONS],
            "min_gap_minutes": gap // 60, "max_per_hour": per_hour, "plan": cfg()["plan_label"]}


def engage_filter(c, b):
    out, handles = [], set()
    day = today()
    seen_h = {r[0] for r in c.execute("SELECT handle FROM suggested WHERE day=?", (day,))}
    seen_h |= {(r[0] or "").lower() for r in c.execute("SELECT handle FROM engage_queue WHERE approved_day=?", (day,))}
    for p in b.get("posts") or []:
        url = str(p.get("url") or "").split("?")[0]
        m = re.match(r"https://(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/status/(\d+)$", url)
        if not m:
            continue
        h = m.group(1).lower()
        if h not in x_allowed_targets(c):
            continue
        if h == OWN_HANDLE or h in seen_h or h in handles:
            continue
        if c.execute("SELECT 1 FROM suggested WHERE url=?", (url,)).fetchone():
            continue
        handles.add(h)
        out.append({**p, "url": url.replace("twitter.com", "x.com"), "handle": m.group(1)})
    return {"posts": out[:6]}

BANNED = re.compile(r"(northstar|yourbrand|gumroad|my (tool|product|agency|app|agents?)|our (tool|product|agency|app)|dm me|check out|"
                    r"link in bio|sign up|free trial|book a call|https?://|www\.|#\w|@\w)", re.I)

def lint(text):
    reasons = []
    if len(text) > 199: reasons.append("over_200_chars")
    if BANNED.search(text): reasons.append("pitch_link_or_mention")
    if os.path.exists(LINT):
        try:
            r = subprocess.run(["python3", LINT, text], capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                reasons.append("reply-lint: " + (r.stdout + r.stderr).strip()[:150])
        except Exception as e:
            reasons.append(f"lint_error {type(e).__name__}")
    return reasons

# ---------------------------------------------------------------- manual-action coach (2026-10-12, Viral plan)
SKIP_REACTIONS = {"black_right_pointing_double_triangle_with_vertical_bar", "next_track_button", "fast_forward", "track_next"}
DONE_REACTIONS = {"white_check_mark", "heavy_check_mark", "ballot_box_with_check"}
X_KINDS = ("reply_trend", "creator_comment", "quote", "join_thread")
PER_LIST_MAX = {"reply_trend": 4, "creator_comment": 3, "quote": 1, "join_thread": 1, "answer_own": 1, "li_comment": 1, "fb_comment": 1}

def coach_eff(b):
    """force_plan is honoured only in dry runs (tests of the coach while example-client stays on Starter)."""
    fp = str(b.get("force_plan") or "")
    return GUARD.for_plan(fp) if (b.get("dry_run") and fp) else cfg()

def kv_get(c, k, default=None):
    r = c.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return r[0] if r else default

def kv_set(c, k, v):
    c.execute("INSERT OR REPLACE INTO kv(k, v) VALUES(?,?)", (k, str(v)))

def coach_slot(b, now_dt=None):
    s = str(b.get("slot") or "")
    if s in ("morning", "evening", "extra"):
        return s
    return "morning" if (now_dt or now_ist()).hour < 14 else "evening"

def coach_context(c, eff, slot):
    """Budget left today per platform (budget - done - still open), list size, topic, own recent posts."""
    co = eff["coach"]
    left, budget = {}, {}
    for p in (co.get("platforms") or []):
        st = ramp_state(c, p, eff)
        bud = CO.manual_budget(eff, p, st.get("step", 0))
        used = manual_used(c, p, states=("done", "open"))
        budget[p] = bud
        left[p] = {k: max(int(v) - used.get(k, 0), 0) for k, v in bud.items() if isinstance(v, int) and not isinstance(v, bool) and k != "per_hour"}
    size = int((co.get("tasks_per_list") or {}).get("morning" if slot != "evening" else "evening", 6))
    size = max(2, int(round(size * float(kv_get(c, "coach_size", "1") or 1))))
    topics = co.get("topics") or [t[0] for t in TOPICS]
    n = int(kv_get(c, "coach_topic_n", "0") or 0)
    topic = topics[n % len(topics)]
    own = []
    for r in c.execute("SELECT result, created FROM proposals WHERE kind='post' AND state IN ('published','partial') AND created>=? ORDER BY created DESC LIMIT 6",
                       (time.time() - 48 * 3600,)).fetchall():
        try:
            for x in (json.loads(r[0] or "{}").get("results") or []):
                if x.get("ok") and x.get("externalLink"):
                    own.append({"url": x["externalLink"], "platform": G.platform_of(x.get("service")), "created": r[1]})
        except Exception:
            pass
    return {"left": left, "budget": budget, "size": size, "topic": topic, "topic_n": n, "own_posts": own[:3]}

def topic_query(topic):
    for name, q in TOPICS:
        if name.lower() == str(topic).lower():
            return q
    return f'"{topic}"'

def coach_plan(c, b):
    eff = coach_eff(b); co = eff["coach"]; dry = bool(b.get("dry_run"))
    slot = coach_slot(b)
    why = ""
    ss, bs = spend_status(c), browser_status(c)
    pa, par = pause_all_info()
    if not co.get("enabled"):
        why = f"The action-list coach is part of the Viral plan; this client is on {eff.get('plan_label')}."
    elif CO.in_quiet(now_ist(), co.get("quiet_hours")) and not b.get("ignore_quiet"):
        why = "Quiet hours: no action lists between " + " and ".join(co.get("quiet_hours") or []) + " IST."
    elif pa:
        why = G.msg("paused", reason=par)
    elif ss["level"] in ("pause", "hard"):
        why = G.msg("spend_pause", spent=ss["spent"], budget=ss["budget"])
    ctx = coach_context(c, eff, slot)
    urls = []
    # MANUAL action lists only may use a light keyword/trend read (Open-in-app). Automated replies never use these.
    if not why and "x" in (co.get("platforms") or []) and (ctx["left"].get("x", {}).get("replies", 0) + ctx["left"].get("x", {}).get("quotes", 0)) > 0:
        since = (now_ist() - timedelta(days=1)).strftime("%Y-%m-%d")
        cr = [str(h).lstrip("@") for h in (co.get("creators") or []) if re.fullmatch(r"@?[A-Za-z0-9_]{1,15}", str(h))][:8]
        wl = watchlist_get(c)[:8]
        handles = list(dict.fromkeys(cr + wl))[:8]
        if handles:
            urls.append({"kind": "creators", "url": CO.x_search_link("(" + " OR ".join(f"from:{h}" for h in handles) + ") -filter:replies", "live"), "light": True})
        else:
            urls.append({"kind": "trending", "url": CO.x_search_link(f"{topic_query(ctx['topic'])} lang:en -filter:replies min_faves:5 since:{since}", "top"), "light": True})
        if not b.get("posts"):
            allowed = []
            for u in urls[:1]:
                g = guard_browser(c, {"task": u["url"], "source": "coach", "dry_run": dry})
                if g["ok"]:
                    allowed.append(u)
                else:
                    audit_ev(c, "coach_read", "coach", slot, "refused:" + g["reason"], u["url"][:150], dry)
                    break
            urls = allowed
    if not dry and not why:
        kv_set(c, "coach_topic_n", ctx["topic_n"] + 1)
    if why:
        audit_ev(c, "coach_list", "coach", slot, "skipped", why[:200], dry)
    return {"ok_to_run": not why, "why_not": why, "slot": slot, "plan": eff.get("plan"), "plan_label": eff.get("plan_label"),
            "topic": ctx["topic"], "size": ctx["size"], "left": ctx["left"], "search_urls": urls, "own_posts": ctx["own_posts"],
            "platforms": co.get("platforms") or [], "handles": co.get("handles") or {}, "dry_run": dry,
            "windows": co.get("windows_minutes") or {}}

def _parse_llm(raw):
    txt = str(raw or "")
    m = re.search(r"\{[\s\S]*\}", txt)
    if m:
        try:
            d = json.loads(m.group(0))
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    m = re.search(r"\[[\s\S]*\]", txt)
    if m:
        try:
            a = json.loads(m.group(0))
            if isinstance(a, list):
                return {"tasks": a}
        except Exception:
            pass
    return {}

def clean_draft(d, kind):
    d = re.sub(r"\s+", " ", str(d or "")).strip().strip('"').strip()
    d = d.replace("—", ", ").replace("–", "-")
    lim = 260 if kind == "quote" else 199
    if not d or len(d) > lim:
        return None
    blocks, _ = G.content_regex(d, "post" if kind == "quote" else "reply", read_notes(), cfg())
    if blocks:
        return None
    if kind != "quote" and lint(d):
        return None
    if kind == "quote" and re.search(r"(https?://|www\.|#\w)", d):
        return None
    return d

def coach_build(c, b):
    """Validate the LLM's picks + drafts, cap by list size and today's manual budget, add links and deadlines, persist (not dry)."""
    eff = coach_eff(b); co = eff["coach"]; dry = bool(b.get("dry_run")); now = float(b.get("now") or time.time())
    slot = coach_slot(b)
    if not co.get("enabled"):
        return {"ok": False, "why_not": f"Coach is off on the {eff.get('plan_label')} plan.", "messages": []}
    ctx = coach_context(c, eff, slot)
    left = {p: dict(v) for p, v in ctx["left"].items()}
    wins = co.get("windows_minutes") or {}
    handle_x = str((co.get("handles") or {}).get("x") or OWN_HANDLE).lower()
    posts = {}
    for p in (b.get("posts") or []):
        u = str(p.get("url") or "").split("?")[0].replace("twitter.com", "x.com")
        m = re.match(r"https://x\.com/([A-Za-z0-9_]{1,15})/status/(\d+)$", u)
        if m and m.group(1).lower() != handle_x:
            posts[u] = {**p, "url": u, "handle": m.group(1)}
    llm = _parse_llm(b.get("raw"))
    week = time.time() - 7 * 86400
    used_urls = {r[0] for r in c.execute("SELECT url FROM coach_tasks WHERE created>=? AND url IS NOT NULL", (week,))}
    used_handles = {(r[0] or "").lower() for r in c.execute("SELECT handle FROM coach_tasks WHERE day=?", (today(),))}
    prev = [d for r in c.execute("SELECT drafts FROM coach_tasks WHERE created>=?", (time.time() - 30 * 86400,)) for d in json.loads(r[0] or "[]")]
    prev += [r[0] for r in c.execute("SELECT text FROM engage_queue WHERE text<>'' AND approved_at>=?", (time.time() - 30 * 86400,))]
    sim = float(eff["engagement"].get("duplicate_similarity", 0.85))
    tasks, per_kind, dropped = [], {}, []

    def take(platform, key, units):
        have = left.get(platform, {}).get(key, 0)
        if have <= 0:
            return 0
        u = min(units, have); left[platform][key] = have - u
        return u

    def good_drafts(ds, kind):
        out = []
        for d in (ds or [])[:3]:
            d2 = clean_draft(d, kind)
            if not d2:
                dropped.append(("draft_rejected", str(d)[:60])); continue
            r, _ = G.most_similar(d2, prev + out)
            if r >= sim:
                dropped.append(("draft_duplicate", d2[:60])); continue
            out.append(d2)
        return out[:2]

    for it in (llm.get("tasks") or []):
        kind = str(it.get("kind") or "").strip()
        url = str(it.get("url") or "").split("?")[0].replace("twitter.com", "x.com")
        if kind not in X_KINDS or "x" not in (co.get("platforms") or []):
            dropped.append(("bad_kind", kind)); continue
        p = posts.get(url)
        if not p or url in used_urls or p["handle"].lower() in used_handles:
            dropped.append(("unknown_or_repeat_url", url)); continue
        if per_kind.get(kind, 0) >= PER_LIST_MAX.get(kind, 1):
            continue
        ds = good_drafts(it.get("drafts") or ([it.get("text")] if it.get("text") else []), kind)
        if not ds:
            continue
        posted = CO.parse_age(p.get("age"), now)
        win = int(wins.get(kind, 180))
        deadline = (posted or now) + win * 60
        if deadline - now < 20 * 60:
            dropped.append(("window_closed", url)); continue
        units = take("x", CO.KINDS[kind]["budget"], 1)
        if not units:
            dropped.append(("manual_budget", kind)); continue
        per_kind[kind] = per_kind.get(kind, 0) + 1
        used_handles.add(p["handle"].lower()); used_urls.add(url); prev.extend(ds)
        excerpt = re.sub(r"\s+", " ", str(p.get("text") or ""))[:220]
        tasks.append({"kind": kind, "platform": "x", "units": units, "url": url, "handle": p["handle"], "excerpt": excerpt,
                      "age_txt": CO.fmt_age(now - posted) if posted else "", "posted_at": posted, "deadline": deadline, "window_min": win,
                      "drafts": ds, "open_url": CO.open_link(kind, "x", url, ds[0])})
    for op_ in ctx["own_posts"][:1]:
        pl = op_["platform"] if op_["platform"] in (co.get("platforms") or []) else None
        if not pl:
            continue
        units = take(pl, "replies" if pl == "x" else "comments", 1)
        if units:
            tasks.append({"kind": "answer_own", "platform": pl, "units": units, "url": op_["url"], "handle": "", "excerpt": "",
                          "title": "People may have replied to your recent post. Answer each one personally.",
                          "tip": "one specific line or a follow-up question per person; skip emoji-only replies", "posted_at": op_["created"],
                          "deadline": now + int(wins.get("answer_own", 360)) * 60, "window_min": int(wins.get("answer_own", 360)),
                          "drafts": [], "open_url": op_["url"]})
    for pl, kind, angles_key, search in (("linkedin", "li_comment", "li_angles", CO.li_search_link), ("facebook", "fb_comment", "fb_angles", CO.fb_search_link)):
        if pl not in (co.get("platforms") or []):
            continue
        ds = good_drafts(llm.get(angles_key) or [], kind)
        if not ds:
            continue
        units = take(pl, "comments", 3)
        if not units:
            continue
        url = search(ctx["topic"])
        tasks.append({"kind": kind, "platform": pl, "units": units, "url": url, "handle": "", "excerpt": "",
                      "title": f"Find {units} {'posts' if units > 1 else 'post'} about '{ctx['topic']}' from the last hour (the link opens {G.PLATFORM_NAMES[pl]} search, "
                               f"newest first) and leave a thoughtful comment on each. Adapt a draft to each post, never paste the same one twice.",
                      "posted_at": None, "deadline": now + int(wins.get(kind, 120)) * 60, "window_min": int(wins.get(kind, 120)),
                      "drafts": ds, "open_url": url})
    timed = sorted([t for t in tasks if CO.KINDS[t["kind"]]["timed"]], key=lambda t: t["deadline"])
    rest = [t for t in tasks if not CO.KINDS[t["kind"]]["timed"]]
    tasks = (timed + rest)[:ctx["size"]]
    for t in tasks:
        if not CO.KINDS[t["kind"]]["timed"]:
            t["show_deadline"] = False
    list_id = f"{today()}-{slot}-{int(now)}"
    if not dry and tasks:
        c.execute("INSERT INTO coach_lists(id, day, slot, plan, created, n_tasks, state) VALUES(?,?,?,?,?,?, 'draft')",
                  (list_id, today(), slot, eff.get("plan"), now, len(tasks)))
        for t in tasks:
            cur = c.execute("""INSERT INTO coach_tasks(list_id, day, slot, platform, kind, units, url, handle, excerpt, open_url, drafts, posted_at,
                               deadline, window_min, state, created, updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'draft', ?, ?)""",
                            (list_id, today(), slot, t["platform"], t["kind"], t["units"], t["url"], t.get("handle"), t.get("excerpt"),
                             t.get("open_url"), json.dumps(t["drafts"]), t.get("posted_at"), t["deadline"], t["window_min"], now, now))
            t["id"] = cur.lastrowid
    else:
        for i, t in enumerate(tasks):
            t["id"] = -(i + 1)
    bud_parts = []
    for p in (co.get("platforms") or []):
        for k in ("replies", "quotes", "comments"):
            if k in ctx["budget"].get(p, {}):
                bud_parts.append(f"{G.PLATFORM_NAMES[p]} {BUDGET_WORDS[k]} {manual_used(c, p).get(k, 0)}/{ctx['budget'][p][k]}")
    st = ramp_state(c, "x", eff, write=not dry)
    msgs = []
    if tasks:
        msgs.append({"type": "header", "slack": {"channel": CHANNEL, "text": CO.list_header(slot, len(tasks), " · ".join(bud_parts), CO.step_label(eff, st.get("step", 0)), dry),
                                                 "unfurl_links": False, "unfurl_media": False}})
        for i, t in enumerate(tasks):
            tt = dict(t)
            if t.get("show_deadline") is False:
                tt["deadline"] = None
            msgs.append({"type": "task", "task_id": t["id"], "slack": {"channel": CHANNEL, "text": CO.task_message(tt, i + 1, len(tasks)),
                                                                       "unfurl_links": False, "unfurl_media": False}})
    audit_ev(c, "coach_list", "coach", list_id, f"built:{len(tasks)}", f"dropped {len(dropped)}: " + ", ".join(f"{a}" for a, _ in dropped[:10]), dry)
    return {"ok": True, "dry_run": dry, "list_id": list_id if tasks else "", "n_tasks": len(tasks), "messages": msgs, "dropped": dropped[:20],
            "why_not": "" if tasks else "No good tasks this round (nothing fresh enough, or today's manual budget is used up). Nothing posted."}

def coach_register(c, b):
    if b.get("dry_run"):
        return {"ok": True, "dry_run": True}
    lid = str(b.get("list_id") or "")
    c.execute("UPDATE coach_lists SET header_ts=?, state='posted' WHERE id=?", (str(b.get("header_ts") or ""), lid))
    n = 0
    for t in (b.get("tasks") or []):
        if t.get("slack_ts"):
            c.execute("UPDATE coach_tasks SET slack_ts=?, state='open', updated=? WHERE id=? AND list_id=?", (str(t["slack_ts"]), time.time(), int(t["task_id"]), lid))
            n += 1
    c.execute("UPDATE coach_tasks SET state='not_posted' WHERE list_id=? AND state='draft'", (lid,))
    audit_ev(c, "coach_list", "coach", lid, f"posted:{n}", "")
    return {"ok": True, "registered": n}

def coach_mark(c, b, out):
    """✅ (done) / ⏭ (skipped) from the client on a task message. Done is final; late ✅ on an expired task still counts."""
    eff = cfg(); co = eff["coach"]; by, dry = b.get("by") or "", bool(b.get("dry_run"))
    t = c.execute("SELECT * FROM coach_tasks WHERE slack_ts=?", (str(b.get("slack_ts") or ""),)).fetchone()
    if not t:
        return {**out, "ok": False, "error": "unknown task"}
    t = dict(t)
    if by not in (co.get("client_users") or eff["approvals"].get("approvers") or [OWNER]):
        audit_ev(c, "coach_mark", by, t["id"], "refused:not_client", b.get("state"), dry)
        return {**out, "ok": False, "error": "not the client"}
    new = "done" if b.get("state") == "done" else "skipped"
    if t["state"] == "done" or (t["state"] == new) or t["state"] not in ("open", "expired", "skipped"):
        return {**out, "ok": True, "info": f"already {t['state']}"}
    if t["state"] == "skipped" and new == "skipped":
        return out
    ver = None
    if new == "done" and t["platform"] in (co.get("verify_platforms") or []) and t["kind"] in X_KINDS:
        ver = "pending"
    if not dry:
        c.execute("UPDATE coach_tasks SET state=?, decided_by=?, updated=?, verify_state=COALESCE(?, verify_state) WHERE id=?",
                  (new, by, time.time(), ver, t["id"]))
    audit_ev(c, "coach_mark", by, t["id"], new, f"{t['platform']} {t['kind']}", dry)
    if not dry and new == "done":
        rows = c.execute("SELECT state FROM coach_tasks WHERE list_id=? AND state NOT IN ('draft','not_posted')", (t["list_id"],)).fetchall()
        if rows and all(r[0] == "done" for r in rows) and not CO.in_quiet(now_ist(), co.get("quiet_hours")):
            outbox_add(c, CO.list_done_text(t["slot"]))
    out["coach_state"] = new
    return out

def day_rows(c, days_back, include_today=False):
    rows = []
    for i in range(0 if include_today else 1, days_back + (1 if include_today else 1)):
        d = (now_ist() - timedelta(days=i)).strftime("%Y-%m-%d")
        r = c.execute("SELECT COUNT(*), SUM(state='done') FROM coach_tasks WHERE day=? AND state IN ('open','done','skipped','expired')", (d,)).fetchone()
        rows.append({"day": d, "suggested": r[0] or 0, "done": r[1] or 0})
    return CO.day_rates(rows)

def nudge_log(c, kind, ref, text, dry):
    if not dry:
        c.execute("INSERT INTO coach_nudges(ts, day, kind, ref, text) VALUES(?,?,?,?,?)", (time.time(), today(), kind, str(ref), text[:500]))
        outbox_add(c, text)
    audit_ev(c, "coach_" + kind, "coach", ref, "sent" if not dry else "dry", text[:150], dry)

def coach_tick(c, b):
    """Every 30 min from n8n: expire closed tasks, then at most ONE nudge/celebration per tick (quiet hours and daily caps respected),
    then maybe one read-only check (completion or follower count) within the browser allowance."""
    eff = coach_eff(b); co = eff["coach"]; dry = bool(b.get("dry_run"))
    now = float(b.get("now") or time.time()); ndt = datetime.fromtimestamp(now, IST)
    out = {"ok": True, "dry_run": dry, "nudges": [], "celebrations": [], "check": None, "expired": 0, "why": ""}
    if not co.get("enabled"):
        out["why"] = "coach off on this plan"; return out
    n_exp = c.execute("SELECT COUNT(*) FROM coach_tasks WHERE state='open' AND deadline<?", (now,)).fetchone()[0]
    if not dry:
        c.execute("UPDATE coach_tasks SET state='expired', updated=? WHERE state='open' AND deadline<?", (now, now))
    out["expired"] = n_exp
    if CO.in_quiet(ndt, co.get("quiet_hours")):
        out["why"] = "quiet hours"; return out
    if pause_all_info()[0]:
        out["why"] = "everything paused"; return out
    day = ndt.strftime("%Y-%m-%d")
    nudges_today = c.execute("SELECT COUNT(*) FROM coach_nudges WHERE day=? AND kind IN ('reminder','low_completion')", (day,)).fetchone()[0]
    cel_today = c.execute("SELECT COUNT(*) FROM coach_nudges WHERE day=? AND kind IN ('streak','win')", (day,)).fetchone()[0]
    cap = int(co.get("max_nudges_per_day", 3)); ccap = int(co.get("max_celebrations_per_day", 1))
    sent = False
    if nudges_today < cap:
        open_t = [dict(r) for r in c.execute("SELECT * FROM coach_tasks WHERE state='open' AND day=?", (day,))]
        due = CO.due_reminders(open_t, now, int(co.get("remind_before_minutes", 45)))
        if due:
            txt = CO.reminder_text(due, now)
            nudge_log(c, "reminder", ",".join(str(t["id"]) for t in due), txt, dry)
            if not dry:
                c.executemany("UPDATE coach_tasks SET nudged=1 WHERE id=?", [(t["id"],) for t in due])
            out["nudges"].append(txt); sent = True
    if not sent and nudges_today < cap and 10 <= ndt.hour < 20:
        nd = int(co.get("low_completion_days", 2))
        days = day_rows(c, nd)
        last = float(kv_get(c, "coach_low_note_ts", "0") or 0)
        if CO.low_completion(days, float(co.get("low_completion_pct", 50)), nd) and now - last >= nd * 86400:
            rows = c.execute("SELECT kind, COUNT(*) n FROM coach_tasks WHERE day IN (%s) AND state IN ('expired','skipped','open') GROUP BY kind ORDER BY n DESC"
                             % ",".join("?" * len(days)), [d["day"] for d in days]).fetchall()
            txt = CO.low_completion_text(days, [r[0] for r in rows])
            nudge_log(c, "low_completion", day, txt, dry)
            if not dry:
                kv_set(c, "coach_low_note_ts", now)
            out["nudges"].append(txt); sent = True
    if not sent and cel_today < ccap:
        days = day_rows(c, 14)
        n = CO.streak_len(days, float(co.get("streak_pct", 70)))
        if n >= int(co.get("streak_days", 3)) and int(kv_get(c, "coach_streak_celebrated", "0") or 0) < n and kv_get(c, "coach_streak_day") != day:
            txt = CO.streak_text(n)
            nudge_log(c, "streak", n, txt, dry)
            if not dry:
                kv_set(c, "coach_streak_celebrated", n); kv_set(c, "coach_streak_day", day)
            out["celebrations"].append(txt); sent = True
        if not sent:
            for p in ("x", "linkedin", "facebook"):
                r = c.execute("SELECT id, followers, celebrated FROM coach_stats WHERE platform=? AND followers IS NOT NULL ORDER BY ts DESC LIMIT 2", (p,)).fetchall()
                if len(r) == 2 and not r[0][2]:
                    pct = CO.follower_win(r[1][1], r[0][1], float(co.get("win_follower_jump_pct", 3)))
                    if pct:
                        txt = CO.win_text(p, r[1][1], r[0][1], pct)
                        nudge_log(c, "win", r[0][0], txt, dry)
                        if not dry:
                            c.execute("UPDATE coach_stats SET celebrated=1 WHERE id=?", (r[0][0],))
                        out["celebrations"].append(txt); sent = True; break
    # read-only checks (browser), one per tick at most, inside verify_per_day and the monthly browser allowance
    handle = str((co.get("handles") or {}).get("x") or "")
    checks_today = c.execute("SELECT COUNT(*) FROM browser_use WHERE day=? AND source IN ('coach-verify','coach-stats') AND allowed=1", (day,)).fetchone()[0]
    if handle and "x" in (co.get("verify_platforms") or []) and checks_today < int(co.get("verify_per_day", 3)) and not b.get("no_check"):
        t = c.execute("SELECT * FROM coach_tasks WHERE verify_state='pending' AND state='done' AND updated<? ORDER BY updated LIMIT 1", (now - 600,)).fetchone()
        prof = f"https://x.com/{handle}/with_replies"
        if t:
            t = dict(t)
            g = guard_browser(c, {"task": prof, "source": "coach-verify", "dry_run": dry})
            if g["ok"]:
                if not dry:
                    c.execute("UPDATE coach_tasks SET verify_state='checking' WHERE id=?", (t["id"],))
                what = "a quote post of" if t["kind"] == "quote" else "a reply to"
                out["check"] = {"type": "verify", "task_id": t["id"], "task":
                    f"Open {prof} . READ-ONLY: do not click like, reply, follow or anything else; only read. Look at the newest 15 posts and replies "
                    f"shown for @{handle}. Is there {what} the post {t['url']} (by @{t['handle']}), posted in the last 24 hours? "
                    f"Call done with ONLY JSON: {{\"found\": true or false, \"url\": \"permalink of that reply or quote, or empty\"}}."}
        elif ndt.hour >= 19 and kv_get(c, "coach_stats_day") != day:
            g = guard_browser(c, {"task": f"https://x.com/{handle}", "source": "coach-stats", "dry_run": dry})
            if g["ok"]:
                if not dry:
                    kv_set(c, "coach_stats_day", day)
                out["check"] = {"type": "stats", "platform": "x", "task":
                    f"Open https://x.com/{handle} . READ-ONLY: do not click anything except to close a popup. Read the number of Followers shown on the profile. "
                    f"Call done with ONLY JSON: {{\"followers\": \"the follower count exactly as shown, e.g. 1,234 or 1.2K\"}}."}
    return out

FOLLOW_RE = re.compile(r"^([\d.,]+)\s*([KkMm])?$")

def parse_followers(v, shown=""):
    for x in (v, shown):
        s = str(x or "").strip().replace(",", "")
        m = FOLLOW_RE.match(s)
        if m:
            n = float(m.group(1)); mul = {"k": 1e3, "m": 1e6}.get((m.group(2) or "").lower(), 1)
            return int(round(n * mul))
    return None

def coach_verified(c, b):
    dry = bool(b.get("dry_run")); typ = b.get("type") or "verify"
    status, txt = str(b.get("status") or ""), str(b.get("result_text") or "")
    if status == "stopped" or (status != "ok" and DANGER.search(txt) and not re.search(r"read-only mode: refused", txt, re.I)):
        if DANGER.search(txt) or status == "stopped":
            n = set_pause(c, f"auto: coach read-only check stopped on X: {txt[:200]}", "coach", dry)
            if not dry:
                outbox_add(c, f":warning: While checking @{(cfg()['coach'].get('handles') or {}).get('x', OWN_HANDLE)} read-only, the browser hit a warning / login / captcha-type page:\n> {txt[:300]}\n"
                              f"I paused automated engagement ({n} queued action(s) cancelled) and the warm-up drops one step. Nothing was clicked. "
                              f"Please have a look in the app yourself; the owner can say 'resume engagement' when it looks fine.")
            if b.get("task_id") and not dry:
                c.execute("UPDATE coach_tasks SET verify_state='unknown', verify_note=? WHERE id=?", (txt[:200], int(b["task_id"])))
            return {"ok": True, "danger": True}
    d = {}
    m = re.search(r"\{[\s\S]*?\}", txt)
    if m:
        try:
            d = json.loads(m.group(0))
        except Exception:
            d = {}
    if typ == "stats":
        f = parse_followers(d.get("followers"), d.get("shown"))
        if f is not None and not dry:
            c.execute("INSERT INTO coach_stats(ts, day, platform, followers, impressions, source) VALUES(?,?,?,?,?,?)",
                      (time.time(), today(), b.get("platform") or "x", f, None, "browser"))
        audit_ev(c, "coach_stats", "coach", b.get("platform") or "x", str(f), txt[:120], dry)
        return {"ok": True, "followers": f}
    tid = int(b.get("task_id") or 0)
    st = "verified" if d.get("found") is True else ("not_found" if d.get("found") is False else "unknown")
    if not dry and tid:
        c.execute("UPDATE coach_tasks SET verify_state=?, verify_note=? WHERE id=?", (st, str(d.get("url") or txt)[:200], tid))
    audit_ev(c, "coach_verify", "coach", tid, st, txt[:150], dry)
    return {"ok": True, "verify_state": st}

def coach_stats(c, b):
    """Follower / impression numbers from read-only checks or Buffer stats (POST {platform, followers, impressions, source})."""
    p = G.platform_of(b.get("platform") or "x")
    f = parse_followers(b.get("followers")); imp = b.get("impressions")
    if not b.get("dry_run"):
        c.execute("INSERT INTO coach_stats(ts, day, platform, followers, impressions, source) VALUES(?,?,?,?,?,?)",
                  (float(b.get("ts") or time.time()), today(), p, f, int(imp) if str(imp or "").isdigit() else None, str(b.get("source") or "api")[:30]))
    return {"ok": True, "platform": p, "followers": f}

def coach_open_for_poll(c, limit=10):
    """Open/expired task messages from the last 24 h whose reactions the poller should read (least recently read first)."""
    rows = [dict(r) for r in c.execute("""SELECT id, slack_ts FROM coach_tasks WHERE state IN ('open','expired','skipped') AND slack_ts IS NOT NULL
                                           AND created>=? ORDER BY last_polled LIMIT ?""", (time.time() - 24 * 3600, limit))]
    if rows:
        c.executemany("UPDATE coach_tasks SET last_polled=? WHERE id=?", [(time.time(), r["id"]) for r in rows])
    return [r["slack_ts"] for r in rows]

def coach_status_text(c):
    eff = cfg(); co = eff["coach"]
    if not co.get("enabled"):
        return f"The action-list coach is part of the Viral plan (this client is on {eff.get('plan_label')}). Type `limits` for today's numbers."
    d = day_rows(c, 1, include_today=True)[0]
    days = day_rows(c, 14)
    n = CO.streak_len(days, float(co.get("streak_pct", 70)))
    st = ramp_state(c, "x", eff)
    return (f"*Today:* {d['done']} of {d['suggested']} action-list tasks done" + (f" ({d['pct']:.0f}%)" if d['pct'] is not None else "")
            + (f" · streak {n} day{'s' if n != 1 else ''}" if n else "") + f" · {CO.step_label(eff, st.get('step', 0))}.\n"
            + "Next lists at " + " and ".join(co.get("list_times") or []) + " IST. Type `limits` for your budget, `weekly report` for the score.")

def switch_plan(c, b, out):
    by, dry = b.get("by") or "", bool(b.get("dry_run"))
    eff = cfg(); new = str(b.get("plan") or "").lower().strip()
    if by not in (eff["approvals"].get("admins") or [OWNER]):
        audit_ev(c, "switch_plan", by, new, "refused:not_admin", "", dry)
        out["reply"] = "Only the owner can change the plan. I've let him know you're interested; he can walk you through what changes."
        if not dry:
            notify_owner(f"<@{by}> asked to 'switch plan {new}' in #example-client. Only admins can do it.")
        return out
    plans = GUARD.plans()
    if new not in plans:
        out["reply"] = f"I don't know a plan called '{new}'. Plans: {', '.join(plans)}."; return out
    old = eff.get("plan"); old_label = eff.get("plan_label") or old
    neweff = GUARD.for_plan(new)
    if not dry:
        txt = open(GUARD.path).read()
        line = f"plan: {new}        # switched from {old} by {by} in Slack {now_ist():%Y-%m-%d %H:%M} IST ('switch plan {old}' undoes it)"
        txt2, n = re.subn(r"(?m)^plan:.*$", line, txt, count=1)
        if not n:
            out["reply"] = "I couldn't find the plan line in guardrails.yaml, so nothing changed. Please edit it by hand."; return out
        tmp = GUARD.path + ".tmp"
        open(tmp, "w").write(txt2); os.replace(tmp, GUARD.path)
        c.execute("DELETE FROM kv WHERE k LIKE ?", (f"ramp:{new}:%",))     # a new tier always starts at warm-up week 1
    audit_ev(c, "switch_plan", by, new, "switched" if not dry else "dry", f"{old} -> {new}", dry)
    caps = []
    for p in ("x", "linkedin", "facebook"):
        r = neweff.get("ramp") or {}
        e = CO.ramp_cap(neweff, p, "engage_per_day", 0); po = CO.ramp_cap(neweff, p, "posts_per_day", 0)
        caps.append(f"{G.PLATFORM_NAMES[p]} {e} engagement / {po} posts a day")
    coach_on = (neweff.get("coach") or {}).get("enabled")
    out["reply"] = (f"{'(dry run) Would switch' if dry else 'Switched'} the plan from *{old_label}* to *{neweff.get('plan_label')}*. "
                    + ("It starts at warm-up week 1 and steps up each week without a platform warning (any warning steps back one week). " if (neweff.get('ramp') or {}).get('enabled') else "")
                    + "Automated caps now: " + "; ".join(caps) + ". "
                    + (f"The action-list coach is on: lists at {' and '.join(neweff['coach'].get('list_times') or [])} IST with drafts and Open links, ✅ done / ⏭ skip. "
                       "Say `action list` to get one now. " if coach_on else "The action-list coach is off on this plan. ")
                    + "Type `limits` for the full picture.")
    return out

# ---------------------------------------------------------------- hourly approval batches (2026-10-12, batches.py)
BATCH_TAG = "APPROVAL BATCH DRAFTS v1"

def batch_eff(b):
    """force_plan is honoured only in dry runs (example-client stays on Starter)."""
    fp = str(b.get("force_plan") or "")
    return GUARD.for_plan(fp) if (b.get("dry_run") and fp) else cfg()

def batch_answered(c, bid, now=None):
    c.execute("UPDATE batches SET answered_at=COALESCE(answered_at, ?) WHERE id=?", (float(now or time.time()), bid))

def batch_recent(c, n=6):
    return [{"id": r[0], "posted_at": r[1], "answered": r[2] is not None}
            for r in c.execute("SELECT id, posted_at, answered_at FROM batches WHERE state='posted' ORDER BY posted_at DESC LIMIT ?", (n,))]

def batch_live(eff, now):
    bc = eff.get("batches") or {}
    if not bc.get("live"):
        return False, "batches are not live yet (guardrails.yaml batches.live: false); dry runs only"
    sa = BA.parse_start_at(bc.get("start_at"))
    if now < sa:
        return False, f"batches start at {bc.get('start_at')} IST"
    return True, ""

def _day0(now):
    return datetime.fromtimestamp(now, IST).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

def batch_pending(c, ttl_h):
    """Unanswered batch/engagement proposals still waiting for ✅ (engage within TTL, posts any age < 48 h) per platform."""
    eng, posts = {}, {}
    for r in c.execute("SELECT kind, payload, created FROM proposals WHERE state IN ('pending','editing','checking')").fetchall():
        try:
            pl = json.loads(r["payload"] or "{}")
        except Exception:
            continue
        if r["kind"] == "engage":
            if pl.get("batch_id") and time.time() - r["created"] > ttl_h * 3600:
                continue
            p = G.platform_of(pl.get("url")); eng[p] = eng.get(p, 0) + 1
        elif r["kind"] == "post":
            for d in pl.get("drafts") or []:
                p = G.platform_of(d.get("service")); posts[p] = posts.get(p, 0) + 1
    return eng, posts

def batch_progress(c, eff, now=None):
    now = float(now or time.time()); day0 = _day0(now)
    bc = eff.get("batches") or {}
    prog = {}
    for p in ("x", "linkedin", "facebook"):
        cap = plat_caps(p, c, eff)
        done = len(_plat_rows(c, p, "SELECT url FROM engage_queue WHERE state='done' AND done_at>=?", (day0,)))
        if int(cap.get("engage_per_day", 0) or 0):
            prog.setdefault(p, {})["replies" if p == "x" else "comments"] = (done, int(cap["engage_per_day"]))
        if p in (bc.get("post_platforms") or []) and int(cap.get("posts_per_day", 0) or 0):
            prog.setdefault(p, {})["posts"] = (posts_used(c, p), int(cap["posts_per_day"]))
    step = ""
    if (eff.get("ramp") or {}).get("enabled"):
        step = CO.step_label(eff, ramp_state(c, "x", eff, write=False).get("step", 0))
    return BA.progress_line(prog, step)

def batch_room(c, eff, now=None):
    """What may still be proposed today: caps - already approved - still waiting for ✅; engagement also limited by the
    browser allowance paced per day (monthly cap spread over the days left)."""
    now = float(now or time.time()); bc = eff.get("batches") or {}
    ttl = float(bc.get("item_ttl_hours", 6) or 6)
    peng, ppost = batch_pending(c, ttl)
    room = {"post": {}, "reply": 0, "comment": {}}
    for p in (bc.get("post_platforms") or []):
        cap = plat_caps(p, c, eff)
        room["post"][p] = max(int(cap.get("posts_per_day", 0) or 0) - posts_used(c, p) - ppost.get(p, 0), 0)
    xcap = plat_caps("x", c, eff)
    room["reply"] = max(int(xcap.get("engage_per_day", 0) or 0) - engage_used(c, "x") - peng.get("x", 0), 0) \
        if "reply" in (eff["engagement"].get("allowed_actions") or []) else 0
    for p in COMMENT_PLATFORMS:
        cap = plat_caps(p, c, eff)
        room["comment"][p] = max(int(cap.get("engage_per_day", 0) or 0) - engage_used(c, p) - peng.get(p, 0), 0)
    cap_m = int(eff["spend"].get("browser_tasks_per_month") or 60)
    d = datetime.fromtimestamp(now, IST)
    days_in = (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    used_before = c.execute("SELECT COUNT(*) FROM browser_use WHERE month=? AND day<? AND allowed=1", (d.strftime("%Y-%m"), d.strftime("%Y-%m-%d"))).fetchone()[0]
    used_today = c.execute("SELECT COUNT(*) FROM browser_use WHERE day=? AND allowed=1", (d.strftime("%Y-%m-%d"),)).fetchone()[0]
    br_room, br_allow = BA.daily_browser_room(cap_m, used_before, used_today, d.day, days_in.day)
    queued = c.execute("SELECT COUNT(*) FROM engage_queue WHERE state IN ('queued','running')").fetchone()[0]
    eng_room = max(br_room - queued - sum(peng.values()), 0)
    info = {"browser_room_today": br_room, "browser_allow_today": br_allow, "engage_browser_room": eng_room, "pending_engage": peng,
            "pending_posts": ppost, "queued": queued}
    return room, info

def _fresh_pool(c, now, used_handles):
    """Only client-given / watchlist / following posts may be auto-reply targets (X bans keyword-search automation)."""
    out = []
    allowed = x_allowed_targets(c)   # empty -> only links the client sent themselves ("client") survive
    for r in c.execute("SELECT * FROM batch_pool WHERE used_at IS NULL AND COALESCE(posted_at, added)>=? ORDER BY COALESCE(posted_at, added) DESC",
                       (now - 6 * 3600,)).fetchall():
        src = (r["source"] if "source" in r.keys() else None) or ""
        if src not in ("client", "watch", "following"):
            continue
        if src != "client" and (r["handle"] or "").lower() not in allowed:
            continue   # e.g. x.com/home 'For you' posts (@elonmusk): not client-chosen, not followed
        if (r["handle"] or "").lower() in used_handles:
            continue
        if c.execute("SELECT 1 FROM engage_queue WHERE url=?", (r["url"],)).fetchone() or c.execute("SELECT 1 FROM suggested WHERE url=?", (r["url"],)).fetchone():
            continue
        out.append(dict(r)); used_handles.add((r["handle"] or "").lower())
    return out

def _handles_today(c):
    hs = {(r[0] or "").lower() for r in c.execute("SELECT handle FROM suggested WHERE day=?", (today(),))}
    hs |= {(r[0] or "").lower() for r in c.execute("SELECT handle FROM engage_queue WHERE approved_day=?", (today(),))}
    return hs

def batch_plan(c, b):
    """Every 15 min from n8n: is a batch due? If yes: what goes in it and which read-only reads are needed first."""
    eff = batch_eff(b); bc = eff.get("batches") or {}; dry = bool(b.get("dry_run"))
    now = float(b.get("now") or time.time()) if dry else time.time()
    out = {"ok_to_run": False, "why_not": "", "dry_run": dry, "plan": eff.get("plan"), "plan_label": eff.get("plan_label")}
    day = datetime.fromtimestamp(now, IST).strftime("%Y-%m-%d")
    live_ok, live_why = batch_live(eff, now)
    st = {"next_due": float(kv_get(c, "batch_next_due", "0") or 0), "streak": BA.ignored_streak(batch_recent(c)),
          "still_there_today": int(kv_get(c, f"batch_still_{day}", "0") or 0), "paused": kv_get(c, "batch_paused") == "1",
          "live_ok": live_ok or dry, "live_reason": live_why}
    if dry and b.get("ignore_timing"):
        st.update(next_due=0, live_ok=True)
    action, why = BA.decide(now, bc, st)
    pa, par = pause_all_info(); ss = spend_status(c)
    if action in ("run", "still_there", "skip_ignored") and pa:
        action, why = "wait", "everything is paused"
    if action == "run" and ss["level"] in ("pause", "hard"):
        action, why = "wait", "this month's AI budget is used up (extras paused)"
    every, jit = int(bc.get("every_minutes", 60) or 60), int(bc.get("jitter_minutes", 10) or 0)
    nxt = BA.next_due(now, every, jit)
    out.update(action=action, streak=st["streak"], next_due_ist=fmt(datetime.fromtimestamp(nxt, IST)) if action != "wait" else "")
    if action == "still_there":
        txt = BA.still_there_text(st["streak"], BA.cadence_text(bc))
        if not dry:
            outbox_add(c, txt); kv_set(c, f"batch_still_{day}", st["still_there_today"] + 1); kv_set(c, "batch_next_due", nxt)
        audit_ev(c, "batch", "batches", day, "still_there", why, dry)
        out.update(why_not=why, still_there=txt); return out
    if action == "skip_ignored":
        if not dry:
            kv_set(c, "batch_next_due", nxt)
        audit_ev(c, "batch", "batches", day, "skipped_ignored", why, dry)
        out["why_not"] = why; return out
    if action == "wait":
        out["why_not"] = why; return out
    room, info = batch_room(c, eff, now)
    hours = bc.get("active_hours") or ["09:00", "21:00"]
    slots = BA.slots_left_today(now, hours, every)
    handles = _handles_today(c)
    x_targets_onboarding(c, dry)      # asks for target accounts once a day while none are set
    pool = _fresh_pool(c, now, set(handles))
    reads_today = c.execute("SELECT COUNT(*) FROM browser_use WHERE day=? AND source='batch-read' AND allowed=1", (day,)).fetchone()[0]
    can_read_x = reads_today < int(bc.get("x_reads_per_day", 1) or 0) and info["engage_browser_room"] >= 2
    eng_budget = info["engage_browser_room"] - (1 if (can_read_x and not pool) else 0)
    avail = {"post": {p: min(n, 1) for p, n in room["post"].items()},
             "reply": min(room["reply"], len(pool) + (6 if can_read_x else 0)),
             "comment": {}}
    targets = [dict(r) for r in c.execute("SELECT * FROM batch_targets WHERE state IN ('new','ready') ORDER BY added").fetchall()]
    for p in COMMENT_PLATFORMS:
        usable = [t for t in targets if t["platform"] == p and (bc.get("read_targets") or t["text"] or t["note"])]
        avail["comment"][p] = min(room["comment"].get(p, 0), len(usable))
    # engagement items are limited by today's browser room (each approved one = 1 browser task)
    e_total = avail["reply"] + sum(avail["comment"].values())
    if e_total > max(eng_budget, 0):
        cut = e_total - max(eng_budget, 0)
        for p in ("facebook", "linkedin"):
            while cut and avail["comment"][p] > 0 and avail["reply"] <= avail["comment"][p]:
                avail["comment"][p] -= 1; cut -= 1
        r = min(cut, avail["reply"]); avail["reply"] -= r; cut -= r
        for p in ("facebook", "linkedin"):
            m = min(cut, avail["comment"][p]); avail["comment"][p] -= m; cut -= m
    remaining_today = sum(room["post"].values()) + room["reply"] + sum(room["comment"].values())
    size = BA.batch_size(bc, min(remaining_today, sum(avail["post"].values()) + avail["reply"] + sum(avail["comment"].values())), slots)
    size = min(size, sum(avail["post"].values()) + avail["reply"] + sum(avail["comment"].values()))
    last_post_p = kv_get(c, "batch_last_post_platform", "")
    order = [p for p in (bc.get("post_platforms") or []) if p != last_post_p] + ([last_post_p] if last_post_p in (bc.get("post_platforms") or []) else [])
    alloc = BA.allocate(size, avail, int(bc.get("max_posts_per_batch", 1) or 1), order)
    if not dry:
        kv_set(c, "batch_next_due", nxt)
    if not alloc:
        why = "nothing to propose right now (today's caps are used, nothing fresh to reply to, or the browser allowance for today is used)"
        if room["reply"] > 0 and len(pool) == 0:
            why = why + ". " + ask_links_tip()
        audit_ev(c, "batch", "batches", day, "skipped_empty", json.dumps({"room": room, "info": info})[:300], dry)
        out.update(why_not=why, room=room, info=info, ask_links=True); return out
    n_reply = sum(1 for a in alloc if a["kind"] == "reply")
    need_read = n_reply > len(pool) and can_read_x
    reads = []
    ask_links = (room["reply"] > 0 and len(pool) == 0 and not can_read_x)
    if need_read:
        spec = auto_x_read_spec(c, now)
        g = guard_browser(c, {"task": spec["url"], "source": "batch-read", "dry_run": dry})
        if g["ok"]:
            reads.append(spec)
        else:
            ask_links = True
            audit_ev(c, "batch_read", "batches", day, "refused:" + g["reason"], spec["url"][:150], dry)
    need_t = [a["platform"] for a in alloc if a["kind"] == "comment"]
    for t in targets:
        if bc.get("read_targets") and t["platform"] in need_t and not (t["text"] or t["note"]) and t["state"] == "new":
            g = guard_browser(c, {"task": t["url"], "source": "batch-read", "dry_run": dry})
            if g["ok"]:
                reads.append({"type": "target", "id": t["id"], "url": t["url"], "platform": t["platform"]})
            break                                    # at most one read-only open of a client-chosen post per batch
    bid = f"{day}-{datetime.fromtimestamp(now, IST):%H%M}-{int(now) % 100000}"
    audit_ev(c, "batch", "batches", bid, "planned", json.dumps(alloc)[:300], dry)
    out.update(ok_to_run=True, batch_id=bid, alloc=alloc, reads=reads, size=len(alloc), room=room, info=info, now=now,
               ask_links=ask_links, watchlist=watchlist_get(c))
    return out

def _read_json(txt, kind):
    m = re.search(r"\[[\s\S]*\]", txt) if kind == "list" else re.search(r"\{[\s\S]*\}", txt)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None

def batch_pick(c, b):
    """After the read-only reads: store fresh X posts / target texts, handle warnings, pick candidates and build the ONE LLM prompt."""
    eff = batch_eff(b); dry = bool(b.get("dry_run")); now = float(b.get("now") or time.time()) if dry else time.time()
    alloc = b.get("alloc") or []
    handle_x = str(((eff.get("coach") or {}).get("handles") or {}).get("x") or OWN_HANDLE).lower()
    for r in (b.get("reads") or []):
        st, txt = str(r.get("status") or ""), str(r.get("result_text") or "")
        if st == "stopped" and not re.search(r"read-only mode: refused", txt, re.I) or (st != "ok" and DANGER.search(txt) and r.get("type") == "target"):
            pf = "x" if r.get("type") in ("x_search", "x_watch", "x_following") else G.platform_of(r.get("url"))
            if pf in COMMENT_PLATFORMS:
                platform_warning(c, pf, f"read-only open stopped: {txt[:200]}", "batches", dry)
                if not dry and r.get("id"):
                    c.execute("UPDATE batch_targets SET state='failed' WHERE id=?", (int(r["id"]),))
            else:
                n = set_pause(c, f"auto: batch read-only X target read stopped: {txt[:200]}", "batches", dry)
                if not dry:
                    outbox_add(c, f":warning: While reading X (watchlist/Following, read-only) for the next draft batch, the browser hit a warning / login / captcha-type page:\n> {txt[:300]}\n"
                                  f"I paused automated engagement ({n} queued cancelled) and the warm-up drops one step. Nothing was clicked.")
            audit_ev(c, "batch_read", "batches", b.get("batch_id"), "danger", txt[:200], dry)
            return {"ok": False, "stop": True, "why_not": "a warning page during a read-only check; paused", "dry_run": dry}
        if r.get("type") in ("x_search", "x_watch", "x_following"):
            # x_search kept for older dry-run fixtures; live paths use x_watch / x_following only (never keyword topics).
            src = {"x_watch": "watch", "x_following": "following"}.get(r.get("type"), "following")
            if isinstance(r.get("follows"), list) and r["follows"] and not dry:
                kv_set(c, "x_follows", json.dumps(sorted(set(x_follows_cached(c)) | {str(h).lstrip("@").lower() for h in r["follows"]})))
            arr = r.get("posts") if isinstance(r.get("posts"), list) else (_read_json(txt, "list") or [])
            for p in arr[:12]:
                u = str(p.get("url") or "").split("?")[0].replace("twitter.com", "x.com")
                m = re.match(r"https://x\.com/([A-Za-z0-9_]{1,15})/status/(\d+)$", u)
                if not m or m.group(1).lower() == handle_x:
                    continue
                posted = CO.parse_age(p.get("age"), now)
                if posted and now - posted > 6 * 3600:
                    continue
                c.execute("INSERT OR IGNORE INTO batch_pool(url, handle, text, age, posted_at, added, source) VALUES(?,?,?,?,?,?,?)",
                          (u, m.group(1), re.sub(r"\s+", " ", str(p.get("text") or ""))[:600], str(p.get("age") or ""), posted, now, src))
                c.execute("UPDATE batch_pool SET source=COALESCE(NULLIF(source,''), ?) WHERE url=?", (src, u))
        elif r.get("type") == "target":
            d = r.get("data") if isinstance(r.get("data"), dict) else (_read_json(txt, "obj") or {})
            if st == "ok" and d.get("text"):
                c.execute("UPDATE batch_targets SET text=?, author=?, state='ready' WHERE id=?", (str(d["text"])[:800], str(d.get("author") or "")[:80], int(r["id"])))
            else:
                c.execute("UPDATE batch_targets SET state='failed' WHERE id=?", (int(r["id"]),))
    handles = _handles_today(c)
    pool = _fresh_pool(c, now, set(handles))
    targets = [dict(t) for t in c.execute("SELECT * FROM batch_targets WHERE state IN ('new','ready') AND (COALESCE(text,'')<>'' OR COALESCE(note,'')<>'') ORDER BY added").fetchall()]
    picks, ri, used_t = [], 0, set()
    for i, a in enumerate(alloc):
        ref = f"i{i + 1}"
        if a["kind"] == "reply":
            if ri < len(pool):
                p = pool[ri]; ri += 1
                picks.append({"ref": ref, "kind": "reply", "platform": "x", "url": p["url"], "handle": p["handle"], "excerpt": p["text"]})
        elif a["kind"] == "comment":
            t = next((t for t in targets if t["platform"] == a["platform"] and t["id"] not in used_t), None)
            if t:
                used_t.add(t["id"])
                picks.append({"ref": ref, "kind": "comment", "platform": a["platform"], "url": t["url"], "target_id": t["id"],
                              "handle": t.get("author") or "", "excerpt": (t.get("text") or "")[:600], "note": t.get("note") or ""})
        else:
            picks.append({"ref": ref, "kind": "post", "platform": a["platform"]})
    if not picks:
        audit_ev(c, "batch", "batches", b.get("batch_id"), "skipped_empty", "no candidates after reads", dry)
        return {"ok": True, "nothing": True, "why_not": "no fresh candidates after the read-only check. " + ask_links_tip(), "dry_run": dry, "ask_links": True}
    notes = read_notes()[:3500]
    topics = (eff.get("coach") or {}).get("topics") or [t[0] for t in TOPICS]
    recent = [r[0] for r in c.execute("SELECT text FROM engage_queue WHERE text<>'' ORDER BY id DESC LIMIT 8")]
    lim = {"x": 270, "linkedin": 1200, "facebook": 600}
    spec = []
    for pk in picks:
        if pk["kind"] == "reply":
            spec.append({"ref": pk["ref"], "type": "X reply (under 200 characters)", "post_by": pk["handle"], "post_text": pk["excerpt"][:500]})
        elif pk["kind"] == "comment":
            spec.append({"ref": pk["ref"], "type": f"{G.PLATFORM_NAMES[pk['platform']]} comment (1-3 short sentences, under 400 characters)",
                         "post_by": pk["handle"], "post_text": pk["excerpt"][:500], "client_note": pk["note"][:300]})
        else:
            spec.append({"ref": pk["ref"], "type": f"original {G.PLATFORM_NAMES[pk['platform']]} post (under {lim[pk['platform']]} characters)",
                         "topic_ideas": topics})
    prompt = (f"{BATCH_TAG}. You write ready-to-approve drafts for the owner's accounts (X @your_x_handle, LinkedIn in/your-linkedin-handle, "
              f"Facebook Page YourBrand). Each draft is shown to him in Slack and only goes out after his ✅.\n"
              f"Client notes (voice, business, audience, rules; follow them):\n<<<\n{notes}\n>>>\n"
              f"Write exactly one draft for each item:\n{json.dumps(spec, ensure_ascii=False)}\n"
              "Rules for replies and comments: mention one concrete detail from their post; sound like a real person; ask a real question or share a "
              "small, plain tip or experience; no links, hashtags, @mentions, emojis, em dashes, pitching, products, offers, 'DM me', 'check out', "
              "generic praise ('great post', 'so true'), and no numbers or claims about clients that are not in the notes.\n"
              "Rules for posts: one clear idea useful to small-business owners or founders in India, in the client's voice, no hype words, at most 1 "
              "hashtag, no invented numbers, results or testimonials, no politics or religion.\n"
              f"Do not repeat these recent replies: {json.dumps(recent, ensure_ascii=False)[:1200]}\n"
              'Return ONLY JSON: {"items": [{"ref": "i1", "text": "...", "why": "max 10 words"}]}')
    ask = not any(pk["kind"] == "reply" for pk in picks)
    return {"ok": True, "dry_run": dry, "batch_id": b.get("batch_id"), "picks": picks, "prompt": prompt, "nothing": False, "ask_links": ask}

def _valid_comment(text, platform):
    d = re.sub(r"\s+", " ", str(text or "")).strip().strip('"').replace("—", ", ").replace("–", "-")
    if not d or len(d) > 400:
        return None
    blocks, _ = G.content_regex(d, "reply", read_notes(), cfg())
    if blocks or BANNED.search(d):
        return None
    return d

def _valid_post(text, platform):
    d = str(text or "").strip().strip('"').replace("—", ", ")
    lim = {"x": 280, "linkedin": 1200, "facebook": 600}.get(platform, 280)
    if not d or len(d) > lim:
        return None
    blocks, _ = G.content_regex(d, "post", read_notes(), cfg())
    return None if blocks else d

def batch_build(c, b):
    """Validate the LLM drafts, number the items, and return the Slack header + thread items + post items. Not posted here."""
    eff = batch_eff(b); bc = eff.get("batches") or {}; dry = bool(b.get("dry_run"))
    now = float(b.get("now") or time.time()) if dry else time.time()
    picks = b.get("picks") or []; bid = str(b.get("batch_id") or "")
    raw = _parse_llm(b.get("raw"))
    texts = {}
    for it in (raw.get("items") or raw.get("tasks") or []):
        if isinstance(it, dict) and it.get("ref"):
            texts[str(it["ref"])] = (it.get("text") or "", str(it.get("why") or "")[:100])
    sim = float(eff["engagement"].get("duplicate_similarity", 0.85))
    prev = [r[0] for r in c.execute("SELECT text FROM engage_queue WHERE text<>'' AND approved_at>=?", (time.time() - 30 * 86400,))]
    for r in c.execute("SELECT payload FROM proposals WHERE created>=?", (time.time() - 7 * 86400,)).fetchall():
        try:
            prev.append(json.loads(r[0] or "{}").get("text") or "")
        except Exception:
            pass
    items, dropped = [], []
    for pk in picks:
        t, why = texts.get(pk["ref"], ("", ""))
        if pk["kind"] == "reply":
            d = clean_draft(t, "reply")
        elif pk["kind"] == "comment":
            d = _valid_comment(t, pk["platform"])
        else:
            d = _valid_post(t, pk["platform"])
        if not d:
            dropped.append(("rejected", pk["ref"], str(t)[:60])); continue
        r, _ = G.most_similar(d, prev)
        if r >= (sim if pk["kind"] != "post" else float(eff["content"].get("post_duplicate_similarity", 0.9))):
            dropped.append(("duplicate", pk["ref"], d[:60])); continue
        prev.append(d)
        items.append({**pk, "text": d, "why": why})
    for i, it in enumerate(items):
        it["n"] = i + 1
    if not items:
        audit_ev(c, "batch", "batches", bid, "skipped_empty", f"all drafts rejected: {dropped[:4]}", dry)
        return {"ok": True, "nothing": True, "why_not": "no draft passed the checks this time", "dropped": dropped, "dry_run": dry}
    prog = batch_progress(c, eff, now)
    header = BA.header_text(items, prog, now, BA.cadence_text(bc), dry)
    if b.get("ask_links") or not any(it["kind"] == "reply" for it in items):
        header = header + "\n\n_" + ask_links_tip() + "_"
    eng = [{"n": it["n"], "kind": it["kind"], "platform": it["platform"],
            "slack": {"channel": CHANNEL, "text": BA.item_text(it), "unfurl_links": False, "unfurl_media": False},
            "payload": {"action": "reply" if it["kind"] == "reply" else "comment", "url": it["url"], "handle": it.get("handle") or "",
                        "text": it["text"], "batch_id": bid, "batch_n": it["n"], "platform": it["platform"], "target_id": it.get("target_id"),
                        "topic": "batch"}} for it in items if it["kind"] != "post"]
    posts = [{"n": it["n"], "text": it["text"], "services": it["platform"], "label": f"Item {it['n']} of this batch"} for it in items if it["kind"] == "post"]
    if not dry:
        c.execute("INSERT OR REPLACE INTO batches(id, day, plan, created, n_items, state, items) VALUES(?,?,?,?,?, 'draft', ?)",
                  (bid, today(), eff.get("plan"), now, len(items), json.dumps([{k: it.get(k) for k in ("n", "kind", "platform", "url", "text")} for it in items])))
        for it in items:
            if it["kind"] == "reply":
                c.execute("UPDATE batch_pool SET used_at=? WHERE url=?", (now, it["url"]))
            elif it["kind"] == "comment" and it.get("target_id"):
                c.execute("UPDATE batch_targets SET state='used', used_at=? WHERE id=?", (now, int(it["target_id"])))
        lp = [it["platform"] for it in items if it["kind"] == "post"]
        if lp:
            kv_set(c, "batch_last_post_platform", lp[-1])
    audit_ev(c, "batch", "batches", bid, f"built:{len(items)}", f"dropped {len(dropped)}", dry)
    return {"ok": True, "nothing": False, "dry_run": dry, "batch_id": bid, "n_items": len(items), "progress": prog,
            "header": {"channel": CHANNEL, "text": header, "unfurl_links": False, "unfurl_media": False},
            "items": eng, "posts": posts, "dropped": dropped}

def batch_register(c, b):
    """After Slack: engagement items become proposals (✅/✏️/❌ in the thread); batch posts (created by the propose-post tool) get linked."""
    if b.get("dry_run"):
        return {"ok": True, "dry_run": True}
    bid, hts = str(b.get("batch_id") or ""), str(b.get("header_ts") or "")
    if not hts:
        c.execute("UPDATE batches SET state='failed' WHERE id=?", (bid,))
        audit_ev(c, "batch", "batches", bid, "failed", "header not posted")
        return {"ok": False, "error": "header not posted"}
    n = 0
    for it in (b.get("items") or []):
        if not it.get("slack_ts"):
            continue
        pl = dict(it.get("payload") or {})
        c.execute("INSERT OR REPLACE INTO proposals(slack_ts, kind, channel, thread_ts, payload, state, created, updated) VALUES(?,?,?,?,?,?,?,?)",
                  (str(it["slack_ts"]), "engage", CHANNEL, hts, json.dumps(pl), "pending", time.time(), time.time()))
        c.execute("INSERT OR IGNORE INTO suggested(url, handle, day, ts) VALUES(?,?,?,?)", (pl.get("url"), (pl.get("handle") or "").lower(), today(), time.time()))
        n += 1
    for po in (b.get("posts") or []):
        ts = str(po.get("preview_ts") or "")
        p = get_prop(c, ts) if ts else None
        if p:
            pl = p["payload"]; pl.update(batch_id=bid, batch_n=po.get("n"))
            c.execute("UPDATE proposals SET payload=? WHERE slack_ts=?", (json.dumps(pl), ts)); n += 1
    c.execute("UPDATE batches SET header_ts=?, posted_at=?, state='posted', n_items=? WHERE id=?", (hts, time.time(), n, bid))
    audit_ev(c, "batch", "batches", bid, "posted", f"{n} items")
    return {"ok": True, "registered": n}

def batch_items_map(c, hours=24):
    out = {}
    for r in c.execute("SELECT id, header_ts FROM batches WHERE state='posted' AND posted_at>=?", (time.time() - hours * 3600,)).fetchall():
        m = {}
        for p in c.execute("SELECT slack_ts, payload, state, kind FROM proposals WHERE thread_ts=?", (r["header_ts"],)).fetchall():
            try:
                pl = json.loads(p["payload"] or "{}")
            except Exception:
                continue
            if pl.get("batch_id") == r["id"] and pl.get("batch_n"):
                m[str(pl["batch_n"])] = {"ts": p["slack_ts"], "state": p["state"], "kind": p["kind"]}
        out[r["header_ts"]] = m
    return out

def batch_op(c, o, b, out):
    eff = cfg(); bc = eff.get("batches") or {}; by, dry = b.get("by") or "", bool(b.get("dry_run"))
    users = set(((eff.get("coach") or {}).get("client_users") or []) + (eff["approvals"].get("approvers") or [OWNER]))
    if o == "batch_edit":
        bt = c.execute("SELECT * FROM batches WHERE header_ts=?", (str(b.get("thread_ts") or ""),)).fetchone()
        if not bt:
            return {**out, "ok": False, "error": "unknown batch"}
        if by not in users:
            return {**out, "ok": False, "error": "not the approver"}
        if not dry:
            batch_answered(c, bt["id"])
        n = int(b.get("n") or 0); it = batch_items_map(c, 72).get(bt["header_ts"], {}).get(str(n))
        if not it:
            out["reply"] = f"I can't find item {n} in this batch."; return out
        p = get_prop(c, it["ts"])
        if p["kind"] == "post":
            out["reply"] = f"For posts, react ✏️ on item {n} and tell me what to change; I'll redo the draft."; return out
        if p["state"] not in ("pending", "editing"):
            out["reply"] = f"Item {n} is already {p['state']}, so I left it as it was."; return out
        pf = G.platform_of(p["payload"].get("url"))
        new = clean_draft(b.get("text"), "reply") if pf == "x" else _valid_comment(b.get("text"), pf)
        if not new:
            pre = content_precheck(str(b.get("text") or ""), "reply")
            out["reply"] = ((pre["message"] if not pre["ok"] else "That wording doesn't pass the reply checks (keep it under 200 characters on X, no links, "
                            "hashtags, @mentions or pitching).") + f" Item {n} keeps its old text."); return out
        if not dry:
            pl = p["payload"]; pl["text"] = new; pl["edited_by"] = by
            c.execute("UPDATE proposals SET payload=?, state='pending', updated=? WHERE slack_ts=?", (json.dumps(pl), time.time(), it["ts"]))
        audit_ev(c, "batch_edit", by, it["ts"], "updated", new[:200], dry)
        out["reply"] = f"Updated item {n}:\n> {new}\nReact ✅ on item {n} to approve it (the safety check runs then)." + (" (dry run)" if dry else "")
        return out
    if o == "batch_target":
        txt = str(b.get("text") or "")
        m = re.search(r"<?(https?://[^\s>|]+)", txt)
        if not m:
            out["reply"] = "Paste the post link after 'comment on', e.g. `comment on https://www.linkedin.com/feed/update/... they ask about pricing`."; return out
        url = m.group(1).split("?")[0] if "linkedin.com" in m.group(1) else m.group(1)
        pf = G.platform_of(url)
        note = re.sub(r"\s+", " ", txt[m.end():]).strip(" >-|:")[:300]
        if pf == "x":
            mm = re.match(r"https://(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/status/(\d+)", url)
            if not mm:
                out["reply"] = "That doesn't look like a link to one X post."; return out
            if not dry:
                c.execute("INSERT OR IGNORE INTO batch_pool(url, handle, text, age, posted_at, added, source) VALUES(?,?,?,?,?,?,?)",
                          (f"https://x.com/{mm.group(1)}/status/{mm.group(2)}", mm.group(1), note, "", time.time(), time.time(), "client"))
            out["reply"] = "Got it: I'll draft a reply to that X post in the next batch (it waits for your ✅)." + (" (dry run)" if dry else ""); return out
        if pf not in COMMENT_PLATFORMS or f"{pf}.com/" not in url:
            out["reply"] = "I can only line up comments on LinkedIn or Facebook posts (and replies on X)."; return out
        cap = plat_caps(pf, c)
        if not cap.get("comments_on"):
            out["reply"] = (f"Automated {G.PLATFORM_NAMES[pf]} comments aren't part of the {eff.get('plan_label')} plan, so I'll keep it manual: "
                            "say what you'd like to say and I'll draft it for you to post yourself."); return out
        if not dry:
            c.execute("INSERT OR IGNORE INTO batch_targets(url, platform, note, added, by_, state) VALUES(?,?,?,?,?, 'new')", (url, pf, note, time.time(), by))
            if note:
                c.execute("UPDATE batch_targets SET note=? WHERE url=? AND state IN ('new','ready')", (note, url))
        paused = cap.get("paused_until")
        if not note and not bc.get("read_targets"):
            out["reply"] = (f"Got the link. One more thing: tell me in a line what that {G.PLATFORM_NAMES[pf]} post says, e.g. "
                            f"`comment on {url} they ask which AI tool to try first`. {G.PLATFORM_NAMES[pf]}'s terms don't allow bots to read posts, "
                            "so I don't open it just to read it. Then I'll draft the comment for your ✅ in the next batch." + (" (dry run)" if dry else ""))
            audit_ev(c, "batch_target", by, pf, "added_needs_gist", url[:200], dry)
            return out
        out["reply"] = (f"Got it: I'll draft a {G.PLATFORM_NAMES[pf]} comment for that post in the next batch; it only goes out after your ✅, "
                        f"by the browser agent, paced (max 1 an hour). {G.PLATFORM_NAMES[pf]} comments today: {engage_used(c, pf)}/{cap.get('engage_per_day', 0)}."
                        + (f" Note: {G.PLATFORM_NAMES[pf]} comments are paused until {fmt(datetime.fromtimestamp(paused, IST))} after a warning." if paused else "")
                        + ("" if note else " Tip: add a line about the post after the link and I won't need to open it first.")
                        + (" (dry run)" if dry else ""))
        audit_ev(c, "batch_target", by, pf, "added", url[:200], dry)
        return out
    if o == "batch_next":
        if by not in users:
            return {**out, "ok": False, "error": "not the approver"}
        if not dry:
            r = c.execute("SELECT id FROM batches WHERE state='posted' ORDER BY posted_at DESC LIMIT 1").fetchone()
            if r:
                batch_answered(c, r[0])
            kv_set(c, "batch_next_due", 0); kv_set(c, "batch_paused", "0")
        hrs = bc.get("active_hours") or ["09:00", "21:00"]
        out["reply"] = ("OK, the next batch comes within about 15 minutes." if BA.in_active(time.time(), hrs)
                        else f"OK. Batches run {'-'.join(hrs)} IST, so the next one comes after {hrs[0]} IST.") + (" (dry run)" if dry else "")
        if not bc.get("enabled"):
            out["reply"] = f"Draft batches aren't on for the {eff.get('plan_label')} plan."
        return out
    if o in ("batch_pause", "batch_resume"):
        if by not in users:
            return {**out, "ok": False, "error": "not the approver"}
        if not dry:
            kv_set(c, "batch_paused", "1" if o == "batch_pause" else "0")
            if o == "batch_resume":
                kv_set(c, "batch_next_due", 0)
        audit_ev(c, o, by, "", "ok", "", dry)
        out["reply"] = ("Draft batches paused. Approved items still run on schedule; say `resume batches` to get them again."
                        if o == "batch_pause" else "Draft batches are back on; the next one comes within about 15 minutes in active hours.") + (" (dry run)" if dry else "")
        return out
    if o == "batch_status":
        out["reply"] = batch_status_text(c); return out
    if o == "batch_help":
        m = batch_items_map(c, 72).get(str(b.get("thread_ts") or ""), {})
        open_ = sorted(int(n) for n, it in m.items() if it["state"] in ("pending", "editing"))
        out["reply"] = ((f"Which one? Open items here: {', '.join(map(str, open_))}. " if open_ else "Nothing in this batch is still waiting. ")
                        + "React ✅ / ✏️ / ❌ on an item, or type `1 ✅`, `2 ❌`, `approve all`, `3: new wording`.")
        return out
    return {**out, "ok": False, "error": f"unknown op {o}"}


def watch_op(c, o, b, out):
    """watch @handle / unwatch @handle / watchlist — client-curated accounts for automated X reply targets."""
    by, dry = b.get("by") or "", bool(b.get("dry_run"))
    users = set((cfg()["approvals"].get("approvers") or []) + (cfg()["approvals"].get("admins") or [OWNER]))
    if by and by not in users and o != "watchlist":
        return {**out, "ok": False, "error": "not the approver"}
    if o == "watchlist":
        wl = watchlist_get(c)
        out["reply"] = (("Watchlist: " + ", ".join(f"@{h}" for h in wl) + f" ({len(wl)}). Automated X replies may target recent posts from these accounts "
                         "or accounts you follow — never keyword search. `watch @handle` / `unwatch @handle`.")
                        if wl else "Watchlist is empty. Add accounts with `watch @handle`, or send `reply to <x link>`. "
                        "I can also draft from accounts you follow (Following feed).")
        return out
    m = re.search(r"@?([A-Za-z0-9_]{1,15})", str(b.get("handle") or b.get("text") or ""))
    handle = m.group(1) if m else ""
    if o == "watch":
        h, err = watchlist_add(c, handle, by, dry)
        if err:
            out["reply"] = err; return out
        audit_ev(c, "watch", by, h, "added", "", dry)
        out["reply"] = (f"Watching @{h}. I'll draft automated replies (for your ✅) from their recent posts, and from accounts you follow. "
                        f"Say `unwatch @{h}` to remove. Current list: " + (", ".join(f"@{x}" for x in watchlist_get(c)) or "(empty)")) + (" (dry run)" if dry else "")
        return out
    h, err = watchlist_remove(c, handle, dry)
    if err:
        out["reply"] = err; return out
    audit_ev(c, "unwatch", by, h, "removed", "", dry)
    left = watchlist_get(c)
    out["reply"] = (f"Removed @{h} from the watchlist." + ((" Still watching: " + ", ".join(f"@{x}" for x in left) + ".") if left else " Watchlist is empty now.")
                    + (" (dry run)" if dry else ""))
    return out

def batch_status_text(c, eff=None):
    eff = eff or cfg(); bc = eff.get("batches") or {}
    if not bc.get("enabled"):
        return f"*Draft batches:* off on the {eff.get('plan_label')} plan."
    nd = float(kv_get(c, "batch_next_due", "0") or 0)
    live, why = batch_live(eff, time.time())
    items = f"{bc.get('items_min', 2)}-{bc.get('items_max', 4)} items"
    st = ("paused ('resume batches')" if kv_get(c, "batch_paused") == "1" else ("on" if live else why))
    streak = BA.ignored_streak(batch_recent(c))
    return (f"*Draft batches:* {st} · {BA.cadence_text(bc)}, {items} each"
            + (f" · next about {fmt(datetime.fromtimestamp(nd, IST))[11:]}" if live and nd > time.time() else "")
            + (f" · on hold: the last {streak} batches got no reaction (say `next batch`)" if streak >= int(bc.get('skip_after_ignored', 2)) else "")
            + f"\n_{batch_progress(c, eff)}_")

# ---------------------------------------------------------------- worker: performs approved actions
ACCOUNTS = {"x": "@your_x_handle", "linkedin": "LinkedIn in/your-linkedin-handle", "facebook": "the Facebook Page YourBrand"}

def task_text(q):
    url, h = q["url"], (q["handle"] or "the author").lstrip("@")
    pf = G.platform_of(url)
    if pf in COMMENT_PLATFORMS:
        P_ = G.PLATFORM_NAMES[pf]
        return (f"Open {url} . It shows one {P_} post. You act as the signed-in account ({ACCOUNTS[pf]}). Do EXACTLY ONE action, once: "
                "leave this comment on this post, with exactly this text, unchanged, nothing added: <<<" + (q["text"] or "") + ">>> . "
                "Click the post's comment box, type the text exactly, click the post/submit button for the comment once, wait 2 seconds and "
                "check that the comment shows under the post. Never like, react, share, follow, connect, message, or comment anywhere else. "
                + ("If Facebook asks which profile to comment as, choose the Page YourBrand; if that is not possible, STOP. " if pf == "facebook" else "")
                + "If anything looks unexpected (post deleted or not found, sign-in page, captcha, security checkpoint, 'unusual activity', "
                "warning, restricted or limited account, rate limit) STOP immediately without acting and call done with success=false and text "
                "starting 'STOPPED:'. When finished call done with text starting 'DONE:' and what you did.")
    base = (f"Open {url} . It shows one X post by @{h} (the main post at the top). You act as the signed-in account @your_x_handle. "
            "Do EXACTLY ONE action, once, on this main post only. Never follow, unfollow, DM, bookmark, like or reply to anything else. ")
    if q["action"] == "like":
        act = ("Click the Like (heart) button under the main post once. If it is already liked (filled heart / 'Liked'), do NOT click; "
               "call done with 'ALREADY: liked'.")
    elif q["action"] == "repost":
        act = ("Click the Repost button under the main post, then choose 'Repost' (NOT Quote) once. If it is already reposted, do nothing "
               "and call done with 'ALREADY: reposted'.")
    else:
        act = ("Reply to the main post with exactly this text, unchanged, nothing added: <<<" + q["text"] + ">>> . "
               "Click the reply box under the main post ('Post your reply'), type the text exactly, click the 'Reply' button once, "
               "wait 2 seconds and check that the reply shows under the post.")
    return base + act + (" If anything looks unexpected (post deleted, sign-in page, captcha, warning, 'unusual activity', locked "
                         "account, rate limit) STOP immediately without acting and call done with success=false and text starting "
                         "'STOPPED:'. When finished call done with text starting 'DONE:' and what you did.")

DANGER = re.compile(r"(STOPPED|captcha|unusual activity|suspicious|locked|restricted|suspended|verify|rate limit|try again later|checkpoint|sign in|log in|security check|temporarily blocked|limit(ed)? (your|this) account)", re.I)

def platform_warning(c, platform, reason, by="state-helper", dry=False, announce=True, now=None):
    """LinkedIn/Facebook: any warning or checkpoint -> that platform's comments pause for warning_pause_days (default 7) at 0,
    queued comments there are cancelled, and on a ramped plan the platform's warm-up restarts at week 1. X keeps running."""
    now = float(now or time.time())
    days = float((cfg()["platforms"].get(platform) or {}).get("warning_pause_days", 7) or 7)
    until = now + days * 86400
    rows = [r for r in c.execute("SELECT id, url FROM engage_queue WHERE state='queued'").fetchall() if G.platform_of(r["url"]) == platform]
    if not dry:
        kv_set(c, f"plat_pause:{platform}", json.dumps({"until": until, "since": now, "reason": str(reason)[:300], "by": by}))
        c.executemany("UPDATE engage_queue SET state='cancelled', result='platform paused after a warning' WHERE id=?", [(r["id"],) for r in rows])
        r = cfg().get("ramp") or {}
        if r.get("enabled"):
            st = CO.ramp_new(now); st.update(last_warning=now, warnings=1, since=until)    # week 1 again, counted from the end of the pause
            c.execute("INSERT OR REPLACE INTO kv(k, v) VALUES(?,?)", (ramp_key(cfg(), platform), json.dumps(st)))
        log(f"PLATFORM PAUSE {platform} {days:.0f}d by {by}: {str(reason)[:160]}")
        if announce:
            P_ = G.PLATFORM_NAMES[platform]
            outbox_add(c, f":warning: {P_} showed a warning or checkpoint while I was working:\n> {str(reason)[:300]}\n"
                          f"I stopped right away. {P_} comments are paused for {days:.0f} days (until {fmt(datetime.fromtimestamp(until, IST))}) and drop to 0 a day; "
                          f"{len(rows)} queued comment(s) cancelled. X and posts through Buffer are not affected. Please open {P_} yourself and clear anything it asks for.")
            notify_owner(f"{platform} warning/checkpoint for example-client: comments paused {days:.0f} days. {str(reason)[:150]}")
    audit_ev(c, "platform_pause", by, platform, f"{days:.0f}d", str(reason)[:200], dry)
    return len(rows), until

def run_one(q):
    req = urllib.request.Request(BROWSER + "/task", method="POST",
                                 data=json.dumps({"task": task_text(q), "max_steps": 12, "allow_write": True}).encode(),
                                 headers={"Content-Type": "application/json", "X-Approved": "yes"})
    try:
        with urllib.request.urlopen(req, timeout=700) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:
            return {"status": "error", "result_text": f"HTTP {e.code}"}
    except Exception as e:
        return {"status": "error", "result_text": f"{type(e).__name__}: {e}"}

def worker():
    log("worker started")
    while True:
        try:
            tick_worker()
        except Exception as e:
            log(f"worker error {type(e).__name__}: {e}")
        time.sleep(15)

def tick_worker():
    c = db()
    try:
        with LOCK:
            cancel_disallowed_queued(c)   # drop likes (etc.) the moment the plan forbids them, not only when due
            paused, reason = pause_info()
            if paused or pause_all_info()[0]:
                return
            q = c.execute("SELECT * FROM engage_queue WHERE state='queued' AND due_at<=? ORDER BY due_at LIMIT 1", (time.time(),)).fetchone()
            if not q:
                return
            q = dict(q)
            pf = G.platform_of(q["url"])
            day0 = now_ist().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            done_rows = _plat_rows(c, pf, "SELECT url, done_at FROM engage_queue WHERE state IN ('running','done','failed','stopped') AND done_at>=?", (day0,))
            done_today = len(done_rows)
            last = max([r["done_at"] for r in _plat_rows(c, pf, "SELECT url, done_at FROM engage_queue WHERE state IN ('done','failed','stopped') AND done_at IS NOT NULL", ())] or [0]) or None
            pcap = int(plat_caps(pf, c).get("engage_per_day", 0))
            gap_s, extra_s, per_hour = gaps(pf)
            if pf in COMMENT_PLATFORMS and plat_pause(c, pf)[0]:
                c.execute("UPDATE engage_queue SET state='cancelled', result='platform paused after a warning' WHERE id=?", (q["id"],))
                audit_ev(c, "engage_run", "worker", q["slack_ts"], "refused:plat_paused", q["url"]); return
            if done_today >= pcap:
                c.execute("UPDATE engage_queue SET state='cancelled', result='daily cap' WHERE id=?", (q["id"],))
                audit_ev(c, "engage_run", "worker", q["slack_ts"], "refused:cap_engage", q["url"])
                outbox_add(c, G.msg("cap_engage", platform=pf, used=done_today, cap=pcap), q["slack_ts"]); return
            hrs = active_hours()
            if hrs and not BA.in_active(time.time(), hrs):
                c.execute("UPDATE engage_queue SET due_at=? WHERE id=?", (BA.next_active_start(time.time(), hrs) + random.randint(60, 900), q["id"])); return
            if last and time.time() - last < gap_s:
                c.execute("UPDATE engage_queue SET due_at=? WHERE id=?", (last + gap_s + random.randint(30, max(extra_s, 60)), q["id"])); return
            n_hour = len([r for r in done_rows if r["done_at"] > time.time() - 3600]) if day0 <= time.time() - 3600 else \
                len(_plat_rows(c, pf, "SELECT url FROM engage_queue WHERE state IN ('running','done','failed','stopped') AND done_at>?", (time.time() - 3600,)))
            if per_hour and n_hour >= per_hour:
                c.execute("UPDATE engage_queue SET due_at=? WHERE id=?", (time.time() + random.randint(600, 1500), q["id"])); return
            if q["action"] not in ALLOWED_ACTIONS or q["action"] not in (cfg()["engagement"].get("allowed_actions") or []):
                c.execute("UPDATE engage_queue SET state='cancelled', result='action not allowed' WHERE id=?", (q["id"],)); return
            g = guard_browser(c, {"task": q["url"], "source": "worker-approved", "essential": True})
            if not g["ok"]:
                c.execute("UPDATE engage_queue SET state='cancelled', result=? WHERE id=?", ("guard: " + g["reason"], q["id"]))
                outbox_add(c, "Not done: " + g["message"], q["slack_ts"]); return
            c.execute("UPDATE engage_queue SET state='running', done_at=? WHERE id=?", (time.time(), q["id"]))
        log(f"RUN q{q['id']} {q['action']} {q['url']}")
        res = run_one(q)
        st, txt = res.get("status"), str(res.get("result_text") or "")
        if st == "ok" and not txt.startswith("STOPPED"):
            state = "done"
        elif st == "stopped" or txt.startswith("STOPPED") or (st != "ok" and DANGER.search(txt)):
            state = "stopped"
        else:
            state = "failed"
        with LOCK:
            c.execute("UPDATE engage_queue SET state=?, browser_id=?, result=?, done_at=? WHERE id=?",
                      (state, res.get("id"), txt[:1000], time.time(), q["id"]))
            set_state(c, q["slack_ts"], {"done": "done", "failed": "failed", "stopped": "stopped"}[state], result={"browser": res.get("id"), "status": st})
            ledger_write({"ts_ist": fmt(now_ist()), "day": today(), "queue_id": q["id"], "platform": pf, "account": ACCOUNTS.get(pf, "@" + OWN_HANDLE),
                          "action": q["action"], "post_url": q["url"], "text": q["text"], "approved_by": q["approver"],
                          "approved_at_ist": fmt(datetime.fromtimestamp(q["approved_at"], IST)), "slack_ts": q["slack_ts"],
                          "status": state, "browser_task_id": res.get("id", ""), "result": txt})
            n_done = c.execute("SELECT COUNT(*) FROM engage_queue WHERE state='done' AND done_at>=?", (now_ist().replace(hour=0, minute=0, second=0, microsecond=0).timestamp(),)).fetchone()[0]
            audit_ev(c, "engage_run", "worker", q["slack_ts"], state, f"{q['action']} {q['url']} {txt[:150]}")
            if state == "done":
                outbox_add(c, f"Done: {q['action']} on {q['url']} (ledger updated, {n_done} done today). {txt[:200]}", q["slack_ts"])
            elif state == "failed":
                outbox_add(c, f"Couldn't {q['action']} (status {st}): {txt[:300]}. Not retried; logged in the ledger.", q["slack_ts"])
            elif pf in COMMENT_PLATFORMS:
                platform_warning(c, pf, f"browser stopped on {q['url']}: {txt[:200]}", "worker")
                outbox_add(c, f"STOPPED before/while commenting: {txt[:300]}. {G.PLATFORM_NAMES[pf]} comments are paused for 7 days.", q["slack_ts"])
            else:
                n = set_pause(c, f"auto: browser stopped on {q['url']}: {txt[:200]}", "state-helper")
                audit_ev(c, "engage_autopause", "worker", q["slack_ts"], "paused", txt[:200])
                outbox_add(c, f"STOPPED before/while acting: {txt[:300]}. All engagement is now paused.", q["slack_ts"])
                outbox_add(c, f":warning: Engagement paused automatically. The browser saw a warning/login/captcha-type page on X:\n> {txt[:300]}\n"
                              f"{n} queued action(s) cancelled. Please check @{OWN_HANDLE} in the browser yourself. Say 'resume engagement' only when it looks fine.")
        log(f"END q{q['id']} {state} {st} {txt[:120]}")
    finally:
        c.close()


# ---------------------------------------------------------------- OpenRouter metering proxy (/or/<lane>/v1/...)
def detect_purpose(lane, body):
    if lane == "browser":
        return "browser"
    try:
        txt = json.dumps(body.get("messages") or body.get("prompt") or "")[:6000]
    except Exception:
        txt = ""
    if G.CHECK_TAG in txt: return "content_check"
    if "client-1 social media assistant" in txt: return "chat"
    if "engage in a friendly, human way" in txt: return "engage_drafter"
    if "onboarding interview" in txt: return "onboarding"
    if BATCH_TAG in txt: return "batch_drafter"
    return "other"

def est_cost(pt, ct):
    sp = cfg()["spend"]
    return (pt or 0) / 1e6 * float(sp.get("est_usd_per_m_input", 0.25)) + (ct or 0) / 1e6 * float(sp.get("est_usd_per_m_output", 1.5))

def lookup_generation(gen_id, auth, row):
    """Fallback when the response carried no cost: ask OpenRouter for the generation's cost, else estimate from tokens."""
    cost, src = None, "estimate"
    for wait in (2, 5, 10):
        time.sleep(wait)
        try:
            req = urllib.request.Request(OR_UPSTREAM + "/generation?id=" + urllib.parse.quote(gen_id), headers={"Authorization": auth})
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.loads(r.read()).get("data") or {}
            if d.get("total_cost") is not None:
                cost, src = float(d["total_cost"]), "generation"
                break
        except Exception:
            continue
    if cost is None:
        cost = est_cost(row.get("pt"), row.get("ct"))
    record_spend({**row, "cost": cost, "src": src})

def account_usage(row, usage, gen_id, auth):
    usage = usage or {}
    row.update(pt=usage.get("prompt_tokens") or 0, ct=usage.get("completion_tokens") or 0, gen_id=gen_id or "")
    if usage.get("cost") is not None:
        record_spend({**row, "cost": float(usage["cost"]), "src": "response"})
    elif gen_id and auth:
        threading.Thread(target=lookup_generation, args=(gen_id, auth, row), daemon=True).start()
    else:
        record_spend({**row, "cost": est_cost(row["pt"], row["ct"]), "src": "estimate"})

def budget_stop_reply(model, stream):
    text = ("This month's AI budget for this bot is used up, so I've stopped AI replies until the 1st. Nothing was posted or changed. "
            "the owner can raise the budget or upgrade the plan to switch it back on. Type limits to see usage.")
    base = {"id": "c1-budget-stop-%d" % int(time.time()), "created": int(time.time()), "model": model or "budget-stop"}
    if stream:
        ch = {**base, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}]}
        end = {**base, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}
        return ("data: " + json.dumps(ch) + "\n\ndata: " + json.dumps(end) + "\n\ndata: [DONE]\n\n").encode(), "text/event-stream"
    return json.dumps({**base, "object": "chat.completion", "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                       "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}).encode(), "application/json"

def handle_proxy(h, method):
    m = re.match(r"^/or/([a-z0-9_-]+)/v1/(.*)$", h.path)
    if not m:
        return h.send({"error": "bad proxy path"}, 404)
    lane, sub = m.group(1), m.group(2)
    n = int(h.headers.get("Content-Length") or 0)
    raw = h.rfile.read(n) if n else b""
    auth = h.headers.get("Authorization") or ""
    hdrs = {k: v for k, v in h.headers.items() if k.lower() in ("authorization", "content-type", "http-referer", "x-title", "accept", "user-agent")}
    body, is_chat, stream, purpose, model = None, False, False, "other", ""
    if method == "POST" and sub.split("?")[0].endswith("chat/completions"):
        try:
            body = json.loads(raw or b"{}"); is_chat = True
        except Exception:
            body = None
    if is_chat:
        stream, model = bool(body.get("stream")), str(body.get("model") or "")
        purpose = detect_purpose(lane, body)
        c = db()
        try:
            lvl = spend_status(c)["level"]
        finally:
            c.close()
        if lvl == "hard" and purpose != "content_check":
            data, ctype = budget_stop_reply(model, stream)
            record_spend({"lane": lane, "purpose": purpose + ":blocked", "model": model, "cost": 0, "src": "blocked", "status": 0})
            if lane == "browser":
                return h.send({"error": {"message": "example-client monthly AI budget used up (hard stop)", "code": 402}}, 402)
            h.send_response(200); h.send_header("Content-Type", ctype); h.send_header("Content-Length", str(len(data))); h.end_headers()
            h.wfile.write(data); return
        body["usage"] = {"include": True}
        if stream:
            body.setdefault("stream_options", {"include_usage": True})
        raw = json.dumps(body).encode()
    url = OR_UPSTREAM + "/" + sub
    req = urllib.request.Request(url, method=method, data=raw if method == "POST" else None, headers=hdrs)
    row = {"lane": lane, "purpose": purpose, "model": model}
    try:
        r = urllib.request.urlopen(req, timeout=600)
        status = r.status
    except urllib.error.HTTPError as e:
        r, status = e, e.code
    except Exception as e:
        return h.send({"error": {"message": f"example-client proxy upstream error: {type(e).__name__}: {e}"}}, 502)
    ctype = r.headers.get("Content-Type") or "application/json"
    h.send_response(status)
    h.send_header("Content-Type", ctype)
    if "event-stream" in ctype:
        h.send_header("Cache-Control", "no-cache"); h.end_headers()
        usage, gen_id, buf = None, "", b""
        while True:
            chunk = r.read1(65536) if hasattr(r, "read1") else r.read(65536)
            if not chunk:
                break
            h.wfile.write(chunk); h.wfile.flush()
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.startswith(b"data: ") and line[6:].strip() not in (b"[DONE]", b""):
                    try:
                        d = json.loads(line[6:])
                        gen_id = gen_id or d.get("id") or ""
                        if d.get("usage"): usage = d["usage"]
                    except Exception:
                        pass
        if is_chat:
            account_usage({**row, "status": status}, usage, gen_id, auth)
        return
    data = r.read()
    h.send_header("Content-Length", str(len(data))); h.end_headers(); h.wfile.write(data)
    if is_chat:
        try:
            d = json.loads(data)
        except Exception:
            d = {}
        if status == 200:
            account_usage({**row, "status": status}, d.get("usage"), d.get("id"), auth)

# ---------------------------------------------------------------- HTTP
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        if self.path.startswith("/or/"):
            return handle_proxy(self, "GET")
        u = urllib.parse.urlparse(self.path)
        qs = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        c = db()
        try:
            if u.path == "/health":
                return self.send({"status": "ok", "service": "state-helper", "db": DB, "worker": WORKER_ON, "time": fmt(now_ist())})
            if u.path == "/context":
                th = qs.get("thread", "")
                return self.send({"notes": read_notes()[:12000], "history": get_history(c, th, int(qs.get("turns", MEMORY_TURNS))),
                                  "thread": th, "engage": engage_status(c), "guard": guard_summary(c),
                                  "paused_all": pause_all_info()[0]})
            if u.path == "/tick":
                cutoff = time.time() - 48 * 3600
                c.execute("UPDATE proposals SET state='expired', updated=? WHERE state IN ('pending','editing') AND created<?", (time.time(), cutoff))
                ttl = float((cfg().get("batches") or {}).get("item_ttl_hours", 6) or 6)
                c.execute("""UPDATE proposals SET state='expired', updated=? WHERE kind='engage' AND state IN ('pending','editing') AND created<?
                             AND payload LIKE '%"batch_id"%'""", (time.time(), time.time() - ttl * 3600))
                pend = []
                for r in c.execute("SELECT * FROM proposals WHERE state IN ('pending','editing') ORDER BY created DESC LIMIT 20"):
                    pend.append({"slack_ts": r["slack_ts"], "kind": r["kind"], "thread_ts": r["thread_ts"], "state": r["state"],
                                 "batch": '"batch_id"' in (r["payload"] or "")})
                ob = [dict(r) for r in c.execute("SELECT id, channel, thread_ts, text FROM outbox WHERE sent IS NULL ORDER BY id LIMIT 10")]
                paused, reason = pause_info()
                co = cfg().get("coach") or {}
                return self.send({"ok": True, "pending": pend, "outbox": ob, "paused": paused, "paused_all": pause_all_info()[0],
                                  "coach_open": coach_open_for_poll(c) if co.get("enabled") else [],
                                  "coach_users": co.get("client_users") or [OWNER],
                                  "batches": batch_items_map(c), "approvers": cfg()["approvals"].get("approvers") or [OWNER]})
            if u.path == "/guard/limits":
                return self.send({"text": limits_text(c), "spend": spend_status(c), "browser": browser_status(c),
                                  "paused_all": pause_all_info()[0], "summary": guard_summary(c)})
            if u.path == "/guard/allowlist":
                return self.send({"domains": G.allowed_domains(cfg()), "paused_all": pause_all_info()[0]})
            if u.path == "/guard/weekly":
                return self.send({"text": weekly_summary(c, int(qs.get("days", 7)))})
            if u.path == "/guard/config":
                return self.send({"config": cfg(), "error": GUARD.error})
            if u.path == "/engage/status":
                return self.send(engage_status(c))
            if u.path == "/batch/status":
                return self.send({"text": batch_status_text(c), "items": batch_items_map(c), "recent": batch_recent(c),
                                  "next_due": kv_get(c, "batch_next_due"), "plan": cfg().get("plan")})
            if u.path == "/coach/status":
                return self.send({"text": coach_status_text(c), "today": day_rows(c, 1, include_today=True)[0],
                                  "ramp": {p: ramp_state(c, p) for p in ("x", "linkedin", "facebook")}, "plan": cfg().get("plan")})
            if u.path == "/proposal":
                return self.send(get_prop(c, qs.get("ts", "")) or {})
            return self.send({"error": "not found"}, 404)
        finally:
            c.close()

    def do_POST(self):
        if self.path.startswith("/or/"):
            try:
                return handle_proxy(self, "POST")
            except Exception as e:
                log(f"proxy error {type(e).__name__}: {e}")
                try:
                    return self.send({"error": {"message": f"example-client proxy error: {type(e).__name__}"}}, 502)
                except Exception:
                    return
        b = self.body()
        c = db()
        try:
            with LOCK:
                dry = bool(b.get("dry_run"))
                c.execute("BEGIN IMMEDIATE")
                try:
                    if self.path == "/memory":
                        add_memory(c, b.get("thread", ""), b.get("user_text", ""), b.get("bot_text", "")); r = {"ok": True}
                    elif self.path == "/proposals":
                        r = register(c, b)
                    elif self.path == "/op":
                        r = op(c, b)
                    elif self.path == "/op/finish":
                        r = op_finish(c, b)
                    elif self.path == "/engage/plan":
                        r = engage_plan(c, b)
                    elif self.path == "/engage/filter":
                        r = engage_filter(c, b)
                    elif self.path == "/engage/lint":
                        r = {"items": [{**it, "lint": lint(str(it.get("text") or ""))} for it in (b.get("items") or [])]}
                    elif self.path == "/engage/pause":
                        n = set_pause(c, b.get("reason") or "auto", b.get("by") or "n8n", dry); r = {"ok": True, "cancelled": n}
                    elif self.path == "/notes/onboarding":
                        r = write_onboarding(b.get("client") or "", b.get("block") or "", dry) if b.get("block") else {"ok": True, "skipped": True}
                    elif self.path == "/guard/browser":
                        r = guard_browser(c, b)
                    elif self.path == "/guard/content":
                        r = content_precheck(str(b.get("text") or ""), b.get("kind") or "post")
                        audit_ev(c, "content_precheck", b.get("by") or "agent", "", "pass" if r["ok"] else "block:regex:" + r["blocks"][0]["cat"], str(b.get("text") or "")[:200], dry)
                    elif self.path == "/guard/verdict":
                        r = guard_verdict(c, b)
                    elif self.path == "/guard/hold_done":
                        r = hold_done(c, b)
                    elif self.path == "/coach/plan":
                        r = coach_plan(c, b)
                    elif self.path == "/coach/build":
                        r = coach_build(c, b)
                    elif self.path == "/coach/register":
                        r = coach_register(c, b)
                    elif self.path == "/coach/tick":
                        r = coach_tick(c, b)
                    elif self.path == "/coach/verified":
                        r = coach_verified(c, b)
                    elif self.path == "/coach/stats":
                        r = coach_stats(c, b)
                    elif self.path == "/batch/plan":
                        r = batch_plan(c, b)
                    elif self.path == "/batch/pick":
                        r = batch_pick(c, b)
                    elif self.path == "/batch/build":
                        r = batch_build(c, b)
                    elif self.path == "/batch/register":
                        r = batch_register(c, b)
                    elif self.path == "/ramp/warning":
                        r = {"ok": True, "state": ramp_warning(c, G.platform_of(b.get("platform") or "x"), b.get("reason") or "", b.get("by") or "n8n", dry)}
                    elif self.path == "/outbox/ack":
                        ids = [int(i) for i in (b.get("ids") or [])]
                        c.executemany("UPDATE outbox SET sent=? WHERE id=?", [(time.time(), i) for i in ids]); r = {"ok": True}
                    else:
                        c.execute("ROLLBACK"); return self.send({"error": "not found"}, 404)
                    c.execute("ROLLBACK" if dry else "COMMIT")
                except Exception:
                    c.execute("ROLLBACK"); raise
            return self.send(r)
        except Exception as e:
            log(f"POST {self.path} error {type(e).__name__}: {e}")
            return self.send({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
        finally:
            c.close()


if __name__ == "__main__":
    init_db()
    try:
        _c = db(); n = audit_reconcile(_c); _c.close()
        if n: log(f"audit reconcile: backfilled {len(n)} row(s)")
    except Exception as e:
        log(f"audit reconcile error {e}")
    if WORKER_ON:
        threading.Thread(target=worker, daemon=True).start()
    log(f"listening 127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
