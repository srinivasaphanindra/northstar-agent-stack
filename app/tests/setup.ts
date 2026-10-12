import crypto from "node:crypto";
import path from "node:path";
process.env.DATABASE_URL = "file:../data/test.db";
process.env.APP_MASTER_KEY ??= crypto.randomBytes(32).toString("base64");
process.env.LLM_MODE = "mock";
process.env.PUBLISH_DRY_RUN = "true";
process.env.ADMIN_EMAILS = "admin@test.local";
process.env.GUARDRAILS_TEMPLATE_PATH = path.resolve(__dirname, "../config/guardrails-default.yaml");
