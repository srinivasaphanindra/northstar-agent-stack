import Link from "next/link";
import { currentUser } from "@/lib/session";

export default async function Home() {
  const user = await currentUser();
  return (
    <main className="min-h-dvh flex flex-col">
      <nav className="border-b border-line">
        <div className="max-w-5xl mx-auto px-6 h-16 flex items-center justify-between">
          <span className="brand">NORTHSTAR</span>
          {user ? <Link href="/app" className="btn btn-sm">Open app</Link> : <Link href="/login" className="text-sm muted hover:text-fg">Sign in</Link>}
        </div>
      </nav>
      <section className="flex-1 max-w-5xl mx-auto px-6 py-20 md:py-32 flex flex-col justify-center">
        <p className="label mb-6">Social media assistant · India</p>
        <h1 className="h1 max-w-[15ch]">Posts in your voice. Nothing goes out without your yes.</h1>
        <p className="text-soft text-lg max-w-xl mt-8 mb-10">Chat with your assistant on the web or in Slack. It drafts, you approve with one tap, and you post from the apps already signed in on your phone.</p>
        <div className="flex flex-col sm:flex-row gap-3">
          <Link href="/signup" className="btn">Start free trial</Link>
          <Link href="/login" className="btn-ghost">I have an account</Link>
        </div>
        <p className="text-sm muted mt-6">7-day trial · Solo ₹1,999/mo · Pro ₹3,999/mo · ₹500-1,000 less with your own OpenRouter key</p>
      </section>
      <footer className="border-t border-line py-8 text-sm muted text-center">Northstar Agents · Hyderabad</footer>
    </main>
  );
}
