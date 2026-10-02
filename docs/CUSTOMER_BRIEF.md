# PriorPath: customer brief

_For a buyer or pilot sponsor. Short brief, no engineering background needed. Technical detail is in
[`ARCHITECTURE.md`](ARCHITECTURE.md); the security position is in [`SECURITY.md`](SECURITY.md)._

## Who it is for

- **Claims auditors** at a patient-advocacy firm or an audit vendor, who review many medical claims and need
  findings they can defend line by line.
- **Patient advocates** who help a person dispute a hospital or physician bill.
- **Self-funded employer benefits teams**, who pay employee claims out of company money and want to catch
  overpayments before or after they pay.

## The problem

Itemized bills and Explanation of Benefits statements regularly contain charges that should not be there: the
same service billed twice, a service billed separately when it is already part of another one, more units than
are medically plausible, codes that are not valid on the date of service, and prices far above any benchmark.
Medicare publishes the rules that catch these (the NCCI code-pair edits, the MUE unit limits and the Physician
Fee Schedule), but they are large tables that change every quarter. Checking a bill means looking up every line,
by code and date, by hand.

**An example.** One of PriorPath's synthetic demo claims, an itemized bill from "Summit Cardiology", has four lines totalling
$554.15, all on 2026-11-27:

| Line | Code | Units | Charge | What PriorPath finds |
|---|---|---|---|---|
| 1 | 99212 | 1 | $123.06 | nothing |
| 2 | 93320 | 1 | $124.48 | nothing |
| 3 | 96375 | 7 | $206.61 | **Unit limit:** Medicare's limit for this code is 6 units per day. Estimated overcharge $29.52 (one excess unit at the billed unit price). |
| 4 | 83992 | 1 | $100.00 | **Invalid code:** not valid for Medicare billing on that date. Estimated overcharge $100.00 (the whole line). |

Each finding comes with the exact Medicare table row and release it was checked against, a plain-English
explanation, and an Accept / Reject decision for the reviewer. Accepted findings become a dispute letter
asking the provider to correct $129.52.

## What PriorPath does

1. **Reads the claim.** A FHIR claim file (the healthcare data standard many payers expose), or a PDF bill that
   an AI vision model reads into lines. When the model is unsure of any value, the case opens on a review screen
   that shows the extracted lines next to the page images so a person can correct them first.
2. **Checks it against published Medicare rules.** Six rules: dates outside the loaded data, duplicates,
   unbundled code pairs, unit limits, invalid codes and price outliers (more than 3x the Medicare national rate).
   Rules decide every finding; AI never does. The same claim always gives the same findings.
3. **Explains each finding** in at most three plain sentences. Any dollar amount or percentage the AI writes
   must appear in the evidence, or the explanation is thrown away.
4. **Keeps billing errors, price outliers and "leads" apart.** A price outlier is not an error (providers set
   their own prices), so the letter only asks for an itemized justification. A lead (for example a code that is
   invalid for Medicare on a commercial plan) is worth checking but never goes into a letter or a total.
5. **Drafts the dispute letter** from accepted findings only, from a template. The reviewer can edit it but
   cannot add amounts or links that were not in the findings, then approves and downloads it as a text or Word
   file. PriorPath never sends anything; the person does.
6. **Logs every action** (upload, audit, accept, reject, letter) in an audit trail.

## What it does not do

- It does not decide that a provider acted wrongly, and it is not legal, medical or billing advice. Findings are
  rule-based estimates for a person to review.
- It does not check diagnosis-to-procedure fit, upcoding, global surgery periods, or modifier misuse beyond the
  NCCI rules.
- It does not know your contract rates. Prices are compared with the Medicare national rate only.
- It does not read X12 835/837 electronic claim files, submit appeals or send letters.
- It covers dates of service from July to December 2026 (the Medicare releases loaded today). Other dates are
  reported as "cannot audit", never silently passed.

## Time saved per bill: a hypothesis, not a result

**Hypothesis:** a reviewer using PriorPath spends their time deciding on a short list of findings, with the
table row already attached, instead of looking up every line in the Medicare tables. For a typical multi-line
bill that should take noticeably less time per bill and miss fewer findings.

**This has not been measured.** PriorPath has only been run on synthetic bills, and no reviewer study has been
done. A pilot would measure it like this:

1. Take a fixed set of real, already-adjudicated bills (for example 50), split at random into two halves.
2. Have the same reviewers audit one half by their current method and the other half with PriorPath, then swap
   on a second set so each reviewer does both.
3. Record minutes per bill (from opening the bill to a final list of findings), findings per bill, findings
   later confirmed by the provider or payer, and PriorPath findings the reviewer rejected (with reasons).
4. Compare per-bill time and confirmed findings between the two methods, and read the rejected findings to see
   where the rules or the PDF reading fall short.

## How accurate it is today (on synthetic data)

- **Rules:** on 300 synthetic claims with planted errors, every planted error was found and nothing else was
  flagged (precision and recall 1.000 for every rule). These claims are built from the same rules, so this shows
  the rules do what they say, not that they match every payer's real adjudication.
- **PDF reading:** on 30 synthetic bills in three layouts (half with simulated scan noise), the vision model read
  150 of 153 lines exactly (line F1 0.980, re-recorded with redaction on; the gate still passes with redaction
  on, same as before), and every planted error was still found after reading. Real bills will score lower. The
  current numbers are in [`evals/results/`](../evals/results/).
- **Explanations:** a separate AI judge checks each demo explanation against its finding; 14 of 15 were judged
  faithful (0.933). The one miss said a price outlier "requires" asking for an itemized justification where the
  rule only "justifies" asking, an overclaim the judge caught. Details are in
  [`evals/results/`](../evals/results/). It covers 15 demo explanations, so it is a smoke signal, not a precise
  measurement.

## Risks and limits

- **Synthetic data only.** Everything has been built and tested on made-up claims and bills. Do not upload real
  patient information to the public demo.
- **Not HIPAA compliant.** There are no Business Associate Agreements with any vendor, no user accounts, and PDF
  page images go to a hosted AI model. Names, phone numbers, IDs and addresses are blacked out on PDF pages that
  have a text layer, but this is best-effort: scanned pages cannot be redacted, and the detector can miss things.
- **AI reads PDFs imperfectly.** Low-confidence lines go to human review, but a confident misreading is possible.
  Lines read from a PDF should be checked against the page.
- **Medicare benchmarks only.** A "price outlier" is relative to Medicare's national rate, not your plan's
  contract.
- **Short retention.** The demo deletes each workspace after 24 hours (up to about 48 hours in practice).

## What a pilot would need

- **Contracts:** signed BAAs with every vendor that would touch claims data (hosting, database, AI model
  provider, tracing), or vendors replaced with ones that will sign. The AI model path needs to be covered end to
  end (a single provider endpoint under a BAA, or a self-hosted model).
- **Access control:** real sign-in (single sign-on or multi-factor), roles for reviewers and approvers, and a
  per-user audit trail kept for as long as your policy requires, instead of anonymous browser workspaces.
- **Your rates:** the payer contract rates (or an allowed-amount file) to compare prices against, instead of the
  Medicare national rate.
- **Your claim format:** X12 837 (claims) and 835 (remittance) input, or whatever export your claims system
  produces, alongside FHIR and PDF.
- **Current Medicare data:** the quarterly NCCI, MUE and fee schedule releases for the dates of service in the
  pilot, with the licence terms accepted.
- **A measurement plan:** the time-per-bill study above, agreed before the pilot starts.
