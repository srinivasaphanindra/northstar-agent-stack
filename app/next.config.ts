import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",          // portable build for a VPS or Docker
  poweredByHeader: false,
  reactStrictMode: true,
  serverExternalPackages: ["@prisma/client", "prisma"],
  experimental: { cpus: 2, webpackMemoryOptimizations: true, serverActions: { bodySizeLimit: "1mb" } },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
      { source: "/sw.js", headers: [{ key: "Cache-Control", value: "no-cache" }, { key: "Service-Worker-Allowed", value: "/" }] },
    ];
  },
};
export default nextConfig;
