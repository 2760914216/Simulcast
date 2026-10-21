from __future__ import annotations

import asyncio
import logging
import queue
import sys
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QColor, QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from ..config import ConfigError, load
from ..single import acquire
from ..store import load_render
from ..wsclient import mirror
from .supervisor import FAILED, RUNNING, STOPPED, EngineSpec, Supervisor

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
INSTANCE = "simulcast-app"

FONT_CHOICES = ((26, "26 px"), (30, "30 px"), (34, "34 px"), (42, "42 px"), (52, "52 px"))
WIDTH_CHOICES = ((0.60, "60%"), (0.74, "75%"), (0.90, "90%"))
HISTORY_CHOICES = ((0, "关闭"), (1, "1 句"), (2, "2 句"))
HANDLE_CHOICES = ((10, "小"), (14, "中"), (20, "大"))
SOURCE_CHOICES = (("loopback", "内录"), ("mic", "麦克风"), ("both", "双路"))

STATE_LABELS = {RUNNING: "运行中", STOPPED: "已停止", FAILED: "失败"}


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


class Shell:
    def __init__(self, cfg, source: str):
        self.cfg = cfg
        self.source = source
        self.outbox: queue.Queue = queue.Queue()
        self._choice: dict[str, str | None] = {"loopback": None, "mic": None}
        self._devices: list[dict] | None = None
        self.app: QApplication | None = None
        self.tray: QSystemTrayIcon | None = None
        self.render_menu: QMenu | None = None
        self.audio_menu: QMenu | None = None
        self.timer: QTimer | None = None
        self.guard = None
        self.sup = Supervisor(
            ROOT,
            [
                EngineSpec("pipeline", "识别引擎", self._pipeline_args(), "pipeline.log"),
                EngineSpec("render", "字幕引擎", self._render_args(), "render.log"),
            ],
        )

    def _ws_url(self) -> str:
        return f"ws://{self.cfg.hub.host}:{self.cfg.hub.port}"

    def _pipeline_args(self) -> list[str]:
        return [
            "-m",
            "simulcast.cli",
            "run",
            "--live",
            self.source,
            "--ws",
            "--port",
            str(self.cfg.hub.port),
        ]

    def _render_args(self) -> list[str]:
        return ["-m", "simulcast.render.qt", "--ws", self._ws_url()]

    def run(self, app: QApplication) -> int:
        self.app = app
        app.setApplicationName("simulcast")
        app.setQuitOnLastWindowClosed(False)

        self.tray = QSystemTrayIcon(make_icon(), app)
        self.tray.setToolTip("Simulcast")

        menu = QMenu()
        self.render_menu = menu.addMenu("字幕")
        self.render_menu.aboutToShow.connect(lambda: self._rebuild_render(self.render_menu))
        self.audio_menu = menu.addMenu("识别")
        self.audio_menu.aboutToShow.connect(lambda: self._rebuild_audio(self.audio_menu))
        menu.addSeparator()
        logs = QAction("打开日志目录", menu)
        logs.triggered.connect(self._open_logs)
        menu.addAction(logs)
        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self.shutdown)
        menu.addAction(quit_action)

        self._rebuild_render(self.render_menu)
        self._rebuild_audio(self.audio_menu)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._activated)
        self.tray.show()

        self.sup.start_all()
        self._refresh_tooltip()
        threading.Thread(target=self._pump, daemon=True).start()

        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)

        try:
            return app.exec()
        finally:
            self.sup.stop_all()

    def notify_running(self) -> None:
        if self.tray is not None:
            self.tray.showMessage("Simulcast", "已经在运行")

    def shutdown(self) -> None:
        log.info("shutting down")
        self.sup.stop_all()
        if self.tray is not None:
            self.tray.hide()
        if self.app is not None:
            self.app.quit()

    def _activated(self, reason) -> None:
        if self.tray is None or reason != QSystemTrayIcon.Trigger:
            return
        self.tray.showMessage("Simulcast", self._status_text())

    def _status_text(self) -> str:
        return "\n".join(
            f"{engine.spec.title}: {STATE_LABELS.get(engine.state, engine.state)}"
            for engine in self.sup.engines.values()
        )

    def _refresh_tooltip(self) -> None:
        if self.tray is not None:
            self.tray.setToolTip("Simulcast\n" + self._status_text())

    def _tick(self) -> None:
        if self.sup.tick():
            self._refresh_tooltip()

    def _status_item(self, menu: QMenu, key: str) -> None:
        engine = self.sup[key]
        action = QAction(f"{engine.spec.title}: {STATE_LABELS.get(engine.state, engine.state)}", menu)
        action.setEnabled(False)
        menu.addAction(action)

    def _radio(self, menu: QMenu, title: str, choices, current, apply) -> QMenu:
        sub = menu.addMenu(title)
        group = QActionGroup(sub)
        group.setExclusive(True)
        for value, label in choices:
            action = QAction(label, sub, checkable=True)
            try:
                action.setChecked(abs(float(value) - float(current)) < 1e-6)
            except (TypeError, ValueError):
                action.setChecked(value == current)
            action.triggered.connect(lambda _=False, v=value: apply(v))
            group.addAction(action)
            sub.addAction(action)
        return sub

    def _rebuild_render(self, menu: QMenu) -> None:
        menu.clear()
        self._status_item(menu, "render")
        menu.addSeparator()
        style = load_render()
        self._radio(menu, "字号", FONT_CHOICES, style.get("font_size", 34), lambda v: self._send_style("font_size", v))
        self._radio(menu, "宽度", WIDTH_CHOICES, style.get("width_ratio", 0.74), lambda v: self._send_style("width_ratio", v))
        self._radio(
            menu, "历史句数", HISTORY_CHOICES, style.get("history_lines", 1), lambda v: self._send_style("history_lines", v)
        )
        self._radio(
            menu, "拖拽点", HANDLE_CHOICES, style.get("handle_size", 14), lambda v: self._send_style("handle_size", v)
        )
        source = QAction("显示原文", menu, checkable=True)
        source.setChecked(bool(style.get("show_source", True)))
        source.triggered.connect(lambda checked: self._send_style("show_source", checked))
        menu.addAction(source)
        menu.addSeparator()
        restart = QAction("重启字幕引擎", menu)
        restart.triggered.connect(lambda: self.sup["render"].restart())
        menu.addAction(restart)
        stop = QAction("停止字幕引擎", menu)
        stop.triggered.connect(lambda: self.sup["render"].stop())
        menu.addAction(stop)

    def _device_items(self) -> list[dict]:
        if self._devices is None:
            try:
                from ..audio.devices import list_inputs

                self._devices = list_inputs()
            except Exception:
                self._devices = []
        return self._devices

    def _device_menu(self, menu: QMenu, kind: str, title: str) -> None:
        sub = menu.addMenu(title)
        group = QActionGroup(sub)
        group.setExclusive(True)
        auto = QAction("跟随系统默认", sub, checkable=True)
        auto.setChecked(self._choice[kind] is None)
        auto.triggered.connect(lambda _=False, k=kind: self._send_device(k, None))
        group.addAction(auto)
        sub.addAction(auto)
        sub.addSeparator()
        for item in self._device_items():
            if bool(item.get("loopback")) != (kind == "loopback"):
                continue
            action = QAction(str(item["name"]), sub, checkable=True)
            action.setChecked(self._choice[kind] == str(item["id"]))
            action.triggered.connect(
                lambda _=False, k=kind, d=str(item["id"]): self._send_device(k, d)
            )
            group.addAction(action)
            sub.addAction(action)

    def _rebuild_audio(self, menu: QMenu) -> None:
        menu.clear()
        self._status_item(menu, "pipeline")
        menu.addSeparator()
        self._radio(menu, "采集源", SOURCE_CHOICES, self.source, self._set_source)
        self._devices = None
        self._device_menu(menu, "loopback", "系统声音")
        self._device_menu(menu, "mic", "麦克风")
        menu.addSeparator()
        restart = QAction("重启识别引擎", menu)
        restart.triggered.connect(lambda: self.sup["pipeline"].restart())
        menu.addAction(restart)
        stop = QAction("停止识别引擎", menu)
        stop.triggered.connect(lambda: self.sup["pipeline"].stop())
        menu.addAction(stop)

    def _set_source(self, source: str) -> None:
        if source == self.source:
            return
        log.info("capture source -> %s", source)
        self.source = source
        engine = self.sup["pipeline"]
        engine.set_args(self._pipeline_args())
        engine.stop()
        engine.start()

    def _send_style(self, key: str, value) -> None:
        self.outbox.put(
            {
                "v": 1,
                "type": "control",
                "target": "render",
                "action": "render.style",
                "style": {key: value},
            }
        )

    def _send_device(self, kind: str, device: str | None) -> None:
        self._choice[kind] = device
        self.outbox.put(
            {
                "v": 1,
                "type": "control",
                "target": "pipeline",
                "action": "capture.device",
                "kind": kind,
                "device": device,
            }
        )

    def _open_logs(self) -> None:
        path = ROOT / "logs"
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _pump(self) -> None:
        try:
            asyncio.run(mirror(self._ws_url(), lambda msg: None, outbox=self.outbox))
        except Exception:
            log.exception("ws pump died")


def main(argv: list[str] | None = None) -> int:
    log_dir = ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.FileHandler(log_dir / "app.log", encoding="utf-8")]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        handlers=handlers,
    )

    try:
        cfg = load()
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    app = QApplication.instance() or QApplication(argv or sys.argv)
    shell = Shell(cfg, cfg.audio.source or "loopback")
    guard = acquire(INSTANCE, lambda message: shell.notify_running())
    if guard is None:
        return 0
    shell.guard = guard
    return shell.run(app)
