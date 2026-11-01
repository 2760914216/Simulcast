from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any


class ConfigError(RuntimeError):
    pass


@dataclass
class AudioCfg:
    source: str = "loopback"
    input: str | None = None
    device: str | None = None
    frame_ms: int = 20
    target_rate: int = 16000
    speed: float = 1.0


@dataclass
class SegmentCfg:
    silence_ms: int = 450
    min_speech_ms: int = 300
    max_segment_s: float = 10.0
    preroll_ms: int = 200
    draft_interval_ms: int = 900
    vad_floor: float = 3e-4
    vad_ratio: float = 3.0
    # Legacy fields accepted for existing config files; assembler ignores them.
    carry_ttl_s: float = 20.0
    carry_min_chars: int = 12
    carry_max_chars: int = 200
    carry_max_words: int = 8
    semantic_heuristics: bool = False
    window_overlap_s: float = 1.5
    utterance_max_s: float = 30.0
    utterance_max_chars: int = 1000
    audio_buffer_s: float = 30.0


@dataclass
class AsrProvider:
    type: str = "openai-chat"
    base_url: str = ""
    model: str = ""
    api_key_env: str = ""
    max_tokens: int = 512
    timeout_s: float = 30.0
    temperature: float = 0.0
    repeat_penalty: float = 1.0
    presence_penalty: float = 0.0
    frequency_penalty: float = 0.0


@dataclass
class MtProvider:
    type: str = "openai-chat"
    base_url: str = ""
    model: str = ""
    api_key_env: str = ""
    max_tokens: int = 256
    draft_max_tokens: int = 96
    timeout_s: float = 60.0
    temperature: float = 0.0
    thinking: bool = False


@dataclass
class AsrCfg:
    provider: str = ""
    language: str | None = None
    guard: bool = True
    guard_diversity: float = 0.30
    guard_max_chars_per_s: float = 30.0
    speech_evidence_mode: str = "off"
    speech_evidence_model: str = ""
    repair_max_s: float = 10.0
    providers: dict[str, AsrProvider] = field(default_factory=dict)
    active: AsrProvider | None = None


@dataclass
class MtCfg:
    provider: str = ""
    target_lang: str = "简体中文"
    context_sentences: int = 2
    context_max_chars: int = 2000
    emit_interval_ms: int = 180
    enabled: bool = True
    providers: dict[str, MtProvider] = field(default_factory=dict)
    active: MtProvider | None = None


@dataclass
class HubCfg:
    host: str = "127.0.0.1"
    port: int = 8765
    snapshot_size: int = 120


@dataclass
class AppCfg:
    profile: str = "local"
    audio: AudioCfg = field(default_factory=AudioCfg)
    segment: SegmentCfg = field(default_factory=SegmentCfg)
    asr: AsrCfg = field(default_factory=AsrCfg)
    mt: MtCfg = field(default_factory=MtCfg)
    hub: HubCfg = field(default_factory=HubCfg)


def _apply(obj: Any, data: dict[str, Any]) -> Any:
    for f in fields(obj):
        if f.name in data:
            setattr(obj, f.name, data[f.name])
    return obj


def _build(cls, data: dict[str, Any]):
    return _apply(cls(), data)


def _merge(dst: dict, src: dict) -> dict:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _merge(dst[key], value)
        else:
            dst[key] = value
    return dst


def _load_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def load(path: str | Path | None = None, profile: str | None = None) -> AppCfg:
    base = Path(path) if path else Path(__file__).resolve().parent.parent / "config" / "default.toml"
    data = _load_toml(base)
    name = profile or data.get("app", {}).get("profile") or "local"
    _merge(data, _load_toml(base.parent / "profiles" / f"{name}.toml"))
    cfg = _from_dict(data, name)
    if cfg.asr.speech_evidence_model:
        model_path = Path(cfg.asr.speech_evidence_model).expanduser()
        if not model_path.is_absolute():
            model_path = base.parent / model_path
        cfg.asr.speech_evidence_model = str(model_path.resolve())
    return cfg


