// Poll-based job queue in the database. Works the same on SQLite and Postgres:
// claim = conditional UPDATE (status queued -> running) so two workers can't take the same job.
import os from "node:os";
import { db } from "./db";

export const WORKER_ID = `${os.hostname()}:${process.pid}`;

export async function enqueue(type: string, payload: unknown, workspaceId: string | null = null, delayMs = 0) {
  return db.job.create({ data: { type, payload: JSON.stringify(payload ?? {}), workspaceId, runAfter: new Date(Date.now() + delayMs) } });
}

export async function claimNext(types?: string[]) {
  const now = new Date();
  // stale lock recovery: running for > 10 min -> back to queued
  await db.job.updateMany({ where: { status: "running", lockedAt: { lt: new Date(now.getTime() - 10 * 60_000) } }, data: { status: "queued", lockedAt: null, lockedBy: null } });
  const candidates = await db.job.findMany({ where: { status: "queued", runAfter: { lte: now }, ...(types ? { type: { in: types } } : {}) }, orderBy: { createdAt: "asc" }, take: 5 });
  for (const c of candidates) {
    const r = await db.job.updateMany({ where: { id: c.id, status: "queued" }, data: { status: "running", lockedAt: now, lockedBy: WORKER_ID, attempts: { increment: 1 } } });
    if (r.count === 1) return { ...c, attempts: c.attempts + 1 };
  }
  return null;
}

export async function complete(id: string) {
  await db.job.update({ where: { id }, data: { status: "done", finishedAt: new Date(), lockedAt: null } });
}

export async function fail(id: string, err: unknown, attempts: number, maxAttempts: number) {
  const msg = (err instanceof Error ? err.message : String(err)).slice(0, 1000);
  const last = attempts >= maxAttempts;
  await db.job.update({
    where: { id },
    data: last
      ? { status: "failed", error: msg, finishedAt: new Date(), lockedAt: null }
      : { status: "queued", error: msg, lockedAt: null, runAfter: new Date(Date.now() + 2 ** attempts * 5000) },
  });
}
