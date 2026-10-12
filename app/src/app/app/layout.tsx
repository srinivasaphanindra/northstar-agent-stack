import Link from "next/link";
import { db } from "@/core/db";
import { currentCtx, currentWorkspace } from "@/lib/session";
import { Nav } from "@/components/Nav";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const ctx = await currentCtx();
  const ws = await currentWorkspace(ctx);
  const pending = await db.draft.count({ where: { workspaceId: ctx.workspaceId, status: "pending" } });
  return (
    <div className="md:flex">
      <Nav pending={pending} isAdmin={ctx.isPlatformAdmin} />
      <div className="flex-1 min-w-0">
        <header className="sticky top-0 z-20 bg-black/85 backdrop-blur border-b border-line">
          <div className="max-w-4xl mx-auto px-5 h-14 flex items-center justify-between gap-3">
            <Link href="/app" className="md:hidden brand">NORTHSTAR</Link>
            <span className="text-sm text-soft truncate">{ws.name}</span>
            <span className="flex items-center gap-2 shrink-0">
              {ws.paused && <span className="pill-bad">paused</span>}
              {ws.status !== "active" && <Link href="/app/settings/billing" className="pill-warn no-underline">payment pending</Link>}
              {ws.onboardingStep !== "done" && <Link href="/onboarding" className="chip no-underline">finish setup</Link>}
            </span>
          </div>
        </header>
        <main className="max-w-4xl mx-auto px-5 py-6 pb-nav">{children}</main>
      </div>
    </div>
  );
}
