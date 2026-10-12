import { config } from "@/core/config";
import { upiLink } from "@/core/billing";

/** Manual payment instructions: UPI QR (configured image or generated) + optional Gumroad link. */
export function PaymentBox({ amountInr, invoiceId, planName }: { amountInr: number; invoiceId: string; planName: string }) {
  const note = `Northstar ${planName} ${invoiceId.slice(-6)}`;
  const link = upiLink(amountInr, note);
  const qr = config.upiQrImage || `/api/pay/qr?amount=${amountInr}&ref=${encodeURIComponent(invoiceId.slice(-6))}`;
  return (
    <div className="card flex flex-col md:flex-row gap-6">
      <div className="bg-white p-3 rounded-xl self-center shrink-0">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={qr} alt="UPI QR code" width={200} height={200} />
      </div>
      <div className="flex flex-col gap-3">
        <p className="label">Pay ₹{amountInr.toLocaleString("en-IN")} · {planName}</p>
        <p className="text-soft">Scan with any UPI app (GPay, PhonePe, Paytm) or pay to <b className="font-medium">{config.upiId}</b> ({config.upiPayee}). Put <b className="font-medium">{invoiceId.slice(-6)}</b> in the note.</p>
        <div className="flex flex-col sm:flex-row gap-2">
          <a className="btn btn-sm md:hidden" href={link}>Open UPI app</a>
          {config.gumroadEnabled && (
            <a className="btn-ghost btn-sm" href={config.gumroadUrl} target="_blank" rel="noopener noreferrer">Pay via Gumroad (card)</a>
          )}
        </div>
        <p className="text-sm muted">After paying, share the UPI reference number. We confirm within a few hours. You can finish setup now; publishing turns on once an admin marks the payment as paid.</p>
      </div>
    </div>
  );
}
