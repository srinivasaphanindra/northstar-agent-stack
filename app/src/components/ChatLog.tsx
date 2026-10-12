export type Msg = { id: string; role: string; content: string; createdAt: string | Date };

export function Bubble({ m }: { m: Msg }) {
  const mine = m.role === "user";
  return (
    <div className={`flex ${mine ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-[85%] md:max-w-[70%] whitespace-pre-wrap break-words rounded-2xl px-4 py-3 text-[0.97rem] ${mine ? "bg-white text-black rounded-br-md" : "bg-[#141414] border border-line rounded-bl-md"}`}>
        {m.content}
      </div>
    </div>
  );
}
