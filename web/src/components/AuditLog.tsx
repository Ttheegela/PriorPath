import { useEffect, useState } from "react";
import { auditLog, messageOf, type AuditEvent } from "../lib/api";
import { humanizeAction } from "../lib/format";
import { isPlainClick, routeHref } from "../lib/route";

const summarize = (detail: Record<string, unknown>) =>
  Object.entries(detail)
    .filter(([, v]) => v !== null && v !== "")
    .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(" · ");

export default function AuditLog({ caseId, refreshKey, onOpenCase }: { caseId?: string; refreshKey?: string; onOpenCase?: (id: string) => void }) {
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    auditLog(caseId).then(
      (e) => {
        if (!live) return;
        setEvents(e);
        setError(null);
      },
      (e) => live && setError(messageOf(e)),
    );
    return () => {
      live = false;
    };
  }, [caseId, refreshKey]);

  const heading = !caseId && <h2 className="text-xl font-semibold">Audit log</h2>;
  if (!events && !error) return <section className="space-y-2">{heading}<p>Loading activity…</p></section>;
  if (events?.length === 0 && !error) return <section className="space-y-2">{heading}<p className="text-neutral-600">No activity yet.</p></section>;

  return (
    <section className="space-y-2">
      {heading}
      {error && <p role="alert" className="border border-black p-2 font-medium">{error}</p>}
      <ol className="space-y-2">
        {(events ?? []).map((e) => (
          <li key={e.id} className="rounded border border-black bg-white p-2 text-sm">
            <div className="flex flex-wrap gap-3">
              <time dateTime={e.at} className="text-neutral-500">{new Date(e.at).toLocaleString()}</time>
              <span className="font-medium">{humanizeAction(e.action)}</span>
              <span className="text-neutral-500">by {e.actor}</span>
              {!caseId && e.case_id && onOpenCase && (
                <a
                  href={routeHref({ name: "case", id: e.case_id })}
                  onClick={(ev) => {
                    if (!isPlainClick(ev)) return;
                    ev.preventDefault();
                    onOpenCase(e.case_id as string);
                  }}
                  className="ml-auto underline"
                >
                  Open case
                </a>
              )}
            </div>
            {Object.keys(e.detail).length > 0 && <p className="mt-1 font-mono text-xs text-neutral-600 break-all">{summarize(e.detail)}</p>}
            {e.ref_versions.length > 0 && <p className="text-xs text-neutral-500">Reference: {e.ref_versions.join(", ")}</p>}
          </li>
        ))}
      </ol>
    </section>
  );
}
