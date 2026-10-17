from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

PROTOCOL_VERSION = 1


def new_session_id() -> str:
    return time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:4]


@dataclass
class Caption:
    id: str
    source: str
    t0: float
    t1: float | None = None
    raw: str = ""
    translation: str = ""
    state: str = "draft"
    lang: str | None = None
    asr_ms: int | None = None
    mt_ms: int | None = None
    seq: int = 0
    sid: str = ""
    updated_at: float = field(default_factory=time.time)

    def to_msg(self) -> dict[str, Any]:
        d = asdict(self)
        d["v"] = PROTOCOL_VERSION
        d["type"] = "caption"
        return d


def snapshot_msg(captions: list[dict[str, Any]]) -> dict[str, Any]:
    return {"v": PROTOCOL_VERSION, "type": "snapshot", "captions": captions}


def session_msg(state: str, sid: str = "", **detail: Any) -> dict[str, Any]:
    msg = {"v": PROTOCOL_VERSION, "type": "session", "state": state, "sid": sid, "ts": time.time()}
    msg.update(detail)
    return msg


def encode(msg: dict[str, Any]) -> str:
    return json.dumps(msg, ensure_ascii=False)


def decode(text: str) -> dict[str, Any]:
    return json.loads(text)
