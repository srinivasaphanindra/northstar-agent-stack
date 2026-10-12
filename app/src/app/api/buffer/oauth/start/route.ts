// GET /api/buffer/oauth/start -> redirects an owner/admin to Buffer's consent page (PKCE). Off until BUFFER_CLIENT_ID is set.
import { NextResponse } from "next/server";
import { isAppError } from "@/core/errors";
import { config } from "@/core/config";
import { startBufferOAuth } from "@/core/buffer-oauth";
import { currentCtx } from "@/lib/session";

export const dynamic = "force-dynamic";
const back = (k: "ok" | "e", v: string) => `${config.appUrl}/app/settings/integrations?${k}=${encodeURIComponent(v.slice(0, 300))}#buffer`;

export async function GET() {
  const ctx = await currentCtx("admin");
  try {
    return NextResponse.redirect(await startBufferOAuth(ctx));
  } catch (e) {
    if (isAppError(e)) return NextResponse.redirect(back("e", e.message));
    throw e;
  }
}
