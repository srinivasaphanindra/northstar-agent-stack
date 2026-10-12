"""Guardrails for the client bot (2026-10-12). Stdlib only. Imported by server.py.

Config: ~/agency/clients/<client>/guardrails.yaml (defaults -> tiers[plan] -> overrides), reloaded on change.
Everything here is deterministic: config, regex content check, classifier prompt + verdict parsing,
browser allow-list, secret detection, friendly 'explain + alternative' messages, audit/upsell logging.
"""
import json, os, re, time, difflib, urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
HOME = os.path.expanduser("~")

# ------------------------------------------------------------------ tiny YAML subset parser
def _scalar(s):
    s = s.strip()
    if s == "" or s in ("~", "null"):
        return None
    if s[0] in "\"'" and s[-1] == s[0] and len(s) >= 2:
        return s[1:-1]
    if s in ("true", "True", "yes"):
        return True
    if s in ("false", "False", "no"):
        return False
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        return [_scalar(x) for x in _split_flow(inner)] if inner else []
    if s.startswith("{") and s.endswith("}"):
        inner = s[1:-1].strip(); d = {}
        for part in (_split_flow(inner) if inner else []):
            k, _, v = part.partition(":")
            d[k.strip()] = _scalar(v)
        return d
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return s

def _split_flow(s):
    out, depth, cur, q = [], 0, "", None
    for ch in s:
        if q:
            cur += ch
            if ch == q: q = None
            continue
        if ch in "\"'": q = ch; cur += ch; continue
        if ch in "[{": depth += 1
        if ch in "]}": depth -= 1
        if ch == "," and depth == 0:
            out.append(cur); cur = ""; continue
        cur += ch
    if cur.strip(): out.append(cur)
    return out

def _strip_comment(line):
    q = None
    for i, ch in enumerate(line):
        if q:
            if ch == q: q = None
        elif ch in "\"'":
            q = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line

def load_yaml(text):
    lines = []
    for raw in text.splitlines():
        l = _strip_comment(raw).rstrip()
        if l.strip():
            lines.append((len(l) - len(l.lstrip(" ")), l.strip()))
    pos = 0
    def block(indent):
        nonlocal pos
        if pos < len(lines) and lines[pos][1].startswith("- "):
            arr = []
            while pos < len(lines) and lines[pos][0] == indent and lines[pos][1].startswith("- "):
                arr.append(_scalar(lines[pos][1][2:])); pos += 1
            return arr
        d = {}
        while pos < len(lines) and lines[pos][0] == indent:
            ind, l = lines[pos]
            k, _, v = l.partition(":")
            pos += 1
            if v.strip():
                d[k.strip()] = _scalar(v)
            elif pos < len(lines) and lines[pos][0] > indent:
                d[k.strip()] = block(lines[pos][0])
            else:
                d[k.strip()] = None
        return d
    return block(lines[0][0]) if lines else {}

def deep_merge(a, b):
    if not isinstance(a, dict) or not isinstance(b, dict):
        return b if b is not None else a
    out = dict(a)
    for k, v in b.items():
        out[k] = deep_merge(a.get(k), v) if isinstance(v, dict) and isinstance(a.get(k), dict) else v
    return out

