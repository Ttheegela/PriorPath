import { useState } from "react";
import { approveLetter, draftLetter, editLetter, exportUrl, messageOf, type CaseDetail } from "../lib/api";

export default function LetterPanel({ caseDetail, onChange }: { caseDetail: CaseDetail; onChange: () => Promise<void> | void }) {
  const letter = caseDetail.letter;
  const [body, setBody] = useState(letter?.body ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const approved = letter?.status === "approved";
  const canDraft = caseDetail.flags.some((f) => f.status === "accepted" && (f.severity === "error" || f.severity === "outlier"));
  const dirty = letter !== null && body !== letter.body;

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      await onChange();
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="letter-heading" className="space-y-3 rounded border bg-white p-4">
      <h3 id="letter-heading" className="font-semibold">Dispute letter</h3>
      <p className="text-sm text-neutral-600">
        Built only from accepted billing errors and price outliers. Price outliers ask for an itemized justification; they are not claimed as errors. Nothing is sent anywhere — you download the letter.
      </p>
      {!approved && (
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" disabled={busy || !canDraft} onClick={() => run(() => draftLetter(caseDetail.id))} className="rounded border border-black px-3 py-1.5 disabled:opacity-50">
            {letter ? "Redraft from accepted flags" : "Draft dispute letter"}
          </button>
          {!canDraft && <span className="text-sm text-neutral-600">Accept at least one billing error or price outlier to draft a letter.</span>}
        </div>
      )}
      {letter && (
        <>
          <label className="flex flex-col text-sm">
            Letter text
            <textarea
              value={body}
              readOnly={approved}
              onChange={(e) => setBody(e.target.value)}
              rows={14}
              maxLength={20000}
              className="mt-1 rounded border p-2 font-mono text-sm read-only:bg-neutral-100"
            />
          </label>
          {approved ? (
            <div className="flex flex-wrap gap-3 text-sm">
              <span className="font-medium">Approved {letter.approved_at ? new Date(letter.approved_at).toLocaleString() : ""}</span>
              <a href={exportUrl(letter.id, "txt")} download className="underline">Download .txt</a>
              <a href={exportUrl(letter.id, "docx")} download className="underline">Download .docx</a>
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              <button type="button" disabled={busy || !dirty} onClick={() => run(() => editLetter(letter.id, body))} className="rounded border border-black px-3 py-1.5 disabled:opacity-50">
                Save edits
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  run(async () => {
                    if (dirty) await editLetter(letter.id, body);
                    await approveLetter(letter.id);
                  })
                }
                className="rounded bg-black px-3 py-1.5 text-white disabled:opacity-50"
              >
                Approve letter
              </button>
            </div>
          )}
        </>
      )}
      {error && <p role="alert" className="border border-black p-2 text-sm font-medium">{error}</p>}
    </section>
  );
}
