# Evals

Run: `python -m evals.run --n 300 --seed 7` (deterministic; writes `evals/results/latest.{md,json}`).
CI runs the same command and then `git diff --exit-code evals/results`, so the committed table must match the code.

## What the eval gate covers

The generator (`evals/generate.py`) builds synthetic claims from real codes in the committed CMS subset
(`data/reference/subset`), plants labeled errors, and sends every claim through the FHIR writer and parser
before the rules run. The gate (`evals/run.py`) fails CI when any of these is not met:

| Check | Gate |
|---|---|
| Precision and recall for R1 to R5 (errors and outliers only) | 1.000 each |
| Positive support per rule | at least 10 |
| Claims with no positive plant (negative plants allowed) with any error or outlier flag | 0 |
| FHIR parse errors | 0 |
| Negative plants flagged by the rule they target ("Neg FP") | 0 |
| Negative-plant support for R1, R2 and R5 | at least 10 each |

**Positive plants** (must be flagged): R1 exact duplicate line; R2 NCCI pair with modifier indicator 0 or 1
and no bypass modifier; R3 units above the MUE limit; R4 PFS status D or I code; R5 office line priced above
3x the non-facility rate, or a facility line (place of service 22) priced between 3x the facility rate and
3x the non-facility rate.

**Negative plants** (must not be flagged by the named rule). About 40% of claims get one, added after the
positive plants and on codes that do not interact with them:

| Kind | Rule kept quiet | What is planted |
|---|---|---|
| `R1-repeat-76-91` | R1 | One line plus two identical repeats that both carry 76 (repeat procedure) or 91 (repeat lab test). The two repeats match on code, modifiers, date and units, so only the 76/91 exemption stops R1. |
| `R2-ind1-59-XS` | R2 | An NCCI pair with modifier indicator 1, with 59 or XS on the column-2 line. |
| `R5-26-TC` | R5 | A line with modifier 26 or TC priced 3.5x to 6x the non-facility rate (the PFS rate is the global rate; component lines are not compared to it). |
| `R5-office-band` | R5 | An office line (place of service 11) priced between 3x the facility rate and 3x the non-facility rate. |

**Which rate R5 uses.** `app/rules/price.py` compares a line at a facility place of service (`FACILITY_POS`,
e.g. 21, 22, 23) with the facility rate and every other line with the non-facility rate. So a price between
3x facility and 3x non-facility is an outlier at a hospital (positive plant) and normal in an office
(negative plant). Together the two plants check that R5 picks the right rate in both directions.

The negative plants were checked by mutation: removing the 76/91 exemption, the NCCI bypass modifiers or the
26/TC skip, or using the facility rate in offices, each fails the gate.

**Not covered:**
- PDF extraction and end-to-end PDF recall. These are scored by a separate eval, `python -m evals.extract_eval`
  (line F1 gate 0.95, end-to-end recall gate 0.90, replayed from a recording in `evals/recorded/`), with results
  in `evals/results/extraction.md`.
- Letter quality. The explanation number check has unit tests but no gate here.
- Explanation faithfulness is scored by `python -m evals.faithfulness`: a judge model (default
  `google/gemini-2.5-flash`, not the explanation model) checks each explanation in `data/demo/explanations.json`
  against its flag (rule text, severity, message, evidence row, estimated overcharge). The flags are rebuilt by auditing
  the demo claims, and an explanation matching no flag is an error. Faithfulness = faithful / total, with junk judge
  output counted as unjudged and not faithful; the gate is 0.90 when replaying `evals/recorded/faithfulness.json`
  (record with `--record MODEL`, needs `OPENROUTER_API_KEY`). Results and every unfaithful item are in
  `evals/results/faithfulness.md`. Result: 0.933 (14/15; gate 0.90 passed). The one unfaithful item (R5, B0002) said an
  outlier "requires" asking for an itemized justification where the rule only "justifies" asking. It is an LLM judge
  over 15 demo explanations, so treat the rate as a smoke signal, not a precise measurement.
- Payer-aware R4 (status I is a lead for non-Medicare payers); the eval claims are Medicare.
- MUE adjudication edge cases beyond per-line vs per-day, and NCCI deletion dates inside a quarter (unit-tested only).
- Real bills. Every claim is synthetic, built from the same rules the engine encodes, so a perfect score shows the
  rules do what they say, not that they match every payer's real adjudication.
