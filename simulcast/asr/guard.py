from __future__ import annotations

from dataclasses import dataclass

from ..config import AsrCfg

MIN_PERIOD = 4
MAX_PERIOD = 40
MIN_REPEATS = 3
MIN_SPAN = 20

WINDOW = 80
KEEP_MIN = 4

LANG_MIN_SECONDS = 2.0
RATE_MIN_SECONDS = 1.0

_LANG_ALIAS = {
    "zh": "chinese",
    "cmn": "chinese",
    "chs": "chinese",
    "中文": "chinese",
    "汉语": "chinese",
    "普通话": "chinese",
    "en": "english",
    "eng": "english",
    "英语": "english",
    "英文": "english",
    "ja": "japanese",
    "jp": "japanese",
    "jpn": "japanese",
    "日语": "japanese",
    "日文": "japanese",
    "ko": "korean",
    "kor": "korean",
    "韩语": "korean",
}


@dataclass
class Verdict:
    text: str
    reason: str = ""

    @property
    def drop(self) -> bool:
        return not self.text


def lang_key(value: str | None) -> str:
    if not value:
        return ""
    key = value.strip().lower()
    if key in _LANG_ALIAS:
        return _LANG_ALIAS[key]
    return _LANG_ALIAS.get(key[:2], key)


def _period_start(s: str, period: int) -> int:
    i = len(s) - 1 - period
    while i >= 0 and s[i] == s[i + period]:
        i -= 1
    return i + 1


def _repeat_cut(text: str) -> int | None:
    """Earliest index of an exact self-repetition that runs to the end of the text."""
    cut = None
    for period in range(MIN_PERIOD, min(MAX_PERIOD, len(text) // MIN_REPEATS) + 1):
        start = _period_start(text, period)
        span = len(text) - start
        if span < MIN_REPEATS * period or span < MIN_SPAN:
            continue
        if cut is None or start < cut:
            cut = start
    return cut


def _diversity(text: str) -> float:
    n = len(text)
    if n < WINDOW:
        return 1.0
    grams = {text[i : i + 4] for i in range(n - 3)}
    return len(grams) / (n - 3)


def _diversity_cut(text: str, floor: float) -> int | None:
    """Earliest index whose suffix has too little 4-gram variety to be speech."""
    n = len(text)
    if n < WINDOW * 2:
        return None
    lo = 0
    if _diversity(text) >= floor:
        return None
    hi = n - WINDOW
    while lo < hi:
        mid = (lo + hi) // 2
        if _diversity(text[mid:]) < floor:
            hi = mid
        else:
            lo = mid + 1
    return lo


def judge(text: str, *, lang: str | None, seconds: float, cfg: AsrCfg) -> Verdict:
    text = (text or "").strip()
    if not text or not cfg.guard:
        return Verdict(text)

    expect = lang_key(cfg.language)
    found = lang_key(lang)
    if expect and found and seconds >= LANG_MIN_SECONDS and found != expect:
        return Verdict("", f"language {lang} != {cfg.language}")

    cut = _repeat_cut(text)
    which = "repeat"
    if cut is None:
        cut = _diversity_cut(text, cfg.guard_diversity)
        which = "degenerate"

    if cut is None:
        keep, reason = text, ""
    else:
        keep = text[:cut].rstrip(" ,，、;；:：-—")
        if len(keep) < KEEP_MIN:
            return Verdict("", f"dropped {which} tail")
        reason = f"trimmed {which} tail"

    if seconds >= RATE_MIN_SECONDS and len(keep) / seconds > cfg.guard_max_chars_per_s:
        return Verdict("", f"{len(keep)} chars in {seconds:.1f}s")
    return Verdict(keep, reason)
