"""2026-10-12 fixes: (1) Buffer editPost carries text+media, (2) X reply targets = watchlist + follows only,
onboarding ask, (3) every outbound engage action has an audit row (reconcile/backfill). Offline: temp DB, mock HTTP only."""
import os, sys, json, shutil, tempfile, time, traceback, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
T = tempfile.mkdtemp(prefix="c1fix3-")
GY = f"{T}/guardrails.yaml"; shutil.copy(os.environ.get("CS_GUARDRAILS_SRC") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "templates", "guardrails.example.yaml"), GY)
open(f"{T}/notes.md", "w").write("# notes\n")
os.environ.update(CS_DB=f"{T}/db.sqlite", CS_NOTES=f"{T}/notes.md", CS_PAUSE=f"{T}/PAUSE", CS_PAUSE_ALL=f"{T}/PAUSE_ALL",
                  CS_LEDGER=f"{T}/ledger.tsv", CS_LOG=f"{T}/log", CS_GUARDRAILS=GY, CS_AUDIT=f"{T}/audit.tsv", CS_UPSELL=f"{T}/upsell.md",
                  CS_NOTIFY="0", CS_WORKER="0", CS_OR_UPSTREAM="http://127.0.0.1:9")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import server as S
S.init_db()
res = []
def test(fn):
    try: fn(); res.append((fn.__name__, True, ""))
    except Exception as e: res.append((fn.__name__, False, f"{e}\n{traceback.format_exc(limit=3)}"))
def clean():
    k = S.db()
    for t in ("watchlist", "kv", "batch_pool", "outbox", "engage_queue", "suggested", "audit"): k.execute(f"DELETE FROM {t}")
    if os.path.exists(f"{T}/audit.tsv"): os.remove(f"{T}/audit.tsv")
    return k

P = {"slack_ts": "1.1", "payload": {"text": "Hello founders", "media_url": "https://example.com/a.png", "when": "queue",
                                    "drafts": [{"service": "twitter", "post_id": "P1", "channel_id": "C1"}]}}

@test
def editpost_has_text_and_media():
    k = clean(); out = {"buffer_ops": []}
    S.post_issue(k, P, "U0000000001", False, True, out)
    inp = out["buffer_ops"][0]["body"]["variables"]["input"]
    assert inp["text"] == "Hello founders" and inp["assets"] == [{"image": {"url": "https://example.com/a.png"}}], inp
    assert inp["id"] == "P1" and inp["mode"] == "addToQueue" and inp["saveToDraft"] is False and inp["schedulingType"] == "automatic"

@test
def editpost_via_mock_http():
    got = {}
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            got["b"] = json.loads(self.rfile.read(int(self.headers["Content-Length"]))); self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
        def log_message(self, *a): pass
    srv = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.handle_request, daemon=True).start()
    import urllib.request
    body = {"query": "mutation", "variables": {"input": S.edit_post_input(P, P["payload"]["drafts"][0], "shareNow")}}
    urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/", json.dumps(body).encode(), {"Content-Type": "application/json"}))
    i = got["b"]["variables"]["input"]; assert i["text"] and i["assets"]

@test
def editpost_refuses_empty():
    try: S.edit_post_input({"payload": {}}, {"post_id": "X"}, "addToQueue"); raise AssertionError("no error")
    except ValueError: pass

def seed_home(k):
    now = time.time()
    for h in ("elonmusk", "randomviral", "friend1", "watched1"):
        k.execute("INSERT INTO batch_pool(url, handle, text, age, posted_at, added, source) VALUES(?,?,?,?,?,?,?)",
                  (f"https://x.com/{h}/status/1{len(h)}", h, "t", "1h", now - 600, now, "following"))
    return now

@test
def home_feed_strangers_dropped():
    k = clean(); now = seed_home(k)
    k.execute("INSERT INTO watchlist(handle, added, by_) VALUES('watched1', 0, 'x')"); S.kv_set(k, "x_follows", json.dumps(["friend1"]))
    hs = {p["handle"] for p in S._fresh_pool(k, now, set())}
    assert hs == {"watched1", "friend1"}, hs
    assert S.filter_reply_candidates(k, [{"handle": "@elonmusk"}, {"handle": "friend1"}]) == [{"handle": "friend1"}]

@test
def empty_allowed_proposes_nothing_and_asks():
    k = clean(); now = seed_home(k)
    assert S._fresh_pool(k, now, set()) == []
    assert S.x_targets_onboarding(k, dry=False) is False
    assert "watch @handle" in k.execute("SELECT text FROM outbox").fetchone()[0]
    S.x_targets_onboarding(k, dry=False); assert k.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1  # asked once a day

@test
def audit_reconcile_backfills_once():
    k = clean(); t = time.time() - 3600
    k.execute("INSERT INTO engage_queue(slack_ts, action, url, handle, text, approver, approved_at, approved_day, state, done_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
              ("9.9", "reply", "https://x.com/a/status/1", "a", "hi", "U", t, "2026-10-11", "done", t))
    S.audit_ev(k, "limits", "U", "", "shown", "")
    add = S.audit_reconcile(k); assert len(add) == 1 and "backfilled" in add[0][4]
    assert S.audit_reconcile(k) == []
    S.audit_ev(k, "engage_run", "worker", "9.8", "done", "x")
    assert any("\t9.8\t" in l for l in open(f"{T}/audit.tsv"))

for n, ok, e in res: print(("PASS " if ok else "FAIL ") + n + ("" if ok else "\n" + e))
print(f"{sum(o for _, o, _ in res)}/{len(res)} passed"); sys.exit(0 if all(o for _, o, _ in res) else 1)
