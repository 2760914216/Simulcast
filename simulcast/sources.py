from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator

import numpy as np

from .audio.capture import LiveCapture
from .audio.wavio import read_wav, resample

log = logging.getLogger(__name__)


async def file_frames(
    path: str,
    frame_ms: int = 20,
    speed: float = 1.0,
    target_rate: int = 16000,
    max_seconds: float | None = None,
) -> AsyncIterator[tuple[float, np.ndarray]]:
    data, sr = read_wav(path)
    if sr != target_rate:
        data = resample(data, sr, target_rate)
    n = max(1, int(target_rate * frame_ms / 1000))
    t = 0.0
    step = frame_ms / 1000.0 / max(0.01, speed)
    origin = time.monotonic()
    index = 0
    for start in range(0, data.size, n):
        chunk = data[start : start + n]
        if chunk.size < n:
            chunk = np.pad(chunk, (0, n - chunk.size))
        t += n / target_rate
        if max_seconds is not None and t > max_seconds:
            return
        yield t, chunk
        index += 1
        delay = origin + index * step - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)


async def live_frames(
    device: str | None,
    loopback: bool,
    frame_ms: int = 20,
    target_rate: int = 16000,
    max_seconds: float | None = None,
) -> AsyncIterator[tuple[float, np.ndarray]]:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=128)
    capture = LiveCapture(device=device, loopback=loopback, frame_ms=frame_ms, target_rate=target_rate)

    def push(t: float, frame: np.ndarray) -> None:
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        queue.put_nowait((t, frame))

    def on_frame(t: float, frame: np.ndarray) -> None:
        loop.call_soon_threadsafe(push, t, frame)

    capture.start(on_frame)
    try:
        while True:
            t, frame = await queue.get()
            if max_seconds is not None and t > max_seconds:
                return
            yield t, frame
    finally:
        capture.stop()
