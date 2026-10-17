from simulcast.config import AppCfg
from simulcast.pipeline.orchestrator import Orchestrator, Segment, _ends_open
from simulcast.sink import NullSink

cases = {
    "So, setting expectations too high can make us look bad if": True,
    "We don't maintain them": False,
    "and these efforts": False,
    "Today we will": True,
    "The budget for next quarter looks tight": False,
    "...and the budget for": True,
    "...and the budget for.": True,
}
for text, expected in cases.items():
    got = _ends_open(text)
    print(("ok  " if got == expected else "FAIL"), repr(text), got)
    assert got == expected

cfg = AppCfg()
cfg.mt.enabled = False
orch = Orchestrator(cfg, None, None, NullSink())
seg = Segment(id="x", source="file", t0=0.0)
assert orch._looks_like_noise(seg, "拜。", "Chinese") is False, "switch off must not filter"

cfg.segment.semantic_heuristics = True
orch = Orchestrator(cfg, None, None, NullSink())
assert orch._looks_like_noise(seg, "Hi.", "English") is False
assert orch._looks_like_noise(seg, "拜。", "Chinese") is True
assert orch._looks_like_noise(seg, "Yes.", "English") is False
print("noise filter: OK")
print("OK")
