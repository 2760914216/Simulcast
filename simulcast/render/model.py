from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CaptionStore:
    """Protocol v1 client-side store: caption upserts plus "which one is on screen"."""

    captions: dict[str, dict[str, Any]] = field(default_factory=dict)
    active_id: str | None = None
    sid: str | None = None

    def apply(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        kind = msg.get("type")
        if kind == "snapshot":
            self.captions = {c["id"]: c for c in msg.get("captions", []) if c.get("id")}
            self.active_id = self._newest()
            return self.current()
        if kind != "caption":
            return self.current()

        sid = msg.get("sid")
        if sid and self.sid and sid != self.sid:
            self.captions.clear()
            self.active_id = None
        if sid:
            self.sid = sid

        cid = msg.get("id")
        if not cid:
            return self.current()
        known = cid in self.captions
        previous = self.captions.get(cid)
        if previous is not None:
            new_translation = (msg.get("translation") or "").strip()
            old_translation = (previous.get("translation") or "").strip()
            if not new_translation and old_translation:
                msg = {**msg, "translation": previous["translation"]}
        self.captions[cid] = msg

        if self.active_id is None or cid == self.active_id:
            self.active_id = cid
        elif not known:
            self.active_id = cid
        else:
            current = self.current()
            if current is not None:
                current_t0 = float(current.get("t0") or 0.0)
                if float(msg.get("t0") or 0.0) >= current_t0 - 0.5 and int(msg.get("seq") or 0) >= int(
                    current.get("seq") or 0
                ):
                    self.active_id = cid
        return self.current()

    def _newest(self) -> str | None:
        visible = [c for c in self.captions.values() if (c.get("raw") or c.get("translation"))]
        if not visible:
            return None
        best = max(visible, key=lambda c: (float(c.get("t0") or 0.0), int(c.get("seq") or 0)))
        return best["id"]

    def current(self) -> dict[str, Any] | None:
        if self.active_id is None:
            return None
        return self.captions.get(self.active_id)
