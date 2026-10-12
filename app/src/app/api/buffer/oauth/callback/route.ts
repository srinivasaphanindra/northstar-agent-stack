// GET /api/buffer/oauth/callback?code=&state= -> swaps the code for tokens (with the stored PKCE verifier).
import { NextResponse, type NextRequest } from "next/server";
import { isAppError } from "@/core/errors";
import { config } from "@/core/config";
import { finishBufferOAuth, refreshBufferChannels } from "@/core/buffer-oauth";
import { currentCtx } from "@/lib/session";

export const dynamic = "force-dynamic";
const back = (k: "ok" | "e", v: string) => `${config.appUrl}/app/settings/integrations?${k}=${encodeURIComponent(v.slice(0, 300))}#buffer`;

export async function GET(req: NextRequest) {
  const ctx = await currentCtx("admin");
  const q = req.nextUrl.searchParams;
  if (q.get("error")) return NextResponse.redirect(back("e", `Buffer said: ${q.get("error_description") ?? q.get("error")}`));
  try {
    await finishBufferOAuth(ctx, q.get("state") ?? "", q.get("code") ?? "");
    let note = "Buffer connected.";
    try { const ch = await refreshBufferChannels(ctx); note += ` Found ${ch.length} channel(s); pick one per platform below.`; } catch { note += " Couldn't read channels yet; use Refresh channels."; }
    return NextResponse.redirect(back("ok", note));
  } catch (e) {
    if (isAppError(e)) return NextResponse.redirect(back("e", e.message));
    throw e;
  }
}
