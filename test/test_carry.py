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


def finals(sink):
    latest = {m["id"]: m for m in sink.msgs if m["type"] == "caption"}
    return [m["raw"] for m in latest.values() if m["state"] == "final"]


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
    assert finals(sink) == ["Yeah, it's better to make sure you are putting in quality time."]
    pending = [m for m in sink.msgs if m["type"] == "caption" and m["state"] == "draft"][-1]
    assert pending["raw"] == "And these efforts."

    second = Segment(id="b-2", source="file", t0=70.86, t1=74.41, t_end=74.41, end_reason="silence")
    await orch._ingest_text(second, "Can easily be demonstrated by the results we achieve.", final=True)
    assert len(finals(sink)) == 2
    assert "and these efforts can easily be demonstrated" in finals(sink)[-1].lower()
    completed = [m for m in sink.msgs if m.get("id") == pending["id"]][-1]
    assert completed["state"] == "final"

    orch = make_orch()
    sink = orch.sink
    third = Segment(id="b-3", source="file", t0=80.0, t1=90.0, t_end=90.0, end_reason="hard_pause")
    await orch._ingest_text(third, "We need to make a decision before Friday. What do you think?", final=True)
    # A short pause at the physical cap cannot finalize the last candidate.
    assert finals(sink) == ["We need to make a decision before Friday."]
    await orch.finish()
    assert finals(sink) == ["We need to make a decision before Friday.", "What do you think?"]

    orch = make_orch()
    sink = orch.sink
    burst = Segment(id="b-4", source="file", t0=58.2, t1=67.63, t_end=67.63, end_reason="hard")
    text = (
        "One mistake people make is to limit communication only to their bosses. "
        "It's important to expand your network and include your coworkers."
    )
    await orch._ingest_text(burst, text, final=False)
    await orch._ingest_text(burst, text, final=True)
    assert finals(sink) == ["One mistake people make is to limit communication only to their bosses."]

    nxt = Segment(id="b-5", source="file", t0=66.13, t1=73.31, t_end=73.31, end_reason="silence", overlap_s=1.5)
    await orch._ingest_text(nxt, "include your coworkers. But some of our coworkers are less senior to us.", final=True)
    assert finals(sink) == [
        "One mistake people make is to limit communication only to their bosses.",
        "It's important to expand your network and include your coworkers.",
        "But some of our coworkers are less senior to us.",
    ]

    orch = make_orch()
    sink = orch.sink
    dup = Segment(id="b-6", source="file", t0=0.0, t1=5.0, t_end=5.0, end_reason="silence")
    await orch._ingest_text(dup, "This is a sentence. This is a sentence.", final=True)
    # Genuine repeated speech is no longer removed by a substring heuristic.
    assert finals(sink) == ["This is a sentence.", "This is a sentence."]
    print("OK")


async def switch_cases():
    text = "So, setting expectations too high at the beginning can make us look bad if."

    for semantic in (False, True):
        orch = make_orch(semantic=semantic)
        seg = Segment(id="b-7", source="file", t0=0.0, t1=9.0, t_end=9.0, end_reason="hard")
        await orch._ingest_text(seg, text, final=True)
        assert finals(orch.sink) == [], "long tails must stay revisable independently of English heuristics"
        await orch.finish()
        assert finals(orch.sink) == [text]


asyncio.run(main())
asyncio.run(switch_cases())
