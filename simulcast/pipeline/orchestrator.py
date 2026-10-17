from __future__ import annotations

import asyncio
import difflib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import AsyncIterator

import numpy as np

from ..audio.segmenter import Segmenter
from ..audio.vad import EnergyVad
from ..config import AppCfg
from ..mt.sanitize import clean
from ..protocol import Caption, new_session_id
from ..sink import Sink
from .sentences import is_terminated, split_sentences

log = logging.getLogger(__name__)


def _merge_carry(prefix: str, text: str) -> str:
    """Join a carried fragment with the next decode, dropping repeated words.

    The cut point can leave the same word in both halves ("Welcome" + "Welcome
    to the meeting"), so compare word tails/heads before concatenating.
    """
    pw = prefix.split()
    nw = text.split()
    norm = lambda w: w.strip(" .,;:!?，；：！？\"'").lower()  # noqa: E731
    keep = 0
    for n in range(min(len(pw), len(nw)), 0, -1):
        if [norm(w) for w in pw[-n:]] == [norm(w) for w in nw[:n]]:
            keep = n
            break
    rest = " ".join(nw[keep:]).strip()
    return f"{prefix} {rest}".strip() if rest else prefix


_TRAILING_CONNECTORS = {
    "if", "and", "but", "or", "so", "because", "that", "which", "who", "whom", "whose",
    "when", "while", "although", "though", "than", "then", "as", "to", "of", "for", "with",
    "without", "in", "on", "at", "by", "from", "into", "about", "the", "a", "an", "my",
    "your", "our", "their", "his", "her", "its", "this", "these", "those", "is", "are",
    "was", "were", "be", "been", "being", "will", "would", "can", "could", "should", "may",
    "might", "must", "not", "have", "has", "had", "do", "does", "did",
}


def _ends_open(text: str) -> bool:
    """True when the tail ends on a word that cannot end a sentence ("...look bad if")."""
    tokens = (text or "").split()
    if not tokens:
        return False
    last = tokens[-1].strip(" .,;:!?。，；：！？\"'").lower()
    return last in _TRAILING_CONNECTORS


