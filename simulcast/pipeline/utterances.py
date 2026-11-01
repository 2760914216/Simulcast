"""Bounded logical captions, independent of ASR window boundaries.

Caption times are proportional estimates, not word alignments. A forced window end
keeps its last candidate revisable. Overlap removal requires an anchored,
unambiguous textual match *and* actual overlapping audio; otherwise every word
is retained and the caller may re-decode the pending audio through ``repair``.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import blake2b
import math

from .sentences import split_sentences


@dataclass(frozen=True)
class WindowDecode:
    window_id: str
    source: str
    t0: float
    t1: float
    text: str
    final: bool = False
    reason: str = ""
    overlap_s: float = 0.0


@dataclass(frozen=True)
class UtteranceUpdate:
    id: str
    source: str
    t0: float
    t1: float
    text: str
    final: bool
    reason: str = ""


@dataclass
class _Source:
    window_id: str = ""
    window_t0: float = -math.inf
    window_t1: float = -math.inf
    window_final: bool = False
    # The incoming window replaces its entire previous draft contribution.
    base: str = ""
    base_uncertain: bool = False
    base_forced: bool = False
    forced: bool = False
    consumed: int = 0
    consumed_hash: bytes = b""
    committed_prefix: str = ""
    window_tail: str = ""
    pending_offset: int = 0
    base_context: str = ""
    base_context_offset: int = 0
    window_letter_count: int = 0
    base_window_letters: int = 0
    base_window_seconds: float = 0.0
    base_window_end: float = 0.0
    previous_candidate: str = ""
    pending: str = ""
    pending_id: str = ""
    t0: float = 0.0
    t1: float = 0.0
    audio_t0: float = 0.0
    audio_t1: float = 0.0
    audio_exact: bool = True
    uncertain: bool = False
    repair_locked: bool = False


def _cjk(char: str) -> bool:
    return ("\u3040" <= char <= "\u30ff" or "\u3400" <= char <= "\u9fff"
            or "\uac00" <= char <= "\ud7af")


def _join(left: str, right: str) -> str:
    if not left or not right:
        return left or right
    separator = "" if _cjk(left[-1]) and _cjk(right[0]) else " "
    return left.rstrip() + separator + right.lstrip()


def _letters(text: str) -> tuple[str, list[int]]:
    chars, positions = [], []
    for index, char in enumerate(text):
        if char.isalnum():
            folded = char.casefold()
            chars.extend(folded)
            positions.extend([index] * len(folded))
    return "".join(chars), positions


def _anchor(left: str, right: str, region: tuple[int, int] | None = None) -> tuple[int, int] | None:
    before, bpos = _letters(left)
    after, apos = _letters(right)
    before_limit, after_limit = region or (len(before), len(after))
    before_limit = min(len(before), before_limit)
    after_limit = min(len(after), after_limit)
    # Match only the old suffix to the new prefix. The new decode supplies the
    # matched wording/punctuation, allowing an artificial cut-period to change.
    for size in range(min(before_limit, after_limit), 1, -1):
        match = before[-size:]
        if match != after[:size]:
            continue
        start = bpos[-size]
        end = apos[size - 1] + 1
        cjk = any(_cjk(c) for c in match)
        if not cjk:
            if size < 4 or (start and left[start - 1].isalnum()):
                continue
            if end < len(right) and right[end].isalnum():
                continue
        # Repeated anchors have multiple plausible audio alignments. Retaining
        # them is safer than deleting intentional repetition on a text guess.
        periodic = any(match == match[:n] * (len(match) // n)
                       for n in range(1, len(match) // 2 + 1) if len(match) % n == 0)
        # Repetition elsewhere in a ten-second window says nothing about a
        # 1.5-second boundary. Check ambiguity only inside its temporal region.
        if (periodic or before[-before_limit:].count(match) != 1
                or after[:after_limit].count(match) != 1
                or size == len(before) == len(after)):
            return None
        return start, size
    return None


def _merge(left: str, right: str, overlap: bool) -> tuple[str, bool]:
    if not left or not right:
        return left or right, False
    if not overlap:
        return _join(left, right), False
    anchor = _anchor(left, right)
    if anchor is None:
        return _join(left, right), True
    return left[:anchor[0]] + right, False


def _merge_context(context: str, pending_offset: int, right: str,
                   region: tuple[int, int] | None = None) -> str | None:
    """Remove only already committed words inside a proven overlap anchor."""
    anchor = _anchor(context, right, region)
    if anchor is None:
        return None
    start, _ = anchor
    if start >= pending_offset:
        return context[pending_offset:start] + right
    committed_letters, _ = _letters(context[start:pending_offset])
    _, positions = _letters(right)
    # Map the old pending onset through the normalized overlap, allowing raw
    # punctuation/spacing/case to change before that onset in the new window.
    index = len(committed_letters)
    raw_start = positions[index] if index < len(positions) else len(right)
    return right[raw_start:]


def _digest(text: str) -> bytes:
    return blake2b(text.encode("utf-8"), digest_size=16).digest()


def _revised_prefix_end(text: str, committed: str) -> int | None:
    """Align only complete candidates at the beginning of the same window.

    Normalized punctuation revisions and a small prepended word can change raw
    offsets. Matching the first candidate(s), rather than searching globally,
    keeps a genuinely repeated later sentence in the pending remainder.
    """
    if not committed:
        return None
    end = 0
    for part in split_sentences(text):
        start = text.find(part, end)
        end = start + len(part)
        candidate, _ = _letters(text[:end])
        if candidate == committed:
            return end
        extra = len(candidate) - len(committed)
        if (len(committed) >= 12 and 0 < extra <= max(4, len(committed) // 5)
                and candidate.endswith(committed)):
            return end
        if len(candidate) > len(committed) + max(4, len(committed) // 5):
            break
    return None


class UtteranceAssembler:
    def __init__(self, max_seconds: float = 30.0, max_chars: int = 1000):
        if not math.isfinite(max_seconds) or max_seconds <= 0 or max_chars <= 0:
            raise ValueError("utterance budgets must be positive and finite")
        self.max_seconds = max_seconds
        self.max_chars = max_chars
        self._sources: dict[str, _Source] = {}
        self._serial = 0

    def _update(self, source: str, state: _Source, text: str,
                final: bool, reason: str) -> UtteranceUpdate:
        if not state.pending_id:
            self._serial += 1
            state.pending_id = f"u{self._serial}"
        update = UtteranceUpdate(state.pending_id, source, state.t0, state.t1,
                                 text, final, reason)
        if final:
            state.pending_id = ""
        return update

    def ingest(self, decode: WindowDecode) -> list[UtteranceUpdate]:
        if not (math.isfinite(decode.t0) and math.isfinite(decode.t1)) or decode.t1 < decode.t0:
            raise ValueError("invalid decode audio interval")
        state = self._sources.setdefault(decode.source, _Source())
        same = state.window_id == decode.window_id
        if decode.t0 < state.window_t0:
            return []
        if same and (state.repair_locked or decode.t1 < state.window_t1
                     or (state.window_final and (not decode.final or decode.t1 <= state.window_t1))):
            return []
        if not same:
            # Equal-start windows are not ordered by arbitrary string IDs.
            if decode.t0 == state.window_t0:
                return []
            state.base = state.pending
            state.base_uncertain = state.uncertain
            state.base_forced = state.forced
            state.base_context = state.window_tail
            state.base_context_offset = state.pending_offset
            state.base_window_letters = state.window_letter_count
            state.base_window_seconds = state.window_t1 - state.window_t0
            state.base_window_end = state.window_t1
            state.consumed = 0
            state.consumed_hash = b""
            state.committed_prefix = ""
            state.previous_candidate = ""
            state.repair_locked = False
        if not state.pending:
            state.t0 = max(decode.t0, state.t1)
            state.audio_t0 = decode.t0
            state.audio_exact = True
        state.window_id = decode.window_id
        state.window_t0 = decode.t0
        state.window_t1 = decode.t1
        state.window_final = decode.final
        state.t1 = decode.t1
        state.audio_t1 = decode.t1
        state.forced = decode.final and decode.reason in ("hard", "hard_pause")
        if not decode.text.strip():
            # Empty recognition is not a textual retraction. In particular a
            # stop/silence result must still finish the last usable draft.
            if state.pending and decode.final and decode.reason not in ("hard", "hard_pause"):
                update = self._update(decode.source, state, state.pending, True,
                    "alignment_degraded" if state.uncertain else (decode.reason or "final"))
                state.pending = state.base = state.previous_candidate = ""
                state.uncertain = state.base_uncertain = False
                state.repair_locked = True
                state.consumed = 0
                state.consumed_hash = b""
                return [update]
            return []
        # The punctuation at a forced cut is provisional; removing it changes
        # no recognized words and prevents it becoming a false interior split.
        base = state.base.rstrip(".!?。！？…") if state.base_forced else state.base
        state.window_letter_count = sum(len(char.casefold()) for char in decode.text if char.isalnum())
        text, uncertain = _merge(base, decode.text.strip(), decode.overlap_s > 0)
        if decode.overlap_s > 0 and state.base_context:
            # Without word timestamps, use each window's observed text density
            # to bound the area plausibly covered by its actual audio overlap.
            # Twice the average rate, with a 16 letters/s allowance for short
            # anchors, tolerates local speed variation while excluding a whole
            # repeated ten-second cycle as an explanation for only 1.5 seconds
            # of shared audio. These are estimates, not word timestamps.
            overlap = min(decode.overlap_s, max(0.0, state.base_window_end - decode.t0),
                          decode.t1 - decode.t0)
            if overlap > 0 and state.base_window_seconds > 0 and decode.t1 > decode.t0:
                region = (
                    math.ceil(overlap * max(16, 2 * state.base_window_letters / state.base_window_seconds)),
                    math.ceil(overlap * max(16, 2 * state.window_letter_count / (decode.t1 - decode.t0))),
                )
                aligned = _merge_context(state.base_context, state.base_context_offset,
                                         decode.text.strip(), region)
            else:
                aligned = None
            if aligned is not None:
                text, uncertain = aligned, False
            else:
                text, uncertain = _join(base, decode.text.strip()), True
        state.uncertain = state.base_uncertain or uncertain
        if state.consumed and _digest(text[:state.consumed]) != state.consumed_hash:
            revised_end = _revised_prefix_end(text, state.committed_prefix)
            if revised_end is not None:
                state.consumed = revised_end
            else:
                # Preserve the new proposal as uncertainty, without prepending
                # the old pending snapshot and duplicating it on every draft.
                state.consumed = 0
                state.uncertain = True
        return self._render(decode, state, text)

    def _render(self, decode: WindowDecode, state: _Source, text: str) -> list[UtteranceUpdate]:
        outputs: list[UtteranceUpdate] = []
        position = state.consumed
        rest = text[position:].lstrip()
        position = len(text) - len(rest)
        parts = split_sentences(rest)
        first = parts[0] if parts else ""
        stable_first = bool(first) and first == state.previous_candidate
        state.previous_candidate = first if len(first) <= self.max_chars else ""
        finish = decode.final and decode.reason not in ("hard", "hard_pause")

        while rest:
            parts = split_sentences(rest)
            reason = ""
            count = 0
            if len(rest) >= self.max_chars:
                # Prefer a candidate boundary within the character budget.
                candidates = [len(part) for part in parts[:-1] if len(part) <= self.max_chars]
                count = candidates[0] if candidates else self.max_chars
                reason = "budget_chars"
            elif finish:
                count = len(rest) if state.uncertain else len(parts[0])
                reason = "alignment_degraded" if state.uncertain else (decode.reason or "final")
            elif not state.uncertain and len(parts) > 1 and (decode.final or stable_first):
                count, reason = len(parts[0]), "stable_interior"
            elif state.t1 - state.t0 >= self.max_seconds:
                count, reason = len(rest), "budget_seconds"
            if not count:
                break
            if state.uncertain and reason.startswith("budget_"):
                reason += "_alignment_degraded"
            end = state.t0 + (state.t1 - state.t0) * count / len(rest)
            full_end = state.t1
            state.t1 = end
            outputs.append(self._update(decode.source, state, rest[:count].rstrip(), True, reason))
            state.t0 = end
            state.t1 = full_end
            state.audio_exact = False
            position += count
            rest = text[position:].lstrip()
            position = len(text) - len(rest)
            stable_first = False

        state.consumed = position
        state.consumed_hash = _digest(text[:position]) if position else b""
        normalized, _ = _letters(text[:position])
        state.committed_prefix = normalized if len(normalized) <= self.max_chars else ""
        state.pending = rest
        state.window_tail = text[-self.max_chars:]
        tail_start = max(0, len(text) - self.max_chars)
        state.pending_offset = max(0, position - tail_start)
        if rest:
            outputs.append(self._update(decode.source, state, rest, False,
                "alignment_uncertain" if state.uncertain else decode.reason))
        else:
            state.pending_id = ""
            state.uncertain = False
            state.previous_candidate = ""
        # A base is only the previous pending caption (bounded by max_chars),
        # never a transcript archive. Consumed prefixes retain a digest/count.
        return outputs

    def pending_audio_bounds(self, source: str) -> tuple[float, float] | None:
        """Return exact cached bounds only before any estimated sentence split.

        Once an interior caption was emitted, audio for its words cannot safely
        be excluded without timestamps. Re-decoding the conservative full range
        would reintroduce committed words, so callers must skip that repair.
        """
        state = self._sources.get(source)
        if state is None or not state.pending or not state.audio_exact:
            return None
        return state.audio_t0, state.audio_t1

    def repair(self, source: str, text: str, t0: float, t1: float, *,
               final: bool = False) -> list[UtteranceUpdate]:
        """Replace unresolved text from a bounded re-decode of its exact bounds.

        Callers must ensure the model accepts this audio interval. The assembler
        does not request audio or claim that a repeated recognition proves truth.
        Subsequent results for the repaired window are ignored.
        """
        state = self._sources.get(source)
        if state is None or not state.pending:
            return []
        if (t0, t1) != self.pending_audio_bounds(source):
            raise ValueError("repair must cover the exact pending audio interval")
        if not text.strip():
            return []
        state.uncertain = state.base_uncertain = False
        state.base = ""
        state.base_context = state.window_tail = ""
        state.base_context_offset = state.pending_offset = 0
        state.consumed = 0
        state.consumed_hash = b""
        state.committed_prefix = ""
        state.previous_candidate = ""
        state.repair_locked = True
        decode = WindowDecode(state.window_id, source, t0, t1, text, final, "alignment_repaired")
        return self._render(decode, state, text.strip())

    def flush(self, source: str | None = None, *, final: bool = True,
              reason: str = "") -> list[UtteranceUpdate]:
        """Close pending captions; failed recognition closes without confirmation.

        A nonfinal close preserves the last draft under its ID but retires its
        assembly state, so later shutdown cannot promote that guess to history.
        """
        outputs = []
        for name, state in self._sources.items():
            if source is not None and name != source:
                continue
            if state.pending:
                outputs.append(self._update(name, state, state.pending, final,
                    reason or ("alignment_degraded" if state.uncertain else "flush")))
            state.pending = state.base = state.previous_candidate = ""
            state.base_context = state.window_tail = ""
            state.base_context_offset = state.pending_offset = 0
            state.pending_id = ""
            state.uncertain = state.base_uncertain = False
            state.window_final = state.repair_locked = True
            state.consumed = 0
            state.consumed_hash = b""
            state.committed_prefix = ""
        return outputs
