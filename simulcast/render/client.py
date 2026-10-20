from __future__ import annotations

import asyncio
import json
import logging
import queue
from typing import Any, Callable

from websockets.asyncio.client import connect

log = logging.getLogger(__name__)


async def _send_loop(ws, outbox: queue.Queue) -> None:
    while True:
        try:
            msg = outbox.get_nowait()
        except queue.Empty:
            await asyncio.sleep(0.05)
            continue
        try:
            await ws.send(json.dumps(msg, ensure_ascii=False))
        except Exception as exc:
            log.warning("ws send failed: %s", exc)
            return


async def mirror(
    url: str,
    on_msg: Callable[[dict], None],
    stop: asyncio.Event | None = None,
    on_status: Callable[[bool], None] | None = None,
    outbox: queue.Queue | None = None,
) -> None:
    while stop is None or not stop.is_set():
        try:
            async with connect(url, max_size=None, open_timeout=5) as ws:
                log.info("connected to %s", url)
                if on_status:
                    on_status(True)
                sender = asyncio.create_task(_send_loop(ws, outbox)) if outbox is not None else None
                try:
                    async for raw in ws:
                        try:
                            on_msg(json.loads(raw))
                        except json.JSONDecodeError:
                            continue
                finally:
                    if sender is not None:
                        sender.cancel()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("ws error: %s", exc)
        finally:
            if on_status:
                on_status(False)
        await asyncio.sleep(1.5)
