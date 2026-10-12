import Link from "next/link";
import { redirect } from "next/navigation";
import { cookies } from "next/headers";
import { db } from "@/core/db";
import { PLANS, planOf, config } from "@/core/config";
import { createWorkspace, workspacesFor, requireMember } from "@/core/tenancy";
import { choosePlan } from "@/core/billing";
import { startOnboarding, answerOnboarding, onboardingState, setOnboardingStep } from "@/core/onboarding";
import { saveIntegration, testIntegration } from "@/core/integrations";
import { run, s } from "@/lib/act";
import { requireUser, WS_COOKIE } from "@/lib/session";
import { Flash } from "@/components/Flash";
import { SubmitButton } from "@/components/SubmitButton";
import { PaymentBox } from "@/components/PaymentBox";
import { Bubble } from "@/components/ChatLog";
import { AutoScroll } from "@/components/AutoScroll";

export const metadata = { title: "Get started" };
const STEPS = ["plan", "interview", "integrations", "channel", "done"] as const;
const LABEL: Record<string, string> = { plan: "Plan", interview: "Interview", integrations: "Connect tools", channel: "Chat channel", done: "Done" };

async function ctxOrNull() {
  const user = await requireUser();
  const mems = await workspacesFor(user.id);
  if (!mems.length) return { user, ctx: null };
  const wanted = (await cookies()).get(WS_COOKIE)?.value;
  const m = mems.find((x) => x.workspaceId === wanted) ?? mems[0];
  return { user, ctx: await requireMember(user.id, m.workspaceId) };
}

async function createWs(f: FormData) {
  "use server";
  await run("/onboarding", async () => {
    const user = await requireUser();
    const ws = await createWorkspace(user.id, { name: s(f, "name"), city: s(f, "city") });
    (await cookies()).set(WS_COOKIE, ws.id, { httpOnly: true, sameSite: "lax", path: "/" });
  });
}
async function pickPlan(f: FormData) {
  "use server";
  await run("/onboarding", async () => {
    const { ctx } = await ctxOrNull();
    if (!ctx) return;
    await choosePlan(ctx, s(f, "plan"), s(f, "byok") === "on");
  });
}
async function goStep(f: FormData) {
  "use server";
  await run("/onboarding", async () => {
    const { ctx } = await ctxOrNull();
    if (!ctx) return;
    const step = s(f, "step") as (typeof STEPS)[number];
    if (!STEPS.includes(step)) return;
    await setOnboardingStep(ctx, step);
    if (step === "interview") await startOnboarding(ctx);
    if (step === "done") return { redirect: "/app" };
  });
}
async function answer(f: FormData) {
  "use server";
  await run("/onboarding#end", async () => {
    const { ctx } = await ctxOrNull();
    if (!ctx) return;
    await answerOnboarding(ctx, s(f, "text"));
  });
}
async function saveBuffer(f: FormData) {
  "use server";
  await run("/onboarding", async () => {
    const { ctx } = await ctxOrNull();
    if (!ctx) return;
    await saveIntegration(ctx, "buffer", s(f, "secret"));
    const r = await testIntegration(ctx, "buffer");
    return r.ok ? `Buffer connected: ${r.note}` : `Saved. Test: ${r.note}`;
  });
}

