// Writes prisma/postgres/schema.prisma: identical models, provider = postgresql.
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
const src = readFileSync(new URL("../prisma/schema.prisma", import.meta.url), "utf8");
const out = src.replace(/provider\s*=\s*"sqlite"/, 'provider = "postgresql"');
if (out === src) throw new Error("sqlite provider line not found");
mkdirSync(new URL("../prisma/postgres/", import.meta.url), { recursive: true });
writeFileSync(new URL("../prisma/postgres/schema.prisma", import.meta.url), out);
console.log("wrote prisma/postgres/schema.prisma (use: npx prisma db push --schema prisma/postgres/schema.prisma)");
