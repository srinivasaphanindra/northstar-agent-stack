import { db } from "@/core/db";
import { createUser } from "@/core/auth";
import { createWorkspace, requireMember } from "@/core/tenancy";

export async function resetDb() {
  // children first
  for (const t of ["message", "conversation", "draft", "job", "usageEvent", "invoice", "auditLog", "integration", "guardrailSettings", "brainNote", "onboardingSession", "channelBinding", "invite", "member", "session", "workspace", "user"] as const) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    await (db as any)[t].deleteMany({});
  }
}

let n = 0;
export async function makeTenant(name = "Tenant", opts: { active?: boolean } = {}) {
  n++;
  const user = await createUser({ email: `owner${n}-${Date.now()}@test.local`, password: "correct horse battery" });
  const ws = await createWorkspace(user.id, { name });
  if (opts.active !== false) await db.workspace.update({ where: { id: ws.id }, data: { status: "active" } });
  const ctx = await requireMember(user.id, ws.id, "owner");
  return { user, ws, ctx };
}
