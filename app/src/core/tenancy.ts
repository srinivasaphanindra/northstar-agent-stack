// Tenant isolation. Every tenant operation starts from a Ctx produced by requireMember(), and every query
// in core/ filters on ctx.workspaceId. Roles: owner > admin > member.
import { db } from "./db";
import { AppError } from "./errors";
import { audit } from "./audit";
import { newWrappedDataKey } from "./crypto";
import { randomToken, sha256 } from "./crypto";

export type Role = "owner" | "admin" | "member";
export const ROLE_RANK: Record<Role, number> = { member: 1, admin: 2, owner: 3 };
export type Ctx = { workspaceId: string; userId: string; role: Role; isPlatformAdmin: boolean };

export async function requireMember(userId: string, workspaceId: string, min: Role = "member"): Promise<Ctx> {
  const m = await db.member.findUnique({
    where: { workspaceId_userId: { workspaceId, userId } },
    include: { user: true },
  });
  if (!m) throw new AppError("not_found", "Workspace not found");
  const role = m.role as Role;
  if (ROLE_RANK[role] < ROLE_RANK[min]) throw new AppError("forbidden", `This needs the ${min} role`);
  return { workspaceId, userId, role, isPlatformAdmin: m.user.isPlatformAdmin };
}

export function can(ctx: Ctx, min: Role) {
  return ROLE_RANK[ctx.role] >= ROLE_RANK[min];
}

export function slugify(name: string) {
  const base = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "workspace";
  return `${base}-${randomToken(4).toLowerCase().replace(/[^a-z0-9]/g, "").slice(0, 5)}`;
}

export async function createWorkspace(userId: string, input: { name: string; city?: string }) {
  const name = input.name.trim().slice(0, 80);
  if (!name) throw new AppError("invalid", "Business name is required");
  const ws = await db.$transaction(async (tx) => {
    const w = await tx.workspace.create({ data: { name, city: (input.city ?? "").trim().slice(0, 80), slug: slugify(name), dataKeyEnc: "pending" } });
    const wrapped = newWrappedDataKey(w.id);
    const w2 = await tx.workspace.update({ where: { id: w.id }, data: { dataKeyEnc: wrapped } });
    await tx.member.create({ data: { workspaceId: w.id, userId, role: "owner" } });
    await tx.channelBinding.create({ data: { workspaceId: w.id, channel: "web", status: "active" } });
    return w2;
  });
  await audit(ws.id, { type: "user", userId }, "workspace.create", ws.name);
  return ws;
}

export async function workspacesFor(userId: string) {
  return db.member.findMany({ where: { userId }, include: { workspace: true }, orderBy: { createdAt: "asc" } });
}

// ---- members & invites ----
export async function listMembers(ctx: Ctx) {
  return db.member.findMany({ where: { workspaceId: ctx.workspaceId }, include: { user: { select: { id: true, email: true, name: true } } }, orderBy: { createdAt: "asc" } });
}

export async function createInvite(ctx: Ctx, email: string, role: Role) {
  if (!can(ctx, "admin")) throw new AppError("forbidden", "Only owners and admins can invite");
  if (role === "owner" && ctx.role !== "owner") throw new AppError("forbidden", "Only the owner can invite another owner");
  const token = randomToken(24);
  const inv = await db.invite.create({
    data: { workspaceId: ctx.workspaceId, email: email.trim().toLowerCase(), role, tokenHash: sha256(token), expiresAt: new Date(Date.now() + 7 * 86400_000), createdBy: ctx.userId },
  });
  await audit(ctx.workspaceId, { type: "user", userId: ctx.userId }, "member.invite", inv.email, { role });
  return { invite: inv, token };
}

export async function acceptInvite(userId: string, token: string) {
  const inv = await db.invite.findUnique({ where: { tokenHash: sha256(token) } });
  if (!inv || inv.acceptedAt || inv.expiresAt < new Date()) throw new AppError("not_found", "This invite link is not valid any more");
  const user = await db.user.findUniqueOrThrow({ where: { id: userId } });
  if (user.email !== inv.email) throw new AppError("forbidden", "This invite was sent to a different email address");
  await db.$transaction([
    db.member.upsert({ where: { workspaceId_userId: { workspaceId: inv.workspaceId, userId } }, create: { workspaceId: inv.workspaceId, userId, role: inv.role }, update: {} }),
    db.invite.update({ where: { id: inv.id }, data: { acceptedAt: new Date() } }),
  ]);
  await audit(inv.workspaceId, { type: "user", userId }, "member.join", user.email, { role: inv.role });
  return inv.workspaceId;
}

export async function changeRole(ctx: Ctx, memberId: string, role: Role) {
  if (ctx.role !== "owner") throw new AppError("forbidden", "Only the owner can change roles");
  const m = await db.member.findFirst({ where: { id: memberId, workspaceId: ctx.workspaceId } });
  if (!m) throw new AppError("not_found", "Member not found");
  if (m.userId === ctx.userId) throw new AppError("invalid", "You can't change your own role");
  await db.member.update({ where: { id: m.id }, data: { role } });
  await audit(ctx.workspaceId, { type: "user", userId: ctx.userId }, "member.role", m.userId, { role });
}

export async function removeMember(ctx: Ctx, memberId: string) {
  if (!can(ctx, "admin")) throw new AppError("forbidden", "Only owners and admins can remove members");
  const m = await db.member.findFirst({ where: { id: memberId, workspaceId: ctx.workspaceId } });
  if (!m) throw new AppError("not_found", "Member not found");
  if (m.role === "owner") throw new AppError("forbidden", "The owner can't be removed");
  await db.member.delete({ where: { id: m.id } });
  await audit(ctx.workspaceId, { type: "user", userId: ctx.userId }, "member.remove", m.userId);
}