BUILTIN = {  # used only if the yaml file is missing or broken (fail safe = approved defaults)
    "platforms": {"x": {"engage_per_day": 5, "posts_per_day": 3}, "linkedin": {"engage_per_day": 3, "posts_per_day": 2},
                  "facebook": {"engage_per_day": 5, "posts_per_day": 3}},
    "engagement": {"allowed_actions": ["reply", "like", "repost"], "min_gap_minutes": 7, "extra_gap_minutes": 8, "max_per_hour": 0,
                   "duplicate_similarity": 0.85, "duplicate_lookback_days": 30, "pause_on_warning": True},
    "content": {"regex_check": True, "llm_check": True, "fail_closed": True, "max_hashtags": 2, "allow_politics": False,
                "allow_religion": False, "numbers_must_be_in_notes": True, "check_model": "google/gemini-3.1-flash-lite",
                "post_duplicate_similarity": 0.9},
    "approvals": {"approvers": ["U0000000001"], "admins": ["U0000000001"], "auto_post_routine": False, "always_need_approval": ["reply", "comment", "dm", "sensitive"]},
    "spend": {"monthly_ai_budget_usd": 10, "browser_tasks_per_month": 60, "warn_pct": 80, "pause_nonessential_pct": 100,
              "hard_stop_pct": 150, "est_usd_per_m_input": 0.25, "est_usd_per_m_output": 1.5},
    "access": {"browser_domains": ["x.com", "twitter.com", "linkedin.com", "facebook.com", "buffer.com"], "client_website": "",
               "slack_can_delete_user_messages": False},
    "scope": {"in_scope": "social media and online presence", "notify_owner": True, "upsell_log": ""},
    "audit": {"log": "", "resume_all_by": ["U0000000001"]},
    "ramp": {"enabled": False},
    "batches": {"enabled": False, "live": False, "start_at": "", "active_hours": ["09:00", "21:00"], "every_minutes": 60, "jitter_minutes": 10,
                "items_min": 2, "items_max": 4, "max_posts_per_batch": 1, "skip_after_ignored": 2, "max_still_there_per_day": 1,
                "item_ttl_hours": 6, "x_reads_per_day": 1, "read_targets": False, "post_platforms": ["x", "linkedin", "facebook"]},
    "manual_budget": {},
    "coach": {"enabled": False, "platforms": ["x", "linkedin", "facebook"], "client_users": ["U0000000001"], "handles": {},
              "creators": [], "topics": ["AI for small businesses", "founders in India", "social media marketing"],
              "list_times": ["09:00", "18:30"], "tasks_per_list": {"morning": 8, "evening": 6}, "quiet_hours": ["22:00", "08:00"],
              "max_nudges_per_day": 3, "max_celebrations_per_day": 1, "remind_before_minutes": 45,
              "windows_minutes": {"reply_trend": 180, "creator_comment": 60, "quote": 360, "join_thread": 120, "answer_own": 360,
                                  "li_comment": 120, "fb_comment": 180},
              "low_completion_pct": 50, "low_completion_days": 2, "streak_days": 3, "streak_pct": 70, "win_follower_jump_pct": 3,
              "verify_platforms": ["x"], "verify_per_day": 3},
}

def effective(raw, plan=None):
    """defaults -> tiers[plan] -> overrides (deep merge). plan=None uses the file's plan."""
    eff = deep_merge(BUILTIN, raw.get("defaults") or {})
    plan = str(plan or raw.get("plan") or "trial")
    tier = (raw.get("tiers") or {}).get(plan) or {}
    eff = deep_merge(eff, tier)
    eff = deep_merge(eff, raw.get("overrides") or {})
    eff["plan"], eff["client"] = plan, raw.get("client") or "example-client"
    eff["plan_label"] = str(tier.get("label") or plan.capitalize())
    return eff

class Config:
    def __init__(self, path):
        self.path, self.mtime, self.data, self.error, self.raw = path, None, None, "", {}
    def get(self):
        try:
            m = os.path.getmtime(self.path)
            if m != self.mtime:
                raw = load_yaml(open(self.path).read())
                eff = effective(raw)
                self.raw, self.data, self.mtime, self.error = raw, eff, m, ""
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            if self.data is None:
                self.data = dict(BUILTIN, plan="builtin", client="example-client", plan_label="Built-in defaults")
        return self.data
    def for_plan(self, plan):
        """Effective config as if the file said plan: <plan> (dry runs, previews). Falls back to the current config."""
        self.get()
        try:
            return effective(self.raw, plan) if self.raw else self.data
        except Exception:
            return self.data
    def plans(self):
        self.get()
        return ["trial"] + list((self.raw.get("tiers") or {}).keys())

def expand(p):
    return os.path.expanduser(p) if p else p

# ------------------------------------------------------------------ platform helpers
PLATFORM_NAMES = {"x": "X", "linkedin": "LinkedIn", "facebook": "Facebook"}

def platform_of(service_or_url):
    s = str(service_or_url or "").lower()
    if s in ("x", "twitter") or re.search(r"(^|//|\.)(x|twitter)\.com", s):
        return "x"
    if s in ("linkedin", "li") or "linkedin.com" in s:
        return "linkedin"
    if s in ("facebook", "fb") or re.search(r"(facebook|fb)\.com", s):
        return "facebook"
    return s or "x"

