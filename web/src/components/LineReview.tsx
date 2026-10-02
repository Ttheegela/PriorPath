import { useState } from "react";
import { auditCase, messageOf, pageUrl, updateLines, type CaseDetail, type Line } from "../lib/api";

const LOW_CONFIDENCE = 0.8;

interface Row {
  id: string;
  code: string;
  modifiers: string;
  units: string;
  charge: string;
  date_of_service: string;
  place_of_service: string | null;
  field_confidence: Record<string, number>;
}

const toRow = (l: Line): Row => ({
  id: l.id, code: l.code, modifiers: l.modifiers.join(", "), units: String(l.units), charge: l.charge,
  date_of_service: l.date_of_service, place_of_service: l.place_of_service, field_confidence: l.field_confidence,
});

const FIELDS = [
  { key: "code", label: "Code" },
  { key: "modifiers", label: "Modifiers" },
  { key: "units", label: "Units" },
  { key: "charge", label: "Charge" },
  { key: "date_of_service", label: "Date" },
] as const;

export default function LineReview({ caseDetail, onSaved }: { caseDetail: CaseDetail; onSaved: () => Promise<void> | void }) {
  const [rows, setRows] = useState<Row[]>(() => caseDetail.lines.map(toRow));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const edit = (i: number, key: keyof Row, value: string) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, [key]: value } : r)));

  const add = () =>
    setRows((rs) => {
      let n = 1;
      while (rs.some((r) => r.id === `NEW-${n}`)) n += 1;
      return [...rs, { id: `NEW-${n}`, code: "", modifiers: "", units: "1", charge: "", date_of_service: "", place_of_service: null, field_confidence: {} }];
    });

  async function save() {
    setError(null);
    if (rows.length === 0) {
      setError("Add at least one line");
      return;
    }
    setBusy(true);
    try {
      await updateLines(
        caseDetail.id,
        rows.map((r) => ({
          id: r.id,
          code: r.code.trim(),
          modifiers: r.modifiers.split(",").map((m) => m.trim()).filter(Boolean),
          units: Number(r.units),
          charge: r.charge.trim(),
          date_of_service: r.date_of_service,
          place_of_service: r.place_of_service,
        })),
      );
      await auditCase(caseDetail.id);
      await onSaved();
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy(false);
    }
  }

  const pages = Array.from({ length: caseDetail.page_count ?? 0 }, (_, i) => i + 1);
  return (
    <div className="space-y-3 lg:grid lg:grid-cols-2 lg:items-start lg:gap-4 lg:space-y-0">
      <div className="space-y-2">
        {pages.map((n) => (
          <img key={n} src={pageUrl(caseDetail.id, n)} alt={`Bill page ${n}`} loading="lazy" className="max-w-full border border-black" />
        ))}
      </div>
      <div className="space-y-3">
        <div className="overflow-x-auto border border-black bg-white">
          <table className="w-full text-left text-sm">
            <thead className="bg-neutral-100">
              <tr>
                <th className="p-2">Line</th>
                {FIELDS.map((f) => <th key={f.key} className="p-2">{f.label}</th>)}
                <th className="p-2"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.id} className="border-t border-neutral-300">
                  <td className="p-2 font-mono">{r.id}</td>
                  {FIELDS.map((f) => {
                    const low = (r.field_confidence[f.key] ?? 1) < LOW_CONFIDENCE;
                    return (
                      <td key={f.key} className="p-2">
                        <input
                          aria-label={`${f.label} for ${r.id}`}
                          aria-invalid="false"
                          data-low-confidence={low ? "true" : undefined}
                          value={r[f.key]}
                          onChange={(e) => edit(i, f.key, e.target.value)}
                          className={`w-full min-w-16 rounded border px-1 py-0.5 ${low ? "border-dashed border-black" : "border-neutral-400"}`}
                        />
                        {low && <span className="font-bold"> check</span>}
                      </td>
                    );
                  })}
                  <td className="p-2">
                    <button type="button" onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))} aria-label={`Remove ${r.id}`} className="rounded border border-black px-2 py-0.5">
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {error && <p role="alert" className="border border-black p-2 font-medium">{error}</p>}
        <div className="flex gap-2">
          <button type="button" onClick={add} className="rounded border border-black px-3 py-1.5">Add line</button>
          <button type="button" onClick={save} disabled={busy} className="rounded bg-black px-3 py-1.5 text-white disabled:opacity-50">
            Save lines and run audit
          </button>
        </div>
      </div>
    </div>
  );
}
