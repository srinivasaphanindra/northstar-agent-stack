import { config } from "@/core/config";
import { PROVIDERS, listIntegrations, saveIntegration, testIntegration, deleteIntegration, type Provider } from "@/core/integrations";
import { can } from "@/core/tenancy";
import { BUFFER_SERVICE } from "@/core/buffer";
import { bufferView, refreshBufferChannels, saveChannelPicks } from "@/core/buffer-oauth";
import { run, s } from "@/lib/act";
import { currentCtx } from "@/lib/session";
import { Flash } from "@/components/Flash";
import { SubmitButton } from "@/components/SubmitButton";

export const metadata = { title: "Integrations" };
const P = "/app/settings/integrations";

// Non-secret config fields per provider
const FIELDS: Partial<Record<Provider, { k: string; label: string; ph: string }[]>> = {
  buffer: [],
  hootsuite: [],
  gohighlevel: [{ k: "locationId", label: "Location id", ph: "e.g. ve9EPM428h8vShlRW1KT" }],
  generic: [
    { k: "name", label: "Tool name", ph: "e.g. Canva, Zapier" },
    { k: "baseUrl", label: "API base URL (https)", ph: "https://api.example.com" },
    { k: "testPath", label: "Test path", ph: "/v1/me" },
    { k: "headerName", label: "Auth header", ph: "Authorization" },
    { k: "webhookUrl", label: "Outgoing webhook URL (optional)", ph: "https://hooks.zapier.com/…" },
  ],
  telegram: [],
  slack: [{ k: "approvers", label: "Slack user ids who may approve (comma separated)", ph: "U0123ABCD" }],
};
const ORDER: Provider[] = ["buffer", "hootsuite", "gohighlevel", "generic", ...(config.telegramEnabled ? ["telegram" as const] : []), "slack"];
const HELP: Partial<Record<Provider, string>> = {
  buffer: "Connect with Buffer (one click), or paste a personal API key from Buffer → Settings → API.",
  hootsuite: "Hootsuite needs an approved developer app; paste an access token if you have one.",
  gohighlevel: "Private integration token from your sub-account settings.",
  generic: "Any tool with an API key or a webhook. Stored encrypted; the assistant can't use it until we add that tool.",
  telegram: "Create a bot with @BotFather and paste its token. Then link your chat in Settings → Channels.",
  slack: "Bot token (xoxb-…) of a Slack app installed in your workspace, with chat:write and channels:history.",
};

async function act(f: FormData) {
  "use server";
  const provider = s(f, "provider");
  await run(`${P}#${provider}`, async () => {
    const ctx = await currentCtx("admin");
    const op = s(f, "op");
    if (op === "delete") { await deleteIntegration(ctx, provider); return "Removed."; }
    const cfg: Record<string, string> = {};
    for (const fl of FIELDS[provider as Provider] ?? []) cfg[fl.k] = s(f, fl.k);
    await saveIntegration(ctx, provider, s(f, "secret") || null, cfg);
    if (op === "test") { const r = await testIntegration(ctx, provider); return `${r.ok ? "✓" : "✗"} ${r.note}`; }
    return "Saved (encrypted).";
  });
}

async function bufferAct(f: FormData) {
  "use server";
  await run(`${P}#buffer-channels`, async () => {
    const ctx = await currentCtx("admin");
    if (s(f, "op") === "refresh") { const ch = await refreshBufferChannels(ctx); return `Found ${ch.length} Buffer channel(s).`; }
    const picks: Record<string, string> = {};
    for (const p of Object.keys(BUFFER_SERVICE)) picks[p] = s(f, `pick_${p}`);
    await saveChannelPicks(ctx, picks);
    return "Channel picks saved.";
  });
}

