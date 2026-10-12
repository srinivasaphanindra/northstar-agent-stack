// Envelope encryption. APP_MASTER_KEY (32 bytes, base64 or hex, env only) wraps one random data key per
// workspace (Workspace.dataKeyEnc). Secrets are AES-256-GCM encrypted with that data key, and the
// workspace id + purpose are bound in as AAD, so a ciphertext copied to another tenant or field fails to decrypt.
import crypto from "node:crypto";
import { config } from "./config";

const PREFIX = "v1";

export function parseKey(raw: string): Buffer {
  const s = raw.trim();
  let buf: Buffer | null = null;
  if (/^[0-9a-fA-F]{64}$/.test(s)) buf = Buffer.from(s, "hex");
  else {
    try { buf = Buffer.from(s, "base64"); } catch { buf = null; }
  }
  if (!buf || buf.length !== 32) throw new Error("APP_MASTER_KEY must be 32 bytes (64 hex chars or base64)");
  return buf;
}

export function masterKey(): Buffer {
  const raw = config.masterKey;
  if (!raw) throw new Error("APP_MASTER_KEY is not set");
  return parseKey(raw);
}

export function seal(key: Buffer, plaintext: string, aad: string): string {
  const iv = crypto.randomBytes(12);
  const c = crypto.createCipheriv("aes-256-gcm", key, iv);
  c.setAAD(Buffer.from(aad, "utf8"));
  const ct = Buffer.concat([c.update(plaintext, "utf8"), c.final()]);
  const tag = c.getAuthTag();
  return [PREFIX, iv.toString("base64"), tag.toString("base64"), ct.toString("base64")].join(":");
}

export function open(key: Buffer, sealed: string, aad: string): string {
  const [v, iv, tag, ct] = sealed.split(":");
  if (v !== PREFIX || !iv || !tag || ct === undefined) throw new Error("bad ciphertext format");
  const d = crypto.createDecipheriv("aes-256-gcm", key, Buffer.from(iv, "base64"));
  d.setAAD(Buffer.from(aad, "utf8"));
  d.setAuthTag(Buffer.from(tag, "base64"));
  return Buffer.concat([d.update(Buffer.from(ct, "base64")), d.final()]).toString("utf8");
}

/** New random data key for a workspace, returned wrapped with the master key. */
export function newWrappedDataKey(workspaceId: string): string {
  const dk = crypto.randomBytes(32);
  return seal(masterKey(), dk.toString("base64"), `dek:${workspaceId}`);
}

export function unwrapDataKey(workspaceId: string, wrapped: string): Buffer {
  return Buffer.from(open(masterKey(), wrapped, `dek:${workspaceId}`), "base64");
}

export function encryptForWorkspace(workspaceId: string, wrappedDk: string, purpose: string, plaintext: string): string {
  return seal(unwrapDataKey(workspaceId, wrappedDk), plaintext, `${workspaceId}:${purpose}`);
}

export function decryptForWorkspace(workspaceId: string, wrappedDk: string, purpose: string, sealed: string): string {
  return open(unwrapDataKey(workspaceId, wrappedDk), sealed, `${workspaceId}:${purpose}`);
}

export function last4(secret: string): string {
  const s = secret.trim();
  return s.length <= 4 ? "****" : s.slice(-4);
}

export function sha256(s: string): string {
  return crypto.createHash("sha256").update(s).digest("hex");
}

export function randomToken(bytes = 32): string {
  return crypto.randomBytes(bytes).toString("base64url");
}

// Passwords: scrypt (node built-in, no native deps). Format: scrypt$N$r$p$salt$hash
const SCRYPT = { N: 16384, r: 8, p: 1, keylen: 64 };
export function hashPassword(pw: string): string {
  const salt = crypto.randomBytes(16);
  const h = crypto.scryptSync(pw, salt, SCRYPT.keylen, { N: SCRYPT.N, r: SCRYPT.r, p: SCRYPT.p });
  return `scrypt$${SCRYPT.N}$${SCRYPT.r}$${SCRYPT.p}$${salt.toString("base64")}$${h.toString("base64")}`;
}
export function verifyPassword(pw: string, stored: string): boolean {
  const parts = stored.split("$");
  if (parts.length !== 6 || parts[0] !== "scrypt") return false;
  const [, N, r, p, salt, hash] = parts;
  const expected = Buffer.from(hash, "base64");
  const h = crypto.scryptSync(pw, Buffer.from(salt, "base64"), expected.length, { N: Number(N), r: Number(r), p: Number(p) });
  return h.length === expected.length && crypto.timingSafeEqual(h, expected);
}
