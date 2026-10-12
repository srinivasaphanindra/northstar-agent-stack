import Link from "next/link";
import { notFound } from "next/navigation";
import { getConversation, listMessages } from "@/core/chat";
import { isAppError } from "@/core/errors";
import { currentCtx } from "@/lib/session";
import { ChatThread } from "@/components/ChatThread";

export default async function ChatPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const ctx = await currentCtx();
  let conv;
  try { conv = await getConversation(ctx, id); } catch (e) { if (isAppError(e)) notFound(); throw e; }
  const msgs = await listMessages(ctx, id);
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-3">
        <Link href="/app/chat" className="chip no-underline" aria-label="Back to chats">←</Link>
        <h1 className="text-lg truncate">{conv.title || "Chat"}</h1>
        <span className="chip">{conv.channel}</span>
      </div>
      <ChatThread conversationId={id} initial={msgs.map((m) => ({ id: m.id, role: m.role, content: m.content, createdAt: m.createdAt.toISOString(), meta: m.meta }))} />
    </div>
  );
}
