# Execution ledger — plan: docs/superpowers/plans/2026-11-01-caption-integrity.md

Spec approved by user on 2026-11-01. Implementation has started.

**RESUMED at user's explicit `continue`, 2026-11-01.**
Recovery source: `docs/2026-11-01-caption-integrity-handoff.md`.
Work is saved uncommitted on `dev`.

Current evidence: real repeated-fixture replay fixed to 15 captions. Full 36-script suite passed
with zero failures/skips (16.2 s), after nonfinal flush, rejected/empty/failed final retirement,
timeout/stop handling, CLI cancellation/input-failure cleanup and overlap-only EOF handling
were fixed with red-green regressions.
Final review package: `runs/caption-integrity/final-review-package.txt` (CLI cleanup added after package).

Task 1: complete — baseline inventory, original service replay and test runner saved.
Task 2: complete — assembler and actual-output overlap regressions saved.
Task 3: complete — ordered window/MT integration, failure and EOF regressions pass.
Task 4: complete for approved observation-only fallback — no automatic veto enabled.
Task 5: verification/docs complete; independent final review found stale-source MT finalization.
Fixed with translation/source association, final-empty renderer clearing and raw fallback.
Producer/store/overlay/export red-green regressions pass; scoped re-review approved.
Task 5: complete for code/tests/review/docs delivery; external acceptance limits remain documented.

Ruling: final rejection/empty/failure retires the current pending draft immediately with
`asr_unconfirmed`, rather than retaining an unverified continuation for normal finish — no guard
rejection may promote an earlier guess — cost: a failed decode can interrupt a logical long sentence;
the draft and diagnostic survive, but it is excluded from official history.

Ruling: confirmed source text survives MT failure/timeout as a raw final with
`translation_incomplete`; only a translation produced for the current raw may remain.
An existing translation may be partial and must not be treated as verified.

External validation still incomplete: no new manual transcript labels, natural >10-second sentence
acceptance, held-out hallucination calibration, or calibrated automatic veto. Observation-only fallback
is the approved delivery in this situation; see `docs/2026-11-01-caption-integrity-validation.md`.

| Tasks | Shared interface / files | Preflight finding |
|---|---|---|
| 1 / 2 | WindowDecode and actual long-sentence failure | Tests exercise production assembler; real audio is separately labelled |
| 2 / 3 | WindowDecode, UtteranceUpdate, ingest/flush | Parent owns integration; worker owns assembler only |
| 3 / 4 | observation hook and config | Parent owns config/orchestrator; worker owns detector/record construction |
| 1 / 5 | local audio and baseline artifacts | Baseline remains ignored and no fake-model output is called real ASR evidence |
| 1 | baseline and runner | No production behavior change required |
| 2 | stable IDs, alignment and caps | Uncertain alignment cannot silently delete text |
| 3 | bounded input and queue lifecycle | No obsolete translation can finalize newer text |
| 4 | optional evidence | Observation is safe to deliver independently of an unvalidated rejection policy |
| 5 | verification and docs | Report both passed tests and unavailable empirical checks |

Ruling: reuse current dev checkout, with no unrelated source edits; do not create a worktree — local models and fixtures are available here — cost if wrong: code changes share the current checkout but remain reviewable and uncommitted.

Ruling: default speech evidence remains off; configured observation never rejects by itself — existing experiments show detector false negatives — cost if wrong: hallucinations persist until calibrated, held-out evidence supports filtering.
