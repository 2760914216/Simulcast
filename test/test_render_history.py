import sys

from PySide6.QtWidgets import QApplication

from simulcast.render.overlay import SubtitleWindow

app = QApplication(sys.argv)
win = SubtitleWindow(font_size=34, width_ratio=0.74, history_lines=1)
win.set_connected(True)
win.show()


def cap(cid, raw, translation, state="final"):
    return {"type": "caption", "id": cid, "t0": 0, "raw": raw, "translation": translation, "state": state, "seq": 1}


# a parent draft and its committed child carry the same sentence: one row only
win.set_caption(cap("parent", "Many of us never seem to get promoted.", "我们中许多人似乎永远都得不到晋升。"))
win.set_caption(cap("child", "Many of us never seem to get promoted.", "我们中许多人似乎永远都得不到晋升。"))
visible = [v.text for v in win.history_views if v.isVisible()]
print("parent->child rows:", visible, "history:", win._history)
assert visible == [], visible
assert len(win._history) == 1 and win._history[0]["id"] == "parent"

win.set_caption(cap("a", "Why is that?", "那是为什么呢？"))
assert win.history_id == "child", win.history_id
win.set_caption(cap("b", "What can we do about it?", "我们又该怎么做呢？"))
assert win.history_id == "a", win.history_id
win.set_caption(cap("c", "First sentence here.", "这里是第三句。"))
assert win.history_id == "b"
assert [h["id"] for h in win._history] == ["a", "b"], win._history
print("one history line:", win.history_id, "history:", [h["id"] for h in win._history], "size", win.size())

win.update_history({"id": "b", "raw": "", "translation": "我们又该怎么做？"})
assert win._history[-1]["translation"].endswith("怎么做？")
win.grab().save("runs/render_history.png")

height_one = win.size().height()
win.apply_style(history_lines=2)
height_two = win.size().height()
print("heights:", height_one, height_two)
assert height_two > height_one
win.grab().save("runs/render_history_two.png")

win.apply_style(history_lines=0)
assert win._history == []
assert win.size().height() < height_one
print("history off height:", win.size().height())
print("OK")
