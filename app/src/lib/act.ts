import "server-only";
import { redirect } from "next/navigation";
import { isAppError } from "@/core/errors";

function withParam(path: string, k: string, v: string) {
  const [p, hash] = path.split("#");
  const u = new URL(p, "http://x");
  u.searchParams.delete("ok"); u.searchParams.delete("e");
  u.searchParams.set(k, v.slice(0, 300));
  return `${u.pathname}${u.search}${hash ? `#${hash}` : ""}`;
}

/** Runs a server action body; AppErrors become a flash message (?e=), success becomes ?ok=. */
export async function run(path: string, fn: () => Promise<string | void | { redirect: string }>) {
  let dest: string;
  try {
    const r = await fn();
    dest = typeof r === "object" && r && "redirect" in r ? r.redirect : r === undefined ? path : withParam(path, "ok", r);
  } catch (e) {
    if (isAppError(e)) dest = withParam(path, "e", e.message);
    else if (e && typeof e === "object" && "issues" in e) dest = withParam(path, "e", "Please check the form");
    else throw e;
  }
  redirect(dest);
}

export const s = (f: FormData, k: string) => String(f.get(k) ?? "");
