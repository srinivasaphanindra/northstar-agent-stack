// Per-tenant memory: brain notes (business, voice, rules...) read into every agent turn.
import { db } from "./db";
import { AppError } from "./errors";
import { audit, type Actor } from "./audit";

export const SECRET_RE = /(password|passwd|pwd|otp)\s*[:=]?\s*\S{4,}|\b(sk-or-v1-[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|xox[abpr]-[A-Za-z0-9-]{10,}|\d{8,10}:[A-Za-z0-9_-]{30,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})\b/i;
export const looksSecret = (s: string) => SECRET_RE.test(s);

export const SECTIONS = ["business", "location", "goals", "audience", "voice", "platforms", "topics", "competitors", "approvals", "contact", "remembered"] as const;

export async function addNote(workspaceId: string, section: string, content: string, source: string, actor: Actor) {
  const c = content.trim().slice(0, 2000);
  if (!c) throw new AppError("invalid", "Empty note");
  if (looksSecret(c)) throw new AppError("invalid", "That looks like a password or key. I never store those; use Settings → Integrations for keys.");
  const sec = (SECTIONS as readonly string[]).includes(section) ? section : "remembered";
  const n = await db.brainNote.create({ data: { workspaceId, section: sec, content: c, source } });
  await audit(workspaceId, actor, "note.add", sec);
  return n;
}

export async function notesText(workspaceId: string, max = 6000) {
  const notes = await db.brainNote.findMany({ where: { workspaceId }, orderBy: { createdAt: "asc" } });
  const by: Record<string, string[]> = {};
  for (const n of notes) (by[n.section] ??= []).push(n.content);
  const s = Object.entries(by).map(([k, v]) => `## ${k}\n${v.map((x) => `- ${x}`).join("\n")}`).join("\n\n");
  return s.slice(-max);
}
