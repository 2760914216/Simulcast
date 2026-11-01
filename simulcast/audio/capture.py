from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import numpy as np

from .devices import _get, default_loopback, default_microphone
from .wavio import StreamResampler

log = logging.getLogger(__name__)

CANDIDATE_RATES = (48000, 44100, 32000, 16000)


class _Switched(Exception):
    """The stream must be reopened: the chosen endpoint changed."""


class LiveCapture:
    """Recorder thread (WASAPI loopback or microphone) -> 16 kHz mono float32 frames.

    The endpoint is resolved again when the Windows default device changes, when the
    device disappears, and when the owner calls set_device(). The stream is reopened
    on the fly; time keeps accumulating captured samples, so a switch costs a short
    gap rather than a restart of the pipeline.
    """

    def __init__(
        self,
        device: str | None = None,
        loopback: bool = True,
        frame_ms: int = 20,
        target_rate: int = 16000,
        poll_s: float = 1.0,
    ):
        self.device = device
        self.loopback = loopback
        self.frame_ms = frame_ms
        self.target_rate = target_rate
        self.poll_s = poll_s
        self._manual: str | None = device or None
        self._current_name: str = ""
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._level = 0.0
        self._captured_samples = 0
        self.last_error: str | None = None

    @property
    def level(self) -> float:
        return self._level

    @property
    def device_name(self) -> str:
        return self._current_name

    def set_device(self, device: str | None) -> None:
        """Pin an endpoint by id, or pass None to follow the system default again."""
        manual = (device or "").strip() or None
        if manual == self._manual:
            return
        self._manual = manual
        self._wake.set()

    def default_info(self) -> dict | None:
        return default_loopback() if self.loopback else default_microphone()

    def _desired(self) -> tuple[str, str]:
        if self._manual:
            mic = _get(self._manual)
            return self._manual, str(getattr(mic, "name", self._manual))
        info = self.default_info()
        if info is None:
            raise RuntimeError("no audio device found")
        return str(info["id"]), str(info["name"])

    def _should_switch(self, device_id: str) -> bool:
        if self._manual:
            return self._manual != device_id
        info = self.default_info()
        return info is None or str(info["id"]) != device_id

    def start(self, on_frame: Callable[[float, np.ndarray], None]) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._captured_samples = 0
        self._thread = threading.Thread(target=self._run, args=(on_frame,), name="capture", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    def _run(self, on_frame: Callable[[float, np.ndarray], None]) -> None:
        backoff = 0.5
        while not self._stop.is_set():
            self._wake.clear()
            try:
                device_id, name = self._desired()
            except Exception as exc:
                self.last_error = str(exc)
                log.error("capture device lookup failed: %s", exc)
                if self._stop.wait(backoff):
                    return
                backoff = min(5.0, backoff * 1.6)
                continue
            self._current_name = name
            try:
                recorded = self._loop(device_id, name, on_frame)
            except _Switched:
                log.info("capture endpoint changed, reopening")
                backoff = 0.5
                continue
            except Exception as exc:
                self.last_error = str(exc)
                log.warning("capture from %r failed: %s", name, exc)
                if self._stop.wait(backoff):
                    return
                backoff = min(5.0, backoff * 1.6)
                continue
            if recorded:
                backoff = 0.5

    def _loop(self, device_id: str, name: str, on_frame: Callable[[float, np.ndarray], None]) -> bool:
        mic = _get(device_id)
        last: Exception | None = None
        for rate in CANDIDATE_RATES:
            if self._stop.is_set():
                return False
            block = max(1, int(rate * self.frame_ms / 1000))
            resampler = StreamResampler(rate, self.target_rate)
            try:
                with mic.recorder(samplerate=rate, blocksize=block) as rec:
                    log.info("capturing from %r at %s Hz", name, rate)
                    deadline = time.monotonic() + self.poll_s
                    while not self._stop.is_set():
                        if self._wake.is_set() or time.monotonic() >= deadline:
                            self._wake.clear()
                            if self._should_switch(device_id):
                                raise _Switched
                            deadline = time.monotonic() + self.poll_s
                        data = rec.record(numframes=block)
                        if data is None or len(data) == 0:
                            continue
                        mono = data.mean(axis=1) if data.ndim == 2 else data
                        out = resampler.process(np.ascontiguousarray(mono, dtype=np.float32))
                        if out.size == 0:
                            continue
                        self._level = float(np.sqrt(np.mean(np.square(out, dtype=np.float64))))
                        self._captured_samples += out.size
                        on_frame(self._captured_samples / self.target_rate, out)
                return True
            except _Switched:
                raise
            except Exception as exc:
                last = exc
                log.warning("capture at %s Hz failed: %s", rate, exc)
        if last is not None:
            raise last
        return False
