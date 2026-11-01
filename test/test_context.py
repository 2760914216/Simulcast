import asyncio
import pathlib
import tempfile

from simulcast.config import AppCfg, ConfigError, load
from simulcast.mt.prompts import HISTORY_HEAD, HISTORY_LABEL, TARGET_LABEL, user_message
from simulcast.pipeline.orchestrator import Orchestrator, Segment, _history_window


class Collect:
    def __init__(self):
        self.msgs = []

    async def emit(self, msg):
        self.msgs.append(msg)

    async def close(self):
        pass


class RecordingMt:
    """Records what the pipeline asks for and can hold the first call open."""

    def __init__(self, block_first=False, fail_first=False):
        self.calls = []
        self.block_first = block_first
        self.fail_first = fail_first
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def stream(self, text, history=None, draft=False):
        self.calls.append({"text": text, "history": list(history or ()), "draft": draft})
        if self.fail_first and len(self.calls) == 1:
            raise RuntimeError("mt down")
        if self.block_first and len(self.calls) == 1:
            self.entered.set()
            await self.release.wait()
        yield "译"
        yield text[:2]

    async def healthy(self):
        return True


def make(block_first=False, fail_first=False):
    cfg = AppCfg()
    sink = Collect()
    mt = RecordingMt(block_first=block_first, fail_first=fail_first)
    orch = Orchestrator(cfg, None, mt, sink)
    parent = Segment(id="b-1", source="file", t0=0.0, t_end=20.0)
    return orch, mt, parent


async def commit(orch, parent, text):
    await orch._emit_child(parent, text, 0.0, 1.0)


async def drain(orch):
    await asyncio.wait_for(orch._mt_q.join(), 5.0)


def test_window_limits():
    win = _history_window
    assert win(["a", "b", "c"], 2, 100) == ["b", "c"]
    assert win(["a", "b", "c"], 3, 0) == []
    assert win(["a", "b", "c"], 0, 100) == []
    assert win([], 2, 100) == []
    assert win(["aaaaa", "bbbbb", "ccccc"], 5, 12) == ["bbbbb", "ccccc"]
    assert win(["aaaaa", "bbbbb"], 5, 4) == [], "the newest original alone blows the budget"
    assert win(["a", "bbbbb", "ccccc"], 5, 6) == ["ccccc"], "newest first, never skip it"
    print("ok window limits", win(["aaaaa", "bbbbb", "ccccc"], 5, 11))


def test_config_limits():
    template = """
[asr]
provider = "a"
[asr.providers.a]
base_url = "http://asr"
model = "asr"
[mt]
provider = "m"
context_sentences = {s}
context_max_chars = {c}
[mt.providers.m]
base_url = "http://mt"
model = "m"
"""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        path = root / "default.toml"
        path.write_text(template.format(s=2, c=1500), encoding="utf-8")
        cfg = load(path)
        assert (cfg.mt.context_sentences, cfg.mt.context_max_chars) == (2, 1500)

        path.write_text(template.format(s=0, c=0), encoding="utf-8")
        cfg = load(path)
        assert (cfg.mt.context_sentences, cfg.mt.context_max_chars) == (0, 0)

        for s, c in ((2, -1), (-1, 2000)):
            path.write_text(template.format(s=s, c=c), encoding="utf-8")
            try:
                load(path)
            except ConfigError:
                pass
            else:
                raise AssertionError(f"expected ConfigError for {s}/{c}")
    print("ok config limits")


def test_message_layout():
    assert user_message("Hello.") == "Hello."
    assert user_message("Hello.", []) == "Hello."
    assert user_message("Hello.", ["  "]) == "Hello."
    body = user_message("Hello.", ["One.", "Two."])
    assert HISTORY_HEAD in body and HISTORY_LABEL in body and TARGET_LABEL in body
    assert body.index("One.") < body.index("Two.") < body.index("Hello.")
    assert "【前文】" in body and "【本次原文】" in body

    from simulcast.config import MtProvider
    from simulcast.mt.openai_chat import OpenAiMt

    mt = OpenAiMt(MtProvider(base_url="http://x", model="m"), "简体中文")
    plan = mt._messages("Hello.", ["One.", "Two."])
    assert [m["role"] for m in plan] == ["system", "user"], plan
    plain = mt._messages("Hello.", None)
    assert plain[-1]["content"] == "Hello."
    asyncio.run(mt.close())
    print("ok message layout")


