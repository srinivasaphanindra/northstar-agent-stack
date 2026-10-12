import { NextResponse } from "next/server";
import { listMessages, sendUserMessage } from "@/core/chat";
import { requireMember, workspacesFor } from "@/core/tenancy";
import { isAppError } from "@/core/errors";
import { db } from "@/core/db";
import { currentUser, sameOrigin } from "@/lib/session";

async function ctxFor(conversationId: string) {
  const user = await currentUser();
  if (!user) return null;
  const conv = await db.conversation.findUnique({ where: { id: conversationId }, select: { workspaceId: true } });
  if (!conv) return null;
  const mems = await workspacesFor(user.id);
  if (!mems.some((m) => m.workspaceId === conv.workspaceId)) return null; // other tenant: same answer as "missing"
  return requireMember(user.id, conv.workspaceId);
}

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const ctx = await ctxFor(id);
  if (!ctx) return NextResponse.json({ error: "not found" }, { status: 404 });
  const msgs = await listMessages(ctx, id);
  const waiting = await db.job.count({ where: { workspaceId: ctx.workspaceId, type: "agent.reply", status: { in: ["queued", "running"] }, payload: { contains: id } } });
  return NextResponse.json({ messages: msgs.map((m) => ({ id: m.id, role: m.role, content: m.content, createdAt: m.createdAt, meta: m.meta })), waiting: waiting > 0 });
}

export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  if (!(await sameOrigin())) return NextResponse.json({ error: "bad origin" }, { status: 403 });
  const { id } = await params;
  const ctx = await ctxFor(id);
  if (!ctx) return NextResponse.json({ error: "not found" }, { status: 404 });
  const body = (await req.json().catch(() => ({}))) as { text?: string };
  try {
    const m = await sendUserMessage(ctx, id, String(body.text ?? ""));
    return NextResponse.json({ ok: true, id: m.id });
  } catch (e) {
    if (isAppError(e)) return NextResponse.json({ error: e.message }, { status: 400 });
    throw e;
  }
}
