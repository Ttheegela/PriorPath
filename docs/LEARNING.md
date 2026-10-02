# Learning notes

What building PriorPath v2 taught me: decisions that changed once real data or a real platform got involved,
what the code reviews caught, and what I would do differently. The full build log, with every review finding per
plan, is [`PROGRESS.md`](PROGRESS.md).

PriorPath v2 was built in five plans over two days (2026-10-01 to 2026-10-02) with subagent-driven development:
a fresh implementer per task, writing tests first, a separate reviewer per task, scoped re-reviews for every fix
round, and a whole-branch review before each deploy.

## Decisions that changed

**R4 "invalid code" became status D *or* I, then payer-aware.** The plan was to flag deleted codes (PFS status D).
The real 2026 Q4 fee schedule file has no status-D codes at all, so a rule for D alone would never fire. R4 now
also flags status I ("not valid for Medicare"). The Plan 1 final review then pointed out that status I is a
Medicare rule: codes like 80320 and 77061 are valid CPT codes that commercial plans may pay. Once claims carried a
payer type, status I became a *lead* for non-Medicare payers, kept out of letters and totals. Lesson: read the
actual reference file before writing the rule, and ask who the rule is true for.

**The "better" explanation model failed outright.** Comparing models on the 12 demo flags,
`deepseek/deepseek-v4-pro` produced 0 of 12 grounded explanations and `deepseek/deepseek-v4-flash` 11 of 12, at a
fifth of the price. The likely cause: v4-pro is a reasoning model, and its reasoning tokens count against the
300-token output cap, so it ran out before writing an answer. The same thing came back in Plan 5 with the
faithfulness judge: `gemini-2.5-flash` thinks too, and review found its first 600-token cap would likely end in a truncated
(`finish_reason: length`) answer, so the judge got 2,000 tokens and low reasoning effort. Lesson: a token cap means something different for a reasoning model;
check `finish_reason` and evaluate on your own task before picking the bigger model.

**A root `public/` folder is not served on Vercel.** The plan was to build the UI into `public/` and let Vercel
serve it statically next to the Python function. A preview deploy showed that a `public/` folder created during
the build is not served. The fix was FastAPI's `app.frontend()` for the routes plus
`[tool.vercel.fastapi.static] exclude = true`, which lets Vercel serve the same files from its CDN. Lesson:
deploy a preview of the riskiest platform assumption on day one; it took one deploy to find, and it would have
been expensive to find at launch.

**pdfium is not thread-safe.** The Plan 4 final review ran 16 concurrent page renders and the process crashed.
pypdfium2 wraps a C library with global state, so every open, render and close now holds one process-wide lock.
That serializes PDF work per instance (acceptable for a demo; the documented upgrade path is rendering in a
separate worker process). Lesson: "it's a Python library" doesn't make a native library safe under a threaded
server; test concurrency for anything with C underneath.

**Empty modifiers had confidence 0.** The first real extraction recording showed the vision model returning
confidence 0 for the modifiers of every line that had none, so every clean bill went to line review. The parser
now treats an empty modifier list as a normal reading (confidence 1), and the prompt asks for it explicitly.
Lesson: confidence scores from a model are not calibrated in the way you assume; look at the raw recorded output
before trusting a threshold.

**Place of service had to be extracted.** R5 compares a charge with the facility rate at a hospital and the
non-facility rate elsewhere, so a PDF line without POS can't be priced correctly. All three bill layouts now
print POS, the extraction schema has it, and an unreadable POS keeps the line but sends it to review instead of
dropping it (the first version dropped the whole line). Lesson: trace every field a rule reads back to every input
path that has to supply it.

**The eval gate needed negative plants.** With only positive plants (errors that must be flagged), a rule that
flags too much still scores well on recall. Plan 5a added traps a rule must *not* flag: repeats with modifier 76
or 91, an indicator-1 NCCI pair with 59 or XS, 26/TC lines priced high, and office lines priced between 3x the
facility rate and 3x the non-facility rate. Each was checked by mutation: removing the 76/91 exemption, the NCCI
bypass modifiers or the 26/TC skip, or using the facility rate in offices, each fails the gate. Lesson: a perfect
score means little until you've shown that breaking the code makes it imperfect.

**Other changes from the spec.** Letters are a template, not model-written (the one artifact sent to a third
party shouldn't be able to invent anything). Explanations stream over SSE, while the rules run inside the upload and audit requests. Rule text is a static dictionary rather than pgvector retrieval (six rules don't need a
vector store). The AI budget was split into separate explanation and PDF-page pools so one PDF can't use up a
visitor's explanations.

