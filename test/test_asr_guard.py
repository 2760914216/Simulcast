import asyncio
from types import SimpleNamespace

import numpy as np

from simulcast.asr.guard import judge, lang_key
from simulcast.config import AppCfg
from simulcast.pipeline.orchestrator import Orchestrator, Segment


def cfg_for(text="", lang=None, seconds=10.0, language=None, **kw):
    cfg = AppCfg().asr
    cfg.language = language
    for key, value in kw.items():
        setattr(cfg, key, value)
    return judge(text, lang=lang, seconds=seconds, cfg=cfg)


def test_normal_speech_untouched():
    text = "Welcome back to the show. Today we are talking about the new release."
    verdict = cfg_for(text, lang="English", seconds=8.0)
    assert verdict.text == text and not verdict.reason, verdict


def test_language_words_normalise():
    assert lang_key("English") == lang_key("en") == lang_key("英语") == "english"
    assert lang_key("Japanese") == lang_key("ja") == lang_key("日语") == "japanese"
    assert lang_key("Chinese") == lang_key("zh") == lang_key("中文") == "chinese"


def test_exact_repeat_trimmed():
    text = "He said he would cook dinner tonight." + " cook it, cook it, cook it, cook it, cook it, cook it, cook it,"
    verdict = cfg_for(text, lang="English", seconds=10.0)
    assert not verdict.drop, verdict
    assert verdict.text.startswith("He said he would cook dinner tonight."), verdict.text
    assert "cook it" not in verdict.text, verdict.text
    assert "repeat" in verdict.reason, verdict.reason


def test_whole_segment_repeat_dropped():
    verdict = cfg_for("伊斯坦布尔伊斯坦布尔伊斯坦布尔伊斯坦布尔伊斯坦布尔伊斯坦布尔", lang="Japanese", seconds=10.0)
    assert verdict.drop, verdict


def test_legit_triple_kept():
    text = "Are you sure about this? No, no, no."
    verdict = cfg_for(text, lang="English", seconds=4.0)
    assert verdict.text == text, verdict.text


def test_degenerate_variety_trimmed():
    parts = ["cook up, ", "cook it, ", "cook the, "]
    junk = "".join(parts[(i * 7) % 3] for i in range(60))
    text = "Love, that is not what we agreed on at all. " + junk
    verdict = cfg_for(text, lang="English", seconds=10.0)
    assert not verdict.drop, verdict
    assert verdict.text.startswith("Love, that is not"), verdict.text
    assert "cook" not in verdict.text, verdict.text


def test_degenerate_only_dropped():
    parts = ["cook up, ", "cook it, ", "cook the, "]
    junk = "".join(parts[(i * 7) % 3] for i in range(60))
    verdict = cfg_for(junk, lang="English", seconds=10.0)
    assert verdict.drop, verdict


def test_language_gate():
    text = "1998 年，他担任了中国驻刚果（金）大使。"
    verdict = cfg_for(text, lang="Chinese", seconds=9.0, language="ja")
    assert verdict.drop and "language" in verdict.reason, verdict
    same = cfg_for("今日はいい天気ですね。", lang="Japanese", seconds=9.0, language="ja")
    assert same.text == "今日はいい天気ですね。", same


def test_language_gate_ignores_short_audio():
    verdict = cfg_for("はい。", lang="Chinese", seconds=1.0, language="ja")
    assert not verdict.drop, verdict


def test_chars_per_second_limit():
    text = "本日は" * 60
    verdict = cfg_for(text, lang="Japanese", seconds=5.0)
    assert verdict.drop, verdict

    fast = "".join(f"{i}番目の文です。" for i in range(40))
    verdict = cfg_for(fast, lang="Japanese", seconds=2.0)
    assert verdict.drop and "chars" in verdict.reason, verdict


def test_guard_can_be_disabled():
    text = "伊斯坦布尔" * 30
    verdict = cfg_for(text, lang="Japanese", seconds=10.0, guard=False)
    assert verdict.text == text, verdict


def test_guard_keeps_clean_draft_prefix():
    text = "立ちなさい、バルス。"
    verdict = cfg_for(text, lang="Japanese", seconds=1.5)
    assert verdict.text == text, verdict


class Collect:
    def __init__(self):
        self.msgs = []

    async def emit(self, msg):
        self.msgs.append(msg)

    async def close(self):
        pass


class FakeAsr:
    def __init__(self, text, language="English"):
        self.text = text
        self.language = language
        self.calls = 0

    async def transcribe(self, audio):
        self.calls += 1
        await asyncio.sleep(0.01)
        return SimpleNamespace(text=self.text, language=self.language, latency_ms=10)

    async def healthy(self):
        return True


async def idle(orch):
    while orch._tasks:
        await asyncio.gather(*list(orch._tasks), return_exceptions=True)


async def test_pipeline_drops_degenerate_decode():
    cfg = AppCfg()
    cfg.mt.enabled = False
    parts = ["cook up, ", "cook it, ", "cook the, "]
    junk = "".join(parts[(i * 7) % 3] for i in range(60))
    asr, sink = FakeAsr(junk), Collect()
    orch = Orchestrator(cfg, asr, None, sink)
    seg = Segment(id="b-1", source="file", t0=0.0, t_end=10.0)
    seg.audio = np.full(160000, 0.1, dtype=np.float32)
    orch._kick_asr(seg, "final")
    await idle(orch)
    assert asr.calls == 1, asr.calls
    caps = [m for m in sink.msgs if m["type"] == "caption"]
    assert not caps, caps
    assert seg.raw == "", seg.raw


async def test_pipeline_keeps_clean_decode():
    cfg = AppCfg()
    cfg.mt.enabled = False
    asr, sink = FakeAsr("Stand up, Barusu."), Collect()
    orch = Orchestrator(cfg, asr, None, sink)
    seg = Segment(id="b-2", source="file", t0=0.0, t_end=2.0)
    seg.audio = np.full(32000, 0.1, dtype=np.float32)
    orch._kick_asr(seg, "final")
    await idle(orch)
    caps = [m for m in sink.msgs if m["type"] == "caption" and m["raw"]]
    assert caps, sink.msgs


for name, fn in list(globals().items()):
    if name.startswith("test_") and callable(fn):
        if asyncio.iscoroutinefunction(fn):
            asyncio.run(fn())
        else:
            fn()
        print("ok", name)
print("OK")
