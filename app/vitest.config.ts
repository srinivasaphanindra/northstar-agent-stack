import { defineConfig } from "vitest/config";
import path from "node:path";
export default defineConfig({
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  test: { include: ["tests/**/*.test.ts"], globalSetup: ["tests/global-setup.ts"], setupFiles: ["tests/setup.ts"], fileParallelism: false, testTimeout: 20000, pool: "forks" },
});
