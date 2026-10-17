from __future__ import annotations

from ..config import ConfigError, MtCfg


def build_mt(cfg: MtCfg):
    provider = cfg.active
    if provider is None:
        raise ConfigError("no active mt provider")
    if provider.type == "openai-chat":
        from .openai_chat import OpenAiMt

        return OpenAiMt(provider, cfg.target_lang, cfg.context_sentences)
    raise ConfigError(f"unsupported mt provider type: {provider.type}")
