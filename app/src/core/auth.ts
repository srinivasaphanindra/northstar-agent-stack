// Email + password accounts with DB-backed sessions (token in an httpOnly cookie, only its sha256 in the DB).
// Framework-free: the Next.js layer only reads/writes the cookie.
import { z } from "zod";
import { db } from "./db";
import { config } from "./config";
import { AppError } from "./errors";
import { audit } from "./audit";
import { hashPassword, verifyPassword, randomToken, sha256 } from "./crypto";

export const SESSION_COOKIE = "ns_session";

const emailSchema = z.string().trim().toLowerCase().email().max(200);
const passwordSchema = z.string().min(10, "Password must be at least 10 characters").max(200);

export async function createUser(input: { email: string; password: string; name?: string }) {
  const email = emailSchema.parse(input.email);
  const pw = passwordSchema.safeParse(input.password);
  if (!pw.success) throw new AppError("invalid", pw.error.issues[0]?.message ?? "Invalid password");
  const exists = await db.user.findUnique({ where: { email } });
  if (exists) throw new AppError("conflict", "An account with this email already exists");
  const user = await db.user.create({
    data: {
      email,
      name: (input.name ?? "").trim().slice(0, 100),
      passwordHash: hashPassword(pw.data),
      isPlatformAdmin: config.adminEmails.includes(email),
    },
  });
  await audit(null, { type: "user", userId: user.id }, "user.signup", email);
  return user;
}

// Small in-process throttle: 8 failed tries per email per 15 minutes.
const failures = new Map<string, { n: number; until: number }>();
export function resetLoginThrottle() { failures.clear(); }

export async function login(emailRaw: string, password: string, ip = "") {
  const parsed = emailSchema.safeParse(emailRaw);
  if (!parsed.success) throw new AppError("unauthorized", "Wrong email or password");
  const email = parsed.data;
  const f = failures.get(email);
  if (f && f.n >= 8 && f.until > Date.now()) throw new AppError("limit", "Too many attempts. Try again in 15 minutes.");
  const user = await db.user.findUnique({ where: { email } });
  const ok = user ? verifyPassword(password, user.passwordHash) : (verifyPassword(password, DUMMY_HASH), false);
  if (!user || !ok) {
    const cur = f && f.until > Date.now() ? f : { n: 0, until: Date.now() + 15 * 60_000 };
    failures.set(email, { n: cur.n + 1, until: cur.until });
    await audit(null, { type: "system" }, "auth.login_failed", email);
    throw new AppError("unauthorized", "Wrong email or password");
  }
  failures.delete(email);
  const token = await createSession(user.id, ip);
  await audit(null, { type: "user", userId: user.id }, "auth.login", email);
  return { user, token };
}
const DUMMY_HASH = hashPassword("timing-equaliser-not-a-password");

export async function createSession(userId: string, ip = "") {
  const token = randomToken(32);
  await db.session.create({
    data: { userId, tokenHash: sha256(token), ip: ip.slice(0, 64), expiresAt: new Date(Date.now() + config.sessionDays * 86400_000) },
  });
  return token;
}

export async function userFromSession(token: string | undefined | null) {
  if (!token) return null;
  const s = await db.session.findUnique({ where: { tokenHash: sha256(token) }, include: { user: true } });
  if (!s) return null;
  if (s.expiresAt < new Date()) {
    await db.session.delete({ where: { id: s.id } }).catch(() => {});
    return null;
  }
  return s.user;
}

export async function logout(token: string | undefined | null) {
  if (!token) return;
  await db.session.deleteMany({ where: { tokenHash: sha256(token) } });
}

export async function changePassword(userId: string, current: string, next: string) {
  const user = await db.user.findUniqueOrThrow({ where: { id: userId } });
  if (!verifyPassword(current, user.passwordHash)) throw new AppError("unauthorized", "Current password is wrong");
  const pw = passwordSchema.safeParse(next);
  if (!pw.success) throw new AppError("invalid", pw.error.issues[0]?.message ?? "Invalid password");
  await db.user.update({ where: { id: userId }, data: { passwordHash: hashPassword(pw.data) } });
  await db.session.deleteMany({ where: { userId } });
  await audit(null, { type: "user", userId }, "auth.password_changed");
}
