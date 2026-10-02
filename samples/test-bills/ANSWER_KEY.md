# PriorPath test bills (synthetic)

Regenerate with `PYTHONPATH=. python scripts/make_test_bills.py`.
Upload each PDF with the synthetic-bill box ticked.
Every bill header carries the same fake identifiers: Alex Example (patient name), 1200 Maple Avenue, Springfield, IL 62704 (address), (217) 555-0143 (phone), XQH4471029 (member ID).

- **TEST-01-table.pdf**: 4 lines; rules: R3. Redaction: the four identifiers above are blacked out; codes, units, charges and dates stay.
- **TEST-02-statement.pdf**: 6 lines; rules: R5. Redaction: the four identifiers above are blacked out; codes, units, charges and dates stay.
- **TEST-03-compact.pdf**: 7 lines; rules: R2. Redaction: the four identifiers above are blacked out; codes, units, charges and dates stay.
- **TEST-04-table-scanned.pdf**: 4 lines; rules: R1. Redaction: scanned page, so nothing is blacked out (not redactable); identifiers are sent as they are.
- **TEST-05-statement-scanned.pdf**: 8 lines; rules: R3, R4. Redaction: scanned page, so nothing is blacked out (not redactable); identifiers are sent as they are.
- **TEST-06-compact-scanned.pdf**: 7 lines; rules: R2. Redaction: scanned page, so nothing is blacked out (not redactable); identifiers are sent as they are.
- **sample-claims-fhir.json**: the claim from TEST-01 as a FHIR bundle (no AI model); rule: R3.
