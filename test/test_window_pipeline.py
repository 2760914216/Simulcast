"""Logical captions must survive physical ASR window changes."""
import asyncio

import numpy as np

from simulcast.asr.base import AsrResult
from simulcast.config import AppCfg
from simulcast.audio.buffer import AudioBuffer
from simulcast.pipeline.orchestrator import Orchestrator, Segment


class Collect:
    def __init__(self):
        self.msgs = []

    async def emit(self, msg):
        self.msgs.append(msg)


def captions(sink, final=None):
    return [m for m in sink.msgs if m.get("type") == "caption"
            and (final is None or (m["state"] == "final") == final)]


async def boundary():
    cfg, sink = AppCfg(), Collect()
    cfg.mt.enabled = False
    orch = Orchestrator(cfg, None, None, sink)
    first = Segment("w1", "file", 0, t1=10, t_end=10, end_reason="hard")
    text = "I do not think the proposal you mentioned yesterday."
    await orch._ingest_text(first, text, final=True)
    assert not captions(sink, True), "a long tail was finalized at the audio cap"
    draft = captions(sink, False)[-1]
    second = Segment("w2", "file", 8.5, t1=15, t_end=15, end_reason="silence")
    second.overlap_s = 1.5
    await orch._ingest_text(second, "mentioned yesterday will work.", final=True)
    finals = captions(sink, True)
    assert len(finals) == 1, finals
    assert finals[0]["id"] == draft["id"], "a logical sentence changed identity at a window boundary"
    assert finals[0]["raw"] == "I do not think the proposal you mentioned yesterday will work.", finals
    await orch.finish()
    assert len(captions(sink, True)) == 1, "finish duplicated a finalized sentence"


async def source_order():
    cfg, sink = AppCfg(), Collect()
    cfg.mt.enabled = False

    class GatedAsr:
        def __init__(self):
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.calls = 0

        async def transcribe(self, audio):
            self.calls += 1
            if self.calls == 1:
                self.entered.set()
                await self.release.wait()
                return AsrResult("A long phrase continues.", "English", 1)
            return AsrResult("continues here.", "English", 1)

    asr = GatedAsr()
    orch = Orchestrator(cfg, asr, None, sink)
    a = Segment("w1", "file", 0, t1=1, t_end=1, end_reason="hard", audio=np.full(16000, .1, dtype=np.float32))
    b = Segment("w2", "file", .5, t1=1.5, t_end=1.5, end_reason="silence", audio=np.full(16000, .1, dtype=np.float32))
    b.overlap_s = .5
    orch._kick_asr(a, "final")
    await asyncio.wait_for(asr.entered.wait(), 2)
    orch._kick_asr(b, "final")
    try:
        await asyncio.sleep(.02)
        assert asr.calls == 1, "same-source windows decoded out of order"
    finally:
        asr.release.set()
        await orch.finish()
        await orch.stop()
    assert captions(sink, True)[-1]["raw"] == "A long phrase continues here."


async def observation_does_not_gate():
    from simulcast.audio.speech_evidence import SpeechEvidence

    class Evidence:
        def measure(self, pcm, sample_rate=16000):
            return SpeechEvidence(0.001, 0.0, len(pcm) / sample_rate)

    class Asr:
        async def transcribe(self, pcm):
            return AsrResult("Quiet but real speech.", "English", 1)

    cfg, sink = AppCfg(), Collect()
    cfg.mt.enabled = False
    cfg.asr.speech_evidence_mode = "observe"
    orch = Orchestrator(cfg, Asr(), None, sink, speech_evidence=Evidence())
    seg = Segment("w1", "file", 0, t1=1, t_end=1, end_reason="silence", audio=np.full(16000, .1, dtype=np.float32))
    orch._kick_asr(seg, "final")
    await orch.finish()
    await orch.stop()
    records = [m for m in sink.msgs if m.get("type") == "asr_observation"]
    assert records and records[0]["text"] == "Quiet but real speech."
    assert sink.msgs.index(records[0]) < next(i for i,m in enumerate(sink.msgs) if m.get("type") == "caption")
    assert captions(sink, True)[-1]["raw"] == "Quiet but real speech."
    assert orch._history[-1][1] == "Quiet but real speech."


