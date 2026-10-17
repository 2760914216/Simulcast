from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable

from websockets.asyncio.client import connect

log = logging.getLogger(__name__)


async def mirror(
    url: str,
    on_msg: Callable[[dict], None],
    stop: asyncio.Event | None = None,
    on_status: Callable[[bool], None] | None = None,
) -> None:
    while stop is None or not stop.is_set():
        try:
            async with connect(url, max_size=None, open_timeout=5) as ws:
                log.info("connected to %s", url)
                if on_status:
                    on_status(True)
                async for raw in ws:
                    try:
                        on_msg(json.loads(raw))
                    except json.JSONDecodeError:
                        continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("ws error: %s", exc)
        finally:
            if on_status:
                on_status(False)
        await asyncio.sleep(1.5)
