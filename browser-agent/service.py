"""browser-agent: tiny local HTTP wrapper around browser-use (client agent tool).

Connects to an EXISTING Chrome over CDP (default http://127.0.0.1:9350 = example-client lane), never launches one.
LLM: OpenRouter via browser-use's OpenAI-compatible ChatOpenAI (base_url https://openrouter.ai/api/v1).

Endpoints (127.0.0.1:8770):
  GET  /health
  POST /task {task, max_steps=25, allow_write=false}
       allow_write=true is refused unless header "X-Approved: yes".
Every task is logged to ~/agency/logs/browser-agent.tsv. Secrets are never logged.
"""
import asyncio, json, os, re, time, uuid, urllib.request
from datetime import datetime
from importlib.metadata import version as pkg_version
from typing import Optional

os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
os.environ.setdefault("BROWSER_USE_CLOUD_SYNC", "false")
os.environ.setdefault("BROWSER_USE_LOGGING_LEVEL", "info")

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from browser_use import Agent, Browser, ChatOpenAI, Tools

CDP_URL = os.environ.get("BROWSER_AGENT_CDP_URL", "http://127.0.0.1:9350")
MODEL = os.environ.get("BROWSER_AGENT_MODEL", "google/gemini-3.1-flash-lite")
# 2026-10-12 guardrails: LLM calls go through the state-helper metering proxy when BROWSER_AGENT_BASE_URL is set
BASE_URL = os.environ.get("BROWSER_AGENT_BASE_URL", "https://openrouter.ai/api/v1")
# Domain allow-list (client's own platforms + website), fetched from state-helper; built-in list if it can't be fetched.
ALLOWLIST_URL = os.environ.get("BROWSER_AGENT_ALLOWLIST_URL", "http://127.0.0.1:8771/guard/allowlist")
DEFAULT_ALLOWED = ["x.com", "twitter.com", "linkedin.com", "facebook.com", "buffer.com"]
_ALLOW_CACHE = {"t": 0.0, "domains": DEFAULT_ALLOWED}
LOG_TSV = os.path.expanduser(os.environ.get("BROWSER_AGENT_LOG_TSV", "~/agency/logs/browser-agent.tsv"))
MAX_STEPS_CAP = int(os.environ.get("BROWSER_AGENT_MAX_STEPS_CAP", "60"))
TASK_TIMEOUT_S = int(os.environ.get("BROWSER_AGENT_TASK_TIMEOUT", "600"))
BU_VERSION = pkg_version("browser-use")

READ_ONLY_RULES = """
HARD RULES (READ-ONLY MODE — these override the task):
- You are strictly READ-ONLY. NEVER post, publish, reply, comment, like, react, repost/retweet, share, follow, unfollow,
  subscribe, connect, send or accept invitations, send messages/DMs, vote, bookmark, edit profiles, change settings,
  submit forms, upload files or buy anything. Do not click buttons with such labels. Do not use keyboard shortcuts
  (e.g. on X: l, r, t, n, f, m) and do not type into any field except a site search box.
- If you reach a login / sign-in page or wall, a captcha, a checkpoint, an identity check, an 'unusual activity',
  'suspicious', 'restricted', 'locked' or any security warning: STOP IMMEDIATELY. Do NOT try to sign in, dismiss or
  solve it. Call done with success=false and text starting with "STOPPED:" describing exactly what you saw.
  (A small dismissible 'sign in' banner on an otherwise readable public page is fine: just read what is visible.)
- Only read what is visible; report it accurately. If something is not visible, say so.
"""
WRITE_RULES = """
SAFETY RULES (APPROVED WRITE MODE):
- Perform ONLY the exact write action(s) the task explicitly describes, once. No extra likes/follows/messages.
- If you reach a login page, captcha, checkpoint, 'unusual activity' or any security warning: STOP IMMEDIATELY,
  do not try to solve or bypass it, call done with success=false and text starting with "STOPPED:".
"""

# --- page danger detection (both modes) -------------------------------------------------------
DANGER_URL = re.compile(r"(checkpoint|captcha|challenge|/account/access|/uas/|authwall|/i/flow/login|"
                        r"/login|/signin|/sign-in|/two_step|/2fa|/suspended|/locked|recaptcha|arkose)", re.I)