async def request_metadata_is_frozen():
    from simulcast.audio.speech_evidence import SpeechEvidence

    class Evidence:
        def measure(self, pcm, sample_rate):
            return SpeechEvidence(.8, .8, len(pcm) / sample_rate)

    class Asr:
        def __init__(self):
            self.entered, self.release = asyncio.Event(), asyncio.Event()
            self.sizes = []

        async def transcribe(self, pcm):
            self.sizes.append(len(pcm))
            if len(self.sizes) == 1:
                self.entered.set()
                await self.release.wait()
            return AsrResult("A sentence keeps growing.", "English", 1)

    cfg, sink, asr = AppCfg(), Collect(), Asr()
    cfg.mt.enabled = False
    orch = Orchestrator(cfg, asr, None, sink, speech_evidence=Evidence())
    seg = Segment("w1", "file", 0, t_end=1, audio=np.full(16000, .1, dtype=np.float32))
    orch._kick_asr(seg, "draft")
    await asyncio.wait_for(asr.entered.wait(), 2)
    seg.audio = np.full(32000, .1, dtype=np.float32)
    seg.t_end = seg.t1 = 2
    seg.end_reason = "silence"
    orch._kick_asr(seg, "final")
    orch._kick_asr(seg, "draft")
    asr.release.set()
    await orch.finish()
    records = [m for m in sink.msgs if m.get("type") == "asr_observation"]
    assert asr.sizes == [16000, 32000], asr.sizes
    assert [(m["t1"], m["decode_kind"]) for m in records] == [(1, "draft"), (2, "final")], records
    assert captions(sink, True)[-1]["t1"] == 2


async def bounded_repair():
    class RepairAsr:
        def __init__(self):
            self.sizes = []

        async def transcribe(self, pcm):
            self.sizes.append(len(pcm))
            return AsrResult("The entire long sentence has been decoded together.", "English", 1)

    for cap, expected_calls in ((30, [16 * 16000]), (10, [])):
        cfg, sink, asr = AppCfg(), Collect(), RepairAsr()
        cfg.mt.enabled = False
        cfg.asr.repair_max_s = cap
        orch = Orchestrator(cfg, asr, None, sink)
        audio = AudioBuffer(16000, 30)
        audio.append(16, np.full(16 * 16000, .1, dtype=np.float32))
        orch._audio["file"] = audio
        first = Segment("w1", "file", 0, t1=10, t_end=10, end_reason="hard")
        await orch._ingest_text(first, "The entire long sentence has a different beginning.", True)
        pending_id = captions(sink, False)[-1]["id"]
        second = Segment("w2", "file", 8.5, t1=16, t_end=16,
                         end_reason="silence", overlap_s=1.5)
        await orch._ingest_text(second, "No reliable matching words at this boundary.", True)
        await orch.finish()
        assert asr.sizes == expected_calls, (cap, asr.sizes)
        finals = captions(sink, True)
        assert len(finals) == 1 and finals[0]["id"] == pending_id, finals
        repairs = [m for m in sink.msgs if m.get("type") == "asr_repair"]
        if cap == 30:
            assert [(m["t0"], m["t1"]) for m in repairs] == [(0, 16)]
            assert finals[0]["raw"] == "The entire long sentence has been decoded together."
        else:
            assert not repairs
            assert any(m.get("reason") == "alignment_degraded" for m in sink.msgs)


async def eof_at_exact_window_cap():
    class Asr:
        def __init__(self):
            self.calls = 0

        async def transcribe(self, pcm):
            self.calls += 1
            return AsrResult("A continuous single sentence.", "English", 1)

    async def frames():
        for index in range(500):
            yield (index + 1) * .02, np.full(320, .1, dtype=np.float32)

    cfg, sink, asr = AppCfg(), Collect(), Asr()
    cfg.mt.enabled = False
    cfg.segment.draft_interval_ms = 20000
    orch = Orchestrator(cfg, asr, None, sink)
    await orch.run(frames(), "file")
    await orch.finish()
    await orch.stop()
    assert asr.calls == 1, "an overlap-only EOF window was sent to ASR"
    assert [m["raw"] for m in captions(sink, True)] == ["A continuous single sentence."]


async def main():
    failures = []
    for check in (boundary, source_order, observation_does_not_gate,
                  request_metadata_is_frozen, bounded_repair, eof_at_exact_window_cap):
        try:
            await check()
        except (AssertionError, TypeError) as exc:
            failures.append(f"{check.__name__}: {exc}")
    assert not failures, "\n".join(failures)
    print("ordered windows, stable IDs and observation integration OK")


asyncio.run(main())
