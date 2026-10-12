import Link from "next/link";
import { cookies } from "next/headers";
import { acceptInvite } from "@/core/tenancy";
import { run } from "@/lib/act";
import { currentUser, WS_COOKIE } from "@/lib/session";
import { Flash } from "@/components/Flash";

export default async function Invite({ params, searchParams }: { params: Promise<{ token: string }>; searchParams: Promise<Record<string, string>> }) {
  const { token } = await params;
  const sp = await searchParams;
  const user = await currentUser();
  const here = `/invite/${token}`;
  async function accept() {
    "use server";
    await run(here, async () => {
      const u = await currentUser();
      if (!u) return { redirect: `/login?next=${encodeURIComponent(here)}` };
      const ws = await acceptInvite(u.id, token);
      (await cookies()).set(WS_COOKIE, ws, { httpOnly: true, sameSite: "lax", path: "/" });
      return { redirect: "/app" };
    });
  }
  return (
    <main className="min-h-dvh flex items-center justify-center px-6">
      <div className="w-full max-w-sm flex flex-col gap-6">
        <span className="brand">NORTHSTAR</span>
        <h1 className="h2">You're invited to a workspace</h1>
        <Flash sp={sp} />
        {user ? (
          <form action={accept}><button className="btn w-full">Join as {user.email}</button></form>
        ) : (
          <div className="flex flex-col gap-3">
            <Link className="btn" href={`/signup?next=${encodeURIComponent(here)}`}>Create an account to join</Link>
            <Link className="btn-ghost" href={`/login?next=${encodeURIComponent(here)}`}>Sign in</Link>
          </div>
        )}
      </div>
    </main>
  );
}
