import Link from "next/link";
import { config } from "@/core/config";
import { listBindings, createTelegramLink, connectSlackChannel, disconnectChannel } from "@/core/channels";
import { listIntegrations } from "@/core/integrations";
import { can } from "@/core/tenancy";
import { run, s } from "@/lib/act";
import { currentCtx } from "@/lib/session";
import { Flash } from "@/components/Flash";
import { SubmitButton } from "@/components/SubmitButton";

export const metadata = { title: "Chat channels" };
const P = "/app/settings/channels";

async function act(f: FormData) {
  "use server";
  await run(P, async () => {
    const ctx = await currentCtx();
    const op = s(f, "op");
    if (op === "tg-link") {
      const r = await createTelegramLink(ctx);
      return r.url ? `Open this on your phone to link Telegram (one-time): ${r.url}` : `Send this to your bot: /start ${r.code} (test the bot token first so we know its username)`;
    }
    if (op === "slack") { await connectSlackChannel(ctx, s(f, "channelId")); return "Slack channel connected. New messages are picked up within a minute."; }
    if (op === "off") { await disconnectChannel(ctx, s(f, "channel")); return "Disconnected."; }
  });
}

export default async function Channels({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const sp = await searchParams;
  const ctx = await currentCtx();
  const binds = await listBindings(ctx);
  const ints = await listIntegrations(ctx);
  const b = (c: string) => binds.find((x) => x.channel === c);
  const has = (p: string) => ints.some((i) => i.provider === p && i.hasSecret);
  const admin = can(ctx, "admin");
  const st = (c: string) => b(c)?.status ?? "off";
  const pill = (x: string) => <span className={x === "active" ? "pill-ok" : x === "pending" ? "pill-warn" : "chip"}>{x === "active" ? "on" : x}</span>;
  return (
    <div className="flex flex-col gap-6 max-w-2xl">
      <h1 className="h2">Chat channels</h1>
      <Flash sp={sp} />
      <div className="card flex items-center justify-between"><div><p className="text-lg font-light">Web chat</p><p className="text-sm muted">In this app. Always on.</p></div>{pill("active")}</div>

      {config.telegramEnabled && (
      <div className="card flex flex-col gap-3">
        <div className="flex items-center justify-between"><p className="text-lg font-light">Telegram</p>{pill(st("telegram"))}</div>
        <p className="text-sm muted">{st("telegram") === "active" ? `Linked to chat ${b("telegram")?.externalId}. Drafts arrive with Approve / Reject buttons.` : "Your own bot: paste its token in Integrations, then create a one-time link and open it on your phone."}</p>
        {!has("telegram") ? <Link href="/app/settings/integrations#telegram" className="btn-ghost btn-sm self-start">Add bot token</Link> : admin && (
          <form action={act} className="flex flex-wrap gap-2">
            <input type="hidden" name="channel" value="telegram" />
            <SubmitButton name="op" value="tg-link" className="btn btn-sm">{st("telegram") === "active" ? "Re-link" : "Create link"}</SubmitButton>
            {st("telegram") !== "off" && st("telegram") !== "disabled" && <SubmitButton name="op" value="off" className="btn-danger btn-sm">Disconnect</SubmitButton>}
          </form>
        )}
      </div>
      )}

      <div className="card flex flex-col gap-3">
        <div className="flex items-center justify-between"><p className="text-lg font-light">Slack</p>{pill(st("slack"))}</div>
        <p className="text-sm muted">Polls one channel every minute (no public URL needed). Approve with “approve &lt;code&gt;” from the approvers set in Integrations. “Add to Slack” with instant replies comes with the server move.</p>
        {!has("slack") ? <Link href="/app/settings/integrations#slack" className="btn-ghost btn-sm self-start">Add bot token</Link> : admin && (
          <form action={act} className="flex flex-col gap-2">
            <input type="hidden" name="channel" value="slack" />
            <input className="input" name="channelId" defaultValue={b("slack")?.externalId ?? ""} placeholder="Channel id, e.g. C0123ABCD" />
            <div className="flex flex-wrap gap-2">
              <SubmitButton name="op" value="slack" className="btn btn-sm">Connect channel</SubmitButton>
              {st("slack") === "active" && <SubmitButton name="op" value="off" className="btn-danger btn-sm">Disconnect</SubmitButton>}
            </div>
          </form>
        )}
      </div>
      <div className="card opacity-60"><p className="text-lg font-light">WhatsApp</p><p className="text-sm muted">Later, on a separate Northstar agent number.</p></div>
    </div>
  );
}
