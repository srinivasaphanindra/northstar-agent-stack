import Link from "next/link";
import { createUser, createSession } from "@/core/auth";
import { config } from "@/core/config";
import { run, s } from "@/lib/act";
import { setSessionCookie, clientIp } from "@/lib/session";
import { Flash } from "@/components/Flash";
import { SubmitButton } from "@/components/SubmitButton";
import { AppError } from "@/core/errors";

export const metadata = { title: "Create account" };

async function doSignup(f: FormData) {
  "use server";
  const next = s(f, "next");
  await run("/signup", async () => {
    if (!config.allowSignup) throw new AppError("forbidden", "Sign-ups are closed right now");
    const u = await createUser({ email: s(f, "email"), password: s(f, "password"), name: s(f, "name") });
    await setSessionCookie(await createSession(u.id, await clientIp()));
    return { redirect: next.startsWith("/invite/") ? next : "/onboarding" };
  });
}

export default async function Signup({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const sp = await searchParams;
  return (
    <main className="min-h-dvh flex items-center justify-center px-6 py-12">
      <form action={doSignup} className="w-full max-w-sm flex flex-col gap-6">
        <Link href="/" className="brand">NORTHSTAR</Link>
        <h1 className="h2">Create your account</h1>
        <Flash sp={sp} />
        <input type="hidden" name="next" value={sp.next ?? ""} />
        <label className="flex flex-col gap-1"><span className="label">Your name</span><input className="input" name="name" autoComplete="name" /></label>
        <label className="flex flex-col gap-1"><span className="label">Email</span><input className="input" name="email" type="email" autoComplete="email" required /></label>
        <label className="flex flex-col gap-1"><span className="label">Password (10+ characters)</span><input className="input" name="password" type="password" minLength={10} autoComplete="new-password" required /></label>
        <SubmitButton pendingText="Creating…">Create account</SubmitButton>
        <p className="text-sm muted">Have an account? <Link href="/login" className="text-soft underline">Sign in</Link></p>
      </form>
    </main>
  );
}
