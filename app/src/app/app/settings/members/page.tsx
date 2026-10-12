import { headers } from "next/headers";
import { listMembers, createInvite, changeRole, removeMember, can, type Role } from "@/core/tenancy";
import { db } from "@/core/db";
import { config } from "@/core/config";
import { run, s } from "@/lib/act";
import { currentCtx } from "@/lib/session";
import { Flash } from "@/components/Flash";
import { SubmitButton } from "@/components/SubmitButton";

export const metadata = { title: "Team" };
const P = "/app/settings/members";

async function act(f: FormData) {
  "use server";
  await run(P, async () => {
    const ctx = await currentCtx();
    const op = s(f, "op");
    if (op === "invite") {
      const { token } = await createInvite(ctx, s(f, "email"), (s(f, "role") || "member") as Role);
      const h = await headers();
      const base = config.appUrl || `http://${h.get("host")}`;
      return `Invite link (valid 7 days, send it yourself): ${base}/invite/${token}`;
    }
    if (op === "role") { await changeRole(ctx, s(f, "memberId"), s(f, "role") as Role); return "Role changed."; }
    if (op === "remove") { await removeMember(ctx, s(f, "memberId")); return "Removed."; }
  });
}

export default async function Members({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const sp = await searchParams;
  const ctx = await currentCtx();
  const members = await listMembers(ctx);
  const invites = await db.invite.findMany({ where: { workspaceId: ctx.workspaceId, acceptedAt: null, expiresAt: { gt: new Date() } } });
  const admin = can(ctx, "admin");
  return (
    <div className="flex flex-col gap-6 max-w-2xl">
      <div><h1 className="h2">Team</h1><p className="text-sm muted mt-1">Owner: everything. Admin: approve posts, integrations, guardrails, invites. Member: chat and view drafts.</p></div>
      <Flash sp={sp} />
      <ul className="flex flex-col">
        {members.map((m) => (
          <li key={m.id} className="flex flex-wrap items-center justify-between gap-3 py-3 border-b border-line">
            <span className="min-w-0"><span className="block truncate">{m.user.name || m.user.email}</span><span className="block text-xs muted truncate">{m.user.email}</span></span>
            {ctx.role === "owner" && m.userId !== ctx.userId ? (
              <form action={act} className="flex gap-2 items-center">
                <input type="hidden" name="memberId" value={m.id} />
                <select name="role" defaultValue={m.role} className="bg-black border border-line rounded-full px-3 min-h-10 text-sm">
                  <option value="member">member</option><option value="admin">admin</option><option value="owner">owner</option>
                </select>
                <SubmitButton name="op" value="role" className="btn-ghost btn-sm">Set</SubmitButton>
                {m.role !== "owner" && <SubmitButton name="op" value="remove" className="btn-danger btn-sm">Remove</SubmitButton>}
              </form>
            ) : <span className="chip">{m.role}</span>}
          </li>
        ))}
      </ul>
      {invites.length > 0 && <p className="text-sm muted">Open invites: {invites.map((i) => `${i.email} (${i.role})`).join(", ")}</p>}
      {admin && (
        <form action={act} className="card flex flex-col gap-3">
          <p className="label">Invite someone</p>
          <input className="input" type="email" name="email" placeholder="email@business.com" required />
          <select name="role" defaultValue="member" className="bg-black border border-line rounded-full px-4 min-h-12">
            <option value="member">member</option><option value="admin">admin</option>{ctx.role === "owner" && <option value="owner">owner</option>}
          </select>
          <input type="hidden" name="op" value="invite" />
          <SubmitButton className="btn">Create invite link</SubmitButton>
          <p className="text-xs muted">We don't send emails yet: copy the link and send it on WhatsApp or email.</p>
        </form>
      )}
    </div>
  );
}
