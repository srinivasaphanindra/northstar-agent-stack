// The agent loop (runs in the worker): notes + rules -> model -> tools -> reply.
// Tools can only draft and take notes. There is deliberately no tool that approves or publishes.
import { db } from "./db";
import { audit } from "./audit";
import { chat, resolveKey, type ChatMsg, type ToolDef } from "./llm";
import { proposeDraft } from "./approvals";
import { addNote, notesText, looksSecret } from "./notes";
import { rulesFor, rulesSummary } from "./guardrails";
import { spendStatus, platformSpendStatus } from "./usage";

export const TOOLS: ToolDef[] = [
  {
    type: "function",
    function: {
      name: "propose_post",
      description: "Save a social post as a DRAFT for the owner to approve. It is NOT published. Use when the user wants a post written.",
      parameters: {
        type: "object",
        properties: {
          text: { type: "string", description: "Final post text in the client's voice" },
          platforms: { type: "array", items: { type: "string", enum: ["x", "linkedin", "facebook", "instagram"] } },
          when: { type: "string", enum: ["queue", "now"], description: "queue (default) = Buffer queue after approval" },
        },
        required: ["text", "platforms"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "save_note",
      description: "Remember a fact or rule about the business (never passwords or keys).",
      parameters: { type: "object", properties: { section: { type: "string", enum: ["business", "audience", "voice", "topics", "rules", "remembered"] }, content: { type: "string" } }, required: ["content"] },
    },
  },
  {
    type: "function",
    function: { name: "list_pending_drafts", description: "List drafts waiting for approval.", parameters: { type: "object", properties: {} } },
  },
];

export const TOOL_NAMES = TOOLS.map((t) => t.function.name);

async function systemPrompt(workspaceId: string) {
  const ws = await db.workspace.findUniqueOrThrow({ where: { id: workspaceId } });
  const rules = await rulesFor(workspaceId);
  const notes = await notesText(workspaceId);
  return [
    `You are the Northstar social media assistant for "${ws.name}"${ws.city ? ` in ${ws.city}` : ""}. You help with ${rules.scope.in_scope}: drafting posts, ideas, planning and answering questions about their social media.`,
    "Rules: you never publish anything yourself. When asked for a post, call propose_post: it saves a draft that the owner approves in the Approvals inbox. Never ask for or accept passwords. Don't invent numbers, reviews or testimonials. If something crosses a limit, explain why in plain words and offer a safe alternative; never just say no. If a request is outside social media, say so in one friendly line.",
    `Limits: ${rulesSummary(rules)}`,
    "Style: short, plain, friendly. Indian English is fine. Use the client's voice from the notes below.",
    `Business notes:\n${notes || "(none yet: suggest finishing the onboarding interview)"}`,
  ].join("\n\n");
}

async function runTool(workspaceId: string, conversationId: string, name: string, argsRaw: string): Promise<unknown> {
  let args: Record<string, unknown> = {};
  try { args = JSON.parse(argsRaw || "{}"); } catch { return { ok: false, reason: "bad tool arguments", alternative: "try again" }; }
  switch (name) {
    case "propose_post":
      return proposeDraft(workspaceId, { text: String(args.text ?? ""), platforms: Array.isArray(args.platforms) ? (args.platforms as string[]) : [], conversationId, mode: args.when === "now" ? "now" : "queue" }, { type: "agent" });
    case "save_note":
      try {
        await addNote(workspaceId, String(args.section ?? "remembered"), String(args.content ?? ""), "chat", { type: "agent" });
        return { ok: true, saved: true };
      } catch (e) {
        return { ok: false, reason: e instanceof Error ? e.message : "could not save", alternative: "" };
      }
    case "list_pending_drafts": {
      const d = await db.draft.findMany({ where: { workspaceId, status: "pending" }, orderBy: { createdAt: "desc" }, take: 10 });
      return { ok: true, drafts: d.map((x) => ({ id: x.id, text: x.text.slice(0, 200), platforms: x.platforms })) };
    }
    default:
      return { ok: false, reason: `unknown tool ${name}`, alternative: "" };
  }
}

async function say(workspaceId: string, conversationId: string, content: string, meta: Record<string, unknown> = {}) {
  return db.message.create({ data: { workspaceId, conversationId, role: "assistant", content, meta: JSON.stringify(meta) } });
}

export async function runAgentReply(workspaceId: string, conversationId: string, messageId: string) {
  const conv = await db.conversation.findFirst({ where: { id: conversationId, workspaceId } });
  const msg = await db.message.findFirst({ where: { id: messageId, workspaceId, conversationId } });
  if (!conv || !msg) throw new Error("conversation/message not found for this workspace");

  if (looksSecret(msg.content)) {
    await audit(workspaceId, { type: "agent" }, "chat.secret_warning", conversationId);
    return say(workspaceId, conversationId, "That looks like a password or key. Please don't share those in chat; I never need them. Add keys in Settings → Integrations, where they're encrypted.", { guard: "secret" });
  }
  const spend = await spendStatus(workspaceId);
  if (spend.level === "stop") {
    return say(workspaceId, conversationId, "This month's AI budget is used up, so I'm paused until next month. An owner can raise the budget in Settings → Guardrails or switch to their own OpenRouter key.", { guard: "budget" });
  }
  const { keyMode } = await resolveKey(workspaceId);
  if (keyMode === "platform") {
    const plat = await platformSpendStatus();
    if (plat.level === "stop") {
      await audit(workspaceId, { type: "system" }, "platform.daily_ai_cap", "stop", { used: plat.used, cap: plat.cap, calls: plat.calls });
      return say(workspaceId, conversationId, "Northstar's shared AI budget for today is used up, so I'm paused until tomorrow (IST). An owner can switch to their own OpenRouter key in Settings → AI key to keep chatting.", { guard: "platform_daily_cap", used: plat.used, cap: plat.cap });
    }
  }

  const history = await db.message.findMany({ where: { workspaceId, conversationId, role: { in: ["user", "assistant"] }, createdAt: { lte: msg.createdAt } }, orderBy: { createdAt: "desc" }, take: 20 });
  const messages: ChatMsg[] = [{ role: "system", content: await systemPrompt(workspaceId) }];
  for (const h of history.reverse()) messages.push({ role: h.role as "user" | "assistant", content: h.content });

  const draftIds: string[] = [];
  let mock = false;
  for (let i = 0; i < 4; i++) {
    const r = await chat(workspaceId, messages, TOOLS);
    mock = mock || r.mock;
    if (!r.toolCalls.length) {
      let text = (r.content ?? "").trim() || "Sorry, I didn't get that. Could you say it another way?";
      if (spend.level === "warn") text += `\n\n(Heads-up: ${Math.round(spend.pct)}% of this month's AI budget is used.)`;
      return say(workspaceId, conversationId, text, { draftIds, mock });
    }
    messages.push({ role: "assistant", content: r.content ?? null, tool_calls: r.toolCalls });
    for (const tc of r.toolCalls) {
      const out = await runTool(workspaceId, conversationId, tc.function.name, tc.function.arguments);
      const o = out as { ok?: boolean; draftId?: string };
      if (o.ok && o.draftId) draftIds.push(o.draftId);
      await db.message.create({ data: { workspaceId, conversationId, role: "tool", content: JSON.stringify(out).slice(0, 4000), meta: JSON.stringify({ tool: tc.function.name }) } });
      messages.push({ role: "tool", tool_call_id: tc.id, content: JSON.stringify(out) });
    }
  }
  return say(workspaceId, conversationId, draftIds.length ? "Your draft is in the Approvals inbox." : "I got stuck on that one. Could you rephrase?", { draftIds, mock });
}
