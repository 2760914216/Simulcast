from __future__ import annotations

import logging
import threading
from typing import Callable

import numpy as np

from .devices import _get, default_loopback, default_microphone
from .wavio import StreamResampler

log = logging.getLogger(__name__)

CANDIDATE_RATES = (48000, 44100, 32000, 16000)


class LiveCapture:
    """Recorder thread (WASAPI loopback or microphone) -> 16 kHz mono float32 frames."""

    def __init__(
        self,
        device: str | None = None,
        loopback: bool = True,
        frame_ms: int = 20,
        target_rate: int = 16000,
    ):
        self.device = device
        self.loopback = loopback
        self.frame_ms = frame_ms
        self.target_rate = target_rate
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._level = 0.0
        self.last_error: str | None = None

    @property
    def level(self) -> float:
        return self._level

    def _resolve(self):
        if self.device:
            return _get(self.device)
        info = default_loopback() if self.loopback else default_microphone()
        if info is None:
            raise RuntimeError("no audio device found")
        return _get(info["id"])

    def start(self, on_frame: Callable[[float, np.ndarray], None]) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(on_frame,), name="capture", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    def _run(self, on_frame: Callable[[float, np.ndarray], None]) -> None:
        mic = self._resolve()
        last: Exception | None = None
        for rate in CANDIDATE_RATES:
            if self._stop.is_set():
                return
            try:
                self._loop(mic, rate, on_frame)
                return
            except Exception as exc:
                last = exc
                log.warning("capture at %s Hz failed: %s", rate, exc)
        self.last_error = str(last)
        log.error("capture failed: %s", last)

    def _loop(self, mic, rate: int, on_frame: Callable[[float, np.ndarray], None]) -> None:
        block = max(1, int(rate * self.frame_ms / 1000))
        resampler = StreamResampler(rate, self.target_rate)
        t = 0.0
        with mic.recorder(samplerate=rate, blocksize=block) as rec:
            log.info("capturing from %r at %s Hz", mic.name, rate)
            while not self._stop.is_set():
                data = rec.record(numframes=block)
                if data is None or len(data) == 0:
                    continue
                mono = data.mean(axis=1) if data.ndim == 2 else data
                out = resampler.process(np.ascontiguousarray(mono, dtype=np.float32))
                if out.size == 0:
                    continue
                self._level = float(np.sqrt(np.mean(np.square(out, dtype=np.float64))))
                t += out.size / self.target_rate
                on_frame(t, out)
