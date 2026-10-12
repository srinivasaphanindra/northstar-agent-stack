import Link from "next/link";
export default function NotFound() {
  return <main className="min-h-dvh flex flex-col items-center justify-center gap-4 px-6 text-center"><span className="brand">NORTHSTAR</span><h1 className="h2">Not found</h1><Link href="/app" className="btn-ghost btn-sm">Go to the app</Link></main>;
}