export default async function Onboarding({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const sp = await searchParams;
  const { ctx } = await ctxOrNull();

  if (!ctx) {
    return (
      <Shell step={-1} sp={sp}>
        <form action={createWs} className="flex flex-col gap-6 max-w-md">
          <h1 className="h2">Tell us about your business</h1>
          <label className="flex flex-col gap-1"><span className="label">Business name</span><input className="input" name="name" required placeholder="e.g. Glow Salon" /></label>
          <label className="flex flex-col gap-1"><span className="label">City</span><input className="input" name="city" placeholder="e.g. Hyderabad" /></label>
          <SubmitButton>Continue</SubmitButton>
        </form>
      </Shell>
    );
  }
  const ws = await db.workspace.findUniqueOrThrow({ where: { id: ctx.workspaceId } });
  if (ws.onboardingStep === "done") redirect("/app");
  const idx = STEPS.indexOf(ws.onboardingStep as (typeof STEPS)[number]);
  const pending = await db.invoice.findFirst({ where: { workspaceId: ws.id, status: "pending" }, orderBy: { createdAt: "desc" } });
  const chosen = ws.plan !== "trial" || pending || ws.status === "active";

  return (
    <Shell step={idx} sp={sp}>
      {ws.onboardingStep === "plan" && (
        <div className="flex flex-col gap-8">
          <div>
            <h1 className="h2">Pick a plan</h1>
            <p className="muted mt-2">Prices in INR per month. Every post still needs your approval.</p>
          </div>
          <form action={pickPlan} className="flex flex-col gap-4">
            <div className="grid md:grid-cols-3 gap-3">
              {Object.values(PLANS).map((p) => (
                <label key={p.id} className="card cursor-pointer has-[:checked]:border-white flex flex-col gap-2">
                  <input type="radio" name="plan" value={p.id} defaultChecked={ws.plan === p.id} className="sr-only" />
                  <span className="label">{p.name}</span>
                  <span className="text-3xl font-extralight">{p.inr ? `₹${p.inr.toLocaleString("en-IN")}` : "Free"}</span>
                  {p.byokInr > 0 && <span className="text-xs muted">₹{p.byokInr.toLocaleString("en-IN")} with your own AI key</span>}
                  <span className="text-sm muted">{p.blurb}</span>
                </label>
              ))}
            </div>
            <label className="flex items-center gap-3 min-h-12"><input type="checkbox" name="byok" className="size-5 accent-white" /> <span>I'll use my own OpenRouter key (cheaper)</span></label>
            <SubmitButton className="btn md:self-start">Choose plan</SubmitButton>
          </form>
          {pending && <PaymentBox amountInr={pending.amountInr} invoiceId={pending.id} planName={planOf(pending.plan).name} />}
          {chosen && (
            <form action={goStep}><input type="hidden" name="step" value="interview" />
              <SubmitButton className="btn w-full md:w-auto">{pending ? "I've paid / I'll pay later: continue" : "Continue"}</SubmitButton>
            </form>
          )}
        </div>
      )}

      {ws.onboardingStep === "interview" && <Interview ctxWs={ws.id} ctx={ctx} />}

      {ws.onboardingStep === "integrations" && (
        <div className="flex flex-col gap-6 max-w-xl">
          <h1 className="h2">Connect your tools</h1>
          <p className="muted">Buffer is how approved posts get scheduled. Make a free Buffer account, connect up to 3 channels there, then paste a personal API key (Buffer → Settings → API). You can skip this and post with the “Open in app” buttons on your phone instead.</p>
          <form action={saveBuffer} className="flex flex-col gap-4">
            <label className="flex flex-col gap-1"><span className="label">Buffer API key</span><input className="input" name="secret" type="password" autoComplete="off" required /></label>
            <SubmitButton className="btn-ghost">Save and test</SubmitButton>
          </form>
          <p className="text-sm muted">Hootsuite, GoHighLevel and other tools: Settings → Integrations.</p>
          <form action={goStep}><input type="hidden" name="step" value="channel" /><SubmitButton className="btn w-full md:w-auto">Next</SubmitButton></form>
        </div>
      )}

      {ws.onboardingStep === "channel" && (
        <div className="flex flex-col gap-6 max-w-xl">
          <h1 className="h2">Where do you want to chat?</h1>
          <div className="card"><p className="font-normal">Web chat <span className="pill-ok ml-2">on</span></p><p className="text-sm muted mt-1">Always on, here in the app. Install it on your phone for one-tap approvals.</p></div>
          <div className="card"><p className="font-normal">Slack</p><p className="text-sm muted mt-1">Paste a bot token and the channel id in Settings → Integrations and Channels. (“Add to Slack” comes later.)</p></div>
          {config.telegramEnabled && (
            <div className="card"><p className="font-normal">Telegram</p><p className="text-sm muted mt-1">Make a bot with @BotFather, paste its token in Settings → Integrations, then open the one-time link from Settings → Channels.</p></div>
          )}
          <p className="text-sm muted">WhatsApp is planned for later.</p>
          <form action={goStep}><input type="hidden" name="step" value="done" /><SubmitButton className="btn w-full md:w-auto">Finish setup</SubmitButton></form>
        </div>
      )}
    </Shell>
  );
}

async function Interview({ ctx }: { ctxWs: string; ctx: Awaited<ReturnType<typeof requireMember>> }) {
  let st = await onboardingState(ctx);
  if (!st.session) { await startOnboarding(ctx); st = await onboardingState(ctx); }
  const done = st.session?.status === "done";
  return (
    <div className="flex flex-col gap-4">
      <h1 className="h2">A quick interview</h1>
      <div className="flex flex-col gap-3 pb-4">{st.messages.map((m) => <Bubble key={m.id} m={m} />)}</div>
      <div id="end" />
      <AutoScroll />
      {done ? (
        <form action={goStep}><input type="hidden" name="step" value="integrations" /><SubmitButton className="btn w-full">Next: connect tools</SubmitButton></form>
      ) : (
        <form action={answer} className="sticky bottom-0 bg-black/90 backdrop-blur py-3 flex gap-2 safe-bottom">
          <input className="input flex-1" name="text" autoFocus autoComplete="off" placeholder="Type your answer, or 'skip'" required />
          <SubmitButton className="btn" pendingText="…">Send</SubmitButton>
        </form>
      )}
    </div>
  );
}

function Shell({ step, sp, children }: { step: number; sp: Record<string, string>; children: React.ReactNode }) {
  return (
    <main className="min-h-dvh max-w-4xl mx-auto px-5 py-6 md:py-10 flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <Link href="/" className="brand">NORTHSTAR</Link>
        <form action="/logout" method="post"><button className="text-sm muted">Sign out</button></form>
      </div>
      {step >= 0 && (
        <ol className="flex gap-2 overflow-x-auto text-xs">
          {STEPS.slice(0, 4).map((x, i) => (
            <li key={x} className={`chip whitespace-nowrap ${i === step ? "border-white text-fg" : i < step ? "text-ok border-ok/40" : ""}`}>{i + 1}. {LABEL[x]}</li>
          ))}
        </ol>
      )}
      <Flash sp={sp} />
      {children}
    </main>
  );
}
