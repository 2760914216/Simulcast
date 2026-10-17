from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget


class DragHandle(QWidget):
    """Small pill that stays interactive; the subtitle window itself is click-through."""

    def __init__(self, target: QWidget, size: int = 14, on_release: Callable[[], None] | None = None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setCursor(Qt.SizeAllCursor)
        self._target = target
        self._on_release = on_release
        self._size = size
        self._hover = False
        self._dragging = False
        self._press_global: QPoint | None = None
        self._last_global: QPoint | None = None
        self.setFixedSize(size, size)
        target.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self._target and event.type() in (QEvent.Move, QEvent.Resize):
            self.follow()
        return super().eventFilter(obj, event)

    def set_size(self, size: int) -> None:
        self._size = size
        self.setFixedSize(size, size)
        self.follow()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
        active = self._hover or self._dragging
        painter.setBrush(QColor(255, 255, 255, 170 if active else 95))
        painter.setPen(QPen(QColor(20, 20, 22, 140 if active else 100), 1.0))
        painter.drawEllipse(rect)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(20, 20, 22, 110))
        dot = max(2.0, self._size * 0.2)
        painter.drawEllipse(QPointF(self.width() / 2, self.height() / 2), dot / 2, dot / 2)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return
        self._dragging = True
        self._press_global = event.globalPosition().toPoint()
        self._last_global = self._press_global
        self.raise_()
        self.update()
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._dragging or self._last_global is None:
            return
        current = event.globalPosition().toPoint()
        delta = current - self._last_global
        self._last_global = current
        if delta.isNull():
            return
        self._target.move(self._clamp(self._target.pos() + delta))
        if hasattr(self._target, "sync_anchor"):
            self._target.sync_anchor()
        self.follow()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if not self._dragging:
            return
        self._dragging = False
        self.update()
        if self._on_release is not None:
            self._on_release()
        event.accept()

    def _screen(self):
        screen = QGuiApplication.screenAt(self._press_global) if self._press_global else None
        return screen or self._target.screen()

    def _clamp(self, pos: QPoint) -> QPoint:
        area = self._screen().availableGeometry()
        width = self._target.width()
        height = self._target.height()
        x = max(area.left(), min(pos.x(), area.right() - width + 1))
        y = max(area.top(), min(pos.y(), area.bottom() - height + 1))
        return QPoint(x, y)

    def follow(self) -> None:
        target = self._target
        area = (QGuiApplication.screenAt(target.frameGeometry().center()) or target.screen()).availableGeometry()
        x = target.x() + target.width() + 6
        y = target.y() + (target.height() - self.height()) // 2
        if x + self.width() > area.right() - 4:
            x = target.x() + target.width() - self.width() - 14
        x = max(area.left(), min(x, area.right() - self.width()))
        y = max(area.top(), min(y, area.bottom() - self.height()))
        self.move(x, y)
        self.raise_()
