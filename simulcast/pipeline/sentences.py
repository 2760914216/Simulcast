from __future__ import annotations

import re

TERMINATORS = "。！？!?…"

_SPLIT = re.compile(r"(?<=[。！？!?…])|(?<=\.)\s+")
_END = re.compile(r"[。！？!?…]|\.$")


def split_sentences(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    parts = [p.strip() for p in _SPLIT.split(text) if p and p.strip()]
    return parts or [text]


def is_terminated(sentence: str) -> bool:
    s = (sentence or "").rstrip("\"'”’)」』 ")
    return bool(s) and bool(_END.search(s))
