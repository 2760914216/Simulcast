import asyncio

from websockets.asyncio.client import connect

from simulcast.config import HubCfg
from simulcast.hub import Hub
from simulcast.protocol import encode


async def main() -> None:
    cfg = HubCfg(host="127.0.0.1", port=8799)
    hub = Hub(cfg)
    seen = []
    hub.on_control = seen.append
    await hub.start()
    try:
        async with connect("ws://127.0.0.1:8799", open_timeout=3) as ws:
            await ws.recv()
            await ws.send(encode({"v": 1, "type": "control", "action": "capture.device", "kind": "loopback", "device": "x"}))
            await ws.send(encode({"v": 1, "type": "caption", "id": "c1"}))
            await asyncio.sleep(0.3)
    finally:
        await hub.stop()
    assert len(seen) == 1, seen
    assert seen[0]["device"] == "x", seen
    print("hub control routing    ok")


asyncio.run(main())
