import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from simulcast.render.qt import RendererApp

app = QApplication.instance() or QApplication([])
renderer = RendererApp(
    ws="ws://127.0.0.1:1",
    font_size=34,
    width_ratio=0.74,
    show_source=True,
    demo=True,
)
renderer._build_tray(app)
renderer._rebuild_capture_menu(renderer.capture_menu)

labels = [a.text() for a in renderer.capture_menu.actions()]
assert labels == ["系统声音", "麦克风"], labels

for sub in renderer.capture_menu.actions():
    items = sub.menu().actions()
    assert items[0].text() == "跟随系统默认", items[0].text()
    assert items[0].isChecked()
    assert items[1].isSeparator()

renderer._choose_device("loopback", "device-1")
frame = renderer.outbox.get_nowait()
assert frame == {
    "v": 1,
    "type": "control",
    "action": "capture.device",
    "kind": "loopback",
    "device": "device-1",
}, frame

renderer._rebuild_capture_menu(renderer.capture_menu)
assert not renderer.capture_menu.actions()[0].menu().actions()[0].isChecked()

renderer._choose_device("loopback", None)
frame = renderer.outbox.get_nowait()
assert frame["device"] is None, frame

print("render capture menu    ok")
