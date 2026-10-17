from __future__ import annotations

import numpy as np


class EnergyVad:
    """Adaptive RMS gate. Good enough for meeting audio; swap for silero-onnx later."""

    def __init__(self, floor: float = 3e-4, ratio: float = 3.0, attack_frames: int = 2):
        self.floor = floor
        self.ratio = ratio
        self.attack_frames = attack_frames
        self.noise = floor
        self._run = 0

    def reset(self) -> None:
        self.noise = self.floor
        self._run = 0

    def is_speech(self, frame: np.ndarray) -> bool:
        if frame.size == 0:
            return False
        rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))
        speech = rms > max(self.floor, self.noise * self.ratio)
        if speech:
            self._run += 1
        else:
            self._run = 0
            self.noise = 0.95 * self.noise + 0.05 * max(rms, 1e-5)
            self.noise = min(self.noise, self.floor * 4)
        return self._run >= self.attack_frames
