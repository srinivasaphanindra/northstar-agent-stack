import Link from "next/link";
import { currentCtx } from "@/lib/session";
import { PushStub } from "@/components/PushStub";
import { InstallButton } from "@/components/InstallButton";

export const metadata = { title: "Settings" };
const items = [
  { href: "/app/settings/ai", t: "AI key", d: "Use Northstar's key or your own OpenRouter key" },
  { href: "/app/settings/integrations", t: "Integrations", d: "Buffer, Hootsuite, GoHighLevel, Slack, other tools" },
  { href: "/app/settings/channels", t: "Chat channels", d: "Web and Slack" },
  { href: "/app/settings/guardrails", t: "Guardrails & limits", d: "Daily caps, content rules, AI budget, pause" },
  { href: "/app/settings/members", t: "Team", d: "Owners, admins and members" },
  { href: "/app/settings/billing", t: "Plan & billing", d: "Plan, invoices, usage" },
];

export default async function Settings() {
  const ctx = await currentCtx();
  return (
    <div className="flex flex-col gap-6">
      <h1 className="h2">Settings</h1>
      <ul className="flex flex-col">
        {items.map((i) => (
          <li key={i.href}><Link href={i.href} className="flex items-center justify-between gap-4 py-4 border-b border-line no-underline min-h-16">
            <span><span className="block">{i.t}</span><span className="block text-sm muted">{i.d}</span></span><span className="muted">›</span>
          </Link></li>
        ))}
      </ul>
      <section className="card flex flex-col gap-3">
        <p className="label">This device</p>
        <InstallButton />
        <PushStub />
      </section>
      <section className="flex items-center justify-between">
        <span className="text-sm muted">Signed in as {ctx.user.email} · {ctx.role}</span>
        <form action="/logout" method="post"><button className="btn-ghost btn-sm">Sign out</button></form>
      </section>
    </div>
  );
}