export default async function Integrations({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const sp = await searchParams;
  const ctx = await currentCtx();
  const rows = await listIntegrations(ctx);
  const admin = can(ctx, "admin");
  const buf = await bufferView(ctx.workspaceId);
  return (
    <div className="flex flex-col gap-6">
      <div><h1 className="h2">Integrations</h1><p className="text-sm muted mt-1">Keys are encrypted with your workspace's own key and never shown again (last 4 characters only).</p></div>
      <Flash sp={sp} />
      {ORDER.map((p) => {
        const row = rows.find((r) => r.provider === p);
        const cfg = (row?.config ?? {}) as Record<string, string>;
        return (
          <form key={p} id={p} action={act} className="card flex flex-col gap-3 scroll-mt-20">
            <input type="hidden" name="provider" value={p} />
            <div className="flex items-center justify-between gap-2">
              <p className="text-lg font-light">{PROVIDERS[p].label}</p>
              {row ? <span className={row.status === "ok" ? "pill-ok" : row.status === "error" ? "pill-bad" : "chip"}>{row.status === "ok" ? "connected" : row.status}</span> : <span className="chip">not set</span>}
            </div>
            <p className="text-sm muted">{HELP[p]}</p>
            {row?.lastTestNote && <p className="text-xs muted">Last test: {row.lastTestNote}</p>}
            {p === "buffer" && admin && (
              config.bufferOauthEnabled
                ? <a href="/api/buffer/oauth/start" className="btn btn-sm self-start">{buf.authMode === "oauth" ? "Reconnect Buffer" : "Connect with Buffer"}</a>
                : <p className="text-xs muted">One-click Buffer connect isn&apos;t switched on for this server yet (needs a Buffer app client).</p>
            )}
            {p === "buffer" && buf.authMode && <p className="text-xs muted">Signed in with {buf.authMode === "oauth" ? "Buffer connect (OAuth)" : "a personal API key"}.</p>}
            {admin && (
              <>
                <input className="input" name="secret" type="password" autoComplete="off" placeholder={row?.hasSecret ? `•••• ${row.last4} (leave empty to keep)` : PROVIDERS[p].secretLabel} />
                {(FIELDS[p] ?? []).map((fl) => <input key={fl.k} className="input" name={fl.k} defaultValue={cfg[fl.k] ?? ""} placeholder={`${fl.label}: ${fl.ph}`} aria-label={fl.label} />)}
                <div className="flex flex-wrap gap-2">
                  <SubmitButton name="op" value="test" className="btn btn-sm">Save & test</SubmitButton>
                  <SubmitButton name="op" value="save" className="btn-ghost btn-sm">Save</SubmitButton>
                  {row && <SubmitButton name="op" value="delete" className="btn-danger btn-sm">Remove</SubmitButton>}
                </div>
              </>
            )}
          </form>
        );
      })}
      {buf.connected && (
        <form id="buffer-channels" action={bufferAct} className="card flex flex-col gap-3 scroll-mt-20">
          <p className="text-lg font-light">Buffer channels</p>
          <p className="text-sm muted">Pick which Buffer channel each platform posts to. &ldquo;Automatic&rdquo; uses the first connected one.</p>
          {buf.channels.length === 0 && <p className="text-sm muted">No channels loaded yet.</p>}
          {Object.entries(BUFFER_SERVICE).map(([plat, svc]) => {
            const opts = buf.channels.filter((c) => c.service === svc);
            if (!opts.length) return null;
            return (
              <label key={plat} className="flex flex-col gap-1 text-sm">
                <span className="muted">{plat}</span>
                <select name={`pick_${plat}`} defaultValue={buf.picks[plat] ?? ""} className="input" disabled={!admin}>
                  <option value="">Automatic</option>
                  {opts.map((c) => <option key={c.id} value={c.id} disabled={c.isDisconnected || c.isLocked}>{c.name}{c.isDisconnected ? " (disconnected)" : c.isLocked ? " (locked)" : ""}</option>)}
                </select>
              </label>
            );
          })}
          {admin && (
            <div className="flex flex-wrap gap-2">
              <SubmitButton name="op" value="save" className="btn btn-sm">Save picks</SubmitButton>
              <SubmitButton name="op" value="refresh" className="btn-ghost btn-sm">Refresh channels</SubmitButton>
            </div>
          )}
        </form>
      )}
    </div>
  );
}
