import { PrismaClient } from "@prisma/client";

const g = globalThis as unknown as { __nsPrisma?: PrismaClient };
export const db: PrismaClient = g.__nsPrisma ?? new PrismaClient();
if (process.env.NODE_ENV !== "production") g.__nsPrisma = db;

export type Db = PrismaClient;
