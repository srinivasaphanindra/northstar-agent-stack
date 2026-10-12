// OpenRouter client (OpenAI-compatible chat completions with tool calls) + an offline mock.
// Key resolution: workspace BYO key (encrypted) when aiKeyMode = byok, else the platform key from env.
import { db } from "./db";
import { config } from "./config";
import { getSecret } from "./integrations";
import { recordLlm } from "./usage";

export type ChatMsg =
  | { role: "system" | "user"; content: string }
  | { role: "assistant"; content: string | null; tool_calls?: ToolCall[] }
  | { role: "tool"; tool_call_id: string; content: string };
export type ToolCall = { id: string; type: "function"; function: { name: string; arguments: string } };
export type ToolDef = { type: "function"; function: { name: string; description: string; parameters: Record<string, unknown> } };
export type LlmReply = { content: string | null; toolCalls: ToolCall[]; mock: boolean };

export async function resolveKey(workspaceId: string): Promise<{ key: string | null; keyMode: "platform" | "byok" }> {
  const ws = await db.workspace.findUniqueOrThrow({ where: { id: workspaceId }, select: { aiKeyMode: true } });
  if (ws.aiKeyMode === "byok") {
    const s = await getSecret(workspaceId, "openrouter");
    return { key: s?.secret ?? null, keyMode: "byok" };
  }
  return { key: config.platformOpenrouterKey || null, keyMode: "platform" };
}

export async function chat(workspaceId: string, messages: ChatMsg[], tools: ToolDef[], opts: { model?: string; maxTokens?: number } = {}): Promise<LlmReply> {
  const { key, keyMode } = await resolveKey(workspaceId);
  const mode = config.llmMode;
  if (mode === "mock" || (mode === "auto" && !key)) {
    const r = mockChat(messages, tools);
    await recordLlm(workspaceId, { model: "mock", keyMode, inputTokens: 0, outputTokens: 0, costUsd: 0 });
    return r;
  }
  if (!key) throw new Error(keyMode === "byok" ? "Your OpenRouter key is missing; add it in Settings → AI key" : "No platform AI key is configured");
  const model = opts.model ?? config.defaultModel;
  const r = await fetch(`${config.openrouterBase}/chat/completions`, {
    method: "POST",
    headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json", "HTTP-Referer": config.appUrl, "X-Title": "Northstar Agents" },
    body: JSON.stringify({ model, messages, tools: tools.length ? tools : undefined, temperature: 0.5, max_tokens: opts.maxTokens ?? 1200, usage: { include: true } }),
    signal: AbortSignal.timeout(60_000),
  });
  const j = (await r.json().catch(() => ({}))) as {
    choices?: { message?: { content?: string | null; tool_calls?: ToolCall[] } }[];
    usage?: { prompt_tokens?: number; completion_tokens?: number; cost?: number };
    error?: { message?: string };
  };
  if (!r.ok || !j.choices?.length) throw new Error(`AI call failed: ${j.error?.message ?? r.status}`);
  await recordLlm(workspaceId, { model, keyMode, inputTokens: j.usage?.prompt_tokens ?? 0, outputTokens: j.usage?.completion_tokens ?? 0, costUsd: j.usage?.cost ?? null });
  const m = j.choices[0].message ?? {};
  return { content: m.content ?? null, toolCalls: m.tool_calls ?? [], mock: false };
}

/** Deterministic stand-in so the whole flow works (and is testable) without any AI key. */
export function mockChat(messages: ChatMsg[], tools: ToolDef[]): LlmReply {
  const last = messages[messages.length - 1];
  const names = new Set(tools.map((t) => t.function.name));
  if (last.role === "tool") {
    let parsed: { ok?: boolean; reason?: string; alternative?: string } = {};
    try { parsed = JSON.parse(last.content); } catch { /* ignore */ }
    const content = parsed.ok === false
      ? `I couldn't do that: ${parsed.reason} ${parsed.alternative ?? ""}`.trim()
      : "Done. The draft is in your Approvals inbox: approve, edit or reject it there (or with the buttons in your chat app). Nothing goes public until you approve.";
    return { content, toolCalls: [], mock: true };
  }
  const text = last.role === "user" ? last.content : "";
  const rem = text.match(/^\s*remember[:\s]+(.+)/i);
  if (rem && names.has("save_note"))
    return { content: null, toolCalls: [{ id: "mock_1", type: "function", function: { name: "save_note", arguments: JSON.stringify({ section: "remembered", content: rem[1] }) } }], mock: true };
  if (/\b(post|draft|write|tweet|caption)\b/i.test(text) && names.has("propose_post")) {
    const plats = ["x", "linkedin", "facebook", "instagram"].filter((p) => new RegExp(p === "x" ? "\\b(x|twitter|tweet)\\b" : p, "i").test(text));
    const topic = text.replace(/^(please\s+)?(draft|write|make|create)\s+(?:(?:an|a|one)\s+)?/i, "").replace(/\b(post|for|on|about|x|twitter|linkedin|facebook|instagram)\b/gi, " ").replace(/\s+/g, " ").trim().slice(0, 120);
    const draft = `Quick tip from our team: ${topic || "small, steady posts beat big bursts"}. Questions? Message us, we reply within the day.`;
    return { content: null, toolCalls: [{ id: "mock_1", type: "function", function: { name: "propose_post", arguments: JSON.stringify({ text: draft, platforms: plats.length ? plats : ["linkedin"] }) } }], mock: true };
  }
  return {
    content: "(Demo mode: no AI key is set yet, so I'm answering with fixed replies.) I can draft posts for X, LinkedIn, Facebook or Instagram, keep notes about your business ('remember: ...'), and send drafts to your Approvals inbox. Try: 'draft a LinkedIn post about our weekend offer'.",
    toolCalls: [], mock: true,
  };
}
