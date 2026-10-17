from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Protocol

import numpy as np


@dataclass
class AsrResult:
    text: str
    language: str | None = None
    latency_ms: int = 0
    raw: str = ""


class AsrTimeout(RuntimeError):
    pass


class AsrEngine(Protocol):
    """Batch/segment level engine. The draft-final scheduler lives above it.

    A streaming engine can satisfy the pipeline by emitting ticks itself; the
    orchestrator only needs transcribe() for the current segment audio.
    """

    async def transcribe(self, pcm: np.ndarray, sample_rate: int = 16000) -> AsrResult: ...

    async def healthy(self) -> bool: ...


class StreamingAsrEngine(Protocol):
    """Optional extension point for true streaming engines (sherpa-onnx etc.)."""

    def start_stream(self) -> "AsrStream": ...


class AsrStream(Protocol):
    async def feed(self, pcm: np.ndarray) -> list[str]: ...
    async def finish(self) -> str: ...
    def events(self) -> AsyncIterator[str]: ...
