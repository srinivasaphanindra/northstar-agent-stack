import { execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

export default function setup() {
  const dir = path.resolve(__dirname, "../data");
  fs.mkdirSync(dir, { recursive: true });
  const f = path.join(dir, "test.db");
  for (const x of [f, `${f}-journal`]) if (fs.existsSync(x)) fs.rmSync(x);
  execSync("npx prisma db push --skip-generate", { cwd: path.resolve(__dirname, ".."), env: { ...process.env, DATABASE_URL: "file:../data/test.db" }, stdio: "pipe" });
}
