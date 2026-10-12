import { describe, it, expect, beforeEach } from "vitest";
import crypto from "node:crypto";
import { seal, open, encryptForWorkspace, decryptForWorkspace, parseKey } from "@/core/crypto";
import { saveIntegration, getSecret, listIntegrations } from "@/core/integrations";
import { db } from "@/core/db";
import { resetDb, makeTenant } from "./helpers";

describe("encryption", () => {
  beforeEach(resetDb);

  it("AES-GCM round trip, tamper and wrong AAD fail", () => {
    const k = crypto.randomBytes(32);
    const c = seal(k, "sk-or-v1-secret", "ws1:integration:openrouter");
    expect(c.startsWith("v1:")).toBe(true);
    expect(open(k, c, "ws1:integration:openrouter")).toBe("sk-or-v1-secret");
    expect(() => open(k, c, "ws2:integration:openrouter")).toThrow();
    const parts = c.split(":");
    const ct = Buffer.from(parts[3], "base64"); ct[0] ^= 1; parts[3] = ct.toString("base64");
    expect(() => open(k, parts.join(":"), "ws1:integration:openrouter")).toThrow();
    expect(() => open(crypto.randomBytes(32), c, "ws1:integration:openrouter")).toThrow();
  });

  it("master key must be 32 bytes", () => {
    expect(() => parseKey("short")).toThrow();
    expect(parseKey(crypto.randomBytes(32).toString("hex")).length).toBe(32);
  });

  it("each workspace has its own data key; secrets are stored encrypted and never listed", async () => {
    const a = await makeTenant("A");
    const b = await makeTenant("B");
    expect(a.ws.dataKeyEnc).not.toBe(b.ws.dataKeyEnc);
    await saveIntegration(a.ctx, "openrouter", "test-openrouter-key-not-real-1234");
    const row = await db.integration.findFirstOrThrow({ where: { workspaceId: a.ws.id } });
    expect(row.secretEnc).not.toContain("sk-or-v1");
    expect(row.last4).toBe("1234");
    expect((await getSecret(a.ws.id, "openrouter"))?.secret).toBe("test-openrouter-key-not-real-1234");
    const listed = JSON.stringify(await listIntegrations(a.ctx));
    expect(listed).not.toContain("sk-or-v1");
    expect(listed).not.toContain(row.secretEnc);
    // ciphertext moved to another tenant does not decrypt
    expect(() => decryptForWorkspace(b.ws.id, b.ws.dataKeyEnc, "integration:openrouter", row.secretEnc)).toThrow();
    // nor does it decrypt as a different purpose in the same tenant
    expect(() => decryptForWorkspace(a.ws.id, a.ws.dataKeyEnc, "integration:buffer", row.secretEnc)).toThrow();
    expect(encryptForWorkspace(a.ws.id, a.ws.dataKeyEnc, "x", "y")).not.toBe(encryptForWorkspace(a.ws.id, a.ws.dataKeyEnc, "x", "y"));
  });
});
