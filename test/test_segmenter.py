import numpy as np
from simulcast.config import load
from simulcast.audio.segmenter import Segmenter

cfg = load()
seg = Segmenter(cfg.segment, "x", frame_ms=20)
t = 0.0
evs = []
for i in range(700):
    t += 0.02
    amp = 0.05 if (i % 150) < 140 else 0.0005
    evs += seg.push(np.full(320, amp, dtype=np.float32), t, amp > 0.01)
for e in evs:
    if e.kind != "tick":
        print(f"{e.kind:5} {e.seg_id} t0={e.t0:5.2f} t1={e.t1:5.2f} dur={e.audio.size/16000:4.2f}s")
