import { useCallback, useEffect, useState } from "react";
import { ApiError, auditCase, getCase, messageOf, streamExplanations, workspaceLikelyExpired, type CaseDetail as Detail, type Flag } from "../lib/api";
import { money, SEVERITY_GROUPS, statusLabel } from "../lib/format";
import AuditLog from "./AuditLog";
import ExpiredNotice from "./ExpiredNotice";
import FlagCard from "./FlagCard";
import LineReview from "./LineReview";
import LetterPanel from "./LetterPanel";

const EXPLAINABLE = new Set(["pending", "unavailable"]);

export default function CaseDetail({ id, onBack }: { id: string; onBack: () => void }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [expired, setExpired] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setDetail(await getCase(id));
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        setNotFound(true);
        setExpired(workspaceLikelyExpired());
      } else setError(messageOf(e));
    }
  }, [id]);
  useEffect(() => {
    let cancelled = false;
    getCase(id).then(
      (d) => !cancelled && setDetail(d),
      (e) => {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 404) {
          setNotFound(true);
          setExpired(workspaceLikelyExpired());
        } else setError(messageOf(e));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (notFound)
    return (
      <div className="space-y-2">
        <p>Case not found.</p>
        {expired && <ExpiredNotice />}
        <button type="button" onClick={onBack} className="rounded border px-3 py-1.5">Back to cases</button>
      </div>
    );
  if (!detail) return error ? <p role="alert" className="border border-black p-2 font-medium">{error}</p> : <p>Loading case…</p>;

  const locked = detail.letter?.status === "approved";
  const replaceFlag = (f: Flag) => setDetail((d) => d && { ...d, flags: d.flags.map((x) => (x.id === f.id ? f : x)) });
  const flagsByLine = new Map<string, string[]>();
  for (const f of detail.flags) for (const l of f.line_ids) flagsByLine.set(l, [...(flagsByLine.get(l) ?? []), f.rule_id]);
  const needsLineReview = detail.status === "needs_line_review";
  const lineReview = <LineReview key={JSON.stringify(detail.lines)} caseDetail={detail} onSaved={reload} locked={locked} />;
  const canExplain = detail.flags.some((f) => EXPLAINABLE.has(f.explanation_status));

  async function runAudit() {
    setBusy(true);
    setError(null);
    try {
      setDetail(await auditCase(id));
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy(false);
    }
  }

  async function explain() {
    setBusy(true);
    setError(null);
    setProgress("Starting…");
    let failed = 0;
    let finished = false;
    try {
      await streamExplanations(id, ({ event, data }) => {
        const d = data as Record<string, unknown>;
        if (event === "start") setProgress(`Explaining 0/${d.pending}…`);
        if (event === "explanation") {
          setProgress(`Explaining ${d.index}/${d.total}…`);
          setDetail((cur) => cur && {
            ...cur,
            flags: cur.flags.map((f) =>
              f.id === d.flag_id ? { ...f, explanation: (d.explanation as string | null) ?? null, explanation_status: d.status as Flag["explanation_status"] } : f,
            ),
          });
        }
        if (event === "error") failed += 1;
        if (event === "done") {
          finished = true;
          const more = Number(d.remaining) > 0 ? ` ${d.remaining} left — run again to finish.` : "";
          setProgress(`Done: ${d.ready} ready, ${d.unavailable} unavailable, ${failed} failed.${more}`);
        }
      });
      if (!finished) {
        setProgress(null);
        setError("The explanation stream stopped early. Try again.");
      }
    } catch (e) {
      setProgress(null);
      setError(messageOf(e));
    } finally {
      setBusy(false);
      await reload();
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-baseline gap-4">
        <h2 className="text-xl font-semibold">Claim <span className="font-mono">{detail.claim_id}</span></h2>
        <span className="text-sm text-neutral-600">{detail.provider ?? "Unknown provider"} · {detail.payer ?? "Unknown payer"} ({detail.payer_type}) · {statusLabel(detail.status)}</span>
      </div>
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <div className="rounded border bg-white p-3"><dt className="text-xs text-neutral-500">Billing errors</dt><dd className="text-lg">{detail.error_count}</dd></div>
        <div className="rounded border bg-white p-3"><dt className="text-xs text-neutral-500">Est. overcharge (errors)</dt><dd className="text-lg" data-total="errors">{money(detail.est_overcharge)}</dd></div>
        <div className="rounded border bg-white p-3"><dt className="text-xs text-neutral-500">Above benchmark (outliers)</dt><dd className="text-lg" data-total="outliers">{money(detail.outlier_amount)}</dd></div>
      </dl>

      <div className="flex flex-wrap gap-2">
        {!needsLineReview && (
          <button type="button" disabled={busy || locked} onClick={runAudit} className="rounded border px-3 py-1.5 disabled:opacity-50">Run audit</button>
        )}
        {canExplain && (
          <button type="button" disabled={busy} onClick={explain} className="rounded bg-black px-3 py-1.5 text-white disabled:opacity-50">Generate explanations</button>
        )}
        {progress && <span role="status" className="self-center text-sm text-neutral-600">{progress}</span>}
      </div>
      {error && <p role="alert" className="border border-black p-2 font-medium">{error}</p>}

      {detail.source === "pdf" && (needsLineReview ? (
        <section aria-labelledby="line-review" className="space-y-2">
          <h3 id="line-review" className="font-semibold">Review extracted lines</h3>
          <p className="text-sm">
            {detail.lines.length === 0
              ? "The lines on this bill could not be read. Add them from the bill pages, then save."
              : "Some values were hard to read. Check the marked fields against the bill, then save."}
          </p>
          {lineReview}
        </section>
      ) : (
        <details className="border border-black p-3">
          <summary className="cursor-pointer font-medium">Extracted lines and bill pages</summary>
          <div className="mt-3">{lineReview}</div>
        </details>
      ))}

      <div className="overflow-x-auto rounded border bg-white">
        <table className="w-full text-left text-sm">
          <thead className="bg-neutral-100">
            <tr>
              <th className="p-2">Line</th><th className="p-2">Code</th><th className="p-2">Modifiers</th>
              <th className="p-2 text-right">Units</th><th className="p-2 text-right">Charge</th><th className="p-2">Date</th><th className="p-2">Flags</th>
            </tr>
          </thead>
          <tbody>
            {detail.lines.map((l) => (
              <tr key={l.id} className="border-t">
                <td className="p-2 font-mono">{l.id}</td>
                <td className="p-2 font-mono">{l.code}</td>
                <td className="p-2 font-mono">{l.modifiers.join(", ") || "—"}</td>
                <td className="p-2 text-right">{l.units}</td>
                <td className="p-2 text-right">{money(l.charge)}</td>
                <td className="p-2">{l.date_of_service}</td>
                <td className="p-2">
                  {(flagsByLine.get(l.id) ?? []).map((r) => (
                    <span key={r} className="mr-1 rounded border border-black px-1.5 font-mono text-xs">{r}</span>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {detail.flags.length === 0 && <p className="text-neutral-600">No flags. Run the audit to check this claim.</p>}
      {SEVERITY_GROUPS.map(({ severity, title }) => {
        const flags = detail.flags.filter((f) => f.severity === severity);
        if (flags.length === 0) return null;
        const headingId = `group-${severity}`;
        return (
          <section key={severity} aria-labelledby={headingId} className="space-y-2">
            <h3 id={headingId} className="font-semibold">{title}</h3>
            {flags.map((f) => <FlagCard key={f.id} flag={f} locked={locked} onChange={replaceFlag} />)}
          </section>
        );
      })}
      <LetterPanel key={`${detail.letter?.id}:${detail.letter?.body}`} caseDetail={detail} onChange={reload} />
      <h3 className="font-semibold">Activity</h3>
      <AuditLog caseId={id} refreshKey={[detail.status, detail.letter?.id, detail.letter?.status, detail.letter?.body, ...detail.flags.map((f) => `${f.status}:${f.explanation_status}`)].join("|")} />
    </div>
  );
}
