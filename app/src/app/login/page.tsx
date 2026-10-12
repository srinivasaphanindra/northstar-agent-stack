import Link from "next/link";
import { redirect } from "next/navigation";
import { login } from "@/core/auth";
import { run, s } from "@/lib/act";
import { setSessionCookie, clientIp, currentUser } from "@/lib/session";
import { Flash } from "@/components/Flash";
import { SubmitButton } from "@/components/SubmitButton";

export const metadata = { title: "Sign in" };

async function doLogin(f: FormData) {
  "use server";
  const next = s(f, "next");
  await run("/login", async () => {
    const { token } = await login(s(f, "email"), s(f, "password"), await clientIp());
    await setSessionCookie(token);
    return { redirect: next.startsWith("/") && !next.startsWith("//") ? next : "/app" };
  });
}

export default async function Login({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const sp = await searchParams;
  if (await currentUser()) redirect("/app");
  return (
    <main className="min-h-dvh flex items-center justify-center px-6 py-12">
      <form action={doLogin} className="w-full max-w-sm flex flex-col gap-6">
        <Link href="/" className="brand">NORTHSTAR</Link>
        <h1 className="h2">Sign in</h1>
        <Flash sp={sp} />
        <input type="hidden" name="next" value={sp.next ?? ""} />
        <label className="flex flex-col gap-1"><span className="label">Email</span><input className="input" name="email" type="email" autoComplete="email" required /></label>
        <label className="flex flex-col gap-1"><span className="label">Password</span><input className="input" name="password" type="password" autoComplete="current-password" required /></label>
        <SubmitButton pendingText="Signing in…">Sign in</SubmitButton>
        <p className="text-sm muted">New here? <Link href={`/signup${sp.next ? `?next=${encodeURIComponent(sp.next)}` : ""}`} className="text-soft underline">Create an account</Link></p>
        <p className="text-xs muted">Magic-link sign-in is coming later.</p>
      </form>
    </main>
  );
}
