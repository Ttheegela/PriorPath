# Reference data sources

| Table | Release | File(s) | URL | Downloaded |
|---|---|---|---|---|
| NCCI PTP (practitioner) | v32.2, 2026 Q3 (effective 2026-07-01), 4 parts | ccipra-v322r0-f1.TXT, ccipra-v322r0-f2.TXT, ccipra-v322r0-f3.txt, ccipra-v322r0-f4.txt | https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits/medicare-ncci-procedure-procedure-ptp-edits (zips: https://www.cms.gov/files/zip/medicare-ncci-2026q3-practitioner-ptp-edits-ccipra-v322r0-f1.zip, -f2, -f3, -f4) | 2026-10-02 |
| NCCI MUE (practitioner) | 2026 Q3 (effective 2026-07-01) | MCR_MUE_PractitionerServices_Eff_07-01-2026.csv | https://www.cms.gov/files/zip/medicare-ncci-2026-q3-practitioner-services-mue-table.zip | 2026-10-02 |
| PFS RVU | RVU26C (July release, updated 2026-06-30) | PPRRVU2026_Jul_nonQPP.csv | https://www.cms.gov/medicare/payment/fee-schedules/physician/pfs-relative-value-files/rvu26c (zip: https://www.cms.gov/files/zip/rvu26c-updated-06-30-2026.zip) | 2026-10-02 |
| NCCI PTP (practitioner) | v32.3, 2026 Q4 (effective 2026-10-01), 4 parts | ccipra-v323r0-f1.TXT, ccipra-v323r0-f2.TXT, ccipra-v323r0-f3.txt, ccipra-v323r0-f4.txt | https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits/medicare-ncci-procedure-procedure-ptp-edits (zips: https://www.cms.gov/files/zip/medicare-ncci-2026q4-practitioner-ptp-edits-ccipra-v323r0-f1.zip, -f2, -f3, -f4) | 2026-10-01 |
| NCCI MUE (practitioner) | 2026 Q4 (effective 2026-10-01) | MCR_MUE_PractitionerServices_Eff_10-01-2026.csv | https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits/medicare-ncci-medically-unlikely-edits (zip: https://www.cms.gov/files/zip/medicare-ncci-2026-q4-practitioner-services-mue-table.zip) | 2026-10-01 |
| PFS RVU | RVU26D (October release, updated 2026-08-26) | PPRRVU2026_Oct_nonQPP.csv | https://www.cms.gov/medicare/payment/fee-schedules/physician/pfs-relative-value-files/rvu26d (zip: https://www.cms.gov/files/zip/rvu26d-updated-08-26-2026.zip) | 2026-10-01 |

Only code numbers, edit dates, indicators, unit limits and computed national rates are committed.
CPT descriptors (AMA copyright) are not committed.

R4 treats PFS status D and I as not billable to Medicare.

Releases are versioned by date of service (Q3: 2026-07-01 to 2026-09-30, Q4: 2026-10-01 to 2026-12-31).
`load_normalized` rejects two versions of the same kind with overlapping dates. For the 100 codes in
`codes.txt`, the Q3 and Q4 rows happen to be identical (the full raw files differ); flags still record
which release they used.
