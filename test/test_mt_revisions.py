"""Queued text is immutable, and obsolete translators cannot finalize new text."""
import asyncio

from simulcast.config import AppCfg
from simulcast.pipeline.orchestrator import Orchestrator, Segment


class Collect:
    def __init__(self):
        self.msgs = []

    async def emit(self, msg):
        self.msgs.append(msg)


class DelayedMt:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = []

    async def stream(self, text, history=None, draft=False):
        self.calls.append(text)
        if len(self.calls) == 1:
            self.entered.set()
            await self.release.wait()
        yield text


async def snapshot():
    mt, sink = DelayedMt(), Collect()
    orch = Orchestrator(AppCfg(), None, mt, sink)
    await orch.start()
    a = Segment("a", "file", 0, raw="First.")
    b = Segment("b", "file", 1, raw="Queued text.")
    try:
        orch._enqueue_mt(a, final=True)
        await asyncio.wait_for(mt.entered.wait(), 2)
        orch._enqueue_mt(b, final=True)
        b.raw = "A mutation which was never enqueued."
        mt.release.set()
        await asyncio.wait_for(orch._mt_q.join(), 3)
        assert mt.calls == ["First.", "Queued text."], mt.calls
    finally:
        await orch.stop()


async def obsolete_completion():
    sink = Collect()
    seg = Segment("a", "file", 0, raw="Old sentence.")

    class RevisingMt:
        async def stream(self, text, history=None, draft=False):
            # The producer revises the caption as a stream ends without another delta.
            seg.mt_rev += 1
            seg.raw = "New sentence."
            if False:
                yield ""

    orch = Orchestrator(AppCfg(), None, RevisingMt(), sink)
    await orch.start()
    try:
        orch._enqueue_mt(seg, final=True)
        await asyncio.wait_for(orch._mt_q.join(), 3)
        assert seg.state == "draft", "an obsolete stream finalized the new sentence"
        assert not sink.msgs, sink.msgs
    finally:
        await orch.stop()


async def finish_waits_for_active_mt():
    sink = Collect()

    class SlowMt:
        async def stream(self, text, history=None, draft=False):
            await asyncio.sleep(0.4)
            yield "完整译文"

    orch = Orchestrator(AppCfg(), None, SlowMt(), sink)
    await orch.start()
    seg = Segment("a", "file", 0, raw="Full sentence.")
    try:
        orch._enqueue_mt(seg, final=True)
        await orch.finish()
        assert seg.state == "final", "finish returned while MT was still running"
        assert sink.msgs[-1]["translation"] == "完整译文"
    finally:
        await orch.stop()


async def main():
    # Run separately so the first regression cannot hide the other failures.
    failures = []
    for check in (snapshot, obsolete_completion, finish_waits_for_active_mt):
        try:
            await check()
        except AssertionError as exc:
            failures.append(f"{check.__name__}: {exc}")
    assert not failures, "\n".join(failures)
    print("MT snapshots/revisions/draining OK")


asyncio.run(main())
