"""An ASR input cap must leave overlap for recognizing a cut word again."""
import numpy as np

from simulcast.audio.segmenter import Segmenter
from simulcast.config import SegmentCfg


def main():
    cfg = SegmentCfg()
    cfg.window_overlap_s = 1.5
    segmenter = Segmenter(cfg, "file")
    events = []
    for index in range(1100):
        frame = np.full(320, (index + 1) / 2000, dtype=np.float32)
        events.extend(segmenter.push(frame, (index + 1) * 0.02, True))
    ends = [e for e in events if e.kind == "end"]
    starts = [e for e in events if e.kind == "start"]
    assert len(ends) >= 2 and len(starts) >= 3
    first_end, second_start = ends[0], starts[1]
    assert abs(first_end.t1 - second_start.t0 - 1.5) < 0.021, (first_end.t1, second_start.t0)
    assert all(e.audio.size <= 160000 for e in ends), "a boundary repair must not enlarge normal ASR requests"
    assert np.array_equal(first_end.audio[-24000:], second_start.audio[:24000]), "the overlap must contain the same audio samples"
    assert abs(second_start.overlap_s - 1.5) < 0.021
    assert first_end.t0 >= 0
    assert starts[0].t0 == 0.0
    last = segmenter.flush(22.0)
    assert last and last[0].reason == "flush"
    assert last[0].overlap_s == 1.5
    assert segmenter.flush(22.0) == []
    exact = Segmenter(cfg, "file")
    for index in range(500):
        exact.push(np.full(320, .1, dtype=np.float32), (index + 1) * .02, True)
    assert exact.flush(10) == [], "EOF must not decode the retained overlap a second time"
    print("overlapping bounded windows OK")


main()
