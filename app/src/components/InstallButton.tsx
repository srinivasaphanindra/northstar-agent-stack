"use client";
import { useEffect, useState } from "react";

type BIP = Event & { prompt: () => Promise<void>; userChoice: Promise<{ outcome: string }> };

export function InstallButton() {
  const [evt, setEvt] = useState<BIP | null>(null);
  const [ios, setIos] = useState(false);
  const [installed, setInstalled] = useState(false);
  useEffect(() => {
    setInstalled(window.matchMedia("(display-mode: standalone)").matches);
    setIos(/iphone|ipad|ipod/i.test(navigator.userAgent));
    const h = (e: Event) => { e.preventDefault(); setEvt(e as BIP); };
    window.addEventListener("beforeinstallprompt", h);
    return () => window.removeEventListener("beforeinstallprompt", h);
  }, []);
  if (installed) return <p className="text-sm muted">Installed on this device ✓</p>;
  if (evt) return <button className="btn-ghost btn-sm" onClick={async () => { await evt.prompt(); setEvt(null); }}>Install the app</button>;
  if (ios) return <p className="text-sm muted">To install: tap Share, then “Add to Home Screen”.</p>;
  return <p className="text-sm muted">Install: open the browser menu and choose “Install app” / “Add to Home screen”.</p>;
}