async def scenario_sequence():
    orch, mt, parent = make()
    await orch.start()
    for text in ("Alpha beta.", "Gamma delta.", "Epsilon zeta."):
        await commit(orch, parent, text)
    await drain(orch)
    await orch.stop()
    return mt.calls


async def scenario_multi_sentence():
    orch, mt, parent = make()
    await orch.start()
    await orch._commit_plain(parent, "Alpha beta. Gamma delta. Epsilon zeta.", 0, 42)
    await drain(orch)
    await orch.stop()
    return mt.calls


async def scenario_deferred():
    orch, mt, parent = make(block_first=True)
    await orch.start()
    await commit(orch, parent, "Alpha beta.")
    await asyncio.wait_for(mt.entered.wait(), 2.0)
    frozen = [dict(call) for call in mt.calls]
    await commit(orch, parent, "Gamma delta.")
    await commit(orch, parent, "Epsilon zeta.")
    mt.release.set()
    await drain(orch)
    await orch.stop()
    return frozen, mt.calls


async def scenario_mt_down():
    orch, mt, parent = make(fail_first=True)
    await orch.start()
    await commit(orch, parent, "Alpha beta.")
    await drain(orch)
    await commit(orch, parent, "Gamma delta.")
    await drain(orch)
    await orch.stop()
    return mt.calls


async def scenario_budget(sentences, chars):
    orch, mt, parent = make()
    orch.cfg.mt.context_sentences = sentences
    orch.cfg.mt.context_max_chars = chars
    await orch.start()
    for text in ("aaaaa.", "bbbbb.", "ccccc."):
        await commit(orch, parent, text)
    await drain(orch)
    await orch.stop()
    return [call["history"] for call in mt.calls]


async def scenario_draft():
    orch, mt, parent = make()
    await orch.start()
    await commit(orch, parent, "Alpha beta.")
    await drain(orch)
    tail = Segment(id="b-2", source="file", t0=5.0, t_end=6.0, raw="Tail of a burst.")
    orch._enqueue_mt(tail, final=False)
    await drain(orch)
    await orch.stop()
    return mt.calls[-1]


def main():
    test_window_limits()
    test_config_limits()
    test_message_layout()

    calls = asyncio.run(scenario_sequence())
    print("sequence:", [(c["text"], c["history"]) for c in calls])
    assert [c["text"] for c in calls] == ["Alpha beta.", "Gamma delta.", "Epsilon zeta."]
    assert [c["history"] for c in calls] == [[], ["Alpha beta."], ["Alpha beta.", "Gamma delta."]]
    assert all(not c["draft"] for c in calls)

    calls = asyncio.run(scenario_multi_sentence())
    print("multi   :", [(c["text"], c["history"]) for c in calls])
    assert len(calls) == 3 and calls[0]["history"] == []
    assert calls[1]["history"] == ["Alpha beta."], "later sentence in the same decode must see the earlier one"

    frozen, calls = asyncio.run(scenario_deferred())
    print("deferred:", [(c["text"], c["history"]) for c in calls])
    assert frozen[0]["history"] == []
    assert calls[0]["history"] == [], "a delayed request must not pick up later originals"
    assert calls[-1]["history"] == ["Alpha beta.", "Gamma delta."]

    calls = asyncio.run(scenario_mt_down())
    print("mt down :", [(c["text"], c["history"]) for c in calls])
    assert calls[-1]["text"] == "Gamma delta."
    assert calls[-1]["history"] == ["Alpha beta."], "a failed translation must not drop the original"

    assert asyncio.run(scenario_budget(0, 2000)) == [[], [], []], "0 sentences disables history"
    assert asyncio.run(scenario_budget(2, 0)) == [[], [], []], "0 chars disables history"
    assert asyncio.run(scenario_budget(5, 12)) == [[], ["aaaaa."], ["aaaaa.", "bbbbb."]]
    assert asyncio.run(scenario_budget(5, 11)) == [[], ["aaaaa."], ["bbbbb."]], "the older one no longer fits"
    assert asyncio.run(scenario_budget(5, 5)) == [[], [], []], "an overlong original ends the window"

    draft = asyncio.run(scenario_draft())
    print("draft   :", draft["text"], draft["history"], draft["draft"])
    assert draft["draft"] and draft["history"] == ["Alpha beta."]

    print("OK")


main()
