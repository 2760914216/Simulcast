from __future__ import annotations

import asyncio
import logging
from typing import Any

from websockets.asyncio.server import ServerConnection, serve

from .config import HubCfg
from .protocol import encode, snapshot_msg

log = logging.getLogger(__name__)


class Hub:
    """WebSocket fan-out. Protocol v1, JSON per frame, caption upserts by id."""

    def __init__(self, cfg: HubCfg):
        self.cfg = cfg
        self._clients: set[ServerConnection] = set()
        self._captions: dict[str, dict[str, Any]] = {}
        self._server = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._server = await serve(self._handler, self.cfg.host, self.cfg.port, max_queue=256)
        log.info("hub listening on ws://%s:%s", self.cfg.host, self.cfg.port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handler(self, ws: ServerConnection) -> None:
        self._clients.add(ws)
        try:
            caps = list(self._captions.values())[-self.cfg.snapshot_size :]
            await ws.send(encode(snapshot_msg(caps)))
            async for _ in ws:
                pass
        except Exception:
            pass
        finally:
            self._clients.discard(ws)

    def publish(self, msg: dict[str, Any]) -> None:
        if msg.get("type") == "caption" and msg.get("id"):
            self._captions[msg["id"]] = msg
            if len(self._captions) > self.cfg.snapshot_size * 4:
                for key in list(self._captions)[: -self.cfg.snapshot_size]:
                    self._captions.pop(key, None)
        if not self._clients or self._loop is None:
            return
        text = encode(msg)
        for ws in list(self._clients):
            self._loop.create_task(self._safe_send(ws, text))

    async def _safe_send(self, ws: ServerConnection, text: str) -> None:
        try:
            await ws.send(text)
        except Exception:
            self._clients.discard(ws)
