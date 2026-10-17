from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from ..config import SegmentCfg


@dataclass
class SegEvent:
    kind: str
    seg_id: str
    t0: float
    t1: float
    audio: np.ndarray
    reason: str = ""


class Segmenter:
    """Turns a stream of frames into speech segments with periodic draft ticks.

    Events:
      start - speech detected; audio holds the pre-roll
      tick  - interval elapsed while talking; audio holds everything from segment start
      end   - endpoint or hard cut; audio holds the full segment
    """

    def __init__(self, cfg: SegmentCfg, source: str = "loopback", frame_ms: int = 20, sample_rate: int = 16000):
        self.cfg = cfg
        self.source = source
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self._pre_len = max(1, int(round(cfg.preroll_ms / frame_ms)))
        self._pre: deque[tuple[float, np.ndarray]] = deque(maxlen=self._pre_len)
        self._frames: list[np.ndarray] = []
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._total_ms = 0.0
        self._seg_start = 0.0
        self._last_tick = 0.0
        self._count = 0
        self._seg_id: str | None = None

    def _concat(self) -> np.ndarray:
        if not self._frames:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self._frames)

    def _reset(self, tail: list[tuple[float, np.ndarray]] | None = None) -> None:
        self._pre = deque((tail or [])[-self._pre_len :], maxlen=self._pre_len)
        self._frames = []
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._total_ms = 0.0
        self._seg_id = None

    def _dur_ms(self, frame: np.ndarray) -> float:
        return frame.size * 1000.0 / self.sample_rate

    def _quietest_cut(self) -> tuple[int, float]:
        """Pick the flattest point in the tail, and measure how long that gap lasts.

        A gap of >=120 ms means a real pause (safe sentence boundary); a shorter
        one is just an inter-word gap, so the sentence is still being spoken.
        """
        n = len(self._frames)
        window = max(2, int(600 / self.frame_ms))
        rms = [float(np.sqrt(np.mean(np.square(f, dtype=np.float64)))) for f in self._frames]
        start = max(1, n - window)
        best_i = min(range(start, n), key=lambda i: rms[i])
        level = float(np.median(rms)) or 1e-6
        thr = max(self.cfg.vad_floor, 0.15 * level)
        left = best_i
        while left > 0 and rms[left - 1] < thr:
            left -= 1
        right = best_i
        while right < n - 1 and rms[right + 1] < thr:
            right += 1
        return best_i, (right - left + 1) * self.frame_ms

    def flush(self, t: float) -> list[SegEvent]:
        if not self._frames or self._seg_id is None:
            return []
        ev = SegEvent("end", self._seg_id, self._seg_start, t, self._concat(), reason="flush")
        self._reset()
        return [ev]

    def push(self, frame: np.ndarray, t: float, speech: bool) -> list[SegEvent]:
        dur = self._dur_ms(frame)
        end_t = t
        events: list[SegEvent] = []

        if not self._frames:
            self._pre.append((end_t, frame))
            if not speech:
                return events
            self._frames = [f for _, f in self._pre]
            self._count += 1
            self._seg_id = f"{self.source}-{self._count:04d}"
            self._seg_start = end_t - self._dur_ms(self._frames[0]) * (len(self._frames) - 1) / 1000.0
            self._speech_ms = dur
            self._silence_ms = 0.0
            self._total_ms = sum(self._dur_ms(f) for f in self._frames)
            self._last_tick = end_t
            events.append(SegEvent("start", self._seg_id, self._seg_start, end_t, self._concat()))
            return events

        self._frames.append(frame)
        self._total_ms += dur
        if speech:
            self._speech_ms += dur
            self._silence_ms = 0.0
        else:
            self._silence_ms += dur

        seg_id = self._seg_id or ""
        endpoint = self._silence_ms >= self.cfg.silence_ms and self._speech_ms >= self.cfg.min_speech_ms
        hard_cut = not endpoint and self._total_ms >= self.cfg.max_segment_s * 1000.0
        if endpoint:
            events.append(SegEvent("end", seg_id, self._seg_start, end_t, self._concat(), reason="silence"))
            self._reset(list(zip([end_t] * len(self._frames[-self._pre_len :]), self._frames[-self._pre_len :])))
            return events
        if hard_cut:
            cut, gap_ms = self._quietest_cut()
            reason = "hard_pause" if gap_ms >= 120 else "hard"
            if 0 < cut < len(self._frames):
                cut_t = end_t - (len(self._frames) - cut) * self.frame_ms / 1000.0
                events.append(
                    SegEvent("end", seg_id, self._seg_start, cut_t, np.concatenate(self._frames[:cut]), reason=reason)
                )
                carry = self._frames[cut:]
                self._count += 1
                self._seg_id = f"{self.source}-{self._count:04d}"
                self._frames = carry
                self._seg_start = cut_t
                self._total_ms = sum(self._dur_ms(f) for f in carry)
                self._speech_ms = self._total_ms
                self._silence_ms = 0.0
                self._last_tick = end_t
                events.append(SegEvent("start", self._seg_id, cut_t, end_t, self._concat()))
                return events
            events.append(SegEvent("end", seg_id, self._seg_start, end_t, self._concat(), reason="hard"))
            self._reset(list(zip([end_t] * len(self._frames[-self._pre_len :]), self._frames[-self._pre_len :])))
            return events

        if self._speech_ms < self.cfg.min_speech_ms and self._silence_ms >= self.cfg.silence_ms:
            events.append(SegEvent("end", seg_id, self._seg_start, end_t, self._concat(), reason="short"))
            self._reset(list(self._pre))
            return events

        if (end_t - self._last_tick) * 1000.0 >= self.cfg.draft_interval_ms and self._speech_ms >= self.cfg.min_speech_ms:
            self._last_tick = end_t
            events.append(SegEvent("tick", seg_id, self._seg_start, end_t, self._concat()))
        return events
