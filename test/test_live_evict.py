import asyncio
from types import SimpleNamespace

import numpy as np

from simulcast.config import AppCfg
from simulcast.pipeline.orchestrator import Orchestrator, Segment


class SlowAsr:
    async def transcribe(self, audio):
        await asyncio.sleep(3600)
        return SimpleNamespace(text="", language="en", latency_ms=1)

    async def healthy(self):
        return True


class QuietSink:
    def __init__(self):
        self.msgs = []

    async def emit(self, msg):
        self.msgs.append(msg)

    async def close(self):
        pass


def make(prefix, n, *, ended=True):
    return {
        f"{prefix}-{i:04d}": Segment(id=f"{prefix}-{i:04d}", source="file", t0=0.0, t1=1.0 if ended else None)
        for i in range(n)
    }


def check_eviction(orch):
    live = make("seg", 130)
    for i in range(0, 130, 4):
        live[f"seg-{i:04d}"].asr_busy = True
    busy_ids = {key for key, seg in live.items() if seg.asr_busy}
    orch._evict_finished(live)
    assert len(live) == 64, len(live)
    assert busy_ids <= set(live), "dropped a segment whose decode was still running"

    live = make("done", 200)
    live["done-0000"].asr_pending = "tick"
    orch._evict_finished(live)
    assert len(live) == 64, len(live)
    assert "done-0000" in live, "dropped a segment with a queued decode"

    live = make("open", 200, ended=False)
    orch._evict_finished(live)
    assert len(live) == 200, len(live)

    live = make("busy", 200)
    for seg in live.values():
        seg.asr_busy = True
    orch._evict_finished(live)
    assert len(live) == 200, len(live)


async def check_kick_marks_busy(orch):
    seg = Segment(id="file-0001", source="file", t0=0.0, t1=1.0)
    seg.audio = np.full(1600, 0.1, dtype=np.float32)
    orch._kick_asr(seg, "final")
    assert seg.asr_busy, "a queued decode left the segment looking finished"
    await asyncio.sleep(0.05)
    assert seg.asr_busy, "the running decode lost its busy flag"
    orch._kick_asr(seg, "tick")
    assert seg.asr_pending == "tick", seg.asr_pending
    for task in list(orch._tasks):
        task.cancel()
    await asyncio.gather(*list(orch._tasks), return_exceptions=True)
    assert not seg.asr_busy, "cancelling the decode left the segment stuck busy"


async def main():
    cfg = AppCfg()
    cfg.mt.enabled = False
    orch = Orchestrator(cfg, SlowAsr(), None, QuietSink())
    check_eviction(orch)
    await check_kick_marks_busy(orch)
    print("OK")


asyncio.run(main())
