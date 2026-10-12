#!/usr/bin/env python3
"""selfheal.py: light self-heal supervisor for the Northstar box (stdlib only, one pass in ~1-3 s, ~20 MB).

  selfheal.py once      one check/repair pass (prints a summary)
  selfheal.py loop      a pass every SELFHEAL_INTERVAL seconds (default 300), single instance via flock
  selfheal.py ensure    start the loop in the background if it isn't running, then run one pass

The box has no cron or systemd, so the loop is started by `ensure` (services-watchdog.sh calls it on each pass).

What it does each pass:
  * health: n8n, each client instance's state helper (SELFHEAL_INSTANCES), app :3100 and its worker, browser agent :8770, Chrome CDP endpoints
  * recovery: restarts through the existing service scripts with exponential backoff, at most 3 tries per service per hour
    (state in STATE_FILE); anything still broken after that is escalated as a JSON alert in ALERT_DIR
  * n8n task-runner "Offer expired" storm in the n8n log -> counts as an n8n failure (same backoff)
  * Chrome hygiene (same safe rules as the v0.5 cleanup): reaps duplicate Chromes on a profile whose CDP is held by
    another live instance (kills only the duplicate's sandbox namespace), and orphaned headless/test Chromes with a temp
    profile and a dead parent; closes only about:blank or exact-duplicate tabs on REAP_TAB_PORTS while the agent is idle.
    It never kills the CDP-holding main process of a profile and never touches a profile directory
  * n8n: retries a failed execution ONCE, only for workflows on IDEMPOTENT_ALLOWLIST and never for anything matching
    NEVER_RETRY (publish / post / reply / comment / engage / buffer / actions / approval / tool)
  * known fixes: a post that failed because its Buffer draft lacked a post type or text is put back to 'pending' so the approval
    flow rebuilds it (it is never posted from here); a pending draft containing '<<<' or '>>>' is flagged and blocked
  * platform warnings, captchas and sign-in pages in the client audit logs -> alert (and no retries for 24 h)
Every action is a row in AUDIT_TSV (ts, action, target, outcome, detail).

Hard limits, enforced in code (safe_write) and by tests: it never writes its own code, any guardrails file or caps; it never
signs in or opens a sign-in link; it never retries past a platform warning.
"""
import fcntl, glob, json, os, re, signal, sqlite3, subprocess, sys, time, urllib.request
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))
HOME = os.environ.get("HOME", os.path.expanduser("~"))
E = lambda k, d: os.environ.get(k, d)
STATE_DIR = E("SELFHEAL_STATE_DIR", f"{HOME}/agency/state")
STATE_FILE = E("SELFHEAL_STATE_FILE", f"{STATE_DIR}/selfheal-state.json")
AUDIT_TSV = E("SELFHEAL_AUDIT_TSV", f"{STATE_DIR}/selfheal-audit.tsv")
ALERT_DIR = E("SELFHEAL_ALERT_DIR", f"{STATE_DIR}/alerts")
BIN = E("SELFHEAL_BIN", f"{HOME}/agency/bin")
N8N_LOG = E("SELFHEAL_N8N_LOG", f"{HOME}/agency/logs/n8n.log")
N8N_ENV = E("SELFHEAL_N8N_ENV", f"{HOME}/apps/n8n/owner.env")
APP_DATA = E("SELFHEAL_APP_DATA", f"{HOME}/apps/northstar-app/data")
LOCK = E("SELFHEAL_LOCK", f"{HOME}/agency/locks/selfheal.lock")
INTERVAL = int(E("SELFHEAL_INTERVAL", "300"))
MAX_TRIES_PER_HOUR = 3
BASE_BACKOFF_S = 300                 # 5 min, then 10, then 20
OFFER_EXPIRED_STORM = int(E("SELFHEAL_OFFER_EXPIRED_STORM", "30"))   # new lines in one pass
CLIENTS_DIR = E("SELFHEAL_CLIENTS_DIR", f"{HOME}/agency/clients")

def parse_instances(spec):
    """SELFHEAL_INSTANCES: comma list of key:port[:health_text[:restart_script]], e.g.
    "example-client:8771,example-client-b:8772". Each client instance runs its own state
    helper; defaults: health text "state-helper", restart "$SELFHEAL_BIN/<key>-state-up.sh restart",
    audit log and helper db under $SELFHEAL_CLIENTS_DIR/<key>/."""
    out = []
    for item in filter(None, (x.strip() for x in spec.split(","))):
        f = item.split(":")
        key, port = f[0], int(f[1])
        out.append({"key": key, "port": port, "expect": f[2] if len(f) > 2 and f[2] else "state-helper",
                    "restart": f[3] if len(f) > 3 and f[3] else f"{BIN}/{key}-state-up.sh"})
    return out