def _validate_windows(cfg: AppCfg) -> None:
    for name in ("max_segment_s", "window_overlap_s", "utterance_max_s", "audio_buffer_s"):
        try:
            value = float(getattr(cfg.segment, name))
        except (ValueError, TypeError) as exc:
            raise ConfigError(f"segment.{name} must be a finite number") from exc
        if not math.isfinite(value) or value < 0 or (name != "window_overlap_s" and value == 0):
            raise ConfigError(f"segment.{name} has an invalid duration")
        setattr(cfg.segment, name, value)
    if cfg.audio.frame_ms <= 0 or cfg.audio.target_rate <= 0:
        raise ConfigError("audio.frame_ms and target_rate must be positive")
    if cfg.segment.window_overlap_s + cfg.audio.frame_ms / 1000 > cfg.segment.max_segment_s:
        raise ConfigError("segment.window_overlap_s must leave room for a new audio frame")
    if cfg.segment.audio_buffer_s < cfg.segment.max_segment_s:
        raise ConfigError("segment.audio_buffer_s must cover at least one full ASR window")
    count = cfg.segment.utterance_max_chars
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise ConfigError("segment.utterance_max_chars must be a positive integer")


def _from_dict(data: dict[str, Any], profile: str) -> AppCfg:
    cfg = AppCfg(profile=profile)
    _apply(cfg.audio, data.get("audio", {}))
    _apply(cfg.segment, data.get("segment", {}))
    _apply(cfg.hub, data.get("hub", {}))
    _validate_windows(cfg)

    asr_data = data.get("asr", {})
    _apply(cfg.asr, asr_data)
    if cfg.asr.speech_evidence_mode not in ("off", "observe"):
        raise ConfigError("asr.speech_evidence_mode must be off or observe")
    if cfg.asr.speech_evidence_mode == "observe" and not cfg.asr.speech_evidence_model:
        raise ConfigError("asr.speech_evidence_model is required for observe mode")
    try:
        cfg.asr.repair_max_s = float(cfg.asr.repair_max_s)
    except (ValueError, TypeError) as exc:
        raise ConfigError("asr.repair_max_s must be a finite positive duration") from exc
    if not math.isfinite(cfg.asr.repair_max_s) or cfg.asr.repair_max_s <= 0:
        raise ConfigError("asr.repair_max_s must be a finite positive duration")
    cfg.asr.provider = asr_data.get("provider", "")
    cfg.asr.language = asr_data.get("language") or None
    cfg.asr.providers = {}
    for name, item in asr_data.get("providers", {}).items():
        cfg.asr.providers[name] = _build(AsrProvider, item)
    cfg.asr.active = cfg.asr.providers.get(cfg.asr.provider)
    if cfg.asr.active is None:
        raise ConfigError(f"unknown asr provider: {cfg.asr.provider!r}")
    if not cfg.asr.active.base_url or not cfg.asr.active.model:
        raise ConfigError(f"asr provider {cfg.asr.provider!r} needs base_url and model")

    mt_data = data.get("mt", {})
    cfg.mt.provider = mt_data.get("provider", "")
    cfg.mt.target_lang = mt_data.get("target_lang", cfg.mt.target_lang)
    cfg.mt.context_sentences = int(mt_data.get("context_sentences", cfg.mt.context_sentences))
    cfg.mt.context_max_chars = int(mt_data.get("context_max_chars", cfg.mt.context_max_chars))
    if cfg.mt.context_sentences < 0 or cfg.mt.context_max_chars < 0:
        raise ConfigError("mt.context_sentences and mt.context_max_chars must not be negative")
    cfg.mt.emit_interval_ms = int(mt_data.get("emit_interval_ms", cfg.mt.emit_interval_ms))
    cfg.mt.enabled = bool(mt_data.get("enabled", cfg.mt.enabled))
    for name, item in mt_data.get("providers", {}).items():
        cfg.mt.providers[name] = _build(MtProvider, item)
    cfg.mt.active = cfg.mt.providers.get(cfg.mt.provider)
    if cfg.mt.active is None:
        raise ConfigError(f"unknown mt provider: {cfg.mt.provider!r}")
    if not cfg.mt.active.base_url or not cfg.mt.active.model:
        raise ConfigError(f"mt provider {cfg.mt.provider!r} needs base_url and model")
    return cfg
