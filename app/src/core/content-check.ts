// Approve-time content check (on top of the free regex check in guardrails.ts):
//  1. "numbers must be in notes": any figure (price, %, count of customers, 5x, 10k...) must appear in the workspace notes.
//  2. LLM classifier (rules.content.llm_check), same rubric as the the example client n8n bot (CONTENT SAFETY CHECK v1).
// The classifier fails open (a human is approving anyway) but the skip is recorded on the draft and in the audit log.
import { config } from "./config";
import { chat, resolveKey } from "./llm";
import { notesText } from "./notes";
import { spendStatus, platformSpendStatus } from "./usage";
import type { Rules } from "./guardrails";

// Port of state-helper guardrails.py NUM_RE / _norm_num.
const NUM_RE = /([₹$]\s?\d[\d,]*(\.\d+)?\s?(k|l|lakh|lakhs|crore|cr|m)?\b|\b\d[\d,]*(\.\d+)?\s?(%|x\b|k\+?(?=\W|$)|(lakh|crore|followers|customers|clients|leads|sales|users|downloads|reviews|stars|orders|bookings|patients)\b))/gi;
const normNum = (s: string) => s.toLowerCase().replace(/[\s,]/g, "");

/** First number-like claim in the text that isn't backed by the notes, else null. */
export function madeUpNumber(text: string, notes: string): string | null {
  const nn = normNum(notes ?? "");
  for (const m of text.matchAll(NUM_RE)) if (!nn.includes(normNum(m[0]))) return m[0].trim().slice(0, 40);
  return null;
}

export const CHECK_TAG = "CONTENT SAFETY CHECK v1";
export function classifierPrompt(text: string, platforms: string[], notes: string, rules: Rules) {
  const c = rules.content;
  return `${CHECK_TAG}. You check ONE social media post for ${platforms.join(", ") || "social media"} right before it is published for a small-business client. Be strict about safety, relaxed about style.
Block it if it contains any of: false or unprovable claims or guarantees; fake reviews or testimonials; numbers or results that are not in the client facts below; hate, harassment or insults; medical, legal or financial advice; ${c.allow_politics ? "" : "politics; "}${c.allow_religion ? "" : "religion; "}or a tone clearly at odds with the voice notes (hype, slogans, corporate buzzwords). Ordinary opinions, tips and questions are fine.
Client facts and voice notes:
<<<
${(notes || "(none)").slice(0, 3500)}
>>>
Text to check:
<<<
${text}
>>>
Return ONLY JSON: {"ok": true|false, "category": "ok|false_claim|fake_review|made_up_number|hate|medical|legal|financial|politics|religion|off_voice|other", "reason": "max 20 plain words", "fix": "max 20 words: how to make it safe"}`;
}

export type Verdict = { ok: boolean; category: string; reason: string; fix: string };
export function parseVerdict(raw: string | null | undefined): Verdict | null {
  const m = String(raw ?? "").match(/\{[\s\S]*\}/);
  if (!m) return null;
  try {
    const v = JSON.parse(m[0]) as Partial<Verdict>;
    if (typeof v !== "object" || v === null || typeof v.ok !== "boolean") return null;
    return { ok: v.ok, category: String(v.category ?? (v.ok ? "ok" : "other")), reason: String(v.reason ?? "").slice(0, 200), fix: String(v.fix ?? "").slice(0, 200) };
  } catch { return null; }
}

export type CheckResult =
  | { ok: true; skipped?: string; model?: string }
  | { ok: false; category: string; reason: string; alternative: string };

export async function approveTimeCheck(workspaceId: string, text: string, platforms: string[], rules: Rules): Promise<CheckResult> {
  const notes = await notesText(workspaceId);
  if (rules.content.numbers_must_be_in_notes) {
    const n = madeUpNumber(text, notes);
    if (n) return { ok: false, category: "made_up_number", reason: `It has a number I can't find in your notes (${n}). I don't post numbers we can't back up.`, alternative: "Post it without the number, or tell the assistant the real figure with 'remember: ...' first." };
  }
  if (!rules.content.llm_check) return { ok: true, skipped: "AI check is off in guardrails" };
  const { key } = await resolveKey(workspaceId);
  if (config.llmMode === "mock" || (config.llmMode === "auto" && !key)) return { ok: true, skipped: "AI check skipped (no AI key / demo mode)" };
  if ((await spendStatus(workspaceId)).level === "stop") return { ok: true, skipped: "AI check skipped (monthly AI budget used up); regex check passed" };
  const { keyMode } = await resolveKey(workspaceId);
  if (keyMode === "platform" && (await platformSpendStatus()).level === "stop") return { ok: true, skipped: "AI check skipped (platform daily AI cap reached); regex check passed" };
  let raw: string | null = null;
  try {
    const r = await chat(workspaceId, [{ role: "user", content: classifierPrompt(text, platforms, notes, rules) }], [], { model: config.checkModel, maxTokens: 200 });
    raw = r.content;
  } catch (e) {
    return { ok: true, skipped: `AI check unavailable (${e instanceof Error ? e.message.slice(0, 80) : "error"}); regex check passed` };
  }
  const v = parseVerdict(raw);
  if (!v) return { ok: true, skipped: "AI check gave no clear answer; regex check passed" };
  if (v.ok) return { ok: true, model: config.checkModel };
  return { ok: false, category: v.category, reason: `AI check (${v.category.replace(/_/g, " ")}): ${v.reason || "this could get the account in trouble."}`, alternative: v.fix || "Rewrite it more plainly, without claims you can't back up." };
}
