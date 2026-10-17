from __future__ import annotations

import json
import logging
import os
from typing import AsyncIterator

import httpx

from ..config import ConfigError, MtProvider
from .prompts import system_prompt

log = logging.getLogger(__name__)


class OpenAiMt:
    def __init__(self, provider: MtProvider, target_lang: str, context_sentences: int):
        self.provider = provider
        self.base_url = provider.base_url.rstrip("/")
        self.context_sentences = max(0, context_sentences)
        self._system = system_prompt(target_lang)
        headers = {}
        if provider.api_key_env:
            key = os.environ.get(provider.api_key_env)
            if not key:
                raise ConfigError(f"environment variable {provider.api_key_env} is not set")
            headers["Authorization"] = f"Bearer {key}"
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(provider.timeout_s, connect=5.0),
            headers=headers,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def healthy(self) -> bool:
        try:
            response = await self._client.get(self.base_url + "/health", timeout=3.0)
            return response.status_code < 500
        except Exception:
            return False

    def _messages(self, text: str, context: list[tuple[str, str]] | None) -> list[dict]:
        msgs: list[dict] = [{"role": "system", "content": self._system}]
        for src, tgt in (context or [])[-self.context_sentences :]:
            if src and tgt:
                msgs.append({"role": "user", "content": src})
                msgs.append({"role": "assistant", "content": tgt})
        msgs.append({"role": "user", "content": text})
        return msgs

    async def stream(
        self,
        text: str,
        context: list[tuple[str, str]] | None = None,
        draft: bool = False,
    ) -> AsyncIterator[str]:
        if not text.strip():
            return
        payload = {
            "model": self.provider.model,
            "messages": self._messages(text, context),
            "temperature": self.provider.temperature,
            "top_p": 1.0,
            "max_tokens": self.provider.draft_max_tokens if draft else self.provider.max_tokens,
            "stream": True,
            "cache_prompt": True,
            "chat_template_kwargs": {"enable_thinking": bool(self.provider.thinking)},
        }
        async with self._client.stream(
            "POST", self.base_url + "/v1/chat/completions", json=payload
        ) as response:
            if response.status_code != 200:
                body = await response.aread()
                raise RuntimeError(f"mt http {response.status_code}: {body[:200]!r}")
            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                piece = delta.get("content")
                if piece:
                    yield piece
