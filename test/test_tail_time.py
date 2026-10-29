import asyncio

from simulcast.config import AppCfg
from simulcast.pipeline.orchestrator import Orchestrator, Segment


class Collect:
    def __init__(self):
        self.msgs = []

    async def emit(self, msg):
        self.msgs.append(msg)

    async def close(self):
        pass


async def main():
    cfg = AppCfg()
    cfg.mt.enabled = False
    sink = Collect()
    orch = Orchestrator(cfg, None, None, sink)
    seg = Segment(id="b-1", source="file", t0=0.0, t_end=12.0)

    await orch._ingest_text(seg, "First sentence here. Second half still going", final=False)
    first = [m for m in sink.msgs if m["id"] == "b-1"][-1]
    print("first draft:", first["t0"], first["raw"])
    assert first["t0"] == 0.0, first["t0"]

    await orch._ingest_text(seg, "First sentence here. Second half still going", final=False)
    kid = [m for m in sink.msgs if m["id"].startswith("b-1-s")][-1]
    tail = [m for m in sink.msgs if m["id"] == "b-1"][-1]
    print("child:", kid["id"], kid["t0"], kid["t1"], "tail:", tail["t0"], tail["raw"])
    assert tail["raw"].startswith("Second half"), tail["raw"]
    assert tail["t0"] >= kid["t1"] - 1e-6, (tail["t0"], kid["t1"])
    print("OK")


asyncio.run(main())
