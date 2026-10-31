import asyncio

import numpy as np

from simulcast.asr.openai_chat import OpenAiAsr, parse_output
from simulcast.config import AsrProvider


class StubResponse:
    status_code = 200
    text = ""

    def json(self):
        return {"choices": [{"message": {"content": "language English<asr_text>hello world"}}]}


class StubClient:
    def __init__(self):
        self.payload = None

    async def post(self, url, json=None):
        self.payload = json
        return StubResponse()

    async def aclose(self):
        pass


def run(provider):
    engine = OpenAiAsr(provider)
    engine._client = StubClient()
    result = asyncio.run(engine.transcribe(np.full(1600, 0.1, dtype=np.float32)))
    return engine._client.payload, result


def provider(**kw):
    base = AsrProvider(type="openai-chat", base_url="http://asr", model="m")
    for key, value in kw.items():
        setattr(base, key, value)
    return base


def test_neutral_payload_has_no_penalties():
    payload, _ = run(provider())
    for key in ("repeat_penalty", "presence_penalty", "frequency_penalty"):
        assert key not in payload, (key, payload)


def test_repeat_penalty_is_sent():
    payload, _ = run(provider(repeat_penalty=1.1))
    assert payload["repeat_penalty"] == 1.1, payload


def test_presence_and_frequency_are_sent():
    payload, _ = run(provider(presence_penalty=0.3, frequency_penalty=0.2))
    assert payload["presence_penalty"] == 0.3, payload
    assert payload["frequency_penalty"] == 0.2, payload


def test_audio_only_payload():
    payload, _ = run(provider())
    content = payload["messages"][0]["content"]
    assert len(content) == 1 and content[0]["type"] == "input_audio", content
    assert payload["stream"] is False and payload["max_tokens"] == 512, payload


def test_output_is_parsed():
    text, lang = parse_output("language Japanese<asr_text>立ちなさい、バルス。")
    assert text == "立ちなさい、バルス。", text
    assert lang == "Japanese", lang
    text, lang = parse_output("language English<asr_text>hello world")
    assert (text, lang) == ("hello world", "English"), (text, lang)


for name, fn in list(globals().items()):
    if name.startswith("test_") and callable(fn):
        fn()
        print("ok", name)
print("OK")
