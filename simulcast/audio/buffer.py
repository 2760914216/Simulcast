"""Bounded, timestamped audio for occasional ASR boundary repair."""
from __future__ import annotations

from collections import deque
import math

import numpy as np


class AudioBuffer:
    def __init__(self, sample_rate: int, max_seconds: float):
        if sample_rate <= 0 or not math.isfinite(max_seconds) or max_seconds <= 0:
            raise ValueError("audio buffer budgets must be positive")
        self.sample_rate = sample_rate
        self.max_seconds = max_seconds
        self._frames: deque[tuple[float, float, np.ndarray]] = deque()

    def append(self, t1: float, frame: np.ndarray) -> None:
        if not frame.size:
            return
        t0 = t1 - frame.size / self.sample_rate
        if self._frames and t0 < self._frames[-1][1] - .5 / self.sample_rate:
            # A discontinuous/restarted source cannot share repair audio.
            self._frames.clear()
        self._frames.append((t0, t1, frame.copy()))
        cutoff = t1 - self.max_seconds
        while self._frames and self._frames[0][1] <= cutoff + .5 / self.sample_rate:
            self._frames.popleft()
        if self._frames and self._frames[0][0] < cutoff:
            start, end, data = self._frames.popleft()
            trim = max(0, int(round((cutoff - start) * self.sample_rate)))
            self._frames.appendleft((start + trim / self.sample_rate, end, data[trim:]))

    def slice(self, t0: float, t1: float) -> np.ndarray | None:
        if not math.isfinite(t0) or not math.isfinite(t1) or t1 <= t0:
            return None
        tolerance = .5 / self.sample_rate
        cursor = t0
        pieces = []
        for start, end, data in self._frames:
            if end <= cursor + tolerance:
                continue
            if start > cursor + tolerance:
                return None
            left = max(0, int(round((cursor - start) * self.sample_rate)))
            right = min(data.size, int(round((min(t1, end) - start) * self.sample_rate)))
            if right > left:
                pieces.append(data[left:right])
                cursor = start + right / self.sample_rate
            if cursor >= t1 - tolerance:
                return np.concatenate(pieces)
        return None
