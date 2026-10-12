// Buffer GraphQL client (https://api.buffer.com). Only used after a human approval.
import { config } from "./config";

export const BUFFER_SERVICE: Record<string, string> = { x: "twitter", linkedin: "linkedin", facebook: "facebook", instagram: "instagram" };

async function gql<T>(token: string, query: string, variables: Record<string, unknown> = {}): Promise<T> {
  const r = await fetch(config.bufferApiUrl, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ query, variables }),
    signal: AbortSignal.timeout(15000),
  });
  const j = (await r.json().catch(() => ({}))) as { data?: T; errors?: { message: string }[] };
  if (!r.ok || j.errors?.length || !j.data) throw new Error(j.errors?.[0]?.message ?? `Buffer HTTP ${r.status}`);
  return j.data;
}

export type BufferChannel = { id: string; service: string; name: string; isDisconnected?: boolean; isLocked?: boolean };

export async function listChannels(token: string): Promise<BufferChannel[]> {
  const a = await gql<{ account: { organizations: { id: string }[] } }>(token, "query { account { organizations { id name } } }");
  const out: BufferChannel[] = [];
  for (const o of a.account.organizations) {
    const c = await gql<{ channels: BufferChannel[] }>(token, "query($o: OrganizationId!) { channels(input: { organizationId: $o }) { id service name displayName isDisconnected isLocked } }", { o: o.id });
    out.push(...c.channels);
  }
  return out;
}

export type PublishMode = "queue" | "now" | "scheduled";
export type PublishOpts = { mode: PublishMode; dueAt?: Date | null; channelId?: string | null };

/** Buffer createPost input. "scheduled" = customScheduled at dueAt (ISO); "now" = shareNow; else addToQueue. */
export function postInput(channelId: string, text: string, o: PublishOpts) {
  if (o.mode === "scheduled" && !o.dueAt) throw new Error("A scheduled post needs a time");
  const mode = o.mode === "now" ? "shareNow" : o.mode === "scheduled" ? "customScheduled" : "addToQueue";
  return {
    text, channelId, schedulingType: "automatic", mode, saveToDraft: false, aiAssisted: true, source: "northstar-app",
    ...(o.mode === "scheduled" ? { dueAt: o.dueAt!.toISOString() } : {}),
  };
}

/** Picks the channel: the workspace's picked channel for that platform when set (must still be usable), else the first usable one. */
export function pickChannel(chans: BufferChannel[], platform: string, pickedId?: string | null): BufferChannel {
  const service = BUFFER_SERVICE[platform];
  if (!service) throw new Error(`Buffer has no ${platform} channel type`);
  const usable = (c: BufferChannel) => c.service === service && !c.isDisconnected && !c.isLocked;
  if (pickedId) {
    const c = chans.find((x) => x.id === pickedId);
    if (!c || !usable(c)) throw new Error(`The picked ${platform} channel isn't available in Buffer any more; pick another in Settings → Integrations`);
    return c;
  }
  const ch = chans.find(usable);
  if (!ch) throw new Error(`No connected ${platform} channel in Buffer`);
  return ch;
}

/** Sends the text to Buffer on the picked (or first usable) channel of that platform: queue, share now, or at a set time. */
export async function publish(token: string, platform: string, text: string, opts: PublishMode | PublishOpts) {
  const o: PublishOpts = typeof opts === "string" ? { mode: opts } : opts;
  const ch = pickChannel(await listChannels(token), platform, o.channelId);
  const input = postInput(ch.id, text, o);
  const d = await gql<{ createPost: { post?: { id: string; status: string; dueAt: string | null }; message?: string } }>(
    token,
    "mutation CreatePost($input: CreatePostInput!) { createPost(input: $input) { ... on PostActionSuccess { post { id status dueAt } } ... on MutationError { message } } }",
    { input },
  );
  if (!d.createPost.post) throw new Error(d.createPost.message ?? "Buffer refused the post");
  return d.createPost.post;
}
