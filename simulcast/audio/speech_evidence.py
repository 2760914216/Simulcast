"""Optional, local Silero measurements for observation, never caption gating.

The mean probability and speech coverage are duration-weighted over 512-sample
frames. Coverage uses a fixed 0.5 model-probability threshold; it is an acoustic
diagnostic, not a calibrated probability that an ASR transcript is correct.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Real
from os import PathLike
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class SpeechEvidence:
    mean_probability: float
    speech_fraction: float
    seconds: float

    def __post_init__(self) -> None:
        for name in ("mean_probability", "speech_fraction", "seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, Real):
                raise TypeError(f"{name} must be a finite number")
            value = float(value)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
            if name != "seconds" and value > 1:
                raise ValueError(f"{name} must be between 0 and 1")
            object.__setattr__(self, name, value)


class SileroEvidence:
    """Measure independent, normalized mono 16 kHz audio windows.

    ``model_path`` must point to a local Silero 16 kHz ONNX model with inputs
    ``input``, ``state``, ``sr``. No model is downloaded. ONNX Runtime is imported
    only when nonempty audio is first measured. ``session`` optionally supplies
    the same ``run(outputs, inputs)`` inference boundary (e.g. for tests).
    Recurrent state and the 64-sample context belong to a single measure call.
    """

    def __init__(self, model_path: str | PathLike[str], *, session: Any = None):
        self.model_path = Path(model_path).expanduser()
        if self.model_path.suffix.lower() != ".onnx":
            raise ValueError("Configure a local Silero .onnx model file")
        if not self.model_path.is_file():
            raise FileNotFoundError(f"Silero ONNX model not found: {self.model_path}")
        self._session = session

    def _inference_session(self) -> Any:
        if self._session is None:
            try:
                import onnxruntime as ort
            except ImportError as exc:
                raise RuntimeError(
                    "Speech evidence requires optional onnxruntime; install it in "
                    "this Python environment or set speech_evidence_mode='off'"
                ) from exc
            try:
                options = ort.SessionOptions()
                options.intra_op_num_threads = 1
                options.inter_op_num_threads = 1
                self._session = ort.InferenceSession(
                    str(self.model_path), options, providers=["CPUExecutionProvider"]
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Cannot load local Silero ONNX model {self.model_path}: {exc}"
                ) from exc
        return self._session

    def measure(self, pcm: np.ndarray, sample_rate: int = 16000) -> SpeechEvidence:
        if sample_rate != 16000:
            raise ValueError("Silero speech evidence requires sample_rate=16000")
        if not isinstance(pcm, np.ndarray):
            raise TypeError("pcm must be a NumPy array of normalized mono samples")
        if pcm.ndim != 1 or not np.issubdtype(pcm.dtype, np.floating):
            raise ValueError("pcm must be a one-dimensional floating-point mono array")
        if not np.isfinite(pcm).all() or (np.abs(pcm) > 1).any():
            raise ValueError("pcm samples must be finite and normalized to [-1, 1]")
        samples = pcm.size
        if not samples:
            return SpeechEvidence(0.0, 0.0, 0.0)

        session = self._inference_session()
        state = np.zeros((2, 1, 128), dtype=np.float32)
        context = np.zeros((1, 64), dtype=np.float32)
        probability_sum = 0.0
        speech_samples = 0
        for offset in range(0, samples, 512):
            chunk = pcm[offset:offset + 512].astype(np.float32, copy=False)
            count = chunk.size
            if count < 512:
                chunk = np.pad(chunk, (0, 512 - count))
            model_input = np.concatenate((context, chunk.reshape(1, 512)), axis=1)
            output, next_state = session.run(
                None, {"input": model_input, "state": state,
                       "sr": np.array(16000, dtype=np.int64)}
            )
            probability = np.asarray(output)
            if probability.size != 1:
                raise ValueError("Silero must return one speech probability per frame")
            probability = float(probability.item())
            if not math.isfinite(probability) or not 0 <= probability <= 1:
                raise ValueError("Silero speech probability must be finite and between 0 and 1")
            state = np.asarray(next_state, dtype=np.float32)
            if state.shape != (2, 1, 128) or not np.isfinite(state).all():
                raise ValueError("Silero returned an invalid recurrent state")
            context = model_input[:, -64:]
            probability_sum += probability * count
            if probability >= 0.5:
                speech_samples += count
        return SpeechEvidence(probability_sum / samples, speech_samples / samples,
                              samples / 16000)
