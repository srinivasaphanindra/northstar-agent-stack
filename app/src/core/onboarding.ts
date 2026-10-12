// The 10-question onboarding interview as a chat (same questions as client-onboarding.md).
// Deterministic (no AI needed): one question at a time, 'skip' works, summary + "Anything to fix?".
import { db } from "./db";
import { audit } from "./audit";
import { looksSecret } from "./notes";
import type { Ctx } from "./tenancy";
import { safeJson } from "./integrations";

export const QUESTIONS = [
  { id: "q1", section: "business", q: "What's the business called, and what do you sell?" },
  { id: "q2", section: "location", q: "Which city or area are you in, and who are your customers there?" },
  { id: "q3", section: "goals", q: "What are your main goals for the next 90 days? (more leads, followers, a launch, anything)" },
  { id: "q4", section: "audience", q: "Who is your ideal customer or audience, the people these posts should reach?" },
  { id: "q5", section: "voice", q: "Give me 3 words for your brand voice, and paste (or describe) an example post you like." },
  { id: "q6", section: "platforms", q: "Which platforms do you use? Please share the links: website, X, LinkedIn, Facebook, Instagram, anything else." },
  { id: "q7", section: "topics", q: "What topics should we post about, and is there anything we should never post about?" },
  { id: "q8", section: "competitors", q: "Any competitors or accounts you admire? Handles or links are perfect." },
  { id: "q9", section: "approvals", q: "How do you want approvals: approve every post yourself, or let routine stuff go out automatically? (For now every post needs your approval.)" },
  { id: "q10", section: "contact", q: "Last one: best way to reach you, and your working hours?" },
] as const;

const SKIP = /^(skip|pass|not sure|later|na|n\/a|-)$/i;
const DONE = /^(looks good|done|ok|okay|all good|lgtm|perfect|yes|finish)\.?!?$/i;

async function conv(ctx: Ctx) {
  return db.conversation.upsert({
    where: { workspaceId_channel_externalId: { workspaceId: ctx.workspaceId, channel: "web", externalId: "onboarding" } },
    create: { workspaceId: ctx.workspaceId, channel: "web", externalId: "onboarding", title: "Onboarding interview" },
    update: {},
  });
}

async function bot(ctx: Ctx, conversationId: string, content: string) {
  await db.message.create({ data: { workspaceId: ctx.workspaceId, conversationId, role: "assistant", content, meta: JSON.stringify({ onboarding: true }) } });
}

export function summary(answers: Record<string, string>) {
  return QUESTIONS.map((q, i) => `${i + 1}. ${q.section}: ${answers[q.id] ? answers[q.id] : "(skipped)"}`).join("\n");
}

export async function startOnboarding(ctx: Ctx) {
  const c = await conv(ctx);
  const existing = await db.onboardingSession.findUnique({ where: { workspaceId: ctx.workspaceId } });
  if (existing && existing.status !== "done") return { conversationId: c.id, session: existing };
  const s = await db.onboardingSession.upsert({ where: { workspaceId: ctx.workspaceId }, create: { workspaceId: ctx.workspaceId }, update: { step: 0, answers: "{}", status: "active" } });
  await bot(ctx, c.id, `Hi! I'll ask 10 short questions so I can write like you. One at a time; say 'skip' for any you'd rather not answer.\n\n1/10: ${QUESTIONS[0].q}`);
  await audit(ctx.workspaceId, { type: "user", userId: ctx.userId }, "onboarding.start");
  return { conversationId: c.id, session: s };
}

