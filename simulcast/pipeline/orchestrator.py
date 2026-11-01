from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field, replace
from typing import AsyncIterator

import numpy as np

from ..asr.guard import judge
from ..asr.verification import observation
from ..audio.buffer import AudioBuffer
from ..audio.segmenter import Segmenter
from ..audio.vad import EnergyVad
from ..config import AppCfg, ConfigError
from ..mt.sanitize import clean
from ..protocol import Caption, new_session_id
from ..sink import Sink
from .sentences import split_sentences
from .utterances import UtteranceAssembler, UtteranceUpdate, WindowDecode

log = logging.getLogger(__name__)


def _norm_words(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s']", " ", (text or "").lower()).split())


def _history_window(texts: list[str], limit_sentences: int, limit_chars: int) -> list[str]:
    """Pick the recent originals that fit both budgets, oldest first.

    An original that alone exceeds the character budget ends the window: it is not
    truncated and not skipped in favour of an older one.
    """
    if limit_sentences <= 0 or limit_chars <= 0 or not texts:
        return []
    recent = texts[-limit_sentences:]
    if len(recent[-1]) > limit_chars:
        return []
    picked: list[str] = []
    total = 0
    for text in reversed(recent):
        if total + len(text) > limit_chars:
            break
        picked.append(text)
        total += len(text)
    return picked[::-1]


@dataclass
class Segment:
    id: str
    source: str
    t0: float
    t1: float | None = None
    audio: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    raw: str = ""
    translation: str = ""
    translation_raw: str = ""
    state: str = "draft"
    lang: str | None = None
    asr_ms: int | None = None
    mt_ms: int | None = None
    asr_busy: bool = False
    asr_pending: str | None = None
    mt_rev: int = 0
    committed_texts: list[str] = field(default_factory=list)
    t_end: float = 0.0
    end_reason: str = ""
    commit_pos: int | None = None
    history: tuple[str, ...] = ()
    overlap_s: float = 0.0


@dataclass(frozen=True)
class _MtJob:
    seg: Segment
    revision: int
    final: bool
    text: str
    history: tuple[str, ...]


class Orchestrator:
    """Ordered audio windows -> revisable logical sentences -> translation.

    Window caps bound ASR work. A separate per-source assembler owns sentence
    identity and keeps the last candidate revisable across forced boundaries.
    """

    def __init__(self, cfg: AppCfg, asr, mt, sink: Sink, sid: str | None = None,
                 *, speech_evidence=None):
        self.cfg = cfg
        self.asr = asr
        self.mt = mt
        self.sink = sink
        self.sid = sid or new_session_id()
        self._seq = 0
        self._history: list[tuple[int, str]] = []
        self._commit_pos = 0
        self._mt_q: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._mt_seq = 0
        self._tasks: set[asyncio.Task] = set()
        self._workers: list[asyncio.Task] = []
        self._mt_ok = mt is not None and cfg.mt.enabled
        self._last_emit: dict[str, tuple] = {}
        self._children: dict[str, Segment] = {}
        self._lang: dict[str, str] = {}
        self._utterances = UtteranceAssembler(cfg.segment.utterance_max_s, cfg.segment.utterance_max_chars)
        self._audio: dict[str, AudioBuffer] = {}
        self._asr_locks: dict[str, asyncio.Lock] = {}
        self._speech_evidence = speech_evidence
        if cfg.asr.speech_evidence_mode == "observe" and speech_evidence is None:
            from ..audio.speech_evidence import SileroEvidence

            try:
                self._speech_evidence = SileroEvidence(cfg.asr.speech_evidence_model)
            except (ValueError, OSError) as exc:
                raise ConfigError(str(exc)) from exc

    async def start(self) -> None:
        if self._speech_evidence is not None:
            try:
                await asyncio.to_thread(self._speech_evidence.measure, np.zeros(512, dtype=np.float32), self.cfg.audio.target_rate)
            except Exception as exc:
                raise ConfigError(f"cannot initialize speech evidence: {exc}") from exc
        self._workers.append(asyncio.create_task(self._mt_loop(), name="mt-worker"))

    async def stop(self, *, reason: str = "stop_unconfirmed") -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        await self._retire_pending(reason=reason)
        for seg in list(self._children.values()):
            if seg.commit_pos is not None and seg.state != "final":
                await self._translation_incomplete(seg)
        # Cancelled workers account for the active job in their finally block;
        # account for queued jobs too, so join/repeated stop remain well defined.
        while not self._mt_q.empty():
            self._mt_q.get_nowait()
            self._mt_q.task_done()

    async def _retire_pending(self, source: str | None = None, *, reason: str,
                              outcome: str = "") -> None:
        for update in self._utterances.flush(source, final=False, reason=reason):
            await self._apply_utterance(update, outcome=outcome)

    async def _boundary(self, seg: Segment, reason: str, outcome: str = "") -> None:
        await self.sink.emit({"v": 1, "type": "utterance_boundary", "sid": self.sid,
                              "id": seg.id, "source": seg.source, "t0": seg.t0,
                              "t1": seg.t_end, "reason": reason, "outcome": outcome})

    async def _translation_incomplete(self, seg: Segment) -> None:
        # Source confirmation is independent of translation service availability.
        seg.mt_rev += 1
        if seg.translation_raw != seg.raw:
            seg.translation = ""
            seg.translation_raw = ""
        seg.state = "final"
        await self._emit(seg)
        await self._boundary(seg, "translation_incomplete")

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
            if seg.asr_pending != "final":
                seg.asr_pending = kind
            return
        seg.asr_busy = True
        self._spawn(self._asr_run(seg, kind))

    def _evict_finished(self, live: dict[str, Segment]) -> None:
        """Forget finished segments only; anything still owed a decode must stay.

        A segment that has not ended yet, or whose decode is queued/running, still
        receives events under its id. Dropping it would let the next event build a
        second Segment for the same id while the old one is still being written to.
        """
        if len(live) <= 128:
            return
        for key, seg in list(live.items()):
            if len(live) <= 64:
                break
            if seg.t1 is not None and not seg.asr_busy and seg.asr_pending is None:
                live.pop(key, None)

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
        try:
            lock = self._asr_locks.setdefault(seg.source, asyncio.Lock())
            async with lock:
                while True:
                    # Audio and times must describe the request actually sent,
                    # even if capture replaces seg.audio while HTTP is in flight.
                    request = replace(seg, t1=seg.t1 if kind == "final" else None)
                    await self._decode_window(request, kind)
                    nxt = seg.asr_pending
                    seg.asr_pending = None
                    if not nxt:
                        break
                    kind = nxt
        finally:
            seg.asr_pending = None
            seg.asr_busy = False

    async def _decode_window(self, seg: Segment, kind: str) -> None:
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
        if result is None:
            if kind == "final":
                await self._retire_pending(seg.source, reason="asr_unconfirmed", outcome="failed")
            return
        original = result.text
        verdict = judge(original, lang=result.language,
                        seconds=seg.audio.size / float(self.cfg.audio.target_rate), cfg=self.cfg.asr)
        if verdict.reason:
            log.warning("asr %s %s, raw=%r", seg.id, verdict.reason, original[:80])
        if self._speech_evidence is not None:
            try:
                evidence = await asyncio.to_thread(self._speech_evidence.measure, seg.audio, self.cfg.audio.target_rate)
                record = observation(source=seg.source, window_id=seg.id, t0=seg.t0,
                                     t1=seg.t_end, text=original, evidence=evidence,
                                     guard_reason=verdict.reason)
                record.update(sid=self.sid, decode_kind=kind)
                await self.sink.emit(record)
            except Exception:
                # Optional diagnostics cannot suppress a real caption.
                log.exception("speech evidence failed for %s", seg.id)
        if self._looks_like_noise(seg, verdict.text, result.language):
            if kind == "final":
                await self._retire_pending(seg.source, reason="asr_unconfirmed", outcome="rejected")
            return
        seg.asr_ms = result.latency_ms
        seg.lang = result.language or seg.lang
        if not verdict.text:
            if kind == "final":
                await self._retire_pending(seg.source, reason="asr_unconfirmed",
                                           outcome="rejected" if verdict.reason else "empty")
        else:
            await self._ingest_text(seg, verdict.text, final=(kind == "final"))

    def _record(self, child: Segment, text: str) -> None:
        """Original text enters the history when it is committed, not when it translates."""
        self._commit_pos += 1
        child.commit_pos = self._commit_pos
        body = text.strip()
        if body and (not self._history or self._history[-1][1] != body):
            self._history.append((child.commit_pos, body))
            if len(self._history) > 64:
                del self._history[:32]

    def _history_snapshot(self, pos: int | None) -> tuple[str, ...]:
        """Frozen view of the originals before pos (everything when pos is unknown)."""
        texts = [text for p, text in self._history if pos is None or p < pos]
        return tuple(
            _history_window(texts, self.cfg.mt.context_sentences, self.cfg.mt.context_max_chars)
        )

    async def _ingest_text(self, seg: Segment, text: str, final: bool) -> None:
        if final:
            await self._emit_eval(seg, list(seg.committed_texts), split_sentences(text))
        decode = WindowDecode(seg.id, seg.source, seg.t0, seg.t_end or seg.t1 or seg.t0,
                              text, final, seg.end_reason, seg.overlap_s)
        natural_end = final and seg.end_reason not in ("hard", "hard_pause")
        # At a natural end, inspect a tentative merge before committing it so
        # an ambiguous overlap still has a chance to use its cached audio.
        preview = replace(decode, final=False) if natural_end and seg.overlap_s else decode
        updates = self._utterances.ingest(preview)
        uncertain = any(u.reason == "alignment_uncertain" for u in updates)
        repaired = await self._repair_pending(seg, final=natural_end) if uncertain and final else []
        if repaired:
            updates = [u for u in updates if u.final] + repaired
        elif preview is not decode:
            updates += self._utterances.ingest(decode)
        for update in updates:
            if update.final:
                seg.committed_texts.append(update.text)
            await self._apply_utterance(update, seg)

    async def _repair_pending(self, window: Segment, *, final: bool) -> list[UtteranceUpdate]:
        bounds = self._utterances.pending_audio_bounds(window.source)
        buffer = self._audio.get(window.source)
        if bounds is None or buffer is None or self.asr is None:
            return []
        t0, t1 = bounds
        if t1 - t0 > self.cfg.asr.repair_max_s + 1e-6:
            return []
        pcm = buffer.slice(t0, t1)
        if pcm is None:
            return []
        try:
            # One attempt per new window. The model input cap was validated
            # separately; normal decode windows remain bounded by max_segment_s.
            result = await self.asr.transcribe(pcm)
            verdict = judge(result.text, lang=result.language,
                            seconds=pcm.size / self.cfg.audio.target_rate, cfg=self.cfg.asr)
            await self.sink.emit({"v": 1, "type": "asr_repair", "sid": self.sid,
                                  "id": window.id, "source": window.source, "t0": t0, "t1": t1,
                                  "raw": result.text, "guard_reason": verdict.reason})
            if verdict.text:
                return self._utterances.repair(window.source, verdict.text, t0, t1, final=final)
        except Exception:
            log.exception("boundary repair failed for %s", window.id)
        return []

    async def _apply_utterance(self, update: UtteranceUpdate, window: Segment | None = None,
                               *, outcome: str = "") -> None:
        seg = self._children.get(update.id)
        if seg is None:
            seg = Segment(update.id, update.source, update.t0)
            self._children[update.id] = seg
        changed = seg.raw != update.text
        retired = update.reason.endswith("_unconfirmed")
        if changed or retired:
            seg.mt_rev += 1
        seg.raw, seg.t0, seg.t_end = update.text, update.t0, update.t1
        seg.t1 = update.t1 if update.final else None
        seg.state = "final" if update.final and not self._mt_ok else "draft"
        if window is not None:
            seg.lang, seg.asr_ms = window.lang, window.asr_ms
        if update.final and seg.commit_pos is None:
            self._record(seg, update.text)
        await self._emit(seg)
        if self._mt_ok and not retired and (changed or update.final):
            self._enqueue_mt(seg, final=update.final)
        if retired or "alignment" in update.reason or update.reason.startswith("budget_"):
            await self._boundary(seg, update.reason, outcome)
        if retired:
            self._children.pop(seg.id, None)
        if len(self._children) > 600:
            for key in list(self._children)[:300]:
                if self._children[key].commit_pos is not None:
                    self._children.pop(key, None)

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
                "prefix": "",
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
        seg.history = self._history_snapshot(seg.commit_pos)
        self._mt_seq += 1
        job = _MtJob(seg, seg.mt_rev, final, seg.raw, seg.history)
        self._mt_q.put_nowait((0 if final else 1, self._mt_seq, job))

    async def _mt_loop(self) -> None:
        while True:
            prio, seq, job = await self._mt_q.get()
            seg, rev, final = job.seg, job.revision, job.final
            try:
                if rev != seg.mt_rev:
                    continue
                await self._translate(seg, rev, final, job.text, job.history)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("mt failed for %s: %s", seg.id, exc)
                if final and rev == seg.mt_rev:
                    await self._translation_incomplete(seg)
            finally:
                self._mt_q.task_done()

    async def _translate(self, seg: Segment, rev: int, final: bool,
                         text: str, history: tuple[str, ...]) -> None:
        started = time.monotonic()
        buf = ""
        last_emit = 0.0
        interval = self.cfg.mt.emit_interval_ms / 1000.0
        async for delta in self.mt.stream(text, list(history), draft=not final):
            if rev != seg.mt_rev:
                return
            buf += delta
            candidate = clean(buf, partial=True)
            if candidate and (seg.translation_raw != text or not seg.translation
                              or len(candidate) >= 0.8 * len(seg.translation)):
                seg.translation = candidate
                seg.translation_raw = text
            now = time.monotonic()
            if not final and now - last_emit >= interval:
                last_emit = now
                await self._emit(seg)
        if rev != seg.mt_rev:
            return
        polished = clean(buf)
        if polished:
            seg.translation = polished
            seg.translation_raw = text
        seg.mt_ms = int((time.monotonic() - started) * 1000)
        if final:
            if not polished:
                await self._translation_incomplete(seg)
                return
            seg.state = "final"
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
        audio = self._audio.setdefault(source, AudioBuffer(self.cfg.audio.target_rate, self.cfg.segment.audio_buffer_s))
        last_t = 0.0
        async for t, frame in frames:
            last_t = t
            audio.append(t, frame)
            speech = vad.is_speech(frame)
            for ev in segmenter.push(frame, t, speech):
                seg = live.get(ev.seg_id)
                if seg is None:
                    seg = Segment(id=ev.seg_id, source=source, t0=ev.t0, overlap_s=ev.overlap_s)
                    live[ev.seg_id] = seg
                seg.audio = ev.audio
                seg.t_end = ev.t1
                if ev.kind == "tick":
                    self._kick_asr(seg, "draft")
                elif ev.kind == "end":
                    seg.t1 = ev.t1
                    seg.end_reason = ev.reason
                    self._kick_asr(seg, "final")
                    self._evict_finished(live)
                    # Apply backpressure rather than retaining an unbounded
                    # queue of final windows when decoding cannot keep up.
                    if len(self._tasks) >= 8:
                        log.warning("ASR backlog reached 8 windows; waiting for a decode")
                        await asyncio.wait(list(self._tasks), return_when=asyncio.FIRST_COMPLETED)
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
        async def drain() -> None:
            while self._tasks:
                await asyncio.gather(*list(self._tasks), return_exceptions=True)
            for update in self._utterances.flush():
                await self._apply_utterance(update)
            # Empty does not mean finished: a worker may already own the last job.
            await self._mt_q.join()

        try:
            await asyncio.wait_for(drain(), timeout)
        except asyncio.TimeoutError:
            log.warning("pipeline finish exceeded %.1fs; closing unfinished work without ASR confirmation", timeout)
            await self.stop(reason="finish_unconfirmed")
