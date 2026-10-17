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


async def scenario(decode_a, decode_b, final_text):
    cfg = AppCfg()
    cfg.mt.enabled = False
    sink = Collect()
    orch = Orchestrator(cfg, None, None, sink)
    seg = Segment(id="b-1", source="file", t0=0.0, t_end=6.0)
    await orch._ingest_text(seg, decode_a, final=False)
    await orch._ingest_text(seg, decode_b, final=False)
    early = [m for m in sink.msgs if m["type"] == "caption"]
    await orch._ingest_text(seg, final_text, final=True)
    ev = [m for m in sink.msgs if m["type"] == "eval"][-1]
    return ev, early


async def main():
    ev, caps = await scenario(
        "Hello everyone. Welcome to the meet",
        "Hello everyone. Welcome to the meet",
        "Hello everyone. Welcome to the meeting. Bye.",
    )
    print("case clean :", ev["early_count"], ev["final_count"], ev["matched_sents"], ev["premature_chars"])
    assert ev["early_count"] == 1 and ev["matched_sents"] == 1 and ev["premature_chars"] == 0

    ev2, caps2 = await scenario(
        "We need to decide. Next topic.",
        "We need to decide. Next topic.",
        "We need to decide before Friday. Next topic.",
    )
    print("case early :", ev2["early_count"], ev2["final_count"], ev2["matched_sents"], ev2["premature_chars"])
    print("  early:", ev2["early_sents"], "final:", ev2["final_sents"])
    assert ev2["early_count"] == 1 and ev2["matched_sents"] == 0

    print("draft caption:", caps[-1]["raw"])
    print("OK")


asyncio.run(main())