DANGER_TEXT = re.compile(r"(unusual activity|suspicious activity|verify (that )?you('| a)re (a )?human|"
                         r"are you a robot|i'?m not a robot|complete the captcha|captcha|security check|"
                         r"security checkpoint|we('| ha)ve (temporarily )?(restricted|locked|limited)|"
                         r"your account (has been|is) (temporarily )?(locked|restricted|suspended|limited)|"
                         r"confirm (it'?s|that it'?s) you|help us confirm|automated (behaviou?r|requests))", re.I)
WRITE_LABEL = re.compile(r"^\W*(post|reply|comment|like|unlike|react|repost|retweet|quote|share|send|follow|"
                         r"unfollow|connect|message|subscribe|invite|accept|submit|publish|tweet|add a comment|"
                         r"start a post|write a|what'?s happening|what'?s on your mind|bookmark|vote|endorse|"
                         r"join|request|add friend|confirm|save)\b", re.I)
SAFE_KEYS = {"escape", "pagedown", "pageup", "arrowdown", "arrowup", "home", "end", "space", "tab"}


class TaskReq(BaseModel):
    task: str = Field(min_length=3, max_length=4000)
    max_steps: int = Field(default=25, ge=1)
    allow_write: bool = False
    close_tabs: bool = True


app = FastAPI(title="browser-agent", version="1.0")
RUN_LOCK = asyncio.Lock()   # one agent at a time on the shared Chrome lane


def ts():
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def clean(s, n=None):
    s = re.sub(r"[\t\r\n]+", " ", "" if s is None else str(s))
    return s[:n] if n else s


def log_task(row: dict):
    os.makedirs(os.path.dirname(LOG_TSV), exist_ok=True)
    cols = ["ts", "id", "status", "allow_write", "approved", "model", "steps", "max_steps", "secs",
            "task", "urls", "result"]
    new = not os.path.exists(LOG_TSV)
    with open(LOG_TSV, "a") as f:
        if new:
            f.write("\t".join(cols) + "\n")
        f.write("\t".join(clean(row.get(c, ""), 600 if c in ("task", "result") else 1000) for c in cols) + "\n")


def cdp_json(path, method="GET"):
    req = urllib.request.Request(CDP_URL.rstrip("/") + path, method=method)
    with urllib.request.urlopen(req, timeout=4) as r:
        return json.loads(r.read() or b"null")


def page_targets():
    try:
        return {t["id"]: t for t in cdp_json("/json/list") if t.get("type") == "page"}
    except Exception:
        return {}


def node_label(node) -> str:
    if node is None:
        return ""
    parts = []
    try:
        if getattr(node, "ax_node", None) and node.ax_node.name:
            parts.append(node.ax_node.name)
        attrs = getattr(node, "attributes", None) or {}
        for k in ("aria-label", "data-testid", "title", "placeholder", "value", "name", "role", "type"):
            if attrs.get(k):
                parts.append(str(attrs[k]))
        parts.append(node.get_all_children_text(max_depth=3)[:200])
    except Exception:
        pass
    return " | ".join(p for p in parts if p)


def looks_like_search(node) -> bool:
    lab = node_label(node).lower()
    attrs = (getattr(node, "attributes", None) or {}) if node is not None else {}
    return ("search" in lab or attrs.get("type") == "search" or attrs.get("role") == "searchbox"
            or attrs.get("enterkeyhint") == "search")


@app.get("/health")
async def health():
    cdp = None
    try:
        v = cdp_json("/json/version")
        cdp = {"ok": True, "browser": v.get("Browser"), "tabs": len(page_targets())}
    except Exception as e:
        cdp = {"ok": False, "error": type(e).__name__}
    return {"status": "ok" if cdp.get("ok") else "degraded", "service": "browser-agent",
            "browser_use": BU_VERSION, "model": MODEL, "cdp_url": CDP_URL, "cdp": cdp,
            "openrouter_key": bool(os.environ.get("OPENROUTER_API_KEY")), "busy": RUN_LOCK.locked(),
            "time": ts()}


def allowed_domains():
    if not ALLOWLIST_URL:
        return DEFAULT_ALLOWED
    if time.time() - _ALLOW_CACHE["t"] < 60:
        return _ALLOW_CACHE["domains"]
    try:
        import urllib.request as _u
        with _u.urlopen(ALLOWLIST_URL, timeout=3) as r:
            d = json.loads(r.read()).get("domains") or DEFAULT_ALLOWED
        _ALLOW_CACHE.update(t=time.time(), domains=[str(x).lower() for x in d])
    except Exception:
        _ALLOW_CACHE.update(t=time.time(), domains=DEFAULT_ALLOWED)
    return _ALLOW_CACHE["domains"]


