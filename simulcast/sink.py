from __future__ import annotations

import json
import sys
from typing import Any, Protocol


class Sink(Protocol):
    async def emit(self, msg: dict[str, Any]) -> None: ...

    async def close(self) -> None: ...


class NullSink:
    async def emit(self, msg: dict[str, Any]) -> None:
        return

    async def close(self) -> None:
        return


class JsonlSink:
    def __init__(self, path: str | None = None, stream=None, truncate: bool = False):
        self._own = path is not None
        self._fh = open(path, "w" if truncate else "a", encoding="utf-8") if path else (stream or sys.stdout)

    def _write(self, msg: dict[str, Any]) -> None:
        self._fh.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self._fh.flush()

    async def emit(self, msg: dict[str, Any]) -> None:
        self._write(msg)

    async def close(self) -> None:
        if self._own:
            self._fh.close()


class TextSink:
    def __init__(self, stream=None):
        self._out = stream or sys.stdout
        self._last: dict[str, str] = {}

    async def emit(self, msg: dict[str, Any]) -> None:
        if msg.get("type") != "caption":
            if msg.get("type") == "session":
                self._out.write(f"-- {msg.get('state')} {msg.get('detail', '')}\n")
                self._out.flush()
            return
        key = msg["id"]
        text = msg.get("translation") or ""
        fingerprint = f"{msg.get('raw','')}\u0000{text}\u0000{msg.get('state')}"
        if self._last.get(key) == fingerprint:
            return
        self._last[key] = fingerprint
        if len(self._last) > 400:
            for k in list(self._last)[:100]:
                self._last.pop(k, None)
        tag = "F" if msg.get("state") == "final" else "d"
        t0 = float(msg.get("t0") or 0.0)
        raw = (msg.get("raw") or "").replace("\n", " ")
        tr = text.replace("\n", " ")
        self._out.write(f"[{t0:7.2f}] {tag} {msg.get('id')}  {raw}  =>  {tr}\n")
        self._out.flush()

    async def close(self) -> None:
        return


class HubSink:
    def __init__(self, hub):
        self.hub = hub

    async def emit(self, msg: dict[str, Any]) -> None:
        self.hub.publish(msg)

    async def close(self) -> None:
        return


class MultiSink:
    def __init__(self, *sinks: Sink):
        self.sinks = [s for s in sinks if s is not None]

    async def emit(self, msg: dict[str, Any]) -> None:
        for s in self.sinks:
            try:
                await s.emit(msg)
            except Exception:
                pass

    async def close(self) -> None:
        for s in self.sinks:
            try:
                await s.close()
            except Exception:
                pass