# ------------------------------------------------------------------ browser allow-list
def allowed_domains(cfg):
    doms = [str(d).lower().strip() for d in (cfg["access"].get("browser_domains") or []) if d]
    site = str(cfg["access"].get("client_website") or "").lower().strip()
    if site:
        site = re.sub(r"^https?://", "", site).split("/")[0]
        doms.append(site[4:] if site.startswith("www.") else site)
    return sorted(set(doms))

URL_RE = re.compile(r"(?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?::\d+)?(?:/[^\s'\"<>|)]*)?", re.I)

def host_of(url):
    u = url if re.match(r"^[a-z]+://", url, re.I) else "https://" + url
    try:
        return (urllib.parse.urlparse(u).hostname or "").lower()
    except Exception:
        return ""

def domain_ok(host, doms):
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in doms)

def check_task_urls(text, cfg):
    doms = allowed_domains(cfg)
    bad = []
    for m in URL_RE.finditer(text or ""):
        tok = m.group(0)
        # skip things that are clearly not hosts (e.g. 'notes.md', 'e.g.')
        h = host_of(tok)
        if not h or not re.search(r"\.(com|net|org|in|io|co|ai|app|dev|me|info|biz|us|uk|gov|edu)$", h) and "://" not in tok:
            continue
        if not domain_ok(h, doms):
            bad.append(h)
    return sorted(set(bad)), doms

# ------------------------------------------------------------------ secrets
SECRET_RE = re.compile(
    r"(\b(my |the |our )?(pass(word|wd|code)?|pwd|login pin|otp)\b\s*(is|=|:|-)\s*\S+|"
    r"xox[abpers]-[A-Za-z0-9-]{8,}|\bsk-[A-Za-z0-9_-]{16,}|\bAKIA[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{20,}|"
    r"\b(api[_ -]?key|secret|token)\b\s*(is|=|:)\s*\S{8,})", re.I)

def looks_secret(text):
    return bool(SECRET_RE.search(text or ""))

def scrub(text):
    return SECRET_RE.sub("[removed: looked like a password/key]", text or "")

# ------------------------------------------------------------------ content: regex pre-check
R = lambda p: re.compile(p, re.I)
BLOCK_PATTERNS = [
    ("fake_review", R(r"\b(testimonials?|5[- ]?stars?|five[- ]stars?|rated \d(\.\d)? ?(/|out of) ?5|our (happy|satisfied|amazing) (customers|clients) (say|said|love)|"
                      r"(review|feedback) from (a |one of )?(our )?(happy|satisfied) (customer|client))\b|★{3,}|\"[^\"]{12,}\"\s*[-–—~]\s*[A-Z][a-z]+,? (a |our )?(happy|satisfied|verified)?\s*(customer|client|user)")),
    ("false_claim", R(r"\b(guaranteed?|100% (results|success|guaranteed|proven)|risk[- ]free|overnight success|get rich|never fails|no\.? ?1 in (india|the world))\b")),
    ("medical", R(r"\b(cures?|cured|heals? your|treatment for|diagnos\w+|prescri\w+|dosage|lose \d+ ?(kg|kgs|pounds|lbs)|stop taking your)\b")),
    ("legal", R(r"\b(legal advice|you (can|should) sue|not legally liable|legal loophole|avoid (the )?(law|court))\b")),
    ("financial", R(r"\b(financial advice|guaranteed returns?|returns? of \d+ ?%|buy (this |these )?(stock|shares|crypto|coins?)|stock tips?|invest (now|today) in|tax (hacks?|loopholes?))\b")),
    ("hate", R(r"\b(kill (all|them)|go back to your country|subhuman|vermin|exterminate (them|all)|deserve to die)\b")),
]
HINT_PATTERNS = [
    ("politics", R(r"\b(bjp|congress party|aam aadmi|modi|rahul gandhi|election|elections|vote for|left[- ]wing|right[- ]wing|trump|biden|democrats?|republicans?|parliament|government is)\b")),
    ("religion", R(r"\b(hindu|hindus|muslims?|islam|christian|christians|jesus|allah|bhagwan|mosque|church|religio\w+|god bless)\b")),
    ("voice", R(r"(\bgame[- ]?changer\b|\bleverage\b|\bunlock\b|great point|\babsolutely\b|this resonates|—)")),
]
NUM_RE = re.compile(r"([₹$]\s?\d[\d,]*(\.\d+)?\s?(k|l|lakh|lakhs|crore|cr|m)?\b|\b\d[\d,]*(\.\d+)?\s?(%|x\b|k\+?(?=\W|$)|(lakh|crore|followers|customers|clients|leads|sales|users|downloads|reviews|stars|orders|bookings|patients)\b))", re.I)

