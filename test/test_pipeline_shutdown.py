"""Failed recognition and shutdown never certify an earlier guess."""
import asyncio
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from simulcast.asr.base import AsrResult
from simulcast import cli
from simulcast.config import AppCfg
from simulcast.pipeline.orchestrator import Orchestrator, Segment
from simulcast.pipeline.utterances import UtteranceAssembler, WindowDecode
from simulcast.render.model import CaptionStore


class Sink:
    def __init__(self):
        self.events = []

    async def emit(self, event):
        self.events.append(event)


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    def make(self, asr=None, mt=None):
        sink = Sink()
        cfg = AppCfg()
        orch = Orchestrator(cfg, asr, mt, sink)
        self.addAsyncCleanup(orch.stop)
        seg = Segment("w1", "file", 0, t_end=3, t1=3, end_reason="silence",
                      audio=np.full(48000, .1, dtype=np.float32))
        return orch, sink, seg

    async def test_rejected_failed_and_empty_final_keep_only_draft(self):
        for result in ("abcd " * 8, "", RuntimeError("ASR unavailable")):
            with self.subTest(result=str(result)):
                class Asr:
                    async def transcribe(self, pcm):
                        if isinstance(result, Exception):
                            raise result
                        return AsrResult(result, "English", 1)

                orch, sink, seg = self.make(Asr())
                await orch._ingest_text(seg, "An earlier unconfirmed guess.", False)
                first = next(e for e in sink.events if e["type"] == "caption")
                orch._kick_asr(seg, "final")
                await orch.finish()
                caps = [e for e in sink.events if e["type"] == "caption"]
                self.assertFalse(any(e["state"] == "final" for e in caps), caps)
                self.assertEqual(caps[-1]["id"], first["id"])
                self.assertEqual(caps[-1]["raw"], first["raw"])
                self.assertEqual(orch._history, [])
                self.assertEqual(orch._utterances.flush(), [])
                boundaries = [e for e in sink.events if e["type"] == "utterance_boundary"]
                self.assertEqual(boundaries[-1]["reason"], "asr_unconfirmed")
                self.assertIn(boundaries[-1]["outcome"], ("rejected", "empty", "failed"))
                # No later finish/stop may turn the retired guess into a final.
                await orch.finish()
                await orch.stop()
                self.assertEqual(orch._history, [])

    async def test_new_window_after_failure_is_independent(self):
        class Asr:
            async def transcribe(self, pcm):
                raise RuntimeError("ASR unavailable")

        orch, sink, seg = self.make(Asr())
        await orch._ingest_text(seg, "An earlier unconfirmed guess.", False)
        await orch._decode_window(seg, "final")
        next_seg = Segment("w2", "file", 4, t_end=7, t1=7, end_reason="silence")
        await orch._ingest_text(next_seg, "A later confirmed sentence.", True)
        await orch.finish()
        self.assertEqual([text for _, text in orch._history], ["A later confirmed sentence."])

    async def test_timeout_and_direct_stop_retire_pending(self):
        for timeout in (True, False):
            with self.subTest(timeout=timeout):
                entered, cancelled = asyncio.Event(), asyncio.Event()

                class Asr:
                    async def transcribe(self, pcm):
                        entered.set()
                        try:
                            await asyncio.Event().wait()
                        finally:
                            cancelled.set()

                orch, sink, seg = self.make(Asr())
                await orch._ingest_text(seg, "A usable pending sentence.", False)
                orch._kick_asr(seg, "final")
                await asyncio.wait_for(entered.wait(), 2)
                if timeout:
                    await orch.finish(timeout=.02)
                await orch.stop()
                self.assertTrue(cancelled.is_set())
                self.assertEqual(orch._utterances.flush(), [])
                self.assertEqual(orch._history, [])
                self.assertFalse(any(e.get("state") == "final" for e in sink.events))
                reasons = [e["reason"] for e in sink.events if e["type"] == "utterance_boundary"]
                self.assertIn("finish_unconfirmed" if timeout else "stop_unconfirmed", reasons)

    async def test_stopped_draft_translation_cannot_resume(self):
        entered = asyncio.Event()

        class Mt:
            async def stream(self, text, history=None, draft=False):
                entered.set()
                await asyncio.Event().wait()
                yield "late"

        orch, sink, seg = self.make(mt=Mt())
        await orch.start()
        await orch._ingest_text(seg, "A pending sentence.", False)
        await asyncio.wait_for(entered.wait(), 2)
        await orch.stop()
        await asyncio.wait_for(orch._mt_q.join(), 1)
        self.assertEqual(orch._utterances.flush(), [])
        self.assertEqual(orch._history, [])

    async def test_confirmed_raw_survives_translation_failure_or_timeout(self):
        for timeout in (True, False):
            with self.subTest(timeout=timeout):
                entered = asyncio.Event()

                class Mt:
                    async def stream(self, text, history=None, draft=False):
                        entered.set()
                        if timeout:
                            await asyncio.Event().wait()
                        raise RuntimeError("MT unavailable")
                        yield ""

                orch, sink, seg = self.make(mt=Mt())
                await orch.start()
                await orch._ingest_text(seg, "A confirmed source sentence.", True)
                await asyncio.wait_for(entered.wait(), 2)
                await orch.finish(timeout=.02 if timeout else 2)
                await orch.stop()
                caps = [e for e in sink.events if e["type"] == "caption"]
                self.assertEqual(caps[-1]["state"], "final")
                self.assertEqual(caps[-1]["raw"], "A confirmed source sentence.")
                reasons = [e["reason"] for e in sink.events if e["type"] == "utterance_boundary"]
                self.assertIn("translation_incomplete", reasons)
                self.assertEqual([text for _, text in orch._history], ["A confirmed source sentence."])

    async def test_final_cannot_certify_translation_of_old_source(self):
        for outcome in ("failed", "timeout", "empty", "partial"):
            with self.subTest(outcome=outcome):
                entered = asyncio.Event()

                class Mt:
                    async def stream(self, text, history=None, draft=False):
                        if text == "We should go.":
                            yield "我们应该走。"
                            return
                        entered.set()
                        if outcome == "timeout":
                            await asyncio.Event().wait()
                        if outcome == "partial":
                            yield "留下"
                        if outcome in ("failed", "partial"):
                            raise RuntimeError("MT unavailable")

                orch, sink, seg = self.make(mt=Mt())
                await orch.start()
                await orch._ingest_text(seg, "We should go.", False)
                await asyncio.wait_for(orch._mt_q.join(), 1)
                await orch._ingest_text(seg, "We should stay.", True)
                await asyncio.wait_for(entered.wait(), 1)
                await orch.finish(timeout=.02 if outcome == "timeout" else 1)
                await orch.stop()
                finals = [e for e in sink.events if e.get("state") == "final"]
                expected = "留下" if outcome == "partial" else ""
                self.assertEqual(finals[-1]["raw"], "We should stay.")
                self.assertEqual(finals[-1]["translation"], expected)
                store = CaptionStore()
                for event in sink.events:
                    store.apply(event)
                self.assertEqual(store.current()["translation"], expected)
                with tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / "events.jsonl"
                    path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in sink.events), encoding="utf-8")
                    args = cli._parser().parse_args(["export", str(path), "--format", "txt"])
                    output = io.StringIO()
                    with redirect_stdout(output):
                        self.assertEqual(cli._cmd_export(args), 0)
                    self.assertEqual(output.getvalue().strip(), expected or "We should stay.")


class AssemblerCloseTests(unittest.TestCase):
    def test_nonfinal_flush_retires_without_reusing_id(self):
        a = UtteranceAssembler()
        draft = a.ingest(WindowDecode("w1", "file", 0, 3, "Pending guess."))[-1]
        updates = a.flush("file", final=False, reason="asr_unconfirmed")
        self.assertEqual(len(updates), 1)
        self.assertEqual((updates[0].id, updates[0].text, updates[0].final, updates[0].reason),
                         (draft.id, draft.text, False, "asr_unconfirmed"))
        self.assertEqual(a.flush(), [])
        newer = a.ingest(WindowDecode("w2", "file", 4, 7, "Fresh sentence.", True, "silence"))
        self.assertNotEqual(newer[-1].id, draft.id)
        self.assertEqual(newer[-1].text, "Fresh sentence.")


if __name__ == "__main__":
    unittest.main()
