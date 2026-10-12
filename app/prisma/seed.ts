// Seeds a demo: platform admin, "Client 1 (demo)" (no real client credentials, nothing publishable),
// and a sample workspace waiting for payment. Idempotent. Passwords: random unless SEED_*_PASSWORD is set;
// written to SEED_CREDENTIALS_FILE (chmod 600) when given, else printed once.
import fs from "node:fs";
import crypto from "node:crypto";
import { db } from "../src/core/db";
import { hashPassword } from "../src/core/crypto";
import { createWorkspace } from "../src/core/tenancy";
import { audit } from "../src/core/audit";

const pw = (env: string) => process.env[env] || crypto.randomBytes(12).toString("base64url");

async function user(email: string, name: string, password: string, admin: boolean) {
  const ex = await db.user.findUnique({ where: { email } });
  if (ex) return { u: ex, created: false };
  return { u: await db.user.create({ data: { email, name, passwordHash: hashPassword(password), isPlatformAdmin: admin } }), created: true };
}

async function main() {
  const adminEmail = (process.env.ADMIN_EMAILS || "admin@example.com").split(",")[0].trim().toLowerCase();
  const aPw = pw("SEED_ADMIN_PASSWORD"), dPw = pw("SEED_DEMO_PASSWORD");
  const admin = await user(adminEmail, "Owner", aPw, true);
  const demo = await user("demo@example.com", "Demo Client", dPw, false);

  let ws = await db.workspace.findFirst({ where: { name: "Client 1 (demo)" } });
  if (!ws) {
    ws = await createWorkspace(admin.u.id, { name: "Client 1 (demo)", city: "Hyderabad" });
    await db.workspace.update({ where: { id: ws.id }, data: { plan: "solo", status: "active", onboardingStep: "done" } });
    await db.member.create({ data: { workspaceId: ws.id, userId: demo.u.id, role: "admin" } });
    await db.invoice.create({ data: { workspaceId: ws.id, plan: "solo", amountInr: 1999, status: "paid", method: "upi", reference: "DEMO-NOT-REAL", paidAt: new Date(), markedBy: admin.u.id } });
    const notes: [string, string][] = [
      ["business", "Demo workspace: an AI-assistant studio that helps small businesses in Hyderabad with their online presence (sample data, not a real client)."],
      ["location", "Hyderabad; customers are clinic, salon and coaching owners."],
      ["goals", "More enquiries on WhatsApp; post 3 times a week."],
      ["audience", "Small business owners, 28-50, busy, on their phones."],
      ["voice", "Plain, warm, practical. Short sentences. No hype."],
      ["platforms", "X, LinkedIn, Facebook Page (sample)."],
      ["topics", "Practical tips, behind the scenes. Never: discounts, politics."],
      ["approvals", "Approve every post."],
    ];
    await db.brainNote.createMany({ data: notes.map(([section, content]) => ({ workspaceId: ws!.id, section, content, source: "onboarding" })) });
    await db.onboardingSession.create({ data: { workspaceId: ws.id, step: 10, status: "done", answers: "{}" } });
    const conv = await db.conversation.create({ data: { workspaceId: ws.id, channel: "web", externalId: "demo-chat", title: "LinkedIn tip for clinics" } });
    const t0 = Date.now() - 3600_000;
    await db.message.createMany({ data: [
      { workspaceId: ws.id, conversationId: conv.id, role: "user", content: "Draft a LinkedIn post for clinic owners about replying to WhatsApp enquiries faster", authorUserId: demo.u.id, createdAt: new Date(t0) },
      { workspaceId: ws.id, conversationId: conv.id, role: "assistant", content: "Here's a draft. It's in your Approvals inbox: approve, edit or reject it there. Nothing goes public until you approve.", meta: "{}", createdAt: new Date(t0 + 4000) },
    ] });
    await db.draft.createMany({ data: [
      { workspaceId: ws.id, conversationId: conv.id, text: "Most patients message 2-3 clinics at once. The one that replies first usually gets the booking.\n\nThree quick fixes:\n1. A saved reply with your timings and fees\n2. One person who owns WhatsApp from 9 to 11 am\n3. A 'we'll call you in 10 minutes' promise you can keep", platforms: JSON.stringify(["linkedin"]), createdAt: new Date(t0 + 3000) },
      { workspaceId: ws.id, text: "Small businesses don't need to post every day. Three useful posts a week beat seven rushed ones.", platforms: JSON.stringify(["x", "linkedin"]) },
      { workspaceId: ws.id, text: "Behind the scenes: every post we draft waits for the owner's OK. No surprises on your page.", platforms: JSON.stringify(["facebook"]), status: "approved", decidedBy: admin.u.id, decidedAt: new Date(), publishResult: "Approved. Dry run: not sent to Buffer (PUBLISH_DRY_RUN is on)." },
    ] });
    await audit(ws.id, { type: "system" }, "seed.demo_workspace", ws.name);
  }

  let sample = await db.workspace.findFirst({ where: { name: "Glow Salon (sample)" } });
  if (!sample) {
    sample = await createWorkspace(demo.u.id, { name: "Glow Salon (sample)", city: "Hyderabad" });
    await db.workspace.update({ where: { id: sample.id }, data: { plan: "pro", status: "pending_payment", onboardingStep: "interview" } });
    await db.invoice.create({ data: { workspaceId: sample.id, plan: "pro", amountInr: 3999 } });
  }

  const lines = [
    `# northstar-app demo logins (local only). Created ${new Date().toISOString()}`,
    admin.created ? `ADMIN_EMAIL=${adminEmail}\nADMIN_PASSWORD=${aPw}` : `# admin ${adminEmail} already existed (password unchanged)`,
    demo.created ? `DEMO_EMAIL=demo@example.com\nDEMO_PASSWORD=${dPw}` : "# demo user already existed (password unchanged)",
  ].join("\n");
  const f = process.env.SEED_CREDENTIALS_FILE;
  if (f && (admin.created || demo.created)) { fs.writeFileSync(f, lines + "\n", { mode: 0o600 }); console.log(`seeded; logins written to ${f}`); }
  else console.log(admin.created || demo.created ? lines : "seed: already present, nothing to do");
  await db.$disconnect();
}
main().catch((e) => { console.error(e); process.exit(1); });
