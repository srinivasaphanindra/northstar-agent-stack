import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/app",
    name: "Northstar Agents",
    short_name: "Northstar",
    description: "Chat with your social media assistant and approve posts from your phone.",
    start_url: "/app",
    scope: "/",
    display: "standalone",
    orientation: "portrait",
    background_color: "#000000",
    theme_color: "#000000",
    categories: ["business", "productivity", "social"],
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
      { src: "/icons/maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
    shortcuts: [
      { name: "Approvals", url: "/app/approvals" },
      { name: "Chat", url: "/app/chat" },
    ],
  };
}
