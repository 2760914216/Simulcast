from __future__ import annotations

import argparse
import asyncio
import sys

from .client import mirror


class Renderer:
    def __init__(self) -> None:
        self.captions: dict[str, dict] = {}
        self.last: dict[str, str] = {}

    def feed(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "snapshot":
            for cap in msg.get("captions", []):
                self.captions[cap["id"]] = cap
            for cap in list(self.captions.values())[-2:]:
                self.print_caption(cap)
            return
        if kind == "caption":
            self.captions[msg["id"]] = msg
            if len(self.captions) > 200:
                for key in list(self.captions)[:100]:
                    self.captions.pop(key, None)
            self.print_caption(msg)
            return
        if kind == "session":
            print(f"-- session {msg.get('state')} {msg.get('detail', '')}", flush=True)

    def print_caption(self, cap: dict) -> None:
        cid = cap["id"]
        text = cap.get("translation") or ""
        if self.last.get(cid) == text:
            return
        self.last[cid] = text
        tag = "F" if cap.get("state") == "final" else "d"
        t0 = float(cap.get("t0") or 0.0)
        raw = (cap.get("raw") or "").replace("\n", " ")
        tr = text.replace("\n", " ")
        print(f"[{t0:7.2f}] {tag} {cid}  {raw}  =>  {tr}", flush=True)


async def _main(ws: str) -> int:
    r = Renderer()
    await mirror(ws, r.feed)
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    p = argparse.ArgumentParser("simulcast-render-cli")
    p.add_argument("--ws", default="ws://127.0.0.1:8765")
    args = p.parse_args()
    try:
        return asyncio.run(_main(args.ws))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
