import sys

from PySide6.QtWidgets import QApplication

from simulcast.render.model import CaptionStore
from simulcast.render.overlay import SubtitleWindow

app = QApplication(sys.argv)
win = SubtitleWindow(font_size=34, width_ratio=0.74, history_lines=1)
win.set_connected(True)
win.show()

store = CaptionStore()
seq = 0


def feed(cid, t0, raw, translation, state="draft"):
    global seq
    seq += 1
    msg = {
        "type": "caption",
        "id": cid,
        "t0": t0,
        "raw": raw,
        "translation": translation,
        "state": state,
        "seq": seq,
        "sid": "s",
    }
    cap = store.apply(msg)
    if cap is None:
        return
    win.note_history(msg)
    if win.history_id and msg["id"] == win.history_id and msg["id"] != cap.get("id"):
        win.update_history(msg)
    win.set_caption(cap)
    win._throttle.stop()
    win._grace.stop()
    if cap is not msg:
        win.set_caption(cap)


def rows():
    upper = [v.text for v in win.history_views if not v.isHidden() and v.text]
    return upper, win.text_view.text


feed("b-30", 419.26, "あれは遠くの世界。", "那是遥远的地方。")
feed("b-30", 419.26, "あれは遠くないうちに必ず起こる。", "那件事迟早一定会发生。")
feed("b-30-s2", 435.75, "あれは遠くないうちに必ず起こる。", "")
feed("b-30-s2", 435.75, "あれは遠くないうちに必ず起こる。", "那件事迟早一定会发生。", "final")

# the burst moves on: the live tail now sits after the child it just gave up
assert store.active_id == "b-30-s2", store.active_id
feed("b-30", 436.75, "絶対に。", "那件事迟早一定会发生。")
assert store.active_id == "b-30", store.active_id
assert rows() == ([], "那件事迟早一定会发生。"), rows()

feed("b-30", 436.75, "絶対に。", "绝对。")
upper, current = rows()
print("tail after child:", upper, current)
assert current == "绝对。", current
assert upper == ["那件事迟早一定会发生。"], upper

feed("b-30", 436.75, "絶対にそれは防がなくじゃならない。", "绝对无法避免。")
upper, current = rows()
print("tail growing:", upper, current)
assert current == "绝对无法避免。", current
assert "绝对无法避免。" not in upper, upper

feed("b-30-s3", 439.66, "絶対にそれは防がなくじゃならない。", "", "final")
feed("b-30-s3", 439.66, "絶対にそれは防がなくじゃならない。", "绝对无法避免。", "final")
upper, current = rows()
print("next sentence:", upper, current)
assert upper == ["那件事迟早一定会发生。"], upper
assert current == "绝对无法避免。", current
print("OK")