def host_allowed(url):
    """True for allow-listed hosts (and their subdomains) and for browser-internal pages."""
    u = (url or "").strip()
    if not u or u.startswith(("about:", "chrome:", "chrome-error:", "data:", "blob:", "devtools:")):
        return True
    try:
        from urllib.parse import urlparse
        h = (urlparse(u if "://" in u else "https://" + u).hostname or "").lower()
    except Exception:
        return False
    return any(h == d or h.endswith("." + d) for d in allowed_domains())


@app.post("/task")
async def task(req: TaskReq, x_approved: Optional[str] = Header(default=None)):
    tid = uuid.uuid4().hex[:10]
    approved = (x_approved or "").strip().lower() == "yes"
    base = {"ts": ts(), "id": tid, "allow_write": req.allow_write, "approved": approved, "model": MODEL,
            "max_steps": req.max_steps, "task": req.task}
    if req.allow_write and not approved:
        log_task({**base, "status": "refused", "result": "allow_write=true without X-Approved: yes"})
        return JSONResponse(status_code=403, content={"id": tid, "status": "refused",
                            "result_text": "allow_write=true requires header 'X-Approved: yes'",
                            "urls_visited": [], "steps": 0})
    if not os.environ.get("OPENROUTER_API_KEY"):
        log_task({**base, "status": "error", "result": "OPENROUTER_API_KEY missing"})
        return JSONResponse(status_code=500, content={"id": tid, "status": "error",
                            "result_text": "OPENROUTER_API_KEY not set", "urls_visited": [], "steps": 0})
    max_steps = min(req.max_steps, MAX_STEPS_CAP)
    bad = sorted({u for u in re.findall(r"https?://[^\s'\"<>)]+", req.task) if not host_allowed(u.rstrip(".,"))})
    if bad:
        msg = f"STOPPED: site not on the client's allow-list ({', '.join(bad)[:200]}). Allowed: {', '.join(allowed_domains())}."
        log_task({**base, "status": "refused", "result": msg})
        return JSONResponse(status_code=403, content={"id": tid, "status": "refused", "result_text": msg, "urls_visited": [], "steps": 0})
    async with RUN_LOCK:
        out = await run_agent(tid, req, max_steps)
    log_task({**base, "ts": ts(), "status": out["status"], "steps": out["steps"], "secs": out.pop("_secs", ""),
              "urls": " ".join(out["urls_visited"]), "result": out["result_text"]})
    return {"id": tid, "model": MODEL, **out}


