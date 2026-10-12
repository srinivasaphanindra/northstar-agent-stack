import { db } from "@/core/db";
import { saveIntegration, testIntegration, setAiKeyMode, deleteIntegration, listIntegrations } from "@/core/integrations";
import { config } from "@/core/config";
import { run, s } from "@/lib/act";
import { currentCtx } from "@/lib/session";
import { Flash } from "@/components/Flash";
import { SubmitButton } from "@/components/SubmitButton";

export const metadata = { title: "AI key" };
const P = "/app/settings/ai";

async function act(f: FormData) {
  "use server";
  await run(P, async () => {
    const ctx = await currentCtx();
    const op = s(f, "op");
    if (op === "save") {
      await saveIntegration(ctx, "openrouter", s(f, "secret"));
      const r = await testIntegration(ctx, "openrouter");
      if (r.ok) { await setAiKeyMode(ctx, "byok"); return `Key works and is now in use. ${r.note}`; }
      return `Saved, but the test failed: ${r.note}`;
    }
    if (op === "platform") { await setAiKeyMode(ctx, "platform"); return "Using Northstar's key."; }
    if (op === "byok") { await setAiKeyMode(ctx, "byok"); return "Using your key."; }
    if (op === "remove") { await deleteIntegration(ctx, "openrouter"); return "Key removed. Using Northstar's key."; }
  });
}

export default async function Ai({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const sp = await searchParams;
  const ctx = await currentCtx();
  const ws = await db.workspace.findUniqueOrThrow({ where: { id: ctx.workspaceId } });
  const key = (await listIntegrations(ctx)).find((i) => i.provider === "openrouter");
  return (
    <div className="flex flex-col gap-6 max-w-xl">
      <h1 className="h2">AI key</h1>
      <Flash sp={sp} />
      <div className="card flex flex-col gap-2">
        <p className="label">In use now</p>
        <p className="text-xl font-extralight">{ws.aiKeyMode === "byok" ? `Your OpenRouter key ••••${key?.last4 ?? ""}` : "Northstar platform key"}</p>
        <p className="text-sm muted">Model: {config.defaultModel}. {ws.aiKeyMode === "byok" ? "OpenRouter bills you directly; your plan is cheaper." : "AI use is included up to your plan's monthly cap."}</p>
      </div>
      <form action={act} className="card flex flex-col gap-4">
        <p className="label">Use your own OpenRouter key</p>
        <p className="text-sm muted">Create one at openrouter.ai → Keys (set a credit limit there too). We test it, encrypt it for your workspace only, and only ever show the last 4 characters.</p>
        <input className="input" name="secret" type="password" autoComplete="off" placeholder="sk-or-v1-…" />
        <SubmitButton name="op" value="save" className="btn">Save, test and use</SubmitButton>
      </form>
      {key && (
        <form action={act} className="flex flex-col sm:flex-row gap-2">
          {ws.aiKeyMode === "byok" ? <SubmitButton name="op" value="platform" className="btn-ghost">Switch to Northstar's key</SubmitButton> : <SubmitButton name="op" value="byok" className="btn-ghost">Use my saved key</SubmitButton>}
          <SubmitButton name="op" value="remove" className="btn-danger">Remove my key</SubmitButton>
        </form>
      )}
    </div>
  );
}
