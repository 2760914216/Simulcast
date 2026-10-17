from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QTextCharFormat, QTextLayout
from PySide6.QtWidgets import QWidget


class TextBlock(QWidget):
    """Two-tone text: committed prefix in full colour, provisional tail dimmed."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ""
        self._stable = 0
        self._font = QFont()
        self._color = QColor("#FFFFFF")
        self._tail = QColor("#8A8A90")
        self._single = False
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    @property
    def text(self) -> str:
        return self._text

    @property
    def stable_len(self) -> int:
        return self._stable

    def set_font(self, font: QFont) -> None:
        self._font = font
        self.update()

    def set_single_line(self, single: bool) -> None:
        self._single = single
        self.update()

    def set_colors(self, color: str, tail: str) -> None:
        self._color = QColor(color)
        self._tail = QColor(tail)
        self.update()

    def set_text(self, text: str, stable_len: int) -> None:
        self._text = text
        self._stable = max(0, min(stable_len, len(text)))
        self.update()

    def _layout(self, width: int):
        layout = QTextLayout(self._text or " ", self._font)
        ranges = []
        if self._stable > 0:
            head = QTextLayout.FormatRange()
            head.start = 0
            head.length = self._stable
            head.format = QTextCharFormat()
            head.format.setForeground(self._color)
            ranges.append(head)
        if self._stable < len(self._text):
            tail = QTextLayout.FormatRange()
            tail.start = self._stable
            tail.length = len(self._text) - self._stable
            tail.format = QTextCharFormat()
            tail.format.setForeground(self._tail)
            ranges.append(tail)
        layout.setFormats(ranges)
        layout.beginLayout()
        lines = []
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            lines.append(line)
        layout.endLayout()
        return layout, lines

    def height_for_width(self, width: int) -> int:
        if self._single:
            return self._font.pixelSize() + 10
        _, lines = self._layout(width)
        height = sum(line.height() for line in lines)
        return max(self._font.pixelSize() + 8, int(height) + 2)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        if self._single:
            painter.setFont(self._font)
            metrics = painter.fontMetrics()
            text = metrics.elidedText(self._text, Qt.ElideRight, self.width())
            painter.setPen(self._color)
            painter.drawText(self.rect(), Qt.AlignHCenter | Qt.AlignVCenter, text)
            painter.end()
            return
        _, lines = self._layout(self.width())
        y = 0.0
        for line in lines:
            x = (self.width() - line.naturalTextWidth()) / 2.0
            line.draw(painter, QPointF(x, y))
            y += line.height()
        painter.end()
