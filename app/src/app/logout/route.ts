import { NextResponse } from "next/server";
import { logout } from "@/core/auth";
import { sessionToken, clearSessionCookie, sameOrigin } from "@/lib/session";

export async function POST(req: Request) {
  if (!(await sameOrigin())) return new NextResponse("bad origin", { status: 403 });
  await logout(await sessionToken());
  await clearSessionCookie();
  return NextResponse.redirect(new URL("/login", req.url), 303);
}
