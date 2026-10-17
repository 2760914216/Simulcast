import os
import sys
import tempfile

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from simulcast.render.handle import DragHandle
from simulcast.render.overlay import SubtitleWindow

app = QApplication(sys.argv)
win = SubtitleWindow(font_size=34, width_ratio=0.74)
win.set_connected(True)
win.show()

released = []
handle = DragHandle(win, size=14, on_release=lambda: released.append(True))
handle.show()
handle.follow()


def press(x, y):
    handle.mousePressEvent(
        QMouseEvent(QEvent.MouseButtonPress, QPointF(7, 7), QPointF(x, y), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    )


def move(x, y):
    handle.mouseMoveEvent(
        QMouseEvent(QEvent.MouseMove, QPointF(7, 7), QPointF(x, y), Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
    )


def release(x, y):
    handle.mouseReleaseEvent(
        QMouseEvent(QEvent.MouseButtonRelease, QPointF(7, 7), QPointF(x, y), Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
    )


def caption(cid, raw, translation, state="final"):
    return {"id": cid, "raw": raw, "translation": translation, "state": state, "t0": 0, "seq": 1}


start = win.pos()
start_bottom = start.y() + win.height()

press(200, 200)
move(260, 240)
assert win.x() == start.x() + 60, (win.x(), start.x())
assert win.y() + win.height() == start_bottom + 40

content_mid_drag = caption("mid", "A rather long sentence that will change the card height.", "一句很长的话，会改变卡片高度。")
win.set_caption(content_mid_drag)
print("mid-drag after content:", win.pos(), "bottom", win.y() + win.height())
assert win.x() == start.x() + 60, "content update must not reset x while dragging"
assert win.y() + win.height() == start_bottom + 40, "content update must not reset the anchor while dragging"

move(280, 260)
assert win.x() == start.x() + 80
assert win.y() + win.height() == start_bottom + 60
release(280, 260)
assert released
print("after drag:", win.pos(), "handle:", handle.pos())
assert abs(handle.x() - (win.geometry().right() + 6)) <= 2, (handle.x(), win.geometry().right())

from simulcast.render.qt import RendererApp

os.environ["APPDATA"] = tempfile.mkdtemp()
renderer = RendererApp(ws="", font_size=34, width_ratio=0.74, show_source=True, demo=True)
renderer.win = win
anchor, bottom = win.position_state()
renderer._persist()
win.set_caption(caption("after", "Another sentence.", "另一句。"))
anchor2, bottom2 = win.position_state()
print("anchor after persist+relayout:", round(anchor2, 3), bottom2)
assert abs(anchor2 - anchor) < 0.01 and abs(bottom2 - bottom) <= 2
assert abs(handle.x() - (win.geometry().right() + 6)) <= 2, "handle must follow a relayout"

win.set_caption(caption("a", "Hello everyone.", "大家好。"))
assert win.text_view.text == "大家好。"
win.set_caption(caption("b", "Welcome to the meeting.", "", "draft"))
assert win.text_view.text == "大家好。", "must not flash source text"
from PySide6.QtTest import QTest

QTest.qWait(1400)
assert win.text_view.text == "Welcome to the meeting."

app.primaryScreen().grabWindow(0).save("runs/render_handle.png")
print("OK")
