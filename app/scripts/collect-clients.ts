// One read-only collector pass: `npm run collect`. Prints per-source results (never secrets).
import { collectAll } from "../src/core/clients/collector";
import { db } from "../src/core/db";
collectAll().then((r) => { console.log(JSON.stringify(r, null, 1)); return db.$disconnect(); });
