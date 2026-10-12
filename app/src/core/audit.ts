import { db } from "./db";

export type Actor = { type: "user" | "agent" | "system" | "admin"; userId?: string | null };

/** Append-only audit trail. Never pass secrets in detail. */
export async function audit(workspaceId: string | null, actor: Actor, action: string, target = "", detail: unknown = "") {
  const d = typeof detail === "string" ? detail : JSON.stringify(detail);
  await db.auditLog.create({
    data: { workspaceId, actorUserId: actor.userId ?? null, actorType: actor.type, action, target, detail: d.slice(0, 2000) },
  });
}
