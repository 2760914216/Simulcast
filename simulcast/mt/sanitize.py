from __future__ import annotations

import re

_PREFIX = re.compile(r"^\s*(译文|翻译|中文|Translation|Translated)\s*[:：]\s*", re.I)
_CONTROL = re.compile(r"<\|[^|]*\|>")
_QUOTES = " \"'“”「」"


def clean(text: str, partial: bool = False) -> str:
    if not text:
        return ""
    t = _CONTROL.sub("", text)
    t = t.replace("\r", "")
    if partial:
        t = t.replace("\n", " ")
    else:
        t = " ".join(line.strip() for line in t.splitlines() if line.strip())
    t = _PREFIX.sub("", t)
    t = t.strip()
    if not partial:
        t = t.strip(_QUOTES)
    return t.strip()
