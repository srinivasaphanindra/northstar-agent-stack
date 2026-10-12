import QRCode from "qrcode";
import { upiLink } from "@/core/billing";
import { currentUser } from "@/lib/session";

export async function GET(req: Request) {
  if (!(await currentUser())) return new Response("unauthorized", { status: 401 });
  const u = new URL(req.url);
  const amount = Math.max(1, Math.min(1_000_000, Number(u.searchParams.get("amount")) || 1));
  const ref = (u.searchParams.get("ref") ?? "").replace(/[^A-Za-z0-9]/g, "").slice(0, 12);
  const png = await QRCode.toBuffer(upiLink(amount, `Northstar ${ref}`), { width: 400, margin: 1 });
  return new Response(new Uint8Array(png), { headers: { "Content-Type": "image/png", "Cache-Control": "private, max-age=300" } });
}
