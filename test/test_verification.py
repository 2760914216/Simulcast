"""Speech evidence records remain observations, including on weak evidence."""
import json
import unittest

from simulcast.asr.verification import observation
from simulcast.audio.speech_evidence import SpeechEvidence


class ObservationTests(unittest.TestCase):
    def record(self, **changes):
        values = dict(source="loopback", window_id="window-7", t0=12.5, t1=14.5,
                      text="Did you hear that?", evidence=SpeechEvidence(0.01, 0.0, 2.0),
                      guard_reason="")
        values.update(changes)
        return observation(**values)

    def test_weak_evidence_preserves_text_in_a_protocol_v1_record(self):
        record = self.record()
        self.assertEqual(record, {
            "v": 1, "type": "asr_observation", "source": "loopback",
            "window_id": "window-7", "t0": 12.5, "t1": 14.5,
            "text": "Did you hear that?", "guard_reason": "",
            "evidence": {"mean_probability": 0.01, "speech_fraction": 0.0, "seconds": 2.0},
        })
        self.assertEqual(json.loads(json.dumps(record, allow_nan=False)), record)

    def test_original_text_and_guard_reason_are_retained_for_diagnostics(self):
        record = self.record(text="  no, no, no  ", guard_reason="repeat")
        self.assertEqual(record["text"], "  no, no, no  ")
        self.assertEqual(record["guard_reason"], "repeat")

    def test_mapping_evidence_is_copied_and_normalized_to_numbers(self):
        evidence = {"mean_probability": 0.8, "speech_fraction": 1, "seconds": 2}
        record = self.record(evidence=evidence)
        evidence["mean_probability"] = 0
        self.assertEqual(record["evidence"]["mean_probability"], 0.8)
        self.assertIsInstance(record["evidence"]["seconds"], float)

    def test_empty_transcript_and_empty_audio_are_valid_observations(self):
        record = self.record(text="", t0=0, t1=0, evidence=SpeechEvidence(0, 0, 0))
        self.assertEqual(record["text"], "")
        self.assertEqual(record["evidence"]["seconds"], 0)

    def test_invalid_window_or_identity_is_rejected(self):
        for changes in ({"t0": -1}, {"t1": 10}, {"t1": float("inf")},
                        {"t0": float("nan")}, {"source": ""}, {"window_id": " "},
                        {"text": None}, {"guard_reason": 3}):
            with self.subTest(changes=changes), self.assertRaises((ValueError, TypeError)):
                self.record(**changes)

    def test_invalid_evidence_mapping_is_rejected(self):
        for evidence in ({}, {"mean_probability": "0.2", "speech_fraction": 0, "seconds": 2},
                         {"mean_probability": 0.2, "speech_fraction": 2, "seconds": 2}):
            with self.subTest(evidence=evidence), self.assertRaises((ValueError, TypeError)):
                self.record(evidence=evidence)


if __name__ == "__main__":
    unittest.main()
