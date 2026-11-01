# Caption Integrity Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development for independent modules and inline integration. Steps use checkboxes to preserve execution progress. Do not create or merge commits without a separate need; deliver a reviewable working diff.

**Goal:** Preserve logical sentences across bounded ASR windows and measure background-audio hallucinations before enabling automatic rejection.

**Architecture:** Overlapping audio windows feed an ordered, per-source utterance assembler. Logical caption updates retain an ID until commitment; translation requests freeze text/history/revision. Optional speech evidence produces observations before caption/history publication and never acts as a hard gate in this delivery without real-audio validation.

**Tech Stack:** Python 3.11+, asyncio, NumPy, existing HTTP ASR/MT clients; optional ONNX Runtime for local Silero observation.

**Spec:** `docs/2026-11-01-caption-integrity-design.md`

**Execution, 2026-11-01:** implemented in the current dev working tree; 36 script tests pass,
zero failures/skips. Whole-change review and scoped re-review of the reported defect are complete.
Real fixtures and limitations are recorded in
`docs/2026-11-01-caption-integrity-validation.md`; natural long-sentence labels and held-out
hallucination calibration remain external validation requirements, not completed acceptance.

## Global Constraints

- Preserve protocol v1 and existing source/audio/service defaults except the reviewed window/utterance behavior.
- Each source owns its new sentence and detector state. Do not expand scope into complete dual-source translation history isolation.
- Default maximum window 10 s; overlap experiment 1.5 s. Pending sentence/audio budgets 30 s and 1000 characters.
- Do not use punctuation, a 120 ms gap, repeated recognition, or a negative VAD result as sole evidence of truth/completion.
- Detection is optional, offline, path-configured, and observation-only unless validation proves an automatic policy safe.
- Never read the obsolete `PLAN.md`. Preserve local models, samples and ignored experimental files.
- Existing baseline: 25 script tests passed in the previous turn. Re-run before editing production behavior.
- Ruling: work in current `dev`, where only this task's design is untracked; preserve local model/sample access and avoid an unnecessary worktree consent round.

## Task 1: Baseline and deterministic reproductions

- [x] Inventory WAV durations and local model/runtime availability; probe localhost ASR/MT health.
- [x] Save original outputs to `runs/caption-integrity/baseline/` where services are available. Use existing real fixtures; identify unlabelled data explicitly.
- [x] Add `tools/check.py` to execute selected or all `test/test_*.py` scripts with project import path, UTF-8, per-script timeout and offscreen Qt. Print skip/failure counts.
- [x] Run the 25 existing tests. Add regressions proving long tails are not prematurely committed and stale translation cannot overwrite the current revision.

Example assertion for the boundary failure:

```python
first = WindowDecode("w1", "file", 0.0, 10.0,
    "I do not think the proposal you mentioned yesterday.", True, "hard")
updates = assembler.ingest(first)
assert not any(update.final for update in updates)
```

## Task 2: Per-source utterance assembler

**Create:** `simulcast/pipeline/utterances.py`, `test/test_utterances.py`.

**Interface:** `WindowDecode(window_id, source, t0, t1, text, final=False, reason="", overlap_s=0.0)`;
`UtteranceUpdate(id, source, t0, t1, text, final, reason="")`;
`UtteranceAssembler(max_seconds=30.0, max_chars=1000).ingest(decode) -> list[UtteranceUpdate]`;
`flush(source: str | None = None, *, final=True, reason="") -> list[UtteranceUpdate]`.
Nonfinal close retires an unconfirmed pending draft without entering official history.

- [x] First write and run failing tests for hard/hard_pause boundaries, more than 8 words, CJK overlap, real repeated phrases, ordinary silence completion, repeated drafts, source isolation and bounded flushing.
- [x] Implement text overlap alignment with explicit uncertainty. A forced boundary never commits the last candidate just because of word count.
- [x] Keep a stable ID while revising an unfinished sentence and emit separate final IDs for completed sentences. Reuse that ID for the final update.
- [x] Signal ambiguous overlap through an update reason; retain text until a bounded boundary repair or explicit degraded finish. Do not silently remove unmatched words.
- [x] Run module tests and self-review for retained/bounded state.

## Task 3: Audio windows, ordered integration and translation snapshots

**Modify:** `audio/segmenter.py`, `pipeline/orchestrator.py`, `config.py`, `config/default.toml`.
**Tests:** `test/test_windows.py`, `test/test_window_pipeline.py`, `test/test_context.py`.

- [x] Write failing tests for overlapping hard windows capped at max input, ordered decode ingestion and same-ID finalization.
- [x] Add/validate `window_overlap_s`, `utterance_max_s`, `utterance_max_chars` and `audio_buffer_s`.
- [x] Route actual ASR window decodes through the assembler; serialize boundaries within each source, coalesce superseded drafts and retain final jobs. Keep bounded audio for repair.
- [x] Translate immutable queue snapshots and check revision both during streaming and after iterator completion, including streams with no final delta.
- [x] Wait for ASR tasks and queue task completion before flushing/stopping. Finalization must include the last carried sentence and in-flight MT request.
- [x] Update tests whose previous expected behavior intentionally split a long sentence; preserve regression coverage for previous busy/eviction/history fixes.

Immutable MT job example:

```python
text, history, revision = seg.raw, tuple(seg.history), seg.mt_rev
async for delta in mt.stream(text, list(history), draft=not final):
    if revision != seg.mt_rev:
        return
    # Publish only this version's result.
if revision != seg.mt_rev:
    return
```

## Task 4: Optional speech-evidence observation

**Create:** `audio/speech_evidence.py`, `asr/verification.py`, `test/test_speech_evidence.py`, `test/test_verification.py`.
**Parent integration:** configuration loading and `_asr_run` observation, never rejection in this delivery without held-out evidence.

**Interface:** `SpeechEvidence(mean_probability, speech_fraction, seconds)`;
`SileroEvidence(model_path).measure(pcm, sample_rate=16000) -> SpeechEvidence` resets state per independent audio window;
`observation(*, source, window_id, t0, t1, text, evidence, guard_reason="") -> dict` produces a protocol-v1 `asr_observation` record.

- [x] Test partial 512-sample windows, empty input, sample-rate validation and state reset with an injected inference session at the ONNX boundary.
- [x] Implement lazy optional dependency loading, validate local model configuration, and report actionable configuration errors.
- [x] Validate observation data and test that weak evidence alone does not remove captions or affect translation history.
- [x] Integrate optional `asr.speech_evidence_model` / `asr.speech_evidence_mode = "off" | "observe"` and record evidence before caption publication.
- [x] Run real local Silero/ASR comparisons where available; label unreviewed transcripts and subtitle-derived references accurately.
- [x] Do not turn observation into a rejection gate if hard-voice examples reveal false negatives or labels/services are unavailable.

## Task 5: Integration review, replay and documentation

- [x] Run all script tests and review the full diff, including source switching, failures, buffer limits, UI order and ID behavior.
- [x] Compare original/current real-fixture outputs where services are available; retain model parameters, runtimes and failures in ignored artifacts.
- [x] Update `docs/current-state.md` with implemented behavior and limitations, not projected gains.
- [x] Update this plan with actual test results and remaining external validation requirements.
- [x] Deliver code, tests and artifact paths. Do not merge, push or claim hallucinations solved from stub tests.
