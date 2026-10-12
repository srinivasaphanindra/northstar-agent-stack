// Slack bot harness: the real connector code against a local fake Slack API (no token, no network).
import { describe, it, expect, beforeAll, afterAll, beforeEach } from "vitest";
import { db } from "@/core/db";
import { saveIntegration, testIntegration } from "@/core/integrations";
import { connectSlackChannel, notifyDraft } from "@/core/channels";
import { pollSlackOnce } from "@/core/channels/slack";
import { claimNext } from "@/core/jobs";
import { runAgentReply } from "@/core/agent";
import { resetDb, makeTenant } from "./helpers";
import { startFakeSlack, type FakeSlack } from "./harness/fake-slack";

let fake: FakeSlack;
beforeAll(async () => { fake = await startFakeSlack(); process.env.SLACK_API_URL = fake.url; });
afterAll(async () => { await fake.close(); delete process.env.SLACK_API_URL; });

async function setup() {
  const t = await makeTenant("Slack Salon");
  await saveIntegration(t.ctx, "slack", fake.token, { approvers: "UOWNER" });
  await connectSlackChannel(t.ctx, "CHARNESS1");
  return t;
}

describe("slack bot harness", () => {
  beforeEach(async () => { await resetDb(); fake.channels.clear(); fake.posted.length = 0; process.env.PUBLISH_DRY_RUN = "true"; });

  it("Save & test uses auth.test", async () => {
    const t = await setup();
    const r = await testIntegration(t.ctx, "slack");
    expect(r).toEqual({ ok: true, note: "Signed in to Harness as northstar_bot" });
  });

  it("message -> agent draft -> Slack notice -> typed approve (approver only) -> dry-run approval", async () => {
    const t = await setup();
    fake.say("CHARNESS1", "UOWNER", "draft a linkedin post about our weekend hair spa offer");
    expect(await pollSlackOnce(t.ws.id)).toBe(1);
    const conv = await db.conversation.findFirstOrThrow({ where: { workspaceId: t.ws.id, channel: "slack" } });
    const job = await claimNext(["agent.reply"]);
    expect(job).toBeTruthy();
    const msg = await db.message.findFirstOrThrow({ where: { conversationId: conv.id, role: "user" } });
    await runAgentReply(t.ws.id, conv.id, msg.id);
    const d = await db.draft.findFirstOrThrow({ where: { workspaceId: t.ws.id } });
    await notifyDraft(t.ws.id, d.id);
    const code = d.id.slice(-6);
    expect(fake.posted.at(-1)?.text).toContain(`approve ${code}`);

    // the bot's own messages are never treated as input
    expect(await pollSlackOnce(t.ws.id)).toBe(0);

    fake.say("CHARNESS1", "USTRANGER", `approve ${code}`);
    await pollSlackOnce(t.ws.id);
    expect(fake.posted.at(-1)?.text).toMatch(/Only the approvers/);
    expect((await db.draft.findUniqueOrThrow({ where: { id: d.id } })).status).toBe("pending");

    fake.say("CHARNESS1", "UOWNER", `approve ${code}`);
    await pollSlackOnce(t.ws.id);
    expect(fake.posted.at(-1)?.text).toMatch(/Dry run/);
    expect((await db.draft.findUniqueOrThrow({ where: { id: d.id } })).status).toBe("approved");
    const acts = (await db.auditLog.findMany({ where: { workspaceId: t.ws.id } })).map((a) => a.action);
    expect(acts).toEqual(expect.arrayContaining(["channel.slack_connected", "draft.approve", "draft.publish_dry_run"]));
  });

  it("typed reject from an approver rejects; unknown code is reported", async () => {
    const t = await setup();
    const d = await db.draft.create({ data: { workspaceId: t.ws.id, text: "Hello from the salon.", platforms: '["linkedin"]' } });
    fake.say("CHARNESS1", "UOWNER", "approve zzzzzz9");
    await pollSlackOnce(t.ws.id);
    expect(fake.posted.at(-1)?.text).toMatch(/can't find a pending draft/);
    fake.say("CHARNESS1", "UOWNER", `reject ${d.id.slice(-6)}`);
    await pollSlackOnce(t.ws.id);
    expect(fake.posted.at(-1)?.text).toMatch(/Rejected/);
    expect((await db.draft.findUniqueOrThrow({ where: { id: d.id } })).status).toBe("rejected");
  });

  it("a wrong token fails cleanly", async () => {
    const t = await makeTenant("Bad token");
    await saveIntegration(t.ctx, "slack", "xoxb-wrong", {});
    const r = await testIntegration(t.ctx, "slack");
    expect(r.ok).toBe(false);
    expect(r.note).toMatch(/invalid_auth/);
  });
});