def _norm_words(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s']", " ", (text or "").lower()).split())


def _covered_offset(early: str, final: str) -> int:
    """How far into `final` does the already-shown prefix reach?

    Walk the matching blocks in order and stop at the first real divergence, so a
    coincidental short match near the end cannot make us skip unshown text.
    """
    if not early:
        return 0
    a, b = early.lower(), final.lower()
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    pos_a = pos_b = 0
    for block in matcher.get_matching_blocks():
        if block.size == 0:
            break
        if block.a - pos_a > 4 or block.b - pos_b > 4:
            break
        pos_a = block.a + block.size
        pos_b = block.b + block.size
    if pos_a < len(a) - 4:
        return pos_b
    return pos_b


@dataclass
class Segment:
    id: str
    source: str
    t0: float
    t1: float | None = None
    audio: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    raw: str = ""
    translation: str = ""
    state: str = "draft"
    lang: str | None = None
    asr_ms: int | None = None
    mt_ms: int | None = None
    asr_busy: bool = False
    asr_pending: str | None = None
    mt_rev: int = 0
    full_text: str = ""
    committed: int = 0
    prev_sents: list[str] = field(default_factory=list)
    committed_texts: list[str] = field(default_factory=list)
    t_end: float = 0.0
    next_sentence_no: int = 0
    last_child_end: float = 0.0
    end_reason: str = ""
    prefix_text: str = ""
    prefix_checked: bool = False


class Orchestrator:
    """Audio burst -> ASR -> sentence commits -> per-sentence translation.

    One burst can yield several captions: every punctuated sentence is promoted to
    its own final caption once it is stable across two decodes, while the as-yet
    unfinished sentence stays on the draft caption of the burst.
    """

    def __init__(self, cfg: AppCfg, asr, mt, sink: Sink, sid: str | None = None):
        self.cfg = cfg
        self.asr = asr
        self.mt = mt
        self.sink = sink
        self.sid = sid or new_session_id()
        self._seq = 0
        self._context: list[tuple[str, str]] = []
        self._mt_q: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._mt_seq = 0
        self._tasks: set[asyncio.Task] = set()
        self._workers: list[asyncio.Task] = []
        self._mt_ok = mt is not None and cfg.mt.enabled
        self._last_emit: dict[str, tuple] = {}
        self._children: dict[str, Segment] = {}
        self._carry: dict[str, tuple[Segment, str, float, float]] = {}
        self._lang: dict[str, str] = {}

    async def start(self) -> None:
        self._workers.append(asyncio.create_task(self._mt_loop(), name="mt-worker"))

    async def stop(self) -> None:
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    async def _emit(self, seg: Segment) -> None:
        fingerprint = (seg.raw, seg.translation, seg.state, seg.t1)
        if self._last_emit.get(seg.id) == fingerprint:
            return
        self._last_emit[seg.id] = fingerprint
        if len(self._last_emit) > 4000:
            for key in list(self._last_emit)[:2000]:
                self._last_emit.pop(key, None)
        self._seq += 1
        cap = Caption(
            id=seg.id,
            source=seg.source,
            t0=round(seg.t0, 3),
            t1=round(seg.t1, 3) if seg.t1 is not None else None,
            raw=seg.raw,
            translation=seg.translation,
            state=seg.state,
            lang=seg.lang,
            asr_ms=seg.asr_ms,
            mt_ms=seg.mt_ms,
            seq=self._seq,
            sid=self.sid,
        )
        await self.sink.emit(cap.to_msg())

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _kick_asr(self, seg: Segment, kind: str) -> None:
        if seg.asr_busy:
            seg.asr_pending = kind
            return
        self._spawn(self._asr_run(seg, kind))

    def _looks_like_noise(self, seg: Segment, text: str, lang: str | None) -> bool:
        if not self.cfg.segment.semantic_heuristics or not text or not lang:
            return False
        previous = self._lang.get(seg.source)
        if previous and lang.lower() != previous.lower() and len(text) <= 4:
            log.info("drop short cross-language fragment %r (%s)", text, lang)
            return True
        self._lang[seg.source] = lang
        return False

    async def _asr_run(self, seg: Segment, kind: str) -> None:
        seg.asr_busy = True
        result = None
        try:
            if seg.audio.size == 0:
                raise ValueError("empty segment")
            rms = float(np.sqrt(np.mean(np.square(seg.audio, dtype=np.float64))))
            if rms < self.cfg.segment.vad_floor:
                raise ValueError(f"segment too quiet (rms={rms:.5f})")
            result = await self.asr.transcribe(seg.audio)
        except Exception as exc:
            log.warning("asr failed for %s: %s", seg.id, exc)
        finally:
            seg.asr_busy = False
        if result is not None and self._looks_like_noise(seg, result.text.strip(), result.language):
            result = None
        if result is not None:
            text = result.text.strip()
            seg.asr_ms = result.latency_ms
            seg.lang = result.language or seg.lang
            if text:
                await self._ingest_text(seg, text, final=(kind == "final"))
        nxt = seg.asr_pending
        seg.asr_pending = None
        if nxt:
            self._kick_asr(seg, nxt)

    def _commit_index(self, sents: list[str], stable: int) -> int:
        n = 0
        for i in range(stable):
            if is_terminated(sents[i]):
                n = i + 1
        return n

    def _sentence_span(self, seg: Segment, sents: list[str], index: int) -> tuple[float, float]:
        total = sum(len(s) for s in sents) or 1
        start = sum(len(s) for s in sents[:index])
        end = start + len(sents[index])
        span = max(0.2, (seg.t_end or seg.t1 or seg.t0) - seg.t0)
        return seg.t0 + span * start / total, seg.t0 + span * end / total

    async def _emit_child(self, parent: Segment, text: str, t0: float, t1: float) -> None:
        key = _norm_words(text)
        if len(key) > 8 and any(key in _norm_words(prev) for prev in parent.committed_texts):
            return
        t0 = max(t0, parent.last_child_end)
        t1 = max(t1, t0 + 0.2)
        parent.last_child_end = t1
        parent.next_sentence_no += 1
        child = Segment(
            id=f"{parent.id}-s{parent.next_sentence_no}",
            source=parent.source,
            t0=t0,
            t1=t1,
            raw=text,
            state="draft" if self._mt_ok else "final",
            lang=parent.lang,
            asr_ms=parent.asr_ms,
            t_end=t1,
        )
        parent.committed_texts.append(text)
        self._children[child.id] = child
        if len(self._children) > 600:
            for key in list(self._children)[:300]:
                self._children.pop(key, None)
        await self._emit(child)
        if self._mt_ok:
            self._enqueue_mt(child, final=True)

    async def _commit_sentence(self, parent: Segment, sents: list[str], index: int) -> None:
        text = sents[index].strip()
        if not text:
            return
        t0, t1 = self._sentence_span(parent, sents, index)
        await self._emit_child(parent, text, t0, t1)

    async def _commit_plain(self, parent: Segment, text: str, start_char: int, total_chars: int) -> None:
        if not text.strip():
            return
        total = max(1, total_chars)
        span = max(0.2, (parent.t_end or parent.t1 or parent.t0) - parent.t0)
        cursor = start_char + (len(text) - len(text.lstrip()))
        for part in split_sentences(text):
            sentence = part.strip()
            if len(sentence.strip(" .,;:!?。，；：！？\"'")) < 2:
                cursor += len(part) + 1
                continue
            t0 = parent.t0 + span * cursor / total
            t1 = parent.t0 + span * (cursor + len(sentence)) / total
            await self._emit_child(parent, sentence, t0, t1)
            cursor += len(sentence) + 1

    async def _commit_remainder(self, parent: Segment, final_text: str) -> None:
        """Emit only what the full decode says beyond what was already shown."""
        early = " ".join(parent.committed_texts).strip()
        offset = _covered_offset(early, final_text)
        await self._commit_plain(parent, final_text[offset:], offset, len(final_text))

    async def _attach_carry(self, seg: Segment) -> None:
        if seg.prefix_checked:
            return
        seg.prefix_checked = True
        item = self._carry.get(seg.source)
        if item is None:
            return
        parent, carry_text, carry_t0, ts = item
        if parent.t_end > seg.t0 + 0.15:
            return  # the carry belongs to a burst that was still running; leave it
        self._carry.pop(seg.source, None)
        if time.monotonic() - ts > self.cfg.segment.carry_ttl_s:
            await self._flush_carry(parent, carry_text, carry_t0)
            return
        seg.prefix_text = carry_text
        seg.t0 = min(seg.t0, carry_t0)
        log.info("carry attached to %s: %r", seg.id, carry_text[:70])

    def _split_hard_cut(self, text: str) -> tuple[str, str]:
        """Mid-speech cut: the trailing sentence is probably unfinished, so hold it.

        ASR always punctuates a truncated segment as if it were complete, so the
        trailing period tells us nothing; the audio-side gap length did, and only
        bursts flagged "hard" (short gap) get here.
        """
        sents = split_sentences(text)
        if not sents:
            return text, ""
        tail = sents[-1].strip(" .,;:，；：!?！？")
        if len(tail) < self.cfg.segment.carry_min_chars or len(tail) > self.cfg.segment.carry_max_chars:
            return text, ""
        if len(tail.split()) > self.cfg.segment.carry_max_words:
            if not (self.cfg.segment.semantic_heuristics and _ends_open(tail)):
                return text, ""
        if len(sents) == 1:
            return "", tail
        return " ".join(sents[:-1]).strip(), tail

    def _stash_carry(self, seg: Segment, tail: str, start_char: int, total_chars: int) -> None:
        total = max(1, total_chars)
        span = max(0.2, (seg.t_end or seg.t1 or seg.t0) - seg.t0)
        t0 = seg.t0 + span * max(0, start_char) / total
        self._carry[seg.source] = (seg, tail, t0, time.monotonic())
        log.info("hard cut: carrying %r into the next burst", tail[:70])

    async def _flush_carry(self, parent: Segment, text: str, t0: float) -> None:
        span = max(0.4, len(text) / 12.0)
        await self._emit_child(parent, text, t0, t0 + span)

    async def _ingest_text(self, seg: Segment, text: str, final: bool) -> None:
        await self._attach_carry(seg)
        if seg.prefix_text:
            text = _merge_carry(seg.prefix_text, text)
        sents = split_sentences(text)
        if not sents:
            return
        seg.full_text = text
        stable = 0
        while stable < min(len(sents), len(seg.prev_sents)) and sents[stable] == seg.prev_sents[stable]:
            stable += 1
        seg.prev_sents = sents
        if final:
            await self._emit_eval(seg, list(seg.committed_texts), sents)
            early = " ".join(seg.committed_texts).strip()
            offset = _covered_offset(early, text)
            rest = text[offset:]
            if seg.end_reason == "hard":
                body, tail = self._split_hard_cut(rest)
            else:
                body, tail = rest, ""
            if body.strip():
                await self._commit_plain(seg, body, offset, len(text))
            if tail:
                self._stash_carry(seg, tail, offset + len(body), len(text))
            return
        target = min(self._commit_index(sents, stable), len(sents) - 1)
        while seg.committed < target:
            await self._commit_sentence(seg, sents, seg.committed)
            seg.committed += 1
        tail = " ".join(sents[seg.committed :]).strip()
        if tail and tail != seg.raw:
            seg.raw = tail
            seg.state = "draft"
            await self._emit(seg)
            if self._mt_ok:
                self._enqueue_mt(seg, final=False)

    async def _emit_eval(self, seg: Segment, early: list[str], final_sents: list[str]) -> None:
        """Compare what the online rule committed against the final full decode.

        premature_ratio is the share of already-finalized characters that the
        full-text decode disagrees with: the direct measure of "cut too early".
        """
        early_text = " ".join(early).strip()
        final_text = " ".join(final_sents).strip()
        early_norm = _norm_words(early_text)
        final_norm = _norm_words(final_text)
        n = 0
        limit = min(len(early_norm), len(final_norm))
        while n < limit and early_norm[n] == final_norm[n]:
            n += 1
        premature_chars = max(0, len(early_norm) - n)
        matched_sents = sum(1 for a, b in zip(early, final_sents) if _norm_words(a) == _norm_words(b))
        await self.sink.emit(
            {
                "v": 1,
                "type": "eval",
                "sid": self.sid,
                "id": seg.id,
                "source": seg.source,
                "reason": seg.end_reason,
                "prefix": seg.prefix_text,
                "t0": round(seg.t0, 3),
                "t1": round(seg.t1, 3) if seg.t1 is not None else None,
                "early_sents": early,
                "final_sents": final_sents,
                "early_count": len(early),
                "final_count": len(final_sents),
                "matched_sents": matched_sents,
                "committed_chars": len(early_norm),
                "matched_chars": n,
                "premature_chars": premature_chars,
                "premature_ratio": round(premature_chars / max(1, len(early_norm)), 4),
                "ts": time.time(),
            }
        )

    def _enqueue_mt(self, seg: Segment, final: bool) -> None:
        seg.mt_rev += 1
        self._mt_seq += 1
        self._mt_q.put_nowait((0 if final else 1, self._mt_seq, seg, seg.mt_rev, final))

    async def _mt_loop(self) -> None:
        while True:
            prio, seq, seg, rev, final = await self._mt_q.get()
            try:
                if rev != seg.mt_rev:
                    continue
                await self._translate(seg, rev, final)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("mt failed for %s: %s", seg.id, exc)
            finally:
                self._mt_q.task_done()

    async def _translate(self, seg: Segment, rev: int, final: bool) -> None:
        started = time.monotonic()
        buf = ""
        last_emit = 0.0
        interval = self.cfg.mt.emit_interval_ms / 1000.0
        async for delta in self.mt.stream(seg.raw, list(self._context), draft=not final):
            if rev != seg.mt_rev:
                return
            buf += delta
            candidate = clean(buf, partial=True)
            if candidate and (not seg.translation or len(candidate) >= 0.8 * len(seg.translation)):
                seg.translation = candidate
            now = time.monotonic()
            if not final and now - last_emit >= interval:
                last_emit = now
                await self._emit(seg)
        polished = clean(buf)
        if polished:
            seg.translation = polished
        seg.mt_ms = int((time.monotonic() - started) * 1000)
        if final:
            seg.state = "final"
            if seg.raw and seg.translation:
                self._context.append((seg.raw, seg.translation))
                self._context = self._context[-8:]
        await self._emit(seg)

    async def run(self, frames: AsyncIterator[tuple[float, np.ndarray]], source: str = "loopback") -> None:
        vad = EnergyVad(self.cfg.segment.vad_floor, self.cfg.segment.vad_ratio)
        segmenter = Segmenter(
            self.cfg.segment,
            source=source,
            frame_ms=self.cfg.audio.frame_ms,
            sample_rate=self.cfg.audio.target_rate,
        )
        live: dict[str, Segment] = {}
        last_t = 0.0
        async for t, frame in frames:
            last_t = t
            speech = vad.is_speech(frame)
            for ev in segmenter.push(frame, t, speech):
                seg = live.get(ev.seg_id)
                if seg is None:
                    seg = Segment(id=ev.seg_id, source=source, t0=ev.t0)
                    live[ev.seg_id] = seg
                seg.audio = ev.audio
                seg.t_end = ev.t1
                if ev.kind == "tick":
                    self._kick_asr(seg, "draft")
                elif ev.kind == "end":
                    seg.t1 = ev.t1
                    seg.end_reason = ev.reason
                    self._kick_asr(seg, "final")
                    if len(live) > 128:
                        for key in list(live)[:64]:
                            live.pop(key, None)
        for ev in segmenter.flush(last_t):
            seg = live.get(ev.seg_id)
            if seg is None:
                continue
            seg.audio = ev.audio
            seg.t_end = ev.t1
            seg.t1 = ev.t1
            seg.end_reason = ev.reason
            self._kick_asr(seg, "final")
        if last_t:
            log.info("stream finished at %.2fs", last_t)

    async def finish(self, timeout: float = 30.0) -> None:
        for source, (parent, text, t0, ts) in list(self._carry.items()):
            self._carry.pop(source, None)
            await self._flush_carry(parent, text, t0)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            running = [t for t in self._tasks if not t.done()]
            if running:
                await asyncio.gather(*running, return_exceptions=True)
            if self._mt_q.empty() and not [t for t in self._tasks if not t.done()]:
                await asyncio.sleep(0.2)
                if self._mt_q.empty() and not [t for t in self._tasks if not t.done()]:
                    break
            await asyncio.sleep(0.05)
