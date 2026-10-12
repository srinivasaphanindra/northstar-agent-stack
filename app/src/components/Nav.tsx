"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

const items = [
  { href: "/app", label: "Home", icon: "◎" },
  { href: "/app/chat", label: "Chat", icon: "✦" },
  { href: "/app/approvals", label: "Approvals", icon: "✓" },
  { href: "/app/settings", label: "Settings", icon: "≡" },
];

export function Nav({ pending, isAdmin }: { pending: number; isAdmin: boolean }) {
  const path = usePathname();
  const active = (h: string) => (h === "/app" ? path === "/app" : path.startsWith(h));
  return (
    <>
      {/* desktop sidebar */}
      <aside className="hidden md:flex flex-col gap-1 w-56 shrink-0 border-r border-line min-h-dvh p-5 sticky top-0">
        <Link href="/app" className="brand mb-8">NORTHSTAR</Link>
        {items.map((i) => (
          <Link key={i.href} href={i.href} className={`flex items-center justify-between rounded-xl px-3 py-2.5 no-underline ${active(i.href) ? "bg-white/10 text-fg" : "text-mute hover:text-fg"}`}>
            <span>{i.label}</span>
            {i.href === "/app/approvals" && pending > 0 && <span className="text-xs bg-white text-black rounded-full px-2">{pending}</span>}
          </Link>
        ))}
        {isAdmin && <Link href="/admin" className="rounded-xl px-3 py-2.5 text-mute hover:text-fg no-underline mt-4">Admin</Link>}
      </aside>
      {/* mobile bottom bar: thumb reach */}
      <nav className="md:hidden fixed bottom-0 inset-x-0 z-30 bg-black/90 backdrop-blur border-t border-line safe-bottom" aria-label="Main">
        <div className="grid grid-cols-4">
          {items.map((i) => (
            <Link key={i.href} href={i.href} className={`relative flex flex-col items-center justify-center h-16 no-underline text-[0.72rem] ${active(i.href) ? "text-fg" : "text-mute"}`}>
              <span className="text-lg leading-none mb-1" aria-hidden>{i.icon}</span>
              {i.label}
              {i.href === "/app/approvals" && pending > 0 && <span className="absolute top-2 right-[calc(50%-22px)] text-[0.65rem] bg-white text-black rounded-full px-1.5 min-w-5 text-center">{pending}</span>}
            </Link>
          ))}
        </div>
      </nav>
    </>
  );
}
