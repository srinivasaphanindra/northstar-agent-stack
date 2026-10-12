import { NextResponse } from "next/server";
import { audit } from "@/core/audit";
import { currentUser, sameOrigin } from "@/lib/session";

// Web push stub: records the opt-in only. Sending pushes (VAPID + web-push) is a later step.
export async function POST(req: Request) {
  if (!(await sameOrigin())) return NextResponse.json({ error: "bad origin" }, { status: 403 });
  const user = await currentUser();
  if (!user) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  const b = (await req.json().catch(() => ({}))) as { endpoint?: string };
  await audit(null, { type: "user", userId: user.id }, "push.subscribe_stub", b.endpoint ? new URL(b.endpoint).host : "no-endpoint");
  return NextResponse.json({ ok: true, stub: true }, { status: 202 });
}