INSTANCES = parse_instances(E("SELFHEAL_INSTANCES", ""))
CLIENTS = {i["key"]: (f"{CLIENTS_DIR}/{i['key']}/audit-log.tsv", f"{CLIENTS_DIR}/{i['key']}/bot-state.sqlite") for i in INSTANCES}
SERVICES = [  # name, health url, expected text, restart command (existing service scripts only)
    ("n8n", "http://127.0.0.1:5678/healthz", '"ok"', [f"{BIN}/n8n-up.sh", "restart"]),
    *[(f"{i['key']}-state", f"http://127.0.0.1:{i['port']}/health", i["expect"], [i["restart"], "restart"]) for i in INSTANCES],
    ("northstar-app", "http://127.0.0.1:3100/api/health", "northstar-app", [f"{BIN}/northstar-app-up.sh", "start"]),
    ("browser-agent", "http://127.0.0.1:8770/health", "browser-agent", [f"{BIN}/browser-agent-up.sh", "restart"]),
]
CDP_PORTS = [int(p) for p in E("SELFHEAL_CDP_PORTS", "").split(",") if p]
REAP_TAB_PORTS = [int(p) for p in E("SELFHEAL_REAP_TAB_PORTS", "").split(",") if p]
IDEMPOTENT_ALLOWLIST = set(filter(None, E("SELFHEAL_RETRY_ALLOWLIST",
    # read / poll / collector workflows only. The Slack pollers are NOT here: they dispatch approvals (which publish).
    "Client bot: read receipt (reusable)").split("|")))
NEVER_RETRY = re.compile(r"publish|post|repl|comment|engage|buffer|action|approv|tool|batch|send|notify|onboard", re.I)
WARN_RE = re.compile(r"(platform_warning|ramp_warning|checkpoint|captcha|sign[- ]?in page|login page|account (locked|restricted)|unusual activity)", re.I)
DRAFT_FIX_RE = re.compile(r"fail:draft|missing (post )?type|no text|neither text nor media|post type", re.I)
SELF = os.path.realpath(__file__)

class Forbidden(Exception):
    pass

def now():
    return time.time()

def ts():
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")

def safe_write(path, data, mode="w"):
    """The only way this script writes files. Refuses its own code, any guardrails file, caps, and anything outside
    its state/alert dirs."""
    rp = os.path.realpath(path)
    base = os.path.basename(rp).lower()
    if rp == SELF or rp.startswith(os.path.dirname(SELF) + os.sep) and rp.endswith(".py"):
        raise Forbidden(f"refusing to write own code: {rp}")
    if "guardrail" in base or "caps" in base or base.endswith((".yaml", ".yml")):
        raise Forbidden(f"refusing to write guardrails/caps: {rp}")
    allowed = [os.path.realpath(d) for d in (STATE_DIR, ALERT_DIR)]
    if not any(rp == d or rp.startswith(d + os.sep) for d in allowed):
        raise Forbidden(f"refusing to write outside state dirs: {rp}")
    os.makedirs(os.path.dirname(rp), exist_ok=True)
    tmp = rp + ".tmp" if mode == "w" else rp
    with open(tmp, mode) as f:
        f.write(data)
    if mode == "w":
        os.replace(tmp, rp)

def audit(action, target, outcome, detail=""):
    if not os.path.exists(AUDIT_TSV):
        safe_write(AUDIT_TSV, "ts_ist\taction\ttarget\toutcome\tdetail\n")
    clean = lambda s: str(s).replace("\t", " ").replace("\n", " ")[:400]
    safe_write(AUDIT_TSV, f"{ts()}\t{clean(action)}\t{clean(target)}\t{clean(outcome)}\t{clean(detail)}\n", mode="a")