def _norm_num(s):
    return re.sub(r"[\s,]", "", s.lower())

def content_regex(text, kind, notes, cfg):
    """Returns (blocks, hints). Each item: {cat, match}. kind: post | reply."""
    c = cfg["content"]; blocks, hints = [], []
    t = text or ""
    for cat, rx in BLOCK_PATTERNS:
        m = rx.search(t)
        if m: blocks.append({"cat": cat, "match": m.group(0)[:60]})
    for cat, rx in HINT_PATTERNS:
        m = rx.search(t)
        if not m: continue
        if cat == "politics" and c.get("allow_politics"): continue
        if cat == "religion" and c.get("allow_religion"): continue
        hints.append({"cat": cat, "match": m.group(0)[:60]})
    tags = re.findall(r"(?<![\w&])#\w+", t)
    lim = 0 if kind == "reply" else int(c.get("max_hashtags", 2))
    if len(tags) > lim:
        blocks.append({"cat": "hashtag_spam", "match": " ".join(tags[:6])})
    if c.get("numbers_must_be_in_notes", True):
        nn = _norm_num(notes or "")
        for m in NUM_RE.finditer(t):
            if _norm_num(m.group(0)) not in nn:
                blocks.append({"cat": "made_up_number", "match": m.group(0).strip()[:40]}); break
    return blocks, hints

# ------------------------------------------------------------------ content: LLM classifier
CHECK_TAG = "CONTENT SAFETY CHECK v1"

def voice_section(notes):
    m = re.search(r"## Voice\n(.*?)(\n## |\Z)", notes or "", re.S)
    return (m.group(1).strip() if m else "(no voice notes)")[:1500]

def classifier_prompt(text, kind, platform, notes, hints, cfg):
    c = cfg["content"]
    return (f"{CHECK_TAG}. You check ONE social media {kind} for {PLATFORM_NAMES.get(platform, platform)} right before it is published "
            f"for a small-business client. Be strict about safety, relaxed about style.\n"
            f"Block it if it contains any of: false or unprovable claims or guarantees; fake reviews or testimonials; "
            f"numbers or results that are not in the client facts below; hate, harassment or insults; medical, legal or financial advice; "
            f"{'' if c.get('allow_politics') else 'politics; '}{'' if c.get('allow_religion') else 'religion; '}"
            f"or a tone clearly at odds with the voice notes (hype, slogans, corporate buzzwords). Ordinary opinions, tips and questions are fine.\n"
            f"Client facts and voice notes:\n<<<\n{(notes or '')[:3500]}\n>>>\n"
            f"Cheap pre-check hints (may be false alarms): {json.dumps(hints)}\n"
            f"Text to check:\n<<<\n{text}\n>>>\n"
            f'Return ONLY JSON: {{"ok": true|false, "category": "ok|false_claim|fake_review|made_up_number|hate|medical|legal|financial|politics|religion|off_voice|other", '
            f'"reason": "max 20 plain words", "fix": "max 20 words: how to make it safe"}}')

def parse_verdict(raw):
    m = re.search(r"\{[\s\S]*\}", str(raw or ""))
    if not m:
        return None
    try:
        v = json.loads(m.group(0))
    except Exception:
        return None
    if not isinstance(v, dict) or "ok" not in v:
        return None
    ok = v.get("ok") in (True, "true", "True")
    return {"ok": ok, "category": str(v.get("category") or ("ok" if ok else "other"))[:30],
            "reason": str(v.get("reason") or "")[:200], "fix": str(v.get("fix") or "")[:200]}

# ------------------------------------------------------------------ duplicates
def norm_text(s):
    return re.sub(r"[^a-z0-9 ]+", "", re.sub(r"\s+", " ", (s or "").lower())).strip()

