"""Offline tests for selfheal.py: backoff, retry allowlist, never retrying publish, alert writing, hard limits.
Usage: python3 tests/test_selfheal.py"""
import json, os, sys, tempfile, unittest
T = tempfile.mkdtemp(prefix="selfheal-")
os.environ.update(SELFHEAL_STATE_DIR=T, SELFHEAL_ALERT_DIR=f"{T}/alerts", SELFHEAL_STATE_FILE=f"{T}/state.json",
                  SELFHEAL_AUDIT_TSV=f"{T}/audit.tsv", SELFHEAL_RETRY_ALLOWLIST="Collector: read stats|Client bot: read receipt (reusable)|Tool: Buffer post (x)")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import selfheal as S

class Backoff(unittest.TestCase):
    def test_three_tries_per_hour_exponential(self):
        st, t = {"tries": {}}, 1_000_000.0
        self.assertTrue(S.can_try(st, "n8n", t)); S.record_try(st, "n8n", t)
        self.assertFalse(S.can_try(st, "n8n", t + 299))      # 5 min gap
        self.assertTrue(S.can_try(st, "n8n", t + 300)); S.record_try(st, "n8n", t + 300)
        self.assertFalse(S.can_try(st, "n8n", t + 300 + 599))  # 10 min gap
        self.assertTrue(S.can_try(st, "n8n", t + 900)); S.record_try(st, "n8n", t + 900)
        self.assertFalse(S.can_try(st, "n8n", t + 3000))       # 3 used this hour
        self.assertTrue(S.exhausted(st, "n8n", t + 3000))
        self.assertTrue(S.can_try(st, "n8n", t + 3601 + 900))  # hour rolled over
        self.assertTrue(S.can_try(st, "other", t))              # per service

class Retry(unittest.TestCase):
    def test_allowlist(self):
        self.assertTrue(S.may_retry("Collector: read stats"))
        self.assertTrue(S.may_retry("Client bot: read receipt (reusable)"))
        self.assertFalse(S.may_retry("Some random workflow"))
    def test_never_publish_even_if_allowlisted(self):
        self.assertFalse(S.may_retry("Tool: Buffer post (x)"))
        for n in ["Client1 bot: actions (approvals, commands, notify)", "Tool: Propose post (client1)", "Client1 engage: drafter",
                  "Client0 bot: Slack poller (1 min)", "Reply sender"]:
            self.assertFalse(S.may_retry(n), n)
    def test_retry_once_only_and_not_publish(self):
        import shutil; shutil.rmtree(f"{T}/alerts", ignore_errors=True)   # no open platform warning for this test
        st = {"retried": []}; calls = []
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        def api(m, p):
            calls.append((m, p))
            if p.startswith("/workflows"): return {"data": [{"id": "a", "name": "Collector: read stats"}, {"id": "b", "name": "Tool: Buffer post (x)"}]}
            if p.startswith("/executions?"): return {"data": [{"id": 1, "workflowId": "a", "startedAt": now}, {"id": 2, "workflowId": "b", "startedAt": now}]}
            return {}
        out = []; S.retry_failed(st, out, api=api); S.retry_failed(st, out, api=api)
        posts = [p for m, p in calls if m == "POST"]
        self.assertEqual(posts, ["/executions/1/retry"])

class Alerts(unittest.TestCase):
    def test_alert_json_written_once_per_hour(self):
        p = S.alert("platform_warning", "client0", "captcha on x.com")
        d = json.load(open(p)); self.assertEqual(d["kind"], "platform_warning"); self.assertIn("captcha", d["detail"])
        self.assertEqual(S.alert("platform_warning", "client0", "again"), p)
        self.assertEqual(len(os.listdir(f"{T}/alerts")), 1)
        self.assertIn("\talert\t", open(f"{T}/audit.tsv").read())
    def test_warning_blocks_retries(self):
        S.alert("platform_warning", "clientX", "checkpoint")
        out = []; S.retry_failed({"retried": []}, out, api=lambda m, p: (_ for _ in ()).throw(AssertionError("api called")))
        self.assertIn("skipped", out[0])

class HardLimits(unittest.TestCase):
    def test_never_edits_own_code_guardrails_caps(self):
        for p in [S.SELF, f"{T}/guardrails.yaml", f"{T}/caps.md", os.path.expanduser("~/agency/clients/client1/guardrails.yaml"), "/etc/passwd"]:
            with self.assertRaises(S.Forbidden, msg=p): S.safe_write(p, "x")
    def test_never_signs_in(self):
        with self.assertRaises(S.Forbidden): S.run([os.path.expanduser("~/agency/bin/signin-link.sh"), "start"])
        src = open(S.SELF).read()
        self.assertNotIn("signin-link.sh", src.replace('"signin" in', ""))
    def test_chrome_plan_never_kills_cdp_holder(self):
        ps = {10: (1, "bwrap --unshare-pid x"), 11: (10, "/usr/bin/google-chrome --remote-debugging-port=9350 --user-data-dir=/p/c1"),
              20: (1, "bwrap --unshare-pid y"), 21: (20, "/usr/bin/google-chrome --remote-debugging-port=9350 --user-data-dir=/p/c1"),
              30: (999, "/opt/google/chrome/chrome --headless --user-data-dir=/tmp/playwright_x"),
              40: (1, "/usr/bin/google-chrome --remote-debugging-port=9333 --user-data-dir=/p/signed")}
        ps.update({12: (11, "/opt/google/chrome/chrome --user-data-dir=/p/c1 --enable-crash-reporter"),   # holder's own child
                   9: (1, "bwrap --unshare-pid /usr/bin/google-chrome --user-data-dir=/p/c1"), 41: (40, "/usr/bin/google-chrome --user-data-dir=/p/signed --no-type")})
        ps[10] = (9, ps[10][1])
        plan = dict(S.chrome_plan(ps, {9350: 11, 9333: 40}))
        self.assertIn(20, plan); self.assertIn(30, plan)
        for keep in (9, 10, 11, 12, 40, 41): self.assertNotIn(keep, plan)

if __name__ == "__main__":
    unittest.main(verbosity=1)
