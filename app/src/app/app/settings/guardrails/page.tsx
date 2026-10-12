import { db } from "@/core/db";
import { rulesFor, saveOverrides, loadTemplate } from "@/core/guardrails";
import { setPaused } from "@/core/approvals";
import { spendStatus } from "@/core/usage";
import { can } from "@/core/tenancy";
import { run, s } from "@/lib/act";
import { currentCtx } from "@/lib/session";
import { Flash } from "@/components/Flash";
import { SubmitButton } from "@/components/SubmitButton";

export const metadata = { title: "Guardrails" };
const P = "/app/settings/guardrails";

async function save(f: FormData) {
  "use server";
  await run(P, async () => {
    const ctx = await currentCtx();
    const form: Record<string, string> = {};
    for (const [k, v] of f.entries()) if (typeof v === "string" && !k.startsWith("$")) form[k] = v;
    await saveOverrides(ctx, form);
    return "Guardrails saved.";
  });
}
async function pause(f: FormData) {
  "use server";
  await run(P, async () => {
    const ctx = await currentCtx();
    const on = s(f, "paused") === "1";
    await setPaused(ctx, on);
    return on ? "Paused. Nothing will be drafted or published until an owner resumes." : "Resumed.";
  });
}

export default async function Guardrails({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const sp = await searchParams;
  const ctx = await currentCtx();
  const ws = await db.workspace.findUniqueOrThrow({ where: { id: ctx.workspaceId } });
  const r = await rulesFor(ctx.workspaceId);
  const spend = await spendStatus(ctx.workspaceId);
  const admin = can(ctx, "admin");
  const src = loadTemplate().source.split("/").slice(-1)[0];
  return (
    <div className="flex flex-col gap-6 max-w-2xl">
      <div><h1 className="h2">Guardrails & limits</h1><p className="text-sm muted mt-1">Defaults come from the Northstar template ({src}), then your plan, then your changes here. Every public action always needs a human approval.</p></div>
      <Flash sp={sp} />

      <form action={pause} className={`card flex items-center justify-between gap-4 ${ws.paused ? "border-bad/50" : ""}`}>
        <div><p className="text-lg font-light">{ws.paused ? "Workspace is paused" : "Kill switch"}</p><p className="text-sm muted">{ws.paused ? "No drafts are created and nothing is published." : "Stops drafting and publishing at once. Anyone can pause; only the owner can resume."}</p></div>
        <input type="hidden" name="paused" value={ws.paused ? "0" : "1"} />
        <SubmitButton className={`${ws.paused ? "btn" : "btn-danger"} shrink-0 whitespace-nowrap`}>{ws.paused ? "Resume" : "Pause all"}</SubmitButton>
      </form>

      <form action={save} className="flex flex-col gap-6">
        <fieldset className="card flex flex-col gap-3" disabled={!admin}>
          <legend className="label px-1">Daily caps (IST day)</legend>
          <div className="grid grid-cols-[1fr_auto_auto] gap-x-4 gap-y-2 items-center text-sm">
            <span className="muted">Platform</span><span className="muted">Posts</span><span className="muted">Engagement</span>
            {(["x", "linkedin", "facebook"] as const).map((p) => (
              <Row key={p} p={p} posts={r.platforms[p]?.posts_per_day ?? 0} engage={r.platforms[p]?.engage_per_day ?? 0} />
            ))}
          </div>
        </fieldset>
        <fieldset className="card flex flex-col gap-3" disabled={!admin}>
          <legend className="label px-1">Content</legend>
          <label className="flex items-center justify-between gap-4 text-sm">Max hashtags per post<input className="input !w-20 text-center" type="number" name="max_hashtags" min={0} max={5} defaultValue={r.content.max_hashtags} /></label>
          <label className="flex items-center gap-3 min-h-11 text-sm"><input type="checkbox" name="allow_politics" defaultChecked={r.content.allow_politics} className="size-5 accent-white" /> Allow political topics</label>
          <label className="flex items-center gap-3 min-h-11 text-sm"><input type="checkbox" name="allow_religion" defaultChecked={r.content.allow_religion} className="size-5 accent-white" /> Allow religious topics</label>
          <p className="text-xs muted">Always blocked: fake reviews and testimonials, guaranteed results, medical or money promises, follows/DMs/mass likes ({r.engagement.never.join(", ")}).</p>
        </fieldset>
        <fieldset className="card flex flex-col gap-3" disabled={!admin}>
          <legend className="label px-1">AI budget</legend>
          <label className="flex items-center justify-between gap-4 text-sm">Monthly AI budget (USD)<input className="input !w-24 text-center" type="number" step="0.5" name="monthly_ai_budget_usd" min={0} max={500} defaultValue={r.spend.monthly_ai_budget_usd} /></label>
          <p className="text-xs muted">This month: ${spend.used.toFixed(3)} of ${spend.cap} ({Math.round(spend.pct)}%). Warning at {r.spend.warn_pct}%, extras stop at {r.spend.pause_nonessential_pct}%, chat stops at {r.spend.hard_stop_pct}%.{ws.aiKeyMode === "platform" ? " On Northstar's key, your plan's cap also applies." : ""}</p>
        </fieldset>
        <fieldset className="card flex flex-col gap-3" disabled={!admin}>
          <legend className="label px-1">Your website</legend>
          <input className="input" name="client_website" defaultValue={r.access.client_website} placeholder="example.com (used for share links and research)" />
        </fieldset>
        {admin && <SubmitButton className="btn md:self-start">Save guardrails</SubmitButton>}
      </form>
    </div>
  );
}

function Row({ p, posts, engage }: { p: string; posts: number; engage: number }) {
  return (
    <>
      <span className="capitalize">{p === "x" ? "X" : p}</span>
      <input className="input !w-16 text-center" type="number" min={0} max={10} name={`${p}_posts`} defaultValue={posts} aria-label={`${p} posts per day`} />
      <input className="input !w-16 text-center" type="number" min={0} max={10} name={`${p}_engage`} defaultValue={engage} aria-label={`${p} engagement per day`} />
    </>
  );
}
