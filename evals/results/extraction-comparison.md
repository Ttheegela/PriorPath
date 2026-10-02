# PDF extraction: candidate model comparison

Recorded 2026-10-02 with `python -m evals.extract_eval --n 30 --seed 11 --record <model>` on 30 synthetic itemized bills (3 layouts; half rendered as noisy scans). Gates: line F1 ≥ 0.95 and end-to-end recall ≥ 0.90 per rule (support ≥ 3), zero negative-plant false positives.

| Model | Price (input / output per M tokens) | Pages read | Line F1 | End-to-end recall (R1–R5) | Negative-plant FP | Result |
|---|---|---|---|---|---|---|
| **`google/gemini-2.5-flash-lite`** | $0.10 / $0.40 | 30 / 30 | **0.980** | 1.000 on every rule | 0 | **Chosen** — promoted to `evals/recorded/extraction.json` |
| `google/gemini-3.1-flash-lite` | $0.25 / $1.50 | 1 / 30 | — | — | — | Excluded: OpenRouter returned `finish_reason: "error"` on the same page in two separate runs (deterministic failure with our strict JSON-schema request) |

Breakdown for the chosen model (from `evals/results/extraction.md`):

| Group | Line F1 |
|---|---|
| table layout | 1.000 |
| compact layout | 1.000 |
| statement layout (two rows per line) | 0.930 |
| clean pages | 0.987 |
| noisy (scan-like) pages | 0.973 |

150 of 153 lines were read exactly (code, units, charge); no rows were dropped by validation.

Demo bills (`scripts/build_demo.py --bills --extract`, same model): B0001 5/5 lines and B0002 6/6 lines exact, including a planted duplicate and a facility place of service (22) on the noisy bill.

Notes:
- All data is synthetic; real bills (other layouts, handwriting, multi-column statements) will score lower. Treat these numbers as a regression gate, not a production accuracy claim.
- The first recording showed the model returns confidence 0 for lines with no modifiers; the parser now treats an empty modifier list as a normal reading (commit e7944e3), so clean bills no longer go to line review for that reason.