def alert(kind, target, detail, extra=None):
    """Escalation: one JSON file per (kind, target) per hour."""
    os.makedirs(ALERT_DIR, exist_ok=True)
    key = re.sub(r"[^a-zA-Z0-9_.-]+", "-", f"{kind}-{target}")[:80]
    name = f"{datetime.now(IST):%Y%m%d-%H}-{key}.json"
    path = os.path.join(ALERT_DIR, name)
    if os.path.exists(path):
        return path
    body = {"ts_ist": ts(), "kind": kind, "target": target, "detail": str(detail)[:1000], "source": "selfheal", **(extra or {})}
    safe_write(path, json.dumps(body, indent=1) + "\n")
    audit("alert", target, kind, detail)
    return path

def load_state():
    try:
        return json.load(open(STATE_FILE))
    except Exception:
        return {"tries": {}, "retried": [], "n8n_log_pos": 0, "seen_warn": [], "fixed": []}

def save_state(st):
    for k in ("retried", "seen_warn", "fixed"):
        st[k] = st.get(k, [])[-500:]
    safe_write(STATE_FILE, json.dumps(st))

# ------------------------------------------------------------------ backoff
def can_try(st, svc, t=None):
    """At most MAX_TRIES_PER_HOUR tries per service per rolling hour, spaced 5/10/20 min apart."""
    t = t or now()
    tries = [x for x in st.setdefault("tries", {}).get(svc, []) if t - x < 3600]
    st["tries"][svc] = tries
    if len(tries) >= MAX_TRIES_PER_HOUR:
        return False
    if tries and t - tries[-1] < BASE_BACKOFF_S * (2 ** (len(tries) - 1)):
        return False
    return True

def record_try(st, svc, t=None):
    st.setdefault("tries", {}).setdefault(svc, []).append(t or now())

def exhausted(st, svc, t=None):
    t = t or now()
    return len([x for x in st.get("tries", {}).get(svc, []) if t - x < 3600]) >= MAX_TRIES_PER_HOUR

# ------------------------------------------------------------------ helpers
def http(url, timeout=8, method="GET", data=None, headers=None):
    req = urllib.request.Request(url, method=method, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")

def healthy(url, expect):
    try:
        return expect in http(url)
    except Exception:
        return False

def pid_alive(pidfile):
    try:
        os.kill(int(open(pidfile).read().strip()), 0)
        return True
    except Exception:
        return False

def warnings_open(t=None):
    """True if a platform-warning alert was raised in the last 24 h (then: no retries, no browser restarts)."""
    t = t or now()
    for f in glob.glob(os.path.join(ALERT_DIR, "*platform_warning*.json")):
        if t - os.path.getmtime(f) < 86400:
            return True
    return False

def run(cmd, timeout=180):
    if any("signin" in str(c).lower() for c in cmd):
        raise Forbidden("never signs in")
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env={**os.environ, "HOME": HOME})
        return p.returncode, (p.stdout + p.stderr).strip()[-300:]
    except Exception as e:
        return 99, f"{type(e).__name__}: {e}"

# ------------------------------------------------------------------ checks
def check_services(st, out, runner=run):
    for name, url, expect, cmd in SERVICES:
        ok = healthy(url, expect)
        if name == "northstar-app" and ok and not pid_alive(f"{APP_DATA}/worker.pid"):
            ok, why = False, "worker down"
        else:
            why = "health check failed"
        if name == "browser-agent" and not ok and warnings_open():
            out.append(f"{name}: down, but a platform warning is open -> not restarting"); alert("unresolved", name, "browser agent down during an open platform warning"); continue
        if ok:
            out.append(f"{name}: ok"); continue
        if can_try(st, name):
            record_try(st, name)
            rc, msg = runner(cmd)
            ok2 = healthy(url, expect)
            audit("restart", name, "ok" if ok2 else f"failed rc={rc}", f"{why}; {msg}")
            out.append(f"{name}: {why} -> restart {'ok' if ok2 else 'FAILED'}")
            if not ok2 and exhausted(st, name):
                alert("unresolved", name, f"still down after {MAX_TRIES_PER_HOUR} restarts this hour: {msg}")
        else:
            out.append(f"{name}: down, backoff")
            if exhausted(st, name):
                alert("unresolved", name, f"down; {MAX_TRIES_PER_HOUR} restarts used this hour")

