from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from . import theme
from .textblock import TextBlock

THROTTLE_MS = 180
GRACE_MS = 1200


def _lcp(a: str, b: str) -> int:
    count = 0
    for left, right in zip(a, b):
        if left != right:
            break
        count += 1
    return count


class SubtitleWindow(QWidget):
    def __init__(
        self,
        font_size: int = 34,
        width_ratio: float = 0.74,
        show_source: bool = True,
        anchor_x: float = 0.5,
        bottom_margin: int = 92,
        history_lines: int = 1,
    ):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        self._font_size = font_size
        self._width_ratio = width_ratio
        self._show_source = show_source
        self._connected = False
        self._caption: dict | None = None
        self._displayed: dict | None = None
        self._pending: dict | None = None
        self._rendered_text = ""
        self._rendered_id: str | None = None
        self._anchor_x = anchor_x
        self._bottom_margin = bottom_margin
        self._history_lines = max(0, min(2, history_lines))
        self._history: list[dict] = []
        self._source_line = False

        self._throttle = QTimer(self)
        self._throttle.setSingleShot(True)
        self._throttle.setInterval(THROTTLE_MS)
        self._throttle.timeout.connect(self._on_throttle)

        self._grace = QTimer(self)
        self._grace.setSingleShot(True)
        self._grace.setInterval(GRACE_MS)
        self._grace.timeout.connect(self._on_grace)

        self.history_views: list[TextBlock] = []
        for _ in range(2):
            view = TextBlock(self)
            view.set_single_line(True)
            view.set_colors(theme.SOURCE, theme.SOURCE)
            view.hide()
            self.history_views.append(view)

        self.source_label = QLabel("", self)
        self.source_label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self.source_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.text_view = TextBlock(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.PAD_L, theme.PAD_T, theme.PAD_R, theme.PAD_B)
        layout.setSpacing(8)
        for view in self.history_views:
            layout.addWidget(view)
        layout.addWidget(self.source_label)
        layout.addWidget(self.text_view)

        self._apply_fonts()
        self.show_placeholder()

    def _font(self, px: int, weight=QFont.Normal) -> QFont:
        font = QFont()
        font.setFamilies(theme.FONT_STACK)
        font.setPixelSize(px)
        font.setWeight(weight)
        return font

    def _apply_fonts(self) -> None:
        source_font = self._font(15)
        source_font.setLetterSpacing(QFont.AbsoluteSpacing, 0.4)
        self.source_label.setFont(source_font)
        self.source_label.setStyleSheet(f"color: {theme.SOURCE};")
        self.text_view.set_font(self._font(self._font_size, QFont.Medium))
        history_font = self._font(max(15, int(self._font_size * 0.62)))
        for view in self.history_views:
            view.set_font(history_font)

    def apply_style(
        self,
        font_size: int | None = None,
        width_ratio: float | None = None,
        show_source: bool | None = None,
        history_lines: int | None = None,
    ) -> None:
        if font_size is not None:
            self._font_size = font_size
        if width_ratio is not None:
            self._width_ratio = width_ratio
        if show_source is not None:
            self._show_source = show_source
        if history_lines is not None:
            self._history_lines = max(0, min(2, history_lines))
            self._history = self._history[-self._history_lines :] if self._history_lines else []
        self._apply_fonts()
        if self._caption is not None:
            self._render(self._caption)
        else:
            self.show_placeholder()

    def set_connected(self, connected: bool) -> None:
        if connected != self._connected:
            self._connected = connected
            self.update()

    def show_placeholder(self) -> None:
        self._grace.stop()
        self._throttle.stop()
        self._caption = None
        self._displayed = None
        self._pending = None
        self._rendered_text = ""
        self._rendered_id = None
        self._history = []
        self._source_line = False
        self.source_label.hide()
        self.text_view.set_font(self._font(max(16, int(self._font_size * 0.55))))
        self.text_view.set_colors(theme.PLACEHOLDER, theme.PLACEHOLDER)
        self.text_view.set_text("等待字幕…", len("等待字幕…"))
        self._relayout()

    def set_caption(self, cap: dict) -> None:
        self._pending = cap
        translated = (cap.get("translation") or "").strip()
        final = cap.get("state") == "final"

        if not translated and self._displayed is not None:
            if not self._grace.isActive():
                self._grace.start()
            return
        if self._skip(cap):
            return
        if final or not self._throttle.isActive():
            self._grace.stop()
            self._render(cap)
            self._throttle.start()

    def _skip(self, cap: dict) -> bool:
        displayed = self._displayed
        if displayed is None or cap.get("id") != displayed.get("id"):
            return False
        if cap.get("state") == "final":
            return False
        new = cap.get("translation") or ""
        old = displayed.get("translation") or ""
        if new == old:
            return True
        return new.startswith(old) and 0 <= len(new) - len(old) < 2

    def _on_throttle(self) -> None:
        cap = self._pending
        if cap is None:
            return
        if not (cap.get("translation") or "").strip() and self._displayed is not None:
            if not self._grace.isActive():
                self._grace.start()
            return
        if self._skip(cap):
            return
        self._render(cap)
        self._throttle.start()

    def _on_grace(self) -> None:
        cap = self._pending
        if cap is None:
            return
        self._render(cap)
        self._throttle.start()

    @staticmethod
    def _same(a: str, b: str) -> bool:
        return " ".join((a or "").split()) == " ".join((b or "").split())

    def _promote(self, cap: dict) -> None:
        text = (cap.get("translation") or "").strip()
        if not text or self._history_lines <= 0:
            return
        if self._history and self._same(self._history[-1]["translation"], text):
            self._history[-1] = {"id": cap.get("id"), "translation": text}
            return
        self._history.append({"id": cap.get("id"), "translation": text})
        self._history = self._history[-2:]

    @property
    def history_id(self) -> str | None:
        return self._history[-1]["id"] if self._history else None

    def update_history(self, cap: dict) -> None:
        cid = cap.get("id")
        text = (cap.get("translation") or "").strip()
        if not cid or not text:
            return
        changed = False
        for item in self._history:
            if item.get("id") == cid and item.get("translation") != text:
                item["translation"] = text
                changed = True
        if changed:
            self._relayout()

    def _render(self, cap: dict) -> None:
        if self._displayed is not None and cap.get("id") != self._displayed.get("id"):
            self._promote(self._displayed)
        self._caption = cap
        self._displayed = cap
        raw = (cap.get("raw") or "").strip()
        translated = (cap.get("translation") or "").strip()
        final = cap.get("state") == "final"
        color = theme.TEXT_FINAL if final else theme.TEXT_DRAFT

        text = translated or raw or "等待字幕…"
        cid = cap.get("id")
        if final:
            stable = len(text)
        elif self._rendered_id == cid and self._rendered_text:
            stable = _lcp(self._rendered_text, text)
        else:
            stable = len(text)
        self._rendered_text = text
        self._rendered_id = cid

        self.text_view.set_font(self._font(self._font_size, QFont.Medium))
        self.text_view.set_colors(color, theme.TEXT_TAIL)
        self.text_view.set_text(text, stable)

        self.source_label.setText(raw)
        self._source_line = bool(translated and self._show_source and raw)
        self.source_label.setVisible(self._source_line)
        self._relayout()

    def set_anchor(self, anchor_x: float, bottom_margin: int) -> None:
        self._anchor_x = max(0.0, min(1.0, anchor_x))
        self._bottom_margin = max(0, bottom_margin)
        self._relayout()

    def _window_screen(self):
        screen = QGuiApplication.screenAt(self.frameGeometry().center())
        return screen or self._screen()

    def sync_anchor(self) -> None:
        """Adopt the current geometry as the anchor (used while dragging)."""
        area = self._window_screen().availableGeometry()
        slack = max(1, area.width() - self.width())
        self._anchor_x = max(0.0, min(1.0, (self.x() - area.x()) / slack))
        self._bottom_margin = max(0, area.y() + area.height() - (self.y() + self.height()))

    def position_state(self) -> tuple[float, int]:
        self.sync_anchor()
        return self._anchor_x, self._bottom_margin

    def _screen(self):
        screen = QGuiApplication.screenAt(QCursor.pos())
        if screen is None:
            screen = QApplication.primaryScreen()
        return screen

    def _relayout(self) -> None:
        area = self._screen().availableGeometry()
        width = int(area.width() * self._width_ratio)
        width = max(560, min(width, area.width() - 96))
        inner = width - theme.PAD_L - theme.PAD_R

        self.text_view.setFixedWidth(inner)
        text_height = self.text_view.height_for_width(inner)
        self.text_view.setFixedHeight(text_height)

        if self._source_line:
            self.source_label.setFixedWidth(inner)
            self.source_label.setFixedHeight(self.source_label.fontMetrics().height())

        current_text = " ".join(((self._displayed or {}).get("translation") or "").split())
        candidates = [h for h in self._history if not self._same(h["translation"], current_text)]
        show_n = min(self._history_lines, len(candidates))
        max_height = int(area.height() * 0.85)
        while True:
            shown = candidates[-show_n:] if show_n else []
            for index, view in enumerate(self.history_views):
                if index < len(shown):
                    text = shown[index]["translation"]
                    view.set_text(text, len(text))
                    view.setFixedWidth(inner)
                    view.setFixedHeight(view.height_for_width(inner))
                    view.setVisible(True)
                else:
                    view.setVisible(False)
            self.layout().activate()
            height = self.layout().minimumSize().height()
            if height <= max_height or show_n == 0:
                break
            show_n -= 1

        self.setFixedSize(width, height)
        self.layout().activate()

        slack = max(0, area.width() - width)
        x = area.x() + int(round(slack * self._anchor_x))
        max_margin = max(0, area.height() - height)
        margin = min(max_margin, self._bottom_margin)
        y = area.y() + area.height() - height - margin
        self.move(x, y)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        path = QPainterPath()
        path.addRoundedRect(rect, theme.RADIUS, theme.RADIUS)
        painter.fillPath(path, QColor(*theme.BG))
        painter.setPen(QPen(QColor(*theme.BORDER), 1.0))
        painter.drawPath(path)

        if self._source_line:
            center_y = self.source_label.geometry().center().y() + 1
        else:
            center_y = self.height() // 2
        center = QPointF(27.0, float(center_y))
        painter.setPen(Qt.NoPen)
        if self._connected:
            painter.setBrush(QColor(*theme.DOT_HALO))
            painter.drawEllipse(center, 8.0, 8.0)
            painter.setBrush(QColor(*theme.DOT_ON))
        else:
            painter.setBrush(QColor(*theme.DOT_OFF))
        painter.drawEllipse(center, 4.0, 4.0)
