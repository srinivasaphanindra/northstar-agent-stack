import Link from "next/link";
import { notFound } from "next/navigation";
import { requireUser } from "@/lib/session";
import { clientsOverview } from "@/core/clients/dashboard";
import { CapBar, PLATFORM_LABEL } from "@/components/ClientBits";

export const metadata = { title: "Clients" };
export const dynamic = "force-dynamic";

const fmt = (d: Date) => d.toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

export default async function Clients() {
  const user = await requireUser();
  if (!user.isPlatformAdmin) notFound();
  const { today, lastRun, clients } = await clientsOverview(user);
  return (
    <main className="max-w-3xl mx-auto px-4 py-5 flex flex-col gap-4">
      <div className="flex items-center justify-between gap-2">
        <span className="brand">CLIENTS</span>
        <Link href="/admin" className="chip no-underline">Admin</Link>
      </div>
      <p className="text-xs muted">Today {today} IST · collector {lastRun ? `${fmt(lastRun.startedAt)} IST${lastRun.ok ? "" : " (some sources skipped)"}` : "has not run yet"} · read-only, every 15 min</p>
      {clients.map((c) => (
        <Link key={c.key} href={`/admin/clients/${c.key}`} className="card flex flex-col gap-3 no-underline text-fg" data-testid={`client-${c.key}`}>
          <div className="flex items-center justify-between gap-2">
            <p className="text-lg font-light">{c.name}</p>
            <span className="chip capitalize">{c.plan} · from {c.start}</span>
          </div>
          <div className="flex flex-wrap gap-2 text-xs">
            {c.profiles.length ? c.profiles.map((p) => <span key={p.platform} className="chip">{PLATFORM_LABEL[p.platform] ?? p.platform}: {p.handle}</span>) : <span className="muted">No profiles found in config yet.</span>}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {c.caps.map((p) => (
              <div key={p.platform} className="flex flex-col gap-2 rounded-xl border border-line p-3">
                <p className="label">{PLATFORM_LABEL[p.platform]}</p>
                <CapBar name="Posts" u={p.posts} />
                <CapBar name="Replies" u={p.replies} />
                <CapBar name="Comments" u={p.comments} />
              </div>
            ))}
          </div>
        </Link>
      ))}
    </main>
  );
}