async def run_agent(tid: str, req: TaskReq, max_steps: int) -> dict:
    t0 = time.time()
    guard = {"stop": False, "reason": "", "search_typed": False}
    before = page_targets()
    if not before and not cdp_alive():
        return {"status": "error", "result_text": f"Chrome CDP not reachable at {CDP_URL} (run client-chrome.sh)",
                "urls_visited": [], "steps": 0, "_secs": 0}

    llm = ChatOpenAI(model=MODEL, base_url=BASE_URL, api_key=os.environ["OPENROUTER_API_KEY"],
                     temperature=0.2, max_retries=2)
    browser = Browser(cdp_url=CDP_URL, is_local=False, keep_alive=True)
    exclude = [] if req.allow_write else ["upload_file", "evaluate"]
    tools = Tools(exclude_actions=exclude)

    def block(model_output, reason):
        # Mark the run as stopped; should_stop() fires right after this callback (before any action executes),
        # so the planned actions of this step are never run.
        guard["stop"] = True
        guard["reason"] = reason

    async def on_step(state, model_output, n):
        # 1) danger page check (both modes)
        url = getattr(state, "url", "") or ""
        title = getattr(state, "title", "") or ""
        try:
            text = state.dom_state.llm_representation()[:60000]
        except Exception:
            text = ""
        if url and not host_allowed(url):
            return block(model_output, f"left the client's allowed sites ({url[:120]}); stopping. Allowed: {', '.join(allowed_domains())}.")
        m = DANGER_URL.search(url)
        if m:
            return block(model_output, f"login/checkpoint/captcha URL reached ({url}); stopping as required.")
        m = DANGER_TEXT.search(title + "\n" + text)
        if m:
            return block(model_output, f"page shows a warning/captcha/checkpoint signal ('{m.group(0)}') at {url}.")
        if req.allow_write:
            return
        # 2) read-only action guard (before actions execute)
        smap = {}
        try:
            smap = state.dom_state.selector_map or {}
        except Exception:
            pass
        for a in (model_output.action or []):
            d = a.model_dump(exclude_unset=True, exclude_none=True)
            if not d:
                continue
            name, params = next(iter(d.items()))
            params = params or {}
            if name == "click":
                idx = params.get("index")
                if idx is None:
                    return block(model_output, "coordinate click blocked in read-only mode.")
                lab = node_label(smap.get(idx))
                if WRITE_LABEL.search(lab.split(" | ")[0] if lab else "") or re.search(
                        r"\b(like|repost|retweet|reply|follow|connect|send|post|comment|share|message)\b",
                        (getattr(smap.get(idx), "attributes", {}) or {}).get("data-testid", "") +
                        " " + (getattr(smap.get(idx), "attributes", {}) or {}).get("aria-label", ""), re.I):
                    return block(model_output, f"read-only mode: refused to click a write-type control ('{clean(lab, 80)}').")
            elif name in ("input", "input_text"):
                node = smap.get(params.get("index"))
                if not looks_like_search(node):
                    return block(model_output, f"read-only mode: refused to type into a non-search field ('{clean(node_label(node), 80)}').")
                guard["search_typed"] = True
            elif name == "send_keys":
                keys = str(params.get("keys", "")).strip().lower()
                if keys == "enter" and guard["search_typed"]:
                    guard["search_typed"] = False
                elif keys not in SAFE_KEYS:
                    return block(model_output, f"read-only mode: refused key press '{keys}'.")
            elif name in ("upload_file", "evaluate", "select_dropdown"):
                return block(model_output, f"read-only mode: action '{name}' not allowed.")

    async def should_stop():
        return guard["stop"]

    first_url = re.search(r"https?://[^\s'\"<>)]+", req.task)
    initial = [{"navigate": {"url": first_url.group(0).rstrip(".,"), "new_tab": True}}] if first_url \
        else [{"navigate": {"url": "about:blank", "new_tab": True}}]

    agent = Agent(
        task=req.task, llm=llm, browser=browser, tools=tools,
        extend_system_message=(WRITE_RULES if req.allow_write else READ_ONLY_RULES),
        register_new_step_callback=on_step, register_should_stop_callback=should_stop,
        initial_actions=initial, use_vision=True, use_judge=False, max_actions_per_step=4,
        max_failures=3, step_timeout=120, calculate_cost=False,
    )
    status, result_text, urls, steps = "error", "", [], 0
    try:
        hist = await asyncio.wait_for(agent.run(max_steps=max_steps), timeout=TASK_TIMEOUT_S)
        steps = hist.number_of_steps()
        urls = [u for u in dict.fromkeys(hist.urls()) if u and u != "about:blank"]
        result_text = hist.final_result() or ""
        if guard["stop"] or result_text.startswith("STOPPED"):
            status = "stopped"
            result_text = ("STOPPED: " + guard["reason"]) if guard["stop"] else result_text
        elif hist.is_done():
            status = "ok" if hist.is_successful() is not False else "failed"
        else:
            status = "incomplete"
            errs = [e for e in hist.errors() if e]
            result_text = result_text or (f"max_steps ({max_steps}) reached without finishing. " +
                                          (f"Last error: {clean(errs[-1], 300)}" if errs else ""))
    except asyncio.TimeoutError:
        status, result_text = "timeout", f"task exceeded {TASK_TIMEOUT_S}s"
    except Exception as e:
        status, result_text = "error", f"{type(e).__name__}: {clean(e, 400)}"
    finally:
        try:
            await browser.stop()   # disconnect only; Chrome keeps running (keep_alive + external CDP)
        except Exception:
            pass
        if req.close_tabs:
            after = page_targets()
            for t_id in set(after) - set(before):
                try:
                    cdp_json(f"/json/close/{t_id}")
                except Exception:
                    pass
    return {"status": status, "result_text": result_text, "urls_visited": urls, "steps": steps,
            "_secs": round(time.time() - t0, 1)}


def cdp_alive():
    try:
        cdp_json("/json/version")
        return True
    except Exception:
        return False