export async function answerOnboarding(ctx: Ctx, textRaw: string) {
  const c = await conv(ctx);
  const s = await db.onboardingSession.findUnique({ where: { workspaceId: ctx.workspaceId } });
  if (!s || s.status === "done") return { done: true, conversationId: c.id };
  const text = textRaw.trim().slice(0, 2000);
  if (!text) return { done: false, conversationId: c.id };
  await db.message.create({ data: { workspaceId: ctx.workspaceId, conversationId: c.id, role: "user", content: text, authorUserId: ctx.userId, meta: JSON.stringify({ onboarding: true }) } });
  const answers = safeJson<Record<string, string>>(s.answers, {});

  if (looksSecret(text)) {
    await bot(ctx, c.id, "That looks like a password or key, so I didn't save it. I never need those. Please answer the question without it, or say 'skip'.");
    return { done: false, conversationId: c.id };
  }

  if (s.status === "review") {
    if (DONE.test(text)) {
      await finish(ctx, answers);
      await bot(ctx, c.id, "Saved. I'll use this every time I write for you. Next: connect your tools.");
      return { done: true, conversationId: c.id };
    }
    const fix = text.match(/^(?:q)?(\d{1,2})\s*[:.)-]\s*(.+)$/is);
    if (fix && Number(fix[1]) >= 1 && Number(fix[1]) <= 10) {
      answers[QUESTIONS[Number(fix[1]) - 1].id] = fix[2].trim();
    } else {
      answers.extra = [answers.extra, text].filter(Boolean).join(" | ");
    }
    await db.onboardingSession.update({ where: { id: s.id }, data: { answers: JSON.stringify(answers) } });
    await bot(ctx, c.id, `Updated:\n${summary(answers)}${answers.extra ? `\nExtra: ${answers.extra}` : ""}\n\nAnything else to fix? Say 'looks good' to finish.`);
    return { done: false, conversationId: c.id };
  }

  const q = QUESTIONS[s.step];
  if (!SKIP.test(text)) answers[q.id] = text;
  const next = s.step + 1;
  if (next >= QUESTIONS.length) {
    await db.onboardingSession.update({ where: { id: s.id }, data: { step: next, answers: JSON.stringify(answers), status: "review" } });
    await bot(ctx, c.id, `Thanks! Here's what I have:\n${summary(answers)}\n\nAnything to fix? Reply like '3: get 20 leads a month', or say 'looks good'.`);
  } else {
    await db.onboardingSession.update({ where: { id: s.id }, data: { step: next, answers: JSON.stringify(answers) } });
    await bot(ctx, c.id, `${next + 1}/10: ${QUESTIONS[next].q}`);
  }
  return { done: false, conversationId: c.id };
}

async function finish(ctx: Ctx, answers: Record<string, string>) {
  await db.$transaction([
    db.brainNote.deleteMany({ where: { workspaceId: ctx.workspaceId, source: "onboarding" } }),
    db.brainNote.createMany({
      data: [
        ...QUESTIONS.filter((q) => answers[q.id]).map((q) => ({ workspaceId: ctx.workspaceId, section: q.section, content: answers[q.id], source: "onboarding" })),
        ...(answers.extra ? [{ workspaceId: ctx.workspaceId, section: "remembered", content: answers.extra, source: "onboarding" }] : []),
      ],
    }),
    db.onboardingSession.update({ where: { workspaceId: ctx.workspaceId }, data: { status: "done" } }),
    db.workspace.update({ where: { id: ctx.workspaceId }, data: { onboardingStep: "integrations" } }),
  ]);
  await audit(ctx.workspaceId, { type: "user", userId: ctx.userId }, "onboarding.done", "", { answered: Object.keys(answers).length });
}

export async function onboardingState(ctx: Ctx) {
  const s = await db.onboardingSession.findUnique({ where: { workspaceId: ctx.workspaceId } });
  const c = await db.conversation.findUnique({ where: { workspaceId_channel_externalId: { workspaceId: ctx.workspaceId, channel: "web", externalId: "onboarding" } } });
  const messages = c ? await db.message.findMany({ where: { workspaceId: ctx.workspaceId, conversationId: c.id }, orderBy: { createdAt: "asc" } }) : [];
  return { session: s, messages };
}

export async function setOnboardingStep(ctx: Ctx, step: "plan" | "interview" | "integrations" | "channel" | "done") {
  await db.workspace.update({ where: { id: ctx.workspaceId }, data: { onboardingStep: step } });
  await audit(ctx.workspaceId, { type: "user", userId: ctx.userId }, "onboarding.step", step);
}
