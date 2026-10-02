Model: `google/gemini-2.5-flash-lite`; pages read: 30; rows dropped by validation: 0

| Line metric | Value |
|---|---|
| True lines | 153 |
| Extracted lines | 153 |
| Matched (code, units, charge) | 150 |
| Precision | 0.980 |
| Recall | 0.980 |
| F1 | 0.980 (gate 0.95) |

End-to-end: rules run on claims rebuilt from extracted lines, scored against the original labels (recall gate 0.9 for rules with support >= 3; negative-plant false positives 0).

| Group | Pages | Precision | Recall | F1 |
|---|---|---|---|---|
| clean | 15 | 0.987 | 0.987 | 0.987 |
| compact | 10 | 1.000 | 1.000 | 1.000 |
| noisy | 15 | 0.973 | 0.973 | 0.973 |
| statement | 10 | 0.930 | 0.930 | 0.930 |
| table | 10 | 1.000 | 1.000 | 1.000 |
| Rule | Support | TP | FP | FN | Precision | Recall | Neg plants | Neg FP |
|---|---|---|---|---|---|---|---|---|
| R1 | 5 | 5 | 0 | 0 | 1.000 | 1.000 | 2 | 0 |
| R2 | 5 | 5 | 0 | 0 | 1.000 | 1.000 | 4 | 0 |
| R3 | 7 | 7 | 0 | 0 | 1.000 | 1.000 | 0 | 0 |
| R4 | 4 | 4 | 0 | 0 | 1.000 | 1.000 | 0 | 0 |
| R5 | 3 | 3 | 0 | 0 | 1.000 | 1.000 | 5 | 0 |

Claims: 30 (clean: 11, clean with flags: 0); FHIR parse errors: 0
