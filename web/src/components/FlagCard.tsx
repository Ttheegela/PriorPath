import { useState } from "react";
import { messageOf, updateFlag, type Flag, type FlagStatus } from "../lib/api";
import { money } from "../lib/format";

const EXPLANATION_TEXT: Record<string, string> = {
  pending: "Explanation not generated yet.",
  unavailable: "Explanation unavailable right now — try Generate explanations again.",
  none: "",
};

export default function FlagCard({ flag, locked, onChange }: { flag: Flag; locked: boolean; onChange: (f: Flag) => void }) {
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reviewable = flag.severity !== "notice";

  async function save(status: FlagStatus, rejectReason?: string) {
    setBusy(true);
    setError(null);
    try {
      onChange(await updateFlag(flag.id, status, rejectReason));
      setRejecting(false);
      setReason("");
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <article aria-label={`${flag.rule_id}: ${flag.message}`} className="space-y-2 rounded border bg-white p-3">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="rounded bg-neutral-200 px-1.5 font-mono text-xs">{flag.rule_id}</span>
        <p className="font-medium">{flag.message}</p>
        {Number(flag.est_overcharge) > 0 && (
          <span className="ml-auto text-sm">Est. {flag.severity === "outlier" ? "excess" : "overcharge"}: {money(flag.est_overcharge)}</span>
        )}
      </div>
      {flag.line_ids.length > 0 && <p className="text-xs text-neutral-500">Lines: {flag.line_ids.join(", ")}</p>}
      <p className="text-sm">{flag.explanation ?? EXPLANATION_TEXT[flag.explanation_status] ?? ""}</p>
      <details className="text-xs">
        <summary className="cursor-pointer text-neutral-600">Evidence</summary>
        <dl className="mt-1 grid grid-cols-[max-content_1fr] gap-x-3">
          {Object.entries(flag.evidence).map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="font-mono text-neutral-500">{k}</dt>
              <dd className="font-mono break-all">{typeof v === "string" ? v : JSON.stringify(v)}</dd>
            </div>
          ))}
        </dl>
      </details>
      {reviewable && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          {flag.status === "open" ? (
            <>
              <button type="button" disabled={locked || busy} onClick={() => save("accepted")} className="rounded bg-black px-2 py-1 text-white disabled:opacity-50">
                Accept
              </button>
              <button type="button" disabled={locked || busy} onClick={() => setRejecting(true)} className="rounded border px-2 py-1 disabled:opacity-50">
                Reject
              </button>
            </>
          ) : (
            <>
              <span className={flag.status === "accepted" ? "font-medium" : "text-neutral-600"}>
                {flag.status === "accepted" ? "Accepted" : `Rejected: ${flag.reject_reason ?? ""}`}
              </span>
              <button type="button" disabled={locked || busy} onClick={() => save("open")} className="rounded border px-2 py-1 disabled:opacity-50">
                Undo
              </button>
            </>
          )}
        </div>
      )}
      {rejecting && (
        <div className="flex flex-wrap items-end gap-2 text-sm">
          <label className="flex grow flex-col">
            Reason
            <input value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} className="rounded border px-2 py-1" />
          </label>
          <button type="button" disabled={busy || !reason.trim()} onClick={() => save("rejected", reason.trim())} className="rounded bg-black px-2 py-1 text-white disabled:opacity-50">
            Confirm reject
          </button>
          <button type="button" onClick={() => setRejecting(false)} className="rounded border px-2 py-1">
            Cancel
          </button>
        </div>
      )}
      {error && <p role="alert" className="border border-black p-2 text-sm font-medium">{error}</p>}
    </article>
  );
}
