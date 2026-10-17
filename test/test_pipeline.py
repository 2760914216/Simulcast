import asyncio
import pathlib

import numpy as np

from simulcast.asr.base import AsrResult
from simulcast.config import AppCfg
from simulcast.pipeline.orchestrator import Orchestrator
from simulcast.sources import file_frames


class StubAsr:
    def __init__(self):
        self.calls = 0

    async def transcribe(self, pcm, sample_rate=16000):
        self.calls += 1
        await asyncio.sleep(0.02)
        return AsrResult(text=f"chunk{self.calls}_{pcm.size // 16000}s", language="English", latency_ms=20)

    async def healthy(self):
        return True


class StubMt:
    async def stream(self, text, context=None, draft=False):
        for piece in ["译文", "：", text[-6:]]:
            yield piece
            await asyncio.sleep(0.01)

    async def healthy(self):
        return True


class Collect:
    def __init__(self):
        self.msgs = []

    async def emit(self, msg):
        self.msgs.append(msg)

    async def close(self):
        pass


async def main():
    cfg = AppCfg()
    asr, sink = StubAsr(), Collect()
    orch = Orchestrator(cfg, asr, StubMt(), sink)
    await orch.start()
    await orch.run(file_frames("test/en.wav", speed=20.0), source="file")
    await orch.finish()
    await orch.stop()

    caps = [m for m in sink.msgs if m["type"] == "caption"]
    ids = {c["id"] for c in caps}
    finals = [c for c in caps if c["state"] == "final"]
    print("messages:", len(sink.msgs), "captions:", len(caps), "segments:", len(ids), "finals:", len(finals))
    print("asr calls:", asr.calls)
    for c in caps[-6:]:
        print(f'  {c["id"]:>10} t0={c["t0"]:6.2f} t1={c["t1"]} {c["state"]:>5} raw={c["raw"]!r:34} tr={c["translation"]!r}')
    assert finals, "no final captions"
    assert all(c["raw"] for c in finals), "final without raw"
    assert all(c["translation"] for c in finals), "final without translation"
    print("OK")


if not pathlib.Path("test/en.wav").exists():
    print("SKIP: test/en.wav not present")
    raise SystemExit(0)

asyncio.run(main())
