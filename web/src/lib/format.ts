import type { Severity } from "./api";

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
export const money = (v: string | number) => usd.format(Number(v));

export const STATUS_LABEL: Record<string, string> = {
  uploaded: "Uploaded",
  needs_line_review: "Needs line review",
  needs_review: "Needs review",
  letter_ready: "Letter drafted",
  approved: "Letter approved",
  exported: "Exported",
};

const sentence = (s: string) => {
  const words = s.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
};

export const statusLabel = (s: string) => STATUS_LABEL[s] ?? sentence(s);
export const humanizeAction = sentence;

export const SEVERITY_GROUPS: { severity: Severity; title: string }[] = [
  { severity: "error", title: "Billing errors" },
  { severity: "outlier", title: "Price outliers" },
  { severity: "lead", title: "Leads (worth checking, not errors)" },
  { severity: "notice", title: "Notices" },
];
