import { NextResponse } from "next/server";
import { db } from "@/core/db";

export const dynamic = "force-dynamic";
export async function GET() {
  try {
    await db.$queryRaw`SELECT 1`;
    const queued = await db.job.count({ where: { status: "queued" } });
    return NextResponse.json({ status: "ok", service: "northstar-app", queued });
  } catch (e) {
    console.error("[health]", e instanceof Error ? e.message : e);
    return NextResponse.json({ status: "error", service: "northstar-app" }, { status: 500 });
  }
}
