import Link from "next/link";
import { redirect } from "next/navigation";
import { listConversations, newWebConversation } from "@/core/chat";
import { currentCtx } from "@/lib/session";

export const metadata = { title: "Chat" };

async function newChat() {
  "use server";
  const ctx = await currentCtx();
  const c = await newWebConversation(ctx);
  redirect(`/app/chat/${c.id}`);
}

export default async function Chats() {
  const ctx = await currentCtx();
  const convs = await listConversations(ctx);
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="h2">Chat</h1>
        <form action={newChat}><button className="btn btn-sm">New chat</button></form>
      </div>
      {convs.length === 0 && <p className="muted">No chats yet. Start one, or message your Slack bot once connected.</p>}
      <ul className="flex flex-col">
        {convs.map((c) => (
          <li key={c.id}>
            <Link href={`/app/chat/${c.id}`} className="flex items-center justify-between gap-3 py-4 border-b border-line no-underline min-h-14">
              <span className="truncate">{c.title || "Chat"}</span>
              <span className="chip shrink-0">{c.channel}</span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
