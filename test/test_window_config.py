"""Reject window budgets which cannot produce bounded, advancing windows."""
import math

from simulcast.config import ConfigError, _from_dict


def config(segment=None, asr=None):
    return _from_dict({
        "segment": segment or {},
        "asr": {"provider": "a", "providers": {"a": {"base_url": "http://a", "model": "a"}}, **(asr or {})},
        "mt": {"provider": "m", "providers": {"m": {"base_url": "http://m", "model": "m"}}},
    }, "test")


def main():
    for overrides in (
        {"window_overlap_s": -0.1}, {"window_overlap_s": 10.0},
        {"window_overlap_s": math.nan}, {"max_segment_s": 0},
        {"utterance_max_s": -1}, {"utterance_max_chars": 0},
        {"audio_buffer_s": 5}, {"utterance_max_s": math.inf},
    ):
        try:
            config(overrides)
        except ConfigError:
            pass
        else:
            raise AssertionError(f"accepted unsafe segment budgets: {overrides}")
    cfg = config({"window_overlap_s": 0.0, "utterance_max_s": 40, "audio_buffer_s": 40})
    assert cfg.segment.window_overlap_s == 0.0
    for options in ({"speech_evidence_mode": "reject"}, {"speech_evidence_mode": "observe"},
                    {"repair_max_s": 0}, {"repair_max_s": -1}, {"repair_max_s": math.inf},
                    {"repair_max_s": math.nan}, {"repair_max_s": "invalid"}):
        try:
            config(asr=options)
        except ConfigError:
            pass
        else:
            raise AssertionError(f"accepted unconfigured/unsupported evidence mode: {options}")
    print("window and evidence config validation OK")


main()