## What the reviews caught

The reviews found real bugs in almost every task. The pattern matters more than the count: most of them were
at a trust boundary, a platform limit or a place where two components disagreed about a number.

- **Grounding bypasses** (Plan 2): "$7.00", "5%", "$4k", "seven dollars" and digits from other scripts all
  slipped past the first number check. It became a typed check (money, percent, plain count) with currency
  markers, number words and ASCII-only digits.
- **Unbounded cost** (Plan 2): SDK retries on top of the graph's retry allowed up to 4 API calls per
  explanation, and the final review found no global AI cap, no storage limit and no stream deadline. All were
  fixed before the first production deploy.
- **A cache hiding a broken key** (Plan 2 deploy): demo explanations are precomputed, so production looked fine
  with an invalid OpenRouter key. The smoke test gained `--require-explanations`, and a live upload is part of
  every release check.
- **Thresholds that disagreed** (Plan 4): the UI marked fields below 0.8 confidence while the backend sent lines
  below 0.9 to review, so a case could need review with nothing marked. The client upload limit (4 MiB) also
  differed from the server's 4,000,000 bytes.
- **Platform limits** (Plan 4 final): a 10-page PDF could outrun the 300-second function limit; PNG pages could
  pass Vercel's 4.5 MB response limit. Fixes: a 240-second extraction deadline, 25-second model timeout, JPEG pages
  and a budget check for all pages before the first model call.
- **Tracing that could hang requests** (Plan 5): the Langfuse SDK's flush can wait 30 seconds plus queue joins,
  and it ran after every response. During a Langfuse outage that would have held every explanation open. Flush
  now runs in a daemon thread and the request waits at most 2 seconds; tracing failures are logged once.
- **Redaction, five review rounds** (Plan 5). The hardest task in the project:
  1. The small spaCy model tagged billing rows as person names, so dates, codes, units and a claim number were
     masked on 20 of 900 generated pages, which would silently break extraction. Over the following rounds, model
     spans were cut at the first word containing a digit and matches scoring below 0.5 were dropped; dates are
     not a redacted type at all.
  2. A labelled patient name could disappear: Presidio's deduplication kept a wider spaCy span over the labelled
     match, then the digit filter discarded the wider span, leaving the name unmasked. Labelled fields now run
     outside Presidio.
  3. pdfium's text joins table cells with a single space, so an address could run into the billing column next
     to it. Lines are now split into columns from character positions, and every match is cut at its column
     edge.
  4. The column threshold then had to work across fonts: a fixed fraction of line height split identifiers at
     every space in Courier; a threshold relative to the line's own space width merged tight table cells; the
     pdfium-generated separator rule then split per-word-positioned text into single words.
  5. The final rule: a gap wider than 2.2 of the line's real spaces always breaks a column; a generated
     separator breaks it at a lower bar only on lines that contain real spaces. A fuzz run over 12 font and
     seed combinations masked no billing row and every header identifier. The remaining edge cases (Courier
     no-space cells, widely justified text, OCR layers) are documented in `SECURITY.md` rather than chased further.

## What I would do differently

- **Fuzz the redaction from the start.** Each review round found a new font or layout that broke the column
  logic, because the tests were hand-written examples. The generated-bill fuzz harness that finally settled it
  (several fonts, seeds and layouts, checking "no billing value masked, every identifier masked") should have
  been the first test, not the fifth.
- **Record raw model output on day one.** The empty-modifier confidence problem and the reasoning-token problem
  were both visible the moment I looked at real responses. Recording first, then writing the parser and thresholds
  against the recording, would have saved two fix rounds.
- **Put the prompt version in every recording.** Replays don't yet refuse a recording made with a different
  prompt version, so a prompt change can be replayed against stale output without warning.
- **Make `main` the deploy branch early.** v1 stayed on `main` while v2 was built, so production was deployed
  from a feature branch with the CLI, and a push to `main` would have deployed the old app. A short-lived
  v2 branch merged often would have kept Git deploys usable.
- **Fail fast on the database.** The health check has a 2-second statement timeout, but the connection itself
  has no connect timeout, so a database that never answers can still hold a request to the platform limit.
- **Isolate PDF parsing.** pdfium runs in-process on untrusted bytes under a global lock. A sandboxed worker
  process would fix both the security concern and the throughput ceiling.
- **Be clearer earlier that this is a demo for synthetic data.** Redaction, the synthetic-bill confirmation and
  the "not HIPAA compliant" checklist came in late plans. Writing `SECURITY.md` first would have made the PHI
  boundaries a design input instead of a retrofit.
