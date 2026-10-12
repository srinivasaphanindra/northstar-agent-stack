import { describe, it, expect, beforeEach } from "vitest";
import { createUser, login, userFromSession, logout, resetLoginThrottle } from "@/core/auth";
import { db } from "@/core/db";
import { resetDb } from "./helpers";

describe("auth", () => {
  beforeEach(async () => { await resetDb(); resetLoginThrottle(); });

  it("signs up, stores only a scrypt hash, logs in and resolves the session", async () => {
    const u = await createUser({ email: "Asha@Example.com", password: "a-long-password-1" });
    expect(u.email).toBe("asha@example.com");
    expect(u.passwordHash).toMatch(/^scrypt\$/);
    expect(u.passwordHash).not.toContain("a-long-password-1");
    const { token } = await login("asha@example.com", "a-long-password-1");
    const s = await db.session.findFirst();
    expect(s?.tokenHash).not.toBe(token); // only the hash is stored
    expect((await userFromSession(token))?.id).toBe(u.id);
    await logout(token);
    expect(await userFromSession(token)).toBeNull();
  });

  it("rejects wrong passwords, short passwords and duplicate emails", async () => {
    await createUser({ email: "b@example.com", password: "a-long-password-1" });
    await expect(login("b@example.com", "nope-nope-nope")).rejects.toThrow(/Wrong email or password/);
    await expect(login("nobody@example.com", "a-long-password-1")).rejects.toThrow(/Wrong email or password/);
    await expect(createUser({ email: "c@example.com", password: "short" })).rejects.toThrow(/at least 10/);
    await expect(createUser({ email: "B@example.com", password: "a-long-password-2" })).rejects.toThrow(/already exists/);
  });

  it("expired sessions are refused", async () => {
    await createUser({ email: "d@example.com", password: "a-long-password-1" });
    const { token } = await login("d@example.com", "a-long-password-1");
    await db.session.updateMany({ data: { expiresAt: new Date(Date.now() - 1000) } });
    expect(await userFromSession(token)).toBeNull();
  });

  it("throttles repeated failures", async () => {
    await createUser({ email: "e@example.com", password: "a-long-password-1" });
    for (let i = 0; i < 8; i++) await expect(login("e@example.com", "wrong-password-x")).rejects.toThrow();
    await expect(login("e@example.com", "a-long-password-1")).rejects.toThrow(/Too many attempts/);
  });

  it("emails in ADMIN_EMAILS become platform admins", async () => {
    expect((await createUser({ email: "admin@test.local", password: "a-long-password-1" })).isPlatformAdmin).toBe(true);
    expect((await createUser({ email: "f@example.com", password: "a-long-password-1" })).isPlatformAdmin).toBe(false);
  });
});
