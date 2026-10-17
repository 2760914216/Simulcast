from __future__ import annotations

import argparse
import asyncio
import json
import os
import queue
import sys
import threading
import tomllib
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import theme
from .client import mirror
from .handle import DragHandle
from .model import CaptionStore
from .overlay import SubtitleWindow

STATUS = "__status__"

FONT_CHOICES = (26, 30, 34, 42, 52)
WIDTH_CHOICES = ((0.60, "60%"), (0.74, "75%"), (0.90, "90%"))

DEMO = [
    ("Welcome to Business Matters.", "欢迎来到《商业事务》。"),
    (
        "Today, we have invited career development consultant Lucy Williams to talk about how to get ahead at work.",
        "今天，我们邀请了职业发展顾问露西·威廉姆斯，来谈谈如何在工作中脱颖而出。",
    ),
    ("Many of us never seem to get promoted. Why is that?", "我们中许多人似乎永远都得不到晋升。这是为什么呢？"),
]


ROOT = Path(__file__).resolve().parents[2]


def load_render_config() -> dict:
    path = ROOT / "config" / "render.toml"
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def settings_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "simulcast" / "render.json"


def load_settings() -> dict:
    try:
        return json.loads(settings_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(data: dict) -> None:
    try:
        path = settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def make_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(255, 255, 255))
    painter.drawEllipse(12, 12, 40, 40)
    painter.setBrush(QColor(28, 28, 30))
    painter.drawEllipse(26, 26, 12, 12)
    painter.end()
    return QIcon(pixmap)


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
        self.tray: QSystemTrayIcon | None = None
        self._demo_step = 0

    def run(self) -> int:
        app = QApplication.instance() or QApplication(sys.argv)
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
        self._build_tray(app)

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

    def _build_tray(self, app: QApplication) -> None:
        self.tray = QSystemTrayIcon(make_icon(), app)
        self.tray.setToolTip("Simulcast 字幕")

        menu = QMenu()
        font_menu = menu.addMenu("字号")
        font_group = QActionGroup(font_menu)
        font_group.setExclusive(True)
        for size in FONT_CHOICES:
            action = QAction(f"{size} px", font_menu, checkable=True)
            action.setChecked(size == self.font_size)
            action.triggered.connect(lambda _=False, value=size: self._set_font(value))
            font_group.addAction(action)
            font_menu.addAction(action)

        width_menu = menu.addMenu("宽度")
        width_group = QActionGroup(width_menu)
        width_group.setExclusive(True)
        for ratio, label in WIDTH_CHOICES:
            action = QAction(label, width_menu, checkable=True)
            action.setChecked(abs(ratio - self.width_ratio) < 0.01)
            action.triggered.connect(lambda _=False, value=ratio: self._set_width(value))
            width_group.addAction(action)
            width_menu.addAction(action)

        history_menu = menu.addMenu("历史句数")
        history_group = QActionGroup(history_menu)
        history_group.setExclusive(True)
        for count, label in ((0, "关闭"), (1, "1 句"), (2, "2 句")):
            action = QAction(label, history_menu, checkable=True)
            action.setChecked(count == self.history_lines)
            action.triggered.connect(lambda _=False, value=count: self._set_history_lines(value))
            history_group.addAction(action)
            history_menu.addAction(action)

        handle_menu = menu.addMenu("拖拽点")
        handle_group = QActionGroup(handle_menu)
        handle_group.setExclusive(True)
        for size, label in ((10, "小"), (14, "中"), (20, "大")):
            action = QAction(label, handle_menu, checkable=True)
            action.setChecked(size == self.handle_size)
            action.triggered.connect(lambda _=False, value=size: self._set_handle_size(value))
            handle_group.addAction(action)
            handle_menu.addAction(action)

        menu.addSeparator()
        source_action = QAction("显示原文", menu, checkable=True)
        source_action.setChecked(self.show_source)
        source_action.triggered.connect(lambda checked: self._set_source(checked))
        menu.addAction(source_action)

        menu.addSeparator()
        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(app.quit)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            if self.win is not None:
                visible = not self.win.isVisible()
                self.win.setVisible(visible)
                if self.handle is not None:
                    self.handle.setVisible(visible)
                    if visible:
                        self.handle.follow()

    def _sync_handle(self) -> None:
        if self.handle is not None and self.win is not None and self.win.isVisible():
            self.handle.follow()

    def _set_font(self, size: int) -> None:
        self.font_size = size
        if self.win is not None:
            self.win.apply_style(font_size=size)
        self._sync_handle()
        self._persist()

    def _set_width(self, ratio: float) -> None:
        self.width_ratio = ratio
        if self.win is not None:
            self.win.apply_style(width_ratio=ratio)
        self._sync_handle()
        self._persist()

    def _set_source(self, enabled: bool) -> None:
        self.show_source = enabled
        if self.win is not None:
            self.win.apply_style(show_source=enabled)
        self._sync_handle()
        self._persist()

    def _set_history_lines(self, count: int) -> None:
        self.history_lines = max(0, min(2, count))
        if self.win is not None:
            self.win.apply_style(history_lines=self.history_lines)
        self._sync_handle()
        self._persist()

    def _set_handle_size(self, size: int) -> None:
        self.handle_size = size
        if self.handle is not None:
            self.handle.set_size(size)
        self._persist()

    def _persist(self) -> None:
        if self.win is not None:
            self.anchor_x, self.bottom_margin = self.win.position_state()
            self.win.set_anchor(self.anchor_x, self.bottom_margin)
        save_settings(
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
        elif step == 0:
            caption["translation"] = ""
        self._demo_step += 1
        self.win.set_caption(caption)
        self._sync_handle()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    stored = load_settings()
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

    font_size = args.font_size or int(pick("font_size", 34))
    width_ratio = args.width_ratio or float(pick("width_ratio", 0.74))
    show_source = bool(pick("show_source", True)) and not args.no_source
    bottom_margin = args.bottom_margin or int(pick("bottom_margin", 92))
    anchor_x = float(pick("anchor_x", 0.5))
    handle_size = int(pick("handle_size", 14))
    history_lines = int(pick("history_lines", 1))

    app = RendererApp(
        ws=args.ws,
        font_size=font_size,
        width_ratio=width_ratio,
        show_source=show_source,
        demo=args.demo,
        bottom_margin=bottom_margin,
        anchor_x=anchor_x,
        handle_size=handle_size,
        history_lines=history_lines,
    )
    return app.run()


if __name__ == "__main__":
    raise SystemExit(main())
