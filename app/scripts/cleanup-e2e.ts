// Removes users/workspaces created by scripts/screenshots.mjs (emails e2e-*@example.com).
import { db } from "../src/core/db";
async function main() {
const users = await db.user.findMany({ where: { email: { startsWith: "e2e-" } } });
for (const u of users) {
  const mems = await db.member.findMany({ where: { userId: u.id, role: "owner" } });
  for (const m of mems) {
    const w = m.workspaceId;
    for (const t of ["message", "conversation", "draft", "job", "usageEvent", "invoice", "integration", "guardrailSettings", "brainNote", "onboardingSession", "channelBinding", "invite"] as const)
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      await (db as any)[t].deleteMany({ where: { workspaceId: w } });
    await db.workspace.delete({ where: { id: w } });
  }
  await db.user.delete({ where: { id: u.id } });
}
console.log(`removed ${users.length} e2e users (audit log rows are kept: append-only)`);
}
main().then(() => db.$disconnect());
