// Next.js glue: cookies <-> core auth/tenancy. Everything else lives in src/core (framework-free).
import "server-only";
import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";
import { SESSION_COOKIE, userFromSession } from "@/core/auth";
import { requireMember, workspacesFor, type Ctx, type Role } from "@/core/tenancy";
import { config } from "@/core/config";
import { db } from "@/core/db";

export const WS_COOKIE = "ns_ws";

export async function setSessionCookie(token: string) {
  (await cookies()).set(SESSION_COOKIE, token, { httpOnly: true, sameSite: "lax", secure: config.secureCookies, path: "/", maxAge: config.sessionDays * 86400 });
}
export async function clearSessionCookie() {
  const c = await cookies();
  c.delete(SESSION_COOKIE);
  c.delete(WS_COOKIE);
}
export async function sessionToken() {
  return (await cookies()).get(SESSION_COOKIE)?.value;
}
export async function currentUser() {
  return userFromSession(await sessionToken());
}
export async function requireUser() {
  const u = await currentUser();
  if (!u) redirect("/login");
  return u;
}
export async function clientIp() {
  const h = await headers();
  return (h.get("x-forwarded-for") ?? "").split(",")[0].trim() || h.get("x-real-ip") || "";
}

/** The active workspace for this user (cookie, else first membership). Redirects to onboarding when there is none. */
export async function currentCtx(min: Role = "member"): Promise<Ctx & { user: NonNullable<Awaited<ReturnType<typeof currentUser>>> }> {
  const user = await requireUser();
  const wanted = (await cookies()).get(WS_COOKIE)?.value;
  const mems = await workspacesFor(user.id);
  if (!mems.length) redirect("/onboarding");
  const m = mems.find((x) => x.workspaceId === wanted) ?? mems[0];
  const ctx = await requireMember(user.id, m.workspaceId, min);
  return { ...ctx, user };
}

export async function currentWorkspace(ctx: Ctx) {
  return db.workspace.findUniqueOrThrow({ where: { id: ctx.workspaceId } });
}

/** Same-origin check for JSON route handlers (server actions have Next's own origin check). */
export async function sameOrigin() {
  const h = await headers();
  const origin = h.get("origin");
  const host = h.get("x-forwarded-host") ?? h.get("host");
  if (!origin) return true;
  try { return new URL(origin).host === host; } catch { return false; }
}
