"""Build speech-evidence diagnostics without making acceptance decisions."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
import math
from numbers import Real
from typing import Any

from ..audio.speech_evidence import SpeechEvidence
from ..protocol import PROTOCOL_VERSION


def observation(*, source: str, window_id: str, t0: float, t1: float,
                text: str, evidence: SpeechEvidence | Mapping[str, float],
                guard_reason: str = "") -> dict[str, Any]:
    """Pair the unmodified ASR text and guard reason with its audio interval.

    ``evidence`` is a SpeechEvidence or a mapping with the same three numeric
    fields: mean_probability, speech_fraction and seconds. These acoustic values
    are not ASR confidence. A low value changes neither text nor caption/history
    state; callers decide where to retain this protocol-v1 diagnostic record.
    """
    for name, value in (("source", source), ("window_id", window_id)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a nonempty string")
    for name, value in (("t0", t0), ("t1", t1)):
        if isinstance(value, bool) or not isinstance(value, Real):
            raise TypeError(f"{name} must be a finite number")
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be finite and nonnegative")
    if t1 < t0:
        raise ValueError("t1 must not precede t0")
    if not isinstance(text, str) or not isinstance(guard_reason, str):
        raise TypeError("text and guard_reason must be strings")
    if isinstance(evidence, Mapping):
        try:
            evidence = SpeechEvidence(evidence["mean_probability"],
                                      evidence["speech_fraction"], evidence["seconds"])
        except KeyError as exc:
            raise ValueError(f"Missing speech evidence field: {exc.args[0]}") from exc
    if not isinstance(evidence, SpeechEvidence):
        raise TypeError("evidence must be SpeechEvidence or a mapping of its fields")
    return {"v": PROTOCOL_VERSION, "type": "asr_observation", "source": source,
            "window_id": window_id, "t0": float(t0), "t1": float(t1), "text": text,
            "evidence": asdict(evidence), "guard_reason": guard_reason}
