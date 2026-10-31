import asyncio
from types import SimpleNamespace

import numpy as np

from simulcast.config import AppCfg
from simulcast.pipeline.orchestrator import Orchestrator, Segment


class GatedSink:
    """Blocks inside emit() so the test can observe the ingest window."""

    def __init__(self):
        self.msgs = []
        self.block = False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def emit(self, msg):
        self.msgs.append(msg)
        self.entered.set()
        if self.block:
            await self.release.wait()

    async def close(self):
        pass


class FakeAsr:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    async def transcribe(self, audio):
        self.calls += 1
        await asyncio.sleep(0.01)
        return SimpleNamespace(text=self.text, language="en", latency_ms=10)

    async def healthy(self):
        return True


async def idle(orch):
    while orch._tasks:
        await asyncio.gather(*list(orch._tasks), return_exceptions=True)


async def main():
    cfg = AppCfg()
    cfg.mt.enabled = False
    sink = GatedSink()
    asr = FakeAsr("Hello world.")
    orch = Orchestrator(cfg, asr, None, sink)
    seg = Segment(id="b-1", source="file", t0=0.0, t_end=1.0)
    seg.audio = np.full(1600, 0.1, dtype=np.float32)

    sink.block = True
    orch._kick_asr(seg, "final")
    await asyncio.wait_for(sink.entered.wait(), 2.0)
    assert seg.asr_busy, "asr_busy was cleared before ingest finished"

    orch._kick_asr(seg, "tick")
    assert seg.asr_pending == "tick", seg.asr_pending
    await asyncio.sleep(0.05)
    assert asr.calls == 1, f"a second decode started during ingest: {asr.calls}"

    sink.block = False
    sink.release.set()
    await idle(orch)

    assert asr.calls == 2, f"deferred tick never ran: {asr.calls}"
    assert not seg.asr_busy and seg.asr_pending is None, (seg.asr_busy, seg.asr_pending)
    print("OK")


asyncio.run(main())
