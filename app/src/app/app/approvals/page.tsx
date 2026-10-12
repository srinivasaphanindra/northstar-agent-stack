import { listDrafts, approveDraft, rejectDraft, editDraft, parseIstLocal } from "@/core/approvals";
import { safeJson } from "@/core/integrations";
import { rulesFor } from "@/core/guardrails";
import { can } from "@/core/tenancy";
import { run, s } from "@/lib/act";
import { currentCtx } from "@/lib/session";
import { Flash } from "@/components/Flash";
import { SubmitButton } from "@/components/SubmitButton";
import { HandoffButtons } from "@/components/HandoffButtons";

export const metadata = { title: "Approvals" };

async function decide(f: FormData) {
  "use server";
  const id = s(f, "id");
  await run(`/app/approvals#${id}`, async () => {
    const ctx = await currentCtx();
    const op = s(f, "op");
    if (op === "approve") {
      const m = s(f, "mode");
      const mode = m === "now" ? "now" : m === "scheduled" ? "scheduled" : "queue";
      return (await approveDraft(ctx, id, { text: s(f, "text"), mode, scheduledAt: mode === "scheduled" ? parseIstLocal(s(f, "at")) : null })).note;
    }
    if (op === "save") { await editDraft(ctx, id, s(f, "text")); return "Edit saved. Still waiting for approval."; }
    if (op === "reject") { await rejectDraft(ctx, id); return "Rejected. Nothing was posted."; }
  });
}

const fmt = (d: Date) => d.toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) + " IST";

export default async function Approvals({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const sp = await searchParams;
  const ctx = await currentCtx();
  const all = await listDrafts(ctx);
  const rules = await rulesFor(ctx.workspaceId);
  const site = rules.access.client_website ? `https://${rules.access.client_website}` : undefined;
  const pending = all.filter((d) => d.status === "pending");
  const done = all.filter((d) => d.status === "approved" || d.status === "published");
  const other = all.filter((d) => !["pending", "approved", "published"].includes(d.status));
  const approver = can(ctx, "admin");

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="h2">Approvals</h1>
        <p className="muted text-sm mt-1">Nothing goes public until you approve it. {approver ? "" : "You can view drafts; owners and admins approve."}</p>
      </div>
      <Flash sp={sp} />

      {pending.length === 0 && <div className="card text-center py-10"><p className="text-soft">Nothing waiting. 🎉</p><p className="muted text-sm mt-1">Ask your assistant for a post in Chat.</p></div>}

      {pending.map((d) => {
        const plats = safeJson<string[]>(d.platforms, []);
        return (
          <form key={d.id} id={d.id} action={decide} className="card flex flex-col gap-4 scroll-mt-20">
            <input type="hidden" name="id" value={d.id} />
            <div className="flex flex-wrap items-center gap-2">
              {plats.map((p) => <span key={p} className="chip">{p}</span>)}
              <span className="text-xs muted ml-auto">{fmt(d.createdAt)} · #{d.id.slice(-6)}</span>
            </div>
            <textarea name="text" defaultValue={d.text} className="textarea text-[1.02rem] leading-relaxed min-h-36" disabled={!approver} aria-label="Draft text" />
            {d.guardNote && <p className="text-sm text-warn">{d.guardNote}</p>}
            {approver && (
              <>
                <fieldset className="flex flex-col gap-2 text-sm muted">
                  <legend className="sr-only">When to post</legend>
                  <label className="flex items-center gap-3 min-h-10"><input type="radio" name="mode" value="queue" defaultChecked={d.mode !== "now"} className="size-5 accent-white" /> Add to the Buffer queue</label>
                  <label className="flex items-center gap-3 min-h-10"><input type="radio" name="mode" value="now" defaultChecked={d.mode === "now"} className="size-5 accent-white" /> Post now</label>
                  <label className="flex flex-wrap items-center gap-3 min-h-10"><input type="radio" name="mode" value="scheduled" className="size-5 accent-white" /> At a time (IST)
                    <input type="datetime-local" name="at" className="input !w-auto !min-h-9 !py-1" aria-label="Scheduled time (IST)" />
                  </label>
                </fieldset>
                <div className="grid grid-cols-[1fr_1fr_2fr] gap-2">
                  <SubmitButton name="op" value="reject" className="btn-danger !px-2">Reject</SubmitButton>
                  <SubmitButton name="op" value="save" className="btn-ghost !px-2">Save</SubmitButton>
                  <SubmitButton name="op" value="approve" className="btn" pendingText="Approving…">Approve ✓</SubmitButton>
                </div>
              </>
            )}
          </form>
        );
      })}

      {done.length > 0 && (
        <section className="flex flex-col gap-3">
          <p className="label mt-4">Approved: post from your phone</p>
          {done.map((d) => (
            <div key={d.id} id={d.id} className="card flex flex-col gap-3 scroll-mt-20">
              <div className="flex flex-wrap items-center gap-2">
                <span className={d.status === "published" ? "pill-ok" : "chip"}>{d.status === "published" ? "sent to Buffer" : "approved"}</span>
                {safeJson<string[]>(d.platforms, []).map((p) => <span key={p} className="chip">{p}</span>)}
                <span className="text-xs muted ml-auto">{d.decidedAt ? fmt(d.decidedAt) : ""}</span>
              </div>
              <p className="whitespace-pre-wrap text-soft">{d.text}</p>
              {d.publishResult && <p className="text-xs muted">{d.publishResult}</p>}
              <HandoffButtons text={d.text} url={site} platforms={safeJson<string[]>(d.platforms, [])} />
            </div>
          ))}
        </section>
      )}

      {other.length > 0 && (
        <details className="card">
          <summary className="cursor-pointer muted min-h-10 flex items-center">Rejected / failed ({other.length})</summary>
          <ul className="mt-3 flex flex-col gap-2">
            {other.map((d) => <li key={d.id} className="text-sm border-t border-line pt-2"><span className="chip mr-2">{d.status}</span><span className="muted">{d.text.slice(0, 140)}</span></li>)}
          </ul>
        </details>
      )}
    </div>
  );
}
