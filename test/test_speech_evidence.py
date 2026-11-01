"""Deterministic tests for the optional ONNX speech evidence boundary."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from simulcast.audio.speech_evidence import SileroEvidence, SpeechEvidence


class AcousticSession:
    """The ONNX boundary only: chunk maximum stands in for model inference."""

    def __init__(self):
        self.feeds = []

    def run(self, outputs, feeds):
        self.feeds.append({key: value.copy() for key, value in feeds.items()})
        assert outputs is None
        assert feeds["input"].shape == (1, 576)
        assert feeds["input"].dtype == np.float32
        assert feeds["state"].shape == (2, 1, 128)
        assert feeds["sr"].dtype == np.int64
        assert feeds["sr"].item() == 16000
        probability = np.max(feeds["input"][:, 64:])
        return np.array([[probability]], dtype=np.float32), feeds["state"] + 1


class SpeechEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "silero.onnx"
        self.path.write_bytes(b"injected session; no real model required")
        self.session = AcousticSession()
        self.detector = SileroEvidence(self.path, session=self.session)

    def test_partial_tail_uses_actual_duration_in_aggregate(self):
        pcm = np.r_[np.full(512, 0.25), np.full(256, 0.75)]
        evidence = self.detector.measure(pcm)
        self.assertAlmostEqual(evidence.mean_probability, 5 / 12)
        self.assertAlmostEqual(evidence.speech_fraction, 1 / 3)
        self.assertEqual(evidence.seconds, 0.048)
        self.assertEqual(len(self.session.feeds), 2)
        np.testing.assert_array_equal(self.session.feeds[1]["input"][0, 320:], 0)
        np.testing.assert_array_equal(self.session.feeds[1]["input"][0, :64], 0.25)
        np.testing.assert_array_equal(self.session.feeds[1]["state"], 1)

    def test_shorter_than_one_chunk_is_measured(self):
        evidence = self.detector.measure(np.full(7, 0.5, dtype=np.float32))
        self.assertEqual(evidence, SpeechEvidence(0.5, 1.0, 7 / 16000))

    def test_empty_audio_is_zero_without_inference(self):
        self.assertEqual(self.detector.measure(np.empty(0, dtype=np.float32)),
                         SpeechEvidence(0.0, 0.0, 0.0))
        self.assertEqual(self.session.feeds, [])

    def test_independent_windows_reset_recurrent_state_and_context(self):
        self.detector.measure(np.full(1024, 0.75, dtype=np.float32))
        second = self.detector.measure(np.full(512, 0.25, dtype=np.float32))
        np.testing.assert_array_equal(self.session.feeds[2]["state"], 0)
        np.testing.assert_array_equal(self.session.feeds[2]["input"][0, :64], 0)
        self.assertEqual(second, SpeechEvidence(0.25, 0.0, 0.032))

    def test_only_16khz_audio_is_accepted_even_when_empty(self):
        for rate in (8000, 48000, 0):
            with self.subTest(rate=rate), self.assertRaisesRegex(ValueError, "16000"):
                self.detector.measure(np.empty(0, dtype=np.float32), sample_rate=rate)

    def test_invalid_pcm_does_not_reach_inference(self):
        for pcm in (np.zeros((10, 2)), np.array([np.nan]), np.array([np.inf]),
                    np.array([1.01]), np.array([-1.01]), np.array([32767], dtype=np.int16),
                    np.array([1j])):
            with self.subTest(pcm=pcm), self.assertRaises((ValueError, TypeError)):
                self.detector.measure(pcm)
        self.assertEqual(self.session.feeds, [])

    def test_missing_local_model_has_actionable_error(self):
        with self.assertRaisesRegex(FileNotFoundError, "[Mm]odel|[Mm]odel.*path|ONNX"):
            SileroEvidence(Path(self.folder.name) / "missing.onnx", session=self.session)
        with self.assertRaises((ValueError, FileNotFoundError)):
            SileroEvidence(self.folder.name, session=self.session)

    def test_onnxruntime_is_loaded_only_when_nonempty_audio_is_measured(self):
        with patch.dict("sys.modules", {"onnxruntime": None}):
            detector = SileroEvidence(self.path)
            self.assertEqual(detector.measure(np.empty(0, dtype=np.float32)).seconds, 0.0)
            with self.assertRaisesRegex(RuntimeError, "onnxruntime"):
                detector.measure(np.zeros(512, dtype=np.float32))

    def test_invalid_inference_probability_is_rejected(self):
        class BrokenSession:
            def run(self, outputs, feeds):
                return np.array([[np.nan]]), feeds["state"]

        detector = SileroEvidence(self.path, session=BrokenSession())
        with self.assertRaisesRegex(ValueError, "probability"):
            detector.measure(np.zeros(512, dtype=np.float32))

    def test_evidence_rejects_nonfinite_or_out_of_range_numbers(self):
        for values in ((np.nan, 0, 1), (1.1, 0, 1), (0, -0.1, 1),
                       (0, 1.1, 1), (0, 0, -1), (0, 0, np.inf)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                SpeechEvidence(*values)


if __name__ == "__main__":
    unittest.main()
