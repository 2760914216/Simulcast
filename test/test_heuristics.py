from simulcast.config import AppCfg
from simulcast.pipeline.orchestrator import Orchestrator, Segment
from simulcast.sink import NullSink

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
