// "Open in app" handoff: the client posts from their own signed-in phone app with the text prefilled
// where the platform allows it. Pure functions so the same logic can run in a Capacitor wrapper later.
export type Handoff = { id: string; label: string; url: string | null; copyFirst: boolean; note: string };

export function handoffLinks(text: string, opts: { url?: string } = {}): Handoff[] {
  const t = encodeURIComponent(text);
  const u = opts.url ? encodeURIComponent(opts.url) : "";
  return [
    { id: "x", label: "Open in X", url: `https://x.com/intent/post?text=${t}`, copyFirst: false, note: "Text is prefilled" },
    {
      id: "linkedin", label: "Open in LinkedIn",
      url: u ? `https://www.linkedin.com/sharing/share-offsite/?url=${u}` : "https://www.linkedin.com/feed/?shareActive=true",
      copyFirst: true, note: "Text copied: paste it into the post",
    },
    {
      id: "facebook", label: "Open in Facebook",
      url: u ? `https://www.facebook.com/sharer/sharer.php?u=${u}` : "https://www.facebook.com/",
      copyFirst: true, note: "Facebook doesn't allow prefilled text: it's copied, paste it",
    },
    { id: "whatsapp", label: "Share on WhatsApp", url: `https://wa.me/?text=${t}`, copyFirst: false, note: "Opens WhatsApp with the text" },
    { id: "share", label: "Share…", url: null, copyFirst: false, note: "Your phone's share sheet (Instagram, etc.)" },
    { id: "copy", label: "Copy text", url: null, copyFirst: true, note: "" },
  ];
}
