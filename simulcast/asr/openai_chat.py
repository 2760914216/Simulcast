from __future__ import annotations

import base64
import logging
import os
import re
import time

import httpx
import numpy as np

from ..audio.wavio import to_wav_bytes
from ..config import AsrProvider, ConfigError
from .base import AsrResult, AsrTimeout

log = logging.getLogger(__name__)

_LANG = re.compile(r"language\s+([A-Za-z][A-Za-z\s]*?)\s*(?:<asr_text>|$)", re.I)
_TEXT = re.compile(r"<asr_text>(.*)$", re.S)
_CONTROL = re.compile(r"<\|[^|]*\|>")


def parse_output(content: str) -> tuple[str, str | None]:
    content = _CONTROL.sub("", content or "").strip()
    lang = None
    match = _LANG.search(content)
    if match:
        lang = match.group(1).strip()
    body = _TEXT.search(content)
    text = body.group(1) if body else content
    return text.strip(), lang


class OpenAiAsr:
    """llama.cpp-style OpenAI chat completions with audio input."""

    def __init__(self, provider: AsrProvider):
        self.provider = provider
        self.base_url = provider.base_url.rstrip("/")
        headers = {}
        if provider.api_key_env:
            key = os.environ.get(provider.api_key_env)
            if not key:
                raise ConfigError(f"environment variable {provider.api_key_env} is not set")
            headers["Authorization"] = f"Bearer {key}"
        self._client = httpx.AsyncClient(timeout=provider.timeout_s, headers=headers)

    async def close(self) -> None:
        await self._client.aclose()

    async def healthy(self) -> bool:
        try:
            response = await self._client.get(self.base_url + "/health", timeout=3.0)
            return response.status_code < 500
        except Exception:
            return False

    async def transcribe(self, pcm: np.ndarray, sample_rate: int = 16000) -> AsrResult:
        if pcm.size == 0:
            return AsrResult(text="", latency_ms=0)
        data = base64.b64encode(to_wav_bytes(pcm, sample_rate)).decode("ascii")
        payload = {
            "model": self.provider.model,
            "temperature": self.provider.temperature,
            "max_tokens": self.provider.max_tokens,
            "stream": False,
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "input_audio", "input_audio": {"data": data, "format": "wav"}}],
                }
            ],
        }
        started = time.monotonic()
        try:
            response = await self._client.post(self.base_url + "/v1/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise AsrTimeout(f"asr timeout after {self.provider.timeout_s}s") from exc
        elapsed = int((time.monotonic() - started) * 1000)
        if response.status_code != 200:
            raise RuntimeError(f"asr http {response.status_code}: {response.text[:200]}")
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        text, lang = parse_output(content)
        return AsrResult(text=text, language=lang, latency_ms=elapsed, raw=content)