def most_similar(text, previous):
    a, best = norm_text(text), (0.0, "")
    for p in previous:
        r = difflib.SequenceMatcher(None, a, norm_text(p)).ratio()
        if r > best[0]:
            best = (r, p)
    return best

# ------------------------------------------------------------------ friendly messages (explain + alternative)
def msg(kind, **k):
    P = PLATFORM_NAMES.get(k.get("platform"), k.get("platform") or "")
    k["spent"], k["budget"] = float(k.get("spent") or 0), float(k.get("budget") or 0)
    M = {
     "cap_engage": f"That would go past today's safe limit for {P}: {k.get('used')}/{k.get('cap')} engagement actions are already used. "
                   f"Going faster than that is how accounts get flagged or restricted (and X already flagged one of the owner's accounts this month). "
                   f"Safe alternative: I'll keep the best ideas for tomorrow's round, or swap this for a stronger one then.",
     "cap_engage_ask": f"Doing {k.get('asked')} on {P} in one day would look like a bot and could get the account restricted. "
                       f"Today's safe limit is {k.get('cap')} ({k.get('left')} left). Safe alternative: I'll suggest {k.get('left')} good, specific ones for your ✅.",
     "cap_post": f"That would be more than {k.get('cap')} posts on {P} today ({k.get('used')} already approved). Posting too often looks spammy and reaches fewer people. "
                 f"Safe alternative: I've kept it as a draft; approve it again tomorrow, or I can hold it for tomorrow's first slot.",
     "action_not_allowed": f"I don't {k.get('action')} on anyone's behalf: automated follows, unfollows, DMs and likes are the fastest way to get an account banned (X's April 2026 automation rules forbid automated likes). "
                           f"Safe alternative: I can draft a thoughtful reply for your ✅, or you can like the post yourself in the app.",
     "like_manual": "On this plan I don't like posts automatically: X's April 2026 automation rules forbid automated likes, and likes from you count more anyway. "
                    "Safe alternative: like it yourself in the app (on Viral it's also on your action list), or I can draft a short reply for your ✅ instead.",
     "duplicate_post": f"This post is almost the same as one already published on {P}. Platforms treat repeated text as spam and show it to fewer people. "
                       f"Safe alternative: say 'rewrite it with a new angle' and I'll make a fresh version, or I can turn it into a follow-up that builds on the first one.",
     "post_gap": f"Another {P} post went out less than {k.get('gap')} minutes ago, so I put this one in Buffer's queue instead of posting it right now. "
                 f"Two posts close together split the attention and both reach fewer people.",
     "duplicate": "This reply is almost the same as one we already posted. Platforms treat copy-paste comments as spam. "
                  "Safe alternative: tell me to rewrite it and I'll mention something specific from their post.",
     "paused": f"Everything is paused right now ({k.get('reason','')}). Nothing will be published, liked or commented until the owner says 'resume everything'. "
               f"Your draft is kept, so you can approve it after that.",
     "domain": f"I can only open the client's own platforms: {', '.join(k.get('doms') or [])}. {', '.join(k.get('bad') or [])} isn't on that list, "
               f"so I didn't open it (keeps your accounts and data safe). Safe alternative: paste the text you need here, or ask the owner to add the site to the allow-list.",
     "browser_cap": f"This month's browser task allowance is used up ({k.get('used')}/{k.get('cap')}). Safe alternative: I can still draft and answer from what I already know; "
                    f"the owner can raise the limit or move you to a bigger plan.",
     "spend_pause": f"This month's AI budget is used up (${k.get('spent'):.2f} of ${k.get('budget'):.2f}), so I've paused the extras (engagement ideas and research browsing). "
                    f"Chat and approvals still work. Safe alternative: wait for the 1st, or the owner can upgrade the plan / raise the budget.",
     "check_failed": "I couldn't run the pre-publish safety check just now, so I didn't publish anything (better safe than sorry). Your draft is kept. Try ✅ again in a minute.",
     "fake_review": "I can't post made-up reviews or testimonials: it misleads customers and breaks platform and consumer rules. "
                    "Safe alternative: a post inviting real customers to share their experience, or share a real review (pasted here with their OK).",
     "false_claim": "That promises a result nobody can guarantee, which can get posts reported and erodes trust. Safe alternative: say what you actually do and what people can expect, without the guarantee.",
     "made_up_number": f"It has a number I can't find in the client notes ({k.get('match','')}). I don't post numbers we can't back up. "
                       f"Safe alternative: post it without the number, or tell me the real figure with 'remember: ...' and I'll use it.",
     "medical": "That reads like medical advice, which we don't post. Safe alternative: a general, friendly tip plus 'talk to your doctor' for anything personal.",
     "legal": "That reads like legal advice, which we don't post. Safe alternative: a general explainer and a nudge to check with a lawyer.",
     "financial": "That reads like financial or investment advice, which we don't post. Safe alternative: share the general idea or your own experience, and suggest a professional for decisions.",
     "politics": "That touches politics, which this client hasn't opted into. Safe alternative: keep it about the work and the customers; the client can opt in later if they want.",
     "religion": "That touches religion, which this client hasn't opted into. Safe alternative: keep it about the work and the customers; the client can opt in later if they want.",
     "hate": "I won't post anything that attacks or insults people or groups. Safe alternative: if something annoyed you, I can help write a calm, factual post about it.",
     "hashtag_spam": f"Too many hashtags ({k.get('match','')}). Hashtag stacks look like spam and the notes say to avoid them. Safe alternative: I'll keep at most {k.get('max', 2)} relevant ones, or none.",
     "off_voice": "It doesn't sound like the client (see the voice notes: plain, short, no hype). Safe alternative: say 'rewrite in my voice' and I'll redo it.",
     "other": "The safety check flagged it. Safe alternative: tell me what you want to say and I'll redo it in a safer way.",
     "secret": "Please don't share passwords or keys here, not even with me. I haven't saved it. "
               "It's still visible in this channel though: please delete your message (hover, then ⋯ > Delete message) and change that password to be safe. "
               "To connect an account, say 'sign in link': the owner will send a one-time link and you log in yourself in a private browser window. I never see the password.",
     "secret_deleted": "I deleted that message because it looked like a password or key. Please change that password to be safe. "
                       "To connect an account, say 'sign in link': you log in yourself in a private browser window, and I never see the password.",
     "out_of_scope": "That's outside what I do here: I look after your social media and online presence. I've passed it to the owner so he can help with it.",
    }
    return M.get(kind, M["other"])

