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


def kids(sink, burst):
    return [m["raw"] for m in sink.msgs if m["type"] == "caption" and m["id"].startswith(burst + "-s")]


def make_orch(semantic=False):
    cfg = AppCfg()
    cfg.mt.enabled = False
    cfg.segment.semantic_heuristics = semantic
    return Orchestrator(cfg, None, None, Collect())


async def main():
    orch = make_orch()
    sink = orch.sink

    first = Segment(id="b-1", source="file", t0=61.42, t1=70.86, t_end=70.86, end_reason="hard")
    await orch._ingest_text(
        first,
        "Yeah, it's better to make sure you are putting in quality time. And these efforts.",
        final=True,
    )
    print("1 emitted:", kids(sink, "b-1"), "carry:", orch._carry.get("file", (None, None))[1:2])
    assert kids(sink, "b-1") == ["Yeah, it's better to make sure you are putting in quality time."]
    assert orch._carry["file"][1] == "And these efforts"

    second = Segment(id="b-2", source="file", t0=70.86, t1=74.41, t_end=74.41, end_reason="silence")
    await orch._ingest_text(second, "Can easily be demonstrated by the results we achieve.", final=True)
    print("2 emitted:", kids(sink, "b-2"))
    assert len(kids(sink, "b-2")) == 1
    assert "and these efforts can easily be demonstrated" in kids(sink, "b-2")[0].lower()
    assert orch._carry == {}

    orch = make_orch()
    sink = orch.sink
    third = Segment(id="b-3", source="file", t0=80.0, t1=90.0, t_end=90.0, end_reason="hard_pause")
    await orch._ingest_text(third, "We need to make a decision before Friday. What do you think?", final=True)
    print("3 emitted:", kids(sink, "b-3"))
    assert len(kids(sink, "b-3")) == 2 and orch._carry == {}

    orch = make_orch()
    sink = orch.sink
    burst = Segment(id="b-4", source="file", t0=58.2, t1=67.63, t_end=67.63, end_reason="hard")
    text = (
        "One mistake people make is to limit communication only to their bosses. "
        "It's important to expand your network and include your coworkers."
    )
    await orch._ingest_text(burst, text, final=False)
    await orch._ingest_text(burst, text, final=True)
    out = kids(sink, "b-4")
    print("4 emitted:", out)
    assert len(out) == 2 and len(set(out)) == 2
    assert orch._carry == {}

    nxt = Segment(id="b-5", source="file", t0=67.63, t1=73.31, t_end=73.31, end_reason="silence")
    await orch._ingest_text(nxt, "But some of our coworkers are less senior to us.", final=True)
    print("5 emitted:", kids(sink, "b-5"))
    assert kids(sink, "b-5") == ["But some of our coworkers are less senior to us."]

    orch = make_orch()
    sink = orch.sink
    dup = Segment(id="b-6", source="file", t0=0.0, t1=5.0, t_end=5.0, end_reason="silence")
    await orch._ingest_text(dup, "This is a sentence. This is a sentence.", final=True)
    print("6 emitted:", kids(sink, "b-6"))
    assert len(kids(sink, "b-6")) == 1
    print("OK")


async def switch_cases():
    text = "So, setting expectations too high at the beginning can make us look bad if."

    orch = make_orch(semantic=False)
    seg = Segment(id="b-7", source="file", t0=0.0, t1=9.0, t_end=9.0, end_reason="hard")
    await orch._ingest_text(seg, text, final=True)
    print("switch off -> carry:", orch._carry, "emitted:", kids(orch.sink, "b-7"))
    assert orch._carry == {}
    assert kids(orch.sink, "b-7") == [text]

    orch = make_orch(semantic=True)
    seg = Segment(id="b-8", source="file", t0=0.0, t1=9.0, t_end=9.0, end_reason="hard")
    await orch._ingest_text(seg, text, final=True)
    carried = orch._carry.get("file", (None, None))[1]
    print("switch on  -> carry:", carried, "emitted:", kids(orch.sink, "b-8"))
    assert carried is not None and carried.endswith("if")
    assert kids(orch.sink, "b-8") == []


asyncio.run(main())
asyncio.run(switch_cases())