def check_offer_expired(st, out, runner=run):
    try:
        size = os.path.getsize(N8N_LOG)
    except OSError:
        out.append("n8n log: missing"); return
    pos = st.get("n8n_log_pos", 0)
    if pos > size or pos == 0:
        st["n8n_log_pos"] = size; out.append("n8n log: offset set"); return
    with open(N8N_LOG, "rb") as f:
        f.seek(pos); chunk = f.read(min(size - pos, 5_000_000)).decode("utf-8", "replace")
    st["n8n_log_pos"] = size
    n = chunk.count("Offer expired")
    out.append(f"n8n 'Offer expired': {n} new")
    if n >= OFFER_EXPIRED_STORM:
        if can_try(st, "n8n"):
            record_try(st, "n8n"); rc, msg = runner([f"{BIN}/n8n-up.sh", "restart"])
            audit("restart", "n8n", f"rc={rc}", f"task-runner 'Offer expired' storm: {n} in one pass; {msg}")
        elif exhausted(st, "n8n"):
            alert("unresolved", "n8n", f"'Offer expired' storm ({n}) persists after {MAX_TRIES_PER_HOUR} restarts")

def check_cdp(out):
    for p in CDP_PORTS:
        try:
            ok = "Browser" in http(f"http://127.0.0.1:{p}/json/version", timeout=10)
        except Exception:
            ok = False
        out.append(f"cdp {p}: {'ok' if ok else 'DOWN'}")
        if not ok:   # never relaunches a signed-in browser (that may need a sign-in): escalate
            alert("unresolved", f"cdp-{p}", "Chrome CDP endpoint not answering (selfheal never relaunches signed-in browsers)")

# ------------------------------------------------------------------ chrome hygiene
def _procs():
    ps = {}
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            cmd = open(f"/proc/{p}/cmdline", "rb").read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
            ppid = int(open(f"/proc/{p}/stat").read().rsplit(")", 1)[1].split()[1])
        except Exception:
            continue
        ps[int(p)] = (ppid, cmd)
    return ps

def _arg(cmd, k):
    m = re.search(re.escape(k) + r"=(\S+)", cmd)
    return m.group(1) if m else ""

def chrome_plan(ps, cdp_holder):
    """Pure: which pids are safe to kill. ps = {pid: (ppid, cmd)}, cdp_holder = {port: pid of the main holding CDP}.
    Returns [(pid, reason)]. Never returns a CDP-holding main process."""
    kill = []
    def anc(p):
        out = []
        while p in ps and p not in out and len(out) < 64:
            out.append(p); p = ps[p][0]
        return out
    holders = set(cdp_holder.values())
    related = set()   # every ancestor of a holder (bwrap, launchers) and every descendant of one (helpers, crashpad)
    for h in holders:
        related.update(anc(h))
    for p in ps:
        if holders & set(anc(p)):
            related.add(p)
    mains = {p: v for p, v in ps.items()
             if os.path.basename(v[1].split(" ")[0]) in ("chrome", "google-chrome", "chromium", "chromium-browser")
             and "--type=" not in v[1] and "--user-data-dir=" in v[1] and p not in related}
    by_dir = {}
    for p, (pp, cmd) in mains.items():
        by_dir.setdefault(_arg(cmd, "--user-data-dir"), []).append(p)
    holders = set(cdp_holder.values())
    for d, pids in by_dir.items():
        keep = [h for h in holders if _arg(ps.get(h, (0, ""))[1], "--user-data-dir") == d]
        if keep and pids:   # duplicates on a profile whose CDP another live instance holds
            for p in pids:
                if p in holders:
                    continue
                pp = ps[p][0]
                target = pp if pp in ps and ps[pp][1].startswith("bwrap") and pp not in related else p   # kill the duplicate's sandbox namespace
                kill.append((target, f"duplicate chrome on {d} (CDP held by {keep[0]})"))
    for p, (pp, cmd) in mains.items():
        if p in holders:
            continue
        d = _arg(cmd, "--user-data-dir")
        temp = d.startswith(("/tmp/", "/var/tmp/")) or "playwright" in d or "puppeteer" in d
        if ("--headless" in cmd or temp) and temp and (pp == 1 or pp not in ps):
            kill.append((p, f"orphan headless/test chrome ({d})"))
    seen, res = set(), []
    for t, r in kill:
        if t not in seen and t not in holders and t not in related:
            seen.add(t); res.append((t, r))
    return res

