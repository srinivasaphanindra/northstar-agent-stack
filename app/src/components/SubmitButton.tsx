"use client";
import { useFormStatus } from "react-dom";

/**
 * Submit button for server-action forms. React's action FormData doesn't carry the clicked button's
 * name/value, so a hidden field mirrors it on click (without JS the browser sends name/value natively).
 */
export function SubmitButton({ children, className = "btn", name, value, pendingText }: { children: React.ReactNode; className?: string; name?: string; value?: string; pendingText?: string }) {
  const { pending } = useFormStatus();
  function mirror(e: React.MouseEvent<HTMLButtonElement>) {
    const form = e.currentTarget.form;
    if (!form || !name) return;
    let input = form.querySelector<HTMLInputElement>(`input[type="hidden"][data-submitter="${name}"]`);
    if (!input) {
      input = document.createElement("input");
      input.type = "hidden";
      input.name = name;
      input.dataset.submitter = name;
      form.appendChild(input);
    }
    input.value = value ?? "";
  }
  return <button type="submit" name={name} value={value} onClick={mirror} disabled={pending} className={className}>{pending ? pendingText ?? "…" : children}</button>;
}
