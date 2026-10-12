// Local fake of the Slack Web API methods the connector uses (auth.test, conversations.history|replies|info,
// chat.postMessage). Runs on 127.0.0.1 with a random port; point SLACK_API_URL at `url`. No network, no token needed.
import http from "node:http";
import type { AddressInfo } from "node:net";

export type FakeMsg = { ts: string; user?: string; bot_id?: string; text: string; thread_ts?: string };
export type FakeSlack = {
  url: string;
  token: string;
  channels: Map<string, FakeMsg[]>;
  posted: { channel: string; text: string; thread_ts?: string }[];
  calls: string[];
  say: (channel: string, user: string, text: string, thread_ts?: string) => FakeMsg;
  close: () => Promise<void>;
};

let tick = 0;
const nextTs = () => `${Math.floor(Date.now() / 1000) + 1}.${String(++tick).padStart(6, "0")}`;

export async function startFakeSlack(token = "xoxb-fake-harness-token"): Promise<FakeSlack> {
  const channels = new Map<string, FakeMsg[]>();
  const posted: FakeSlack["posted"] = [];
  const calls: string[] = [];
  const list = (c: string) => { if (!channels.has(c)) channels.set(c, []); return channels.get(c)!; };
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (d) => (body += d));
    req.on("end", () => {
      const method = (req.url ?? "").split("?")[0].replace(/^\//, "");
      calls.push(method);
      const p = new URLSearchParams(body);
      const send = (o: object) => { res.setHeader("Content-Type", "application/json"); res.setHeader("x-oauth-scopes", "chat:write,channels:history,channels:read"); res.end(JSON.stringify(o)); };
      if (req.headers.authorization !== `Bearer ${token}`) return send({ ok: false, error: "invalid_auth" });
      switch (method) {
        case "auth.test": return send({ ok: true, team: "Harness", user: "northstar_bot", user_id: "UBOT", bot_id: "BBOT" });
        case "conversations.info": return send({ ok: true, channel: { id: p.get("channel"), name: "harness", is_member: true } });
        case "conversations.history": {
          const oldest = p.get("oldest") ?? "0";
          const msgs = list(p.get("channel") ?? "").filter((m) => (!m.thread_ts || m.thread_ts === m.ts) && m.ts > oldest).reverse();
          return send({ ok: true, messages: msgs.slice(0, Number(p.get("limit") ?? 100)) });
        }
        case "conversations.replies": {
          const root = p.get("ts") ?? "", oldest = p.get("oldest") ?? "0";
          const msgs = list(p.get("channel") ?? "").filter((m) => (m.ts === root || m.thread_ts === root) && (m.ts === root || m.ts > oldest));
          return send({ ok: true, messages: msgs });
        }
        case "chat.postMessage": {
          const ch = p.get("channel") ?? "";
          const m: FakeMsg = { ts: nextTs(), bot_id: "BBOT", text: p.get("text") ?? "", thread_ts: p.get("thread_ts") ?? undefined };
          list(ch).push(m);
          posted.push({ channel: ch, text: m.text, thread_ts: m.thread_ts });
          return send({ ok: true, channel: ch, ts: m.ts });
        }
        default: return send({ ok: false, error: "unknown_method" });
      }
    });
  });
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
  const { port } = server.address() as AddressInfo;
  return {
    url: `http://127.0.0.1:${port}`, token, channels, posted, calls,
    say(channel, user, text, thread_ts) { const m: FakeMsg = { ts: nextTs(), user, text, thread_ts }; list(channel).push(m); return m; },
    close: () => new Promise((r) => server.close(() => r())),
  };
}
