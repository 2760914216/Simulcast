from __future__ import annotations

import argparse
import asyncio
import logging
import queue
import sys
import threading
import tomllib
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from ..single import acquire
from ..store import load_render, save_render
from ..wsclient import mirror
from . import theme
from .handle import DragHandle
from .model import CaptionStore
from .overlay import SubtitleWindow

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
INSTANCE = "simulcast-render"
STATUS = "__status__"

DEMO = [
    ("Welcome to Business Matters.", "欢迎来到《商业事务》。"),
    (
        "Today, we have invited career development consultant Lucy Williams to talk about how to get ahead at work.",
        "今天，我们邀请了职业发展顾问露西·威廉姆斯，来谈谈如何在工作中脱颖而出。",
    ),
    ("Many of us never seem to get promoted. Why is that?", "我们中许多人似乎永远都得不到晋升。这是为什么呢？"),
]


def load_render_config() -> dict:
    path = ROOT / "config" / "render.toml"
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


class RendererApp:
    def __init__(
        self,
        ws: str,
        font_size: int,
        width_ratio: float,
        show_source: bool,
        demo: bool,
        bottom_margin: int = 92,
        anchor_x: float = 0.5,
        handle_size: int = 14,
        history_lines: int = 1,
    ):
        self.ws = ws
        self.font_size = font_size
        self.width_ratio = width_ratio
        self.show_source = show_source
        self.demo = demo
        self.bottom_margin = bottom_margin
        self.anchor_x = anchor_x
        self.handle_size = handle_size
        self.history_lines = max(0, min(2, history_lines))
        self.queue: queue.Queue = queue.Queue()
        self.store = CaptionStore()
        self.win: SubtitleWindow | None = None
        self.handle: DragHandle | None = None
        self._demo_step = 0

    def run(self, app: QApplication) -> int:
        app.setApplicationName("simulcast")
        app.setQuitOnLastWindowClosed(False)

        self.win = SubtitleWindow(
            font_size=self.font_size,
            width_ratio=self.width_ratio,
            show_source=self.show_source,
            anchor_x=self.anchor_x,
            bottom_margin=self.bottom_margin,
            history_lines=self.history_lines,
        )
        self.win.set_connected(self.demo)
        self.win.show()

        self.handle = DragHandle(self.win, size=self.handle_size, on_release=self._persist)
        self.handle.show()
        self.handle.follow()

        timer = QTimer()
        timer.timeout.connect(self._drain)
        timer.start(60)

        if self.demo:
            demo_timer = QTimer()
            demo_timer.timeout.connect(self._demo_tick)
            demo_timer.start(900)
        else:
            threading.Thread(target=self._pump, daemon=True).start()

        return app.exec()

    def show_window(self) -> None:
        if self.win is None:
            return
        self.win.setVisible(True)
        if self.handle is not None:
            self.handle.setVisible(True)
            self.handle.follow()

    def _sync_handle(self) -> None:
        if self.handle is not None and self.win is not None and self.win.isVisible():
            self.handle.follow()

    def _apply_control(self, msg: dict) -> None:
        if msg.get("target") != "render" or msg.get("action") != "render.style":
            return
        self._set_style(msg.get("style") or {})

    def _set_style(self, style: dict) -> None:
        if "font_size" in style:
            self.font_size = int(style["font_size"])
        if "width_ratio" in style:
            self.width_ratio = float(style["width_ratio"])
        if "show_source" in style:
            self.show_source = bool(style["show_source"])
        if "history_lines" in style:
            self.history_lines = max(0, min(2, int(style["history_lines"])))
        if "handle_size" in style:
            self.handle_size = int(style["handle_size"])
            if self.handle is not None:
                self.handle.set_size(self.handle_size)
        if self.win is not None:
            self.win.apply_style(
                font_size=self.font_size,
                width_ratio=self.width_ratio,
                show_source=self.show_source,
                history_lines=self.history_lines,
            )
        self._sync_handle()
        self._persist()

    def _persist(self) -> None:
        if self.win is not None:
            self.anchor_x, self.bottom_margin = self.win.position_state()
            self.win.set_anchor(self.anchor_x, self.bottom_margin)
        save_render(
            {
                "font_size": self.font_size,
                "width_ratio": self.width_ratio,
                "show_source": self.show_source,
                "bottom_margin": self.bottom_margin,
                "anchor_x": self.anchor_x,
                "handle_size": self.handle_size,
                "history_lines": self.history_lines,
            }
        )

    def _pump(self) -> None:
        def on_msg(msg: dict) -> None:
            self.queue.put(msg)

        def on_status(connected: bool) -> None:
            self.queue.put((STATUS, connected))

        try:
            asyncio.run(mirror(self.ws, on_msg, on_status=on_status))
        except Exception:
            pass

    def _drain(self) -> None:
        if self.win is None:
            return
        while True:
            try:
                item = self.queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, tuple) and item and item[0] == STATUS:
                self.win.set_connected(bool(item[1]))
                continue
            if item.get("type") == "control":
                self._apply_control(item)
                continue
            caption = self.store.apply(item)
            if caption is not None:
                if item.get("type") == "caption":
                    history_id = self.win.history_id
                    if history_id and item.get("id") == history_id and item.get("id") != caption.get("id"):
                        self.win.update_history(item)
                self.win.set_caption(caption)
                self._sync_handle()

    def _demo_tick(self) -> None:
        if self.win is None:
            return
        index = (self._demo_step // 3) % len(DEMO)
        step = self._demo_step % 3
        raw, translated = DEMO[index]
        caption = {
            "type": "caption",
            "id": f"demo-{index}",
            "t0": float(index * 10),
            "t1": None,
            "raw": raw,
            "translation": "",
            "state": "draft",
            "seq": self._demo_step,
            "sid": "demo",
        }
        if step == 1:
            caption["translation"] = translated[: max(1, len(translated) // 2)]
        elif step == 2:
            caption["translation"] = translated
            caption["state"] = "final"
        self._demo_step += 1
        self.win.set_caption(caption)
        self._sync_handle()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(name)s: %(message)s")

    stored = load_render()
    render_config = load_render_config()
    theme.apply(render_config.get("theme", {}))
    window_defaults = render_config.get("window", {})

    def pick(key, fallback):
        if key in stored:
            return stored[key]
        if key in window_defaults:
            return window_defaults[key]
        return fallback

    parser = argparse.ArgumentParser("simulcast-render-qt")
    parser.add_argument("--ws", default="ws://127.0.0.1:8765")
    parser.add_argument("--font-size", type=int, default=None)
    parser.add_argument("--width-ratio", type=float, default=None)
    parser.add_argument("--no-source", action="store_true")
    parser.add_argument("--bottom-margin", type=int, default=None)
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()

    app = QApplication.instance() or QApplication(sys.argv)
    renderer = RendererApp(
        ws=args.ws,
        font_size=args.font_size or int(pick("font_size", 34)),
        width_ratio=args.width_ratio or float(pick("width_ratio", 0.74)),
        show_source=bool(pick("show_source", True)) and not args.no_source,
        demo=args.demo,
        bottom_margin=args.bottom_margin or int(pick("bottom_margin", 92)),
        anchor_x=float(pick("anchor_x", 0.5)),
        handle_size=int(pick("handle_size", 14)),
        history_lines=int(pick("history_lines", 1)),
    )

    guard = acquire(INSTANCE, lambda message: renderer.show_window())
    if guard is None:
        return 0
    renderer.guard = guard
    return renderer.run(app)


if __name__ == "__main__":
    raise SystemExit(main())
