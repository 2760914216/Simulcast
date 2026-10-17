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


async def run_case(decodes, final_text):
    cfg = AppCfg()
    cfg.mt.enabled = False
    sink = Collect()
    orch = Orchestrator(cfg, None, None, sink)
    seg = Segment(id="b-1", source="file", t0=0.0, t_end=12.0)
    for text in decodes:
        await orch._ingest_text(seg, text, final=False)
    await orch._ingest_text(seg, final_text, final=True)
    kids = [m for m in sink.msgs if m["type"] == "caption" and m["id"].startswith("b-1-s")]
    ev = [m for m in sink.msgs if m["type"] == "eval"][-1]
    return kids, ev


async def main():
    # 0013: final re-splits a semicolon-joined sentence -> must not duplicate
    kids, ev = await run_case(
        [
            "But it goes without saying that you shouldn't act like a know-it-all; otherwise, you'll appear arrogant.",
            "But it goes without saying that you shouldn't act like a know-it-all; otherwise, you'll appear arrogant.",
        ],
        "But it goes without saying that you shouldn't act like a know-it-all. Otherwise, you'll appear arrogant. Absolutely, be humble.",
    )
    print("0013 children:", len(kids))
    for k in kids:
        print("   ", k["id"], repr(k["raw"]))
    texts = [k["raw"] for k in kids]
    assert len(texts) == 3, texts
    assert sum("otherwise" in t.lower() for t in texts) == 1, texts

    # 0004: final prepends a word we already missed -> emit only the new sentence
    kids2, ev2 = await run_case(
        [
            "Some of our coworkers are less senior to us.",
            "Some of our coworkers are less senior to us.",
        ],
        "But some of our coworkers are less senior to us. Is it really necessary to connect with them?",
    )
    print("0004 children:", [(k["id"], k["raw"]) for k in kids2])
    assert len(kids2) == 2, kids2
    assert kids2[1]["raw"].startswith("Is it really"), kids2[1]["raw"]

    # times must be monotonic per burst
    for group in (kids, kids2):
        t0s = [k["t0"] for k in group]
        assert t0s == sorted(t0s), t0s
        assert all(k["t1"] > k["t0"] for k in group)
    print("eval premature:", ev["premature_chars"], ev2["premature_chars"])
    print("OK")


asyncio.run(main())
