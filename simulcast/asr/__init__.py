from __future__ import annotations

from ..config import AsrCfg, ConfigError


def build_asr(cfg: AsrCfg):
    provider = cfg.active
    if provider is None:
        raise ConfigError("no active asr provider")
    if provider.type == "openai-chat":
        from .openai_chat import OpenAiAsr

        return OpenAiAsr(provider)
    raise ConfigError(f"unsupported asr provider type: {provider.type}")
