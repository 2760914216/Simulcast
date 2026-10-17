import sys

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from simulcast.render.overlay import SubtitleWindow

app = QApplication(sys.argv)
win = SubtitleWindow(font_size=34, width_ratio=0.74)
win.set_connected(True)
win.show()

renders = []
original = win._render


def spy(cap):
    renders.append((cap.get("id"), cap.get("translation"), cap.get("state")))
    original(cap)


win._render = spy


def cap(cid, translation, state="draft", raw="Hello everyone."):
    return {"type": "caption", "id": cid, "t0": 0, "raw": raw, "translation": translation, "state": state, "seq": 1}


win.set_caption(cap("a", "大家好。"))
assert len(renders) == 1 and win.text_view.stable_len == len("大家好。")

win.set_caption(cap("a", "大家好，欢迎参加本次会议。"))
assert len(renders) == 1, "update inside the throttle window must not repaint"
QTest.qWait(260)
assert len(renders) == 2, renders
assert win.text_view.stable_len == len("大家好"), win.text_view.stable_len
print("stable prefix after growth:", win.text_view.stable_len, "of", len(win.text_view.text))

win.set_caption(cap("a", "大家好，欢迎参加本次会议。", "final"))
assert len(renders) == 3 and win.text_view.stable_len == len(win.text_view.text)

renders.clear()
win.set_caption(cap("b", "新的一句。"))
QTest.qWait(260)
base = len(renders)
win.set_caption(cap("b", "新的一句。。"))
QTest.qWait(260)
assert len(renders) == base, "a one character append should not repaint"
print("renders after 1-char append:", len(renders), "(unchanged)")

win.set_caption(cap("b", "新的一句。。这是更长的尾巴。"))
QTest.qWait(260)
assert len(renders) == base + 1
print("two-tone sample:", win.text_view.stable_len, "/", len(win.text_view.text))
win.grab().save("runs/render_twotone.png")
print("OK")
