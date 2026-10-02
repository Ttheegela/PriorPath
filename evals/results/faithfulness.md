Judge: `google/gemini-2.5-flash`; n=15 demo explanations; judged: 15; unjudged (junk output): 0

n=15 demo explanations; rates are indicative only; one miss moves the rate by ~0.067.

Faithfulness: **0.933** (14/15; gate 0.9). Unjudged items count as not faithful.

| Rule | Faithful | n | Rate |
|---|---|---|---|
| R1 | 4 | 4 | 1.000 |
| R3 | 4 | 4 | 1.000 |
| R4 | 4 | 4 | 1.000 |
| R5 | 2 | 3 | 0.667 |

Rules with no explanations: R0, R2

## Unfaithful explanations

- `d78ab3832a07` R5 (B0002): The charge for code 17003 is more than three times the Medicare national rate, which is considered an outlier. This finding requires asking the provider for an itemized justification. The estimated overcharge is $11.85.
  - Unsupported: This finding requires asking the provider for an itemized justification.
  - Reason: The rule states that a charge far above the Medicare national payment rate 'justifies asking the provider for an itemized justification,' not that it 'requires' it. The explanation misstates the meaning of the rule by using a stronger term.
