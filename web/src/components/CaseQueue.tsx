import { useCallback, useEffect, useMemo, useState } from "react";
import { isPdf, listCases, messageOf, resetDemo, uploadCases, type CaseSummary, type PayerType, type UploadResult } from "../lib/api";
import { money, STATUS_LABEL, statusLabel } from "../lib/format";
import { isPlainClick, routeHref } from "../lib/route";

const MAX_UPLOAD = 4_000_000;

export default function CaseQueue({ onOpen }: { onOpen: (id: string) => void }) {
  const [cases, setCases] = useState<CaseSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState("all");
  const [sort, setSort] = useState<"newest" | "overcharge">("newest");
  const [file, setFile] = useState<File | null>(null);
  const [payerType, setPayerType] = useState<PayerType>("medicare");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uploaded, setUploaded] = useState<UploadResult | null>(null);

  const load = useCallback(() => {
    listCases().then(setCases, (e) => setError(messageOf(e)));
  }, []);
  useEffect(load, [load]);

  const shown = useMemo(() => {
    const rows = (cases ?? []).filter((c) => status === "all" || c.status === status);
    return sort === "overcharge"
      ? [...rows].sort((a, b) => Number(b.est_overcharge) - Number(a.est_overcharge))
      : rows;
  }, [cases, status, sort]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy(false);
    }
  }

  const upload = () =>
    run(async () => {
      setUploaded(null);
      if (!file) throw new Error("Choose a FHIR JSON or PDF file first.");
      if (file.size > MAX_UPLOAD) throw new Error("That file is larger than 4 MB; split the bundle and try again.");
      if (isPdf(file) && !confirmed) throw new Error("Confirm that this is a synthetic or test bill before uploading a PDF.");
      setUploaded(await uploadCases(file, payerType, { confirmSynthetic: confirmed }));
      load();
    });

  const reset = () =>
    run(async () => {
      await resetDemo();
      setUploaded(null);
      load();
    });

  return (
    <section className="space-y-6">
      <div className="flex flex-wrap items-end gap-4 border border-black bg-white p-4">
        <div className="flex flex-col text-sm">
          <label htmlFor="claim-file">Claim file (FHIR JSON or PDF bill, up to 4 MB)</label>
          <div className="mt-1 flex items-center gap-3">
            <input id="claim-file" type="file" className="peer sr-only" accept=".json,application/json,.pdf,application/pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            <label htmlFor="claim-file" className="cursor-pointer border border-black bg-white px-3 py-1.5 hover:bg-neutral-100 peer-focus-visible:ring-2 peer-focus-visible:ring-black peer-focus-visible:ring-offset-2">
              Choose file
            </label>
            <span className="text-neutral-700">{file ? file.name : "No file chosen"}</span>
          </div>
        </div>
        {file && isPdf(file) && (
          <div className="flex flex-col text-sm">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} aria-describedby="pdf-redaction-help" />
              This is a synthetic or test bill (page images are sent to an AI model)
            </label>
            <span id="pdf-redaction-help" className="text-neutral-600">
              We try to black out names, phone numbers, addresses and member IDs before the AI model sees text-based PDFs; scanned images can't be redacted.
            </span>
          </div>
        )}
        <label className="flex flex-col text-sm">
          Payer type
          <select value={payerType} onChange={(e) => setPayerType(e.target.value as PayerType)} className="rounded border border-black px-2 py-1">
            <option value="medicare">Medicare</option>
            <option value="commercial">Commercial</option>
            <option value="unknown">Unknown</option>
          </select>
        </label>
        <button type="button" onClick={upload} disabled={busy || !file} className="rounded bg-black px-3 py-1.5 text-white disabled:opacity-50">
          Upload
        </button>
        <button type="button" onClick={reset} disabled={busy} className="ml-auto rounded border border-black px-3 py-1.5 disabled:opacity-50">
          Reset demo data
        </button>
      </div>
      <p className="text-sm text-neutral-700">
        Upload a FHIR ExplanationOfBenefit bundle or a PDF bill. PriorPath runs the audit automatically and the case appears below.{" "}
        <a href="/api/samples/claim.json" download className="underline">Download a sample claim (FHIR JSON)</a>{" "}
        <a href="/api/samples/bill.pdf" download className="underline">Download a sample bill (PDF)</a>
      </p>

      {error && <p role="alert" className="border border-black p-2 font-medium">{error}</p>}
      {uploaded && (
        <div className="border border-black p-3 text-sm" role="status">
          <p>{uploaded.cases.length} case{uploaded.cases.length === 1 ? "" : "s"} added.</p>
          <ul className="mt-1 list-disc pl-5">
            {uploaded.cases.map((c) => (
              <li key={c.id}>
                {`${c.claim_id}: ${c.error_count} billing error${c.error_count === 1 ? "" : "s"}${
                  Number(c.est_overcharge) > 0 || Number(c.outlier_amount) <= 0 ? `, est. overcharge ${money(c.est_overcharge)}` : ""
                }${Number(c.outlier_amount) > 0 ? `, price outliers ${money(c.outlier_amount)} above benchmark` : ""}`}{" "}
                <a
                  href={routeHref({ name: "case", id: c.id })}
                  aria-label={`Open ${c.claim_id}`}
                  onClick={(e) => {
                    if (!isPlainClick(e)) return;
                    e.preventDefault();
                    onOpen(c.id);
                  }}
                  className="underline"
                >
                  Open
                </a>
              </li>
            ))}
          </ul>
          {uploaded.errors.length > 0 && (
            <ul className="mt-1 list-disc pl-5">
              {uploaded.errors.map((e) => (
                <li key={`${e.path}:${e.message}`}>{`${e.path}: ${e.message}`}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="flex gap-4 text-sm">
        <label>
          Status{" "}
          <select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)} className="rounded border border-black px-2 py-1">
            <option value="all">All</option>
            {Object.entries(STATUS_LABEL).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </label>
        <label>
          Sort{" "}
          <select aria-label="Sort" value={sort} onChange={(e) => setSort(e.target.value as "newest" | "overcharge")} className="rounded border border-black px-2 py-1">
            <option value="newest">Newest first</option>
            <option value="overcharge">Highest est. overcharge</option>
          </select>
        </label>
      </div>

      {cases === null ? (
        error ? null : <p>Loading cases…</p>
      ) : (
        <div className="overflow-x-auto border border-black bg-white">
          <table className="w-full text-left text-sm">
            <thead className="bg-neutral-100">
              <tr>
                <th className="p-2">Claim</th>
                <th className="p-2">Provider</th>
                <th className="p-2 text-right">Lines</th>
                <th className="p-2 text-right">Billing errors</th>
                <th className="p-2 text-right">Est. overcharge</th>
                <th className="p-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((c) => (
                <tr key={c.id} className="border-t border-neutral-300">
                  <td className="p-2">
                    <a
                      href={routeHref({ name: "case", id: c.id })}
                      onClick={(e) => {
                        if (!isPlainClick(e)) return;
                        e.preventDefault();
                        onOpen(c.id);
                      }}
                      className="font-mono underline"
                    >
                      {c.claim_id}
                    </a>
                  </td>
                  <td className="p-2">{c.provider ?? "—"}</td>
                  <td className="p-2 text-right">{c.line_count}</td>
                  <td className="p-2 text-right">{c.error_count}</td>
                  <td className="p-2 text-right">{money(c.est_overcharge)}</td>
                  <td className="p-2">{statusLabel(c.status)}</td>
                </tr>
              ))}
              {shown.length === 0 && (
                <tr>
                  <td colSpan={6} className="p-4 text-center text-neutral-500">No cases match.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