def content_message(blocks_or_verdict, cfg):
    """First issue -> friendly sentence."""
    if isinstance(blocks_or_verdict, dict):
        v = blocks_or_verdict
        cat = v.get("category") or "other"
        base = msg(cat if cat in ("fake_review", "false_claim", "made_up_number", "medical", "legal", "financial", "politics", "religion", "hate", "off_voice") else "other",
                   max=cfg["content"].get("max_hashtags", 2))
        extra = (f" (Checker: {v['reason']}" + (f"; fix: {v['fix']}" if v.get("fix") else "") + ")") if v.get("reason") else ""
        return base + extra
    b = blocks_or_verdict[0]
    return msg(b["cat"], match=b.get("match"), max=cfg["content"].get("max_hashtags", 2))

# ------------------------------------------------------------------ logs
def now_ist():
    return datetime.now(IST)

def audit(path, event, detail="", by="", ref="", outcome=""):
    if not path:
        return
    p = expand(path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    new = not os.path.exists(p)
    with open(p, "a") as f:
        if new:
            f.write("ts_ist\tevent\tby\tref\toutcome\tdetail\n")
        clean = lambda s: re.sub(r"[\t\r\n]+", " ", scrub(str(s)))[:400]
        f.write("\t".join([now_ist().strftime("%Y-%m-%d %H:%M:%S"), clean(event), clean(by), clean(ref), clean(outcome), clean(detail)]) + "\n")

def upsell(path, ask, summary, by):
    p = expand(path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if not os.path.exists(p):
        open(p, "w").write("# Example Client: out-of-scope asks (possible upsells)\n\nLogged by the client bot. Newest at the bottom.\n\n"
                           "| When (IST) | Who | Ask | Summary |\n|---|---|---|---|\n")
    cell = lambda s: re.sub(r"[|\r\n]+", " ", scrub(str(s))).strip()[:300]
    with open(p, "a") as f:
        f.write(f"| {now_ist():%Y-%m-%d %H:%M} | {cell(by)} | {cell(ask)} | {cell(summary)} |\n")
