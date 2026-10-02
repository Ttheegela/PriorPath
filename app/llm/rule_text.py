RULE_TEXT = {
    "R0": "PriorPath can only audit dates of service covered by the CMS reference releases it has loaded; "
    "lines outside those dates are not checked.",
    "R1": "Billing the same service, with the same modifiers and units, more than once on the same date is a "
    "duplicate charge unless a repeat-procedure modifier explains it.",
    "R2": "CMS National Correct Coding Initiative (NCCI) procedure-to-procedure edits list code "
    "pairs where the "
    "column 2 code is part of the column 1 code and should not be billed separately on the same date. "
    "Modifier indicator 0 means never billed together; indicator 1 means allowed only with an appropriate "
    "modifier.",
    "R3": "CMS Medically Unlikely Edits (MUE) set the maximum units of a service that one provider would "
    "normally report for one patient, per claim line or per date of service.",
    "R4": "Physician Fee Schedule status D codes were deleted, and status I codes are not valid for "
    "Medicare, "
    "which requires a different code.",
    "R5": "A charge far above the Medicare national payment rate for the same service is not an "
    "error by itself, "
    "but it justifies asking the provider for an itemized justification.",
}