def cdp_holders():
    out = {}
    try:
        txt = subprocess.run(["ss", "-ltnpH"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return out
    for line in txt.splitlines():
        m = re.search(r"127\.0\.0\.1:(\d+)\s.*pid=(\d+)", line)
        if m and int(m.group(1)) in CDP_PORTS + [9224, 9225, 9226, 9227]:
            out[int(m.group(1))] = int(m.group(2))
    return out

def reap_chrome(out, dry=False):
    plan = chrome_plan(_procs(), cdp_holders())
    for pid, why in plan:
        if not dry:
            try:
                os.kill(pid, signal.SIGKILL)
                audit("reap_chrome", str(pid), "killed", why)
            except Exception as e:
                audit("reap_chrome", str(pid), "error", f"{why}: {e}")
    out.append(f"chrome reap: {len(plan)} {'(dry)' if dry else 'killed'}")
    try:
        if '"busy":true' in http("http://127.0.0.1:8770/health").replace(" ", ""):
            out.append("tabs: browser agent busy, skipped"); return
    except Exception:
        pass
    for port in REAP_TAB_PORTS:
        try:
            tabs = [t for t in json.loads(http(f"http://127.0.0.1:{port}/json/list")) if t.get("type") == "page"]
        except Exception:
            continue
        seen, closed = set(), 0
        for t in tabs:
            u = t.get("url", "")
            if (u == "about:blank" or u in seen) and len(tabs) - closed > 1:
                if not dry:
                    try:
                        http(f"http://127.0.0.1:{port}/json/close/{t['id']}"); closed += 1
                    except Exception:
                        pass
            seen.add(u)
        if closed:
            audit("close_tabs", f"cdp-{port}", f"closed {closed}", "about:blank / duplicate tabs")
        out.append(f"tabs {port}: closed {closed}")

# ------------------------------------------------------------------ n8n retries
def may_retry(workflow_name):
    """Allowlist AND not publish-like. Both must hold."""
    return workflow_name in IDEMPOTENT_ALLOWLIST and not NEVER_RETRY.search(workflow_name)

def n8n_key():
    env = {}
    try:
        for l in open(N8N_ENV):
            m = re.match(r"\s*([A-Z0-9_]+)=(.*)", l)
            if m: env[m.group(1)] = m.group(2).strip().strip("\"'")
    except Exception:
        pass
    return env.get("N8N_URL", "http://127.0.0.1:5678").rstrip("/"), env.get("N8N_API_KEY", "")

def retry_failed(st, out, api=None):
    if warnings_open():
        out.append("n8n retries: skipped (platform warning open)"); return
    base, key = n8n_key()
    if not key and api is None:
        out.append("n8n retries: no API key"); return
    def _api(method, path):
        return json.loads(http(base + "/api/v1" + path, method=method, data=b"{}" if method == "POST" else None,
                               headers={"X-N8N-API-KEY": key, "content-type": "application/json"}, timeout=20))
    api = api or _api
    try:
        names = {w["id"]: w["name"] for w in api("GET", "/workflows?limit=250")["data"]}
        ex = api("GET", "/executions?status=error&limit=50")["data"]
    except Exception as e:
        out.append(f"n8n retries: api error {type(e).__name__}"); return
    done = set(st.setdefault("retried", []))
    n = 0
    for e in ex:
        eid, name = str(e["id"]), names.get(e.get("workflowId"), "")
        if eid in done or now() - datetime.fromisoformat(e["startedAt"].replace("Z", "+00:00")).timestamp() > 3600:
            continue
        st["retried"].append(eid)   # once only, whatever happens
        if not may_retry(name):
            continue
        try:
            api("POST", f"/executions/{eid}/retry"); n += 1
            audit("retry_execution", name, "retried", f"execution {eid}")
        except Exception as ex2:
            audit("retry_execution", name, "error", f"execution {eid}: {type(ex2).__name__}")
    out.append(f"n8n retries: {n}")

# ------------------------------------------------------------------ audit-log scans + known fixes
def scan_clients(st, out):
    seen = set(st.setdefault("seen_warn", [])); fixed = set(st.setdefault("fixed", []))
    baseline = not st.get("baseline_done")   # first run: remember existing rows, act only on rows that appear later
    for client, (log, dbp) in CLIENTS.items():
        try:
            rows = open(log).read().splitlines()[-400:]
        except Exception:
            continue
        for r in rows:
            parts = r.split("\t")
            if len(parts) < 5 or parts[0] == "ts_ist":
                continue
            key = f"{client}|{parts[0]}|{parts[1]}|{parts[3]}"
            if WARN_RE.search(r) and key not in seen:
                st["seen_warn"].append(key); seen.add(key)
                if not baseline: alert("platform_warning", client, r[:500])
            if parts[1] == "publish_result" and "fail" in parts[4] and DRAFT_FIX_RE.search(r) and key not in fixed:
                st["fixed"].append(key); fixed.add(key)
                if not baseline: requeue_for_rebuild(client, dbp, parts[3], r)
        block_fenced_drafts(client, dbp, st)
    if baseline:
        st["baseline_done"] = True; audit("baseline", "client audit logs", "recorded", f"{len(seen)} warning rows, {len(fixed)} draft failures already present (not acted on)")
    out.append("client scans: " + ("baseline recorded" if baseline else "done"))

def _db(path):
    c = sqlite3.connect(path, timeout=5); c.row_factory = sqlite3.Row
    return c

def requeue_for_rebuild(client, dbp, slack_ts, row):
    """Known fix: Buffer draft missing post type/text. Put the proposal back to 'pending' (the approval flow rebuilds the
    draft on the next ✅). Never posts. Skips if the audit log shows it was already published (no double post)."""
    try:
        if any(f"\t{slack_ts}\t" in l and "\tpublish_result\t" in l and ("\tpublished" in l or "\tdone" in l)
               for l in open(CLIENTS[client][0]).read().splitlines()):
            audit("requeue_for_rebuild", f"{client}:{slack_ts}", "skipped", "already published"); return
        c = _db(dbp)
        n = c.execute("UPDATE proposals SET state='pending', updated=? WHERE slack_ts=? AND state IN ('failed','partial')", (now(), slack_ts)).rowcount
        c.commit(); c.close()
        audit("requeue_for_rebuild", f"{client}:{slack_ts}", "requeued" if n else "nothing to do", row[:200])
    except Exception as e:
        audit("requeue_for_rebuild", f"{client}:{slack_ts}", "error", str(e))

def block_fenced_drafts(client, dbp, st):
    """Known fix: a pending draft with a stray '<<<' / '>>>' is blocked and flagged."""
    try:
        c = _db(dbp)
        rows = c.execute("SELECT slack_ts, payload FROM proposals WHERE state IN ('pending','editing')").fetchall()
        for r in rows:
            if "<<<" in (r["payload"] or "") or ">>>" in (r["payload"] or ""):
                c.execute("UPDATE proposals SET state='refused', updated=? WHERE slack_ts=?", (now(), r["slack_ts"]))
                audit("block_draft", f"{client}:{r['slack_ts']}", "blocked", "stray '<<<'/'>>>' in draft text")
                alert("draft_blocked", f"{client}-{r['slack_ts']}", "draft contained '<<<' or '>>>'; blocked, needs a rewrite")
        c.commit(); c.close()
    except Exception:
        pass

# ------------------------------------------------------------------ main
def one_pass(dry=False):
    st, out = load_state(), []
    for step in (lambda: check_services(st, out), lambda: check_offer_expired(st, out), lambda: check_cdp(out),
                 lambda: reap_chrome(out, dry), lambda: retry_failed(st, out), lambda: scan_clients(st, out)):
        try:
            step()
        except Forbidden:
            raise
        except Exception as e:
            out.append(f"step error: {type(e).__name__}: {e}")
    save_state(st)
    audit("pass", "selfheal", "done", "; ".join(o for o in out if not o.endswith(": ok"))[:380])
    return out

def main(argv):
    mode = argv[1] if len(argv) > 1 else "once"
    if mode == "once":
        print("\n".join(one_pass())); return 0
    if mode == "loop":
        os.makedirs(os.path.dirname(LOCK), exist_ok=True)
        fd = open(LOCK, "w")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("loop already running"); return 0
        while True:
            try:
                one_pass()
            except Exception as e:
                try: audit("pass", "selfheal", "error", str(e))
                except Exception: pass
            time.sleep(INTERVAL)
    if mode == "ensure":
        fd = open(LOCK, "a+")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); fcntl.flock(fd, fcntl.LOCK_UN); fd.close()
            subprocess.Popen([sys.executable, SELF, "loop"], stdout=open(f"{HOME}/agency/logs/selfheal.log", "a"),
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
            print("loop started (first pass runs now in the loop)")
        except OSError:
            print("loop already running"); print("\n".join(one_pass()))
        return 0
    print(__doc__); return 2

if __name__ == "__main__":
    sys.exit(main(sys.argv))
