"use client";
import { useEffect } from "react";
export function AutoScroll({ dep }: { dep?: unknown }) {
  useEffect(() => { window.scrollTo({ top: document.body.scrollHeight }); }, [dep]);
  return null;
}
