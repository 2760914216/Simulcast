"""Retranslating a draft must not restart its visible translation token by token."""
import asyncio
import unittest

from simulcast.config import AppCfg
from simulcast.pipeline.orchestrator import Orchestrator, Segment
from simulcast.pipeline.utterances import UtteranceUpdate
from simulcast.render.model import CaptionStore


class Collect:
    def __init__(self):
        self.events = []
        self.store = CaptionStore()

    async def emit(self, msg):
        if msg.get("type") == "caption":
            self.events.append(msg)
            self.store.apply(msg)


class DraftDisplayTests(unittest.IsolatedAsyncioTestCase):
    def make(self, mt):
        cfg = AppCfg()
        # Make every token eligible for publication, including slow streams.
        cfg.mt.emit_interval_ms = 0
        sink = Collect()
        orch = Orchestrator(cfg, None, mt, sink)
        self.addAsyncCleanup(orch.stop)
        return orch, sink

    async def test_growing_source_does_not_restart_visible_translation(self):
        rows = [
            ("Or x to y.", "或者从 x 到 y。"),
            ("Or x to y mapping.", "或者从 x 到 y 的映射。"),
            ("Or x to y mappings, and in.", "或者 x 到 y 的映射，以及 in。"),
            ("Or x to y mappings, and in the last video.",
             "或者从 x 到 y 的映射，以及在上一期视频中。"),
        ]

        class Mt:
            async def stream(self, text, history=None, draft=False):
                translated = dict(rows)[text]
                yield "或者"
                yield translated[2:]

        orch, sink = self.make(Mt())
        await orch.start()
        previous = ""
        for index, (raw, translated) in enumerate(rows):
            sink.events.clear()
            await orch._apply_utterance(
                UtteranceUpdate("u1", "file", 24.56, 25.5 + index, raw, False))
            await asyncio.wait_for(orch._mt_q.join(), 1)
            visible = [event["translation"] for event in sink.events]
            if previous:
                self.assertTrue(all(text in (previous, translated) for text in visible), visible)
            else:
                self.assertIn("或者", visible, "the first translation should still stream")
            self.assertEqual(sink.store.current()["translation"], translated)
            previous = translated

    async def test_replacement_waits_for_completion_even_when_long_enough(self):
        entered, release = asyncio.Event(), asyncio.Event()

        class Mt:
            async def stream(self, text, history=None, draft=False):
                yield "或者"
                yield "从 x 到 y 的映射"
                entered.set()
                await release.wait()
                yield "，以及在上一期视频中。"

        orch, sink = self.make(Mt())
        seg = Segment("u1", "file", 0, raw="Or x to y mapping, and in the last video.",
                      translation="或者从 x 到 y。", translation_raw="Or x to y.")
        await orch._emit(seg)
        await orch.start()
        orch._enqueue_mt(seg, final=False)
        await asyncio.wait_for(entered.wait(), 1)
        self.assertEqual(sink.store.current()["translation"], "或者从 x 到 y。")
        self.assertEqual(seg.translation_raw, "Or x to y.")
        release.set()
        await asyncio.wait_for(orch._mt_q.join(), 1)
        self.assertEqual(sink.store.current()["translation"],
                         "或者从 x 到 y 的映射，以及在上一期视频中。")
        self.assertEqual(seg.translation_raw, seg.raw)

    async def test_completed_shorter_correction_replaces_old_translation(self):
        class Mt:
            async def stream(self, text, history=None, draft=False):
                yield "留"
                yield "下。"

        orch, sink = self.make(Mt())
        seg = Segment("u1", "file", 0, raw="Stay.",
                      translation="我们应该立刻离开这里。", translation_raw="We should leave now.")
        await orch._emit(seg)
        await orch.start()
        orch._enqueue_mt(seg, final=False)
        await asyncio.wait_for(orch._mt_q.join(), 1)
        self.assertEqual([e["translation"] for e in sink.events],
                         ["我们应该立刻离开这里。", "留下。"])

    async def test_superseded_buffer_never_reaches_display(self):
        entered, release = asyncio.Event(), asyncio.Event()

        class Mt:
            async def stream(self, text, history=None, draft=False):
                if text == "We should go now.":
                    yield "走"
                    entered.set()
                    await release.wait()
                    yield "吧。"
                else:
                    yield "留"
                    yield "下。"

        orch, sink = self.make(Mt())
        seg = Segment("u1", "file", 0, raw="We should go now.",
                      translation="我们应该走。", translation_raw="We should go.")
        await orch._emit(seg)
        await orch.start()
        orch._enqueue_mt(seg, final=False)
        await asyncio.wait_for(entered.wait(), 1)
        seg.raw = "Stay."
        orch._enqueue_mt(seg, final=False)
        release.set()
        await asyncio.wait_for(orch._mt_q.join(), 1)
        self.assertEqual([e["translation"] for e in sink.events], ["我们应该走。", "留下。"])
        self.assertEqual(sink.store.current()["raw"], "Stay.")

    async def test_failed_replacement_cannot_certify_old_translation(self):
        for outcome in ("empty", "failed"):
            with self.subTest(outcome=outcome):
                class Mt:
                    async def stream(self, text, history=None, draft=False):
                        if text == "We should go.":
                            yield "我们应该走。"
                        elif draft and outcome == "failed":
                            yield "留"
                            raise RuntimeError("translation interrupted")

                orch, sink = self.make(Mt())
                await orch.start()
                await orch._apply_utterance(
                    UtteranceUpdate("u1", "file", 0, 1, "We should go.", False))
                await asyncio.wait_for(orch._mt_q.join(), 1)
                await orch._apply_utterance(
                    UtteranceUpdate("u1", "file", 0, 2, "Stay.", False))
                await asyncio.wait_for(orch._mt_q.join(), 1)
                self.assertEqual(sink.store.current()["translation"], "我们应该走。")
                await orch._apply_utterance(
                    UtteranceUpdate("u1", "file", 0, 2, "Stay.", True))
                await asyncio.wait_for(orch._mt_q.join(), 1)
                current = sink.store.current()
                self.assertEqual((current["raw"], current["translation"], current["state"]),
                                 ("Stay.", "", "final"))


if __name__ == "__main__":
    unittest.main()
