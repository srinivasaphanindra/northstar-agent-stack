// Real Slack bot test against a THROWAWAY Slack workspace (not run by CI; needs a token you provide).
//   SLACK_TEST_BOT_TOKEN=xoxb-... SLACK_TEST_CHANNEL=C0123ABCD npx tsx scripts/slack-live-check.ts          # read-only checks
//   ... npx tsx scripts/slack-live-check.ts --post                                                           # also posts 1 test message + reads it back

// Checks: auth.test, granted scopes (chat:write, channels:history), bot is in the channel, history readable.
const API = process.env.SLACK_API_URL || "https://slack.com/api";
const token = process.env.SLACK_TEST_BOT_TOKEN ?? "";
const channel = process.env.SLACK_TEST_CHANNEL ?? "";
const post = process.argv.includes("--post");
if (!/^xoxb-/.test(token) || !/^[CG][A-Z0-9]{6,}$/.test(channel)) {
  console.log("SKIP: set SLACK_TEST_BOT_TOKEN (xoxb-...) and SLACK_TEST_CHANNEL (C...) for a throwaway Slack workspace.");
  process.exit(0);
}
async function call(method: string, params: Record<string, string> = {}) {
  const r = await fetch(`${API}/${method}`, { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/x-www-form-urlencoded" }, body: new URLSearchParams(params), signal: AbortSignal.timeout(15000) });
  const j = (await r.json()) as Record<string, unknown> & { ok: boolean; error?: string };
  return { j, scopes: r.headers.get("x-oauth-scopes") ?? "" };
}
const results: [string, boolean, string][] = [];
const check = (name: string, ok: boolean, note = "") => { results.push([name, ok, note]); console.log(ok ? "PASS" : "FAIL", name, note); };
async function main() {
  const a = await call("auth.test");
  check("auth.test", a.j.ok, a.j.ok ? `team=${a.j.team} bot=${a.j.user}` : String(a.j.error));
  for (const s of ["chat:write", "channels:history"]) check(`scope ${s}`, a.scopes.split(",").includes(s), a.scopes);
  const info = await call("conversations.info", { channel });
  check("channel visible", info.j.ok, String(info.j.error ?? ""));
  check("bot is a member", !!(info.j.channel as { is_member?: boolean } | undefined)?.is_member, "invite the bot with /invite @bot");
  const hist = await call("conversations.history", { channel, limit: "1" });
  check("history readable", hist.j.ok, String(hist.j.error ?? ""));
  if (post) {
    const m = await call("chat.postMessage", { channel, text: "Northstar harness: test message (safe to delete)." });
    check("chat.postMessage", m.j.ok, String(m.j.error ?? ""));
    if (m.j.ok) {
      const back = await call("conversations.history", { channel, oldest: String(Number(m.j.ts) - 1), limit: "5" });
      check("message read back", !!(back.j.messages as { ts: string }[] | undefined)?.some((x) => x.ts === m.j.ts));
    }
  }
}
main().then(() => {
  const failed = results.filter((r) => !r[1]).length;
  console.log(`${results.length - failed}/${results.length} passed`);
  process.exit(failed ? 1 : 0);
});
