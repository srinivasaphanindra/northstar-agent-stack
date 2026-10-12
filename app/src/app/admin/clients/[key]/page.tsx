import Link from "next/link";
import { notFound } from "next/navigation";
import { requireUser } from "@/lib/session";
import { clientDetail } from "@/core/clients/dashboard";
import { isAppError } from "@/core/errors";
import { Bars, CapBar, PLATFORM_LABEL } from "@/components/ClientBits";

export const metadata = { title: "Client analytics" };
export const dynamic = "force-dynamic";

const fmt = (d: Date) => d.toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

export default async function ClientPage({ params, searchParams }: { params: Promise<{ key: string }>; searchParams: Promise<Record<string, string>> }) {
  const user = await requireUser();
  if (!user.isPlatformAdmin) notFound();
  const { key } = await params;
  const range = (await searchParams).range === "30" ? "30" : "7";
  let d;
  try { d = await clientDetail(user, key); } catch (e) { if (isAppError(e)) notFound(); throw e; }
  const days = range === "30" ? d.d30 : d.d7;
  const series = range === "30" ? d.series30 : d.series7;
  const t = range === "30" ? d.t30 : d.t7;
  const fl = ["x", "linkedin", "facebook"].map((p) => ({ p, pts: d.followers.filter((f) => f.platform === p) })).filter((x) => x.pts.length);
  return (
    <main className="max-w-3xl mx-auto px-4 py-5 flex flex-col gap-4">
      <div className="flex items-center justify-between gap-2">
        <Link href="/admin/clients" className="chip no-underline">← Clients</Link>
        <div className="flex gap-1" role="tablist">
          {(["7", "30"] as const).map((r) => <Link key={r} href={`?range=${r}`} className={`chip no-underline ${range === r ? "bg-white/10" : ""}`} aria-selected={range === r} role="tab">{r} days</Link>)}
        </div>
      </div>
      <div>
        <p className="text-xl font-light">{d.client.name}</p>
        <p className="text-xs muted capitalize">{d.client.plan} plan · warm-up from {d.client.start} · today {d.today} IST</p>
      </div>
      <div className="flex flex-wrap gap-2 text-xs">
        {d.profiles.map((p) => p.url ? <a key={p.platform} href={p.url} target="_blank" rel="noreferrer" className="chip no-underline">{PLATFORM_LABEL[p.platform] ?? p.platform}: {p.handle}</a> : <span key={p.platform} className="chip">{PLATFORM_LABEL[p.platform] ?? p.platform}: {p.handle}</span>)}
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="card !p-4"><p className="label">Posts</p><p className="text-2xl font-extralight">{t.posts}</p>{t.dryRuns > 0 && <p className="text-xs muted">{t.dryRuns} dry-run approvals (not posted)</p>}</div>
        <div className="card !p-4"><p className="label">Replies</p><p className="text-2xl font-extralight">{t.replies}</p></div>
        <div className="card !p-4"><p className="label">Comments</p><p className="text-2xl font-extralight">{t.comments}</p></div>
        <div className="card !p-4"><p className="label">Inbound handled</p><p className="text-2xl font-extralight">{t.inbound}</p></div>
        <div className="card !p-4"><p className="label">Approved</p><p className="text-2xl font-extralight">{t.approved}</p></div>
        <div className="card !p-4"><p className="label">Rejected</p><p className="text-2xl font-extralight">{t.rejected}</p>{t.edited > 0 && <p className="text-xs muted">{t.edited} edited</p>}</div>
        <div className="card !p-4"><p className="label">Impressions</p><p className="text-2xl font-extralight">{t.impressions ?? "n/a"}</p></div>
        <div className="card !p-4"><p className="label">AI spend (month)</p><p className={`text-2xl font-extralight ${d.spend.pct !== null && d.spend.pct >= 100 ? "text-warn" : ""}`}>{d.spend.spent === null ? "n/a" : `$${d.spend.spent.toFixed(2)}`}<span className="text-sm muted"> / ${d.spend.budget}</span></p></div>
      </div>

      <section className="card flex flex-col gap-3">
        <p className="label">Today against caps</p>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {d.caps.map((p) => (
            <div key={p.platform} className="flex flex-col gap-2">
              <p className="text-sm">{PLATFORM_LABEL[p.platform]}</p>
              <CapBar name="Posts" u={p.posts} /><CapBar name="Replies" u={p.replies} /><CapBar name="Comments" u={p.comments} />
            </div>
          ))}
        </div>
      </section>

      <section className="card flex flex-col gap-4">
        <p className="label">Per day, last {range} days</p>
        {Object.entries(series).map(([p, kinds]) => (
          <div key={p} className="flex flex-col gap-2">
            <p className="text-sm">{PLATFORM_LABEL[p]}</p>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <Bars label="Posts" values={kinds.post} days={days} />
              <Bars label="Replies" values={kinds.reply} days={days} />
              <Bars label="Comments" values={kinds.comment} days={days} />
            </div>
          </div>
        ))}
      </section>

      <section className="card flex flex-col gap-2">
        <p className="label">Followers</p>
        {fl.length ? fl.map(({ p, pts }) => (
          <p key={p} className="text-sm">{PLATFORM_LABEL[p]}: {pts.map((x) => `${x.value} (${x.day.slice(5)})`).join(" → ")}</p>
        )) : <p className="text-sm muted">No follower counts recorded yet.</p>}
      </section>

      <section className="card flex flex-col gap-2">
        <p className="label">Recent activity</p>
        {d.recent.length ? d.recent.map((r, i) => (
          <div key={i} className="flex flex-col border-b border-line/60 pb-2 last:border-0">
            <p className="text-xs muted">{fmt(r.at)} · {PLATFORM_LABEL[r.platform] ?? r.platform} · {r.kind}{r.kind === "inbound" && r.count !== 1 ? ` (${r.count})` : ""}</p>
            {r.url ? <a href={r.url} target="_blank" rel="noreferrer" className="text-sm break-all">{r.url}</a> : <p className="text-sm break-words">{r.detail.slice(0, 140)}</p>}
          </div>
        )) : <p className="text-sm muted">Nothing collected yet.</p>}
      </section>
    </main>
  );
}
