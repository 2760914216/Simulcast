import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from simulcast.render import qt
from simulcast.render.overlay import SubtitleWindow

tmp = tempfile.TemporaryDirectory()
os.environ["APPDATA"] = tmp.name

app = QApplication.instance() or QApplication([])
renderer = qt.RendererApp(ws="ws://127.0.0.1:1", font_size=34, width_ratio=0.74, show_source=True, demo=True)
renderer.win = SubtitleWindow()
renderer.handle = None

renderer._apply_control({"type": "control", "target": "render", "action": "render.style", "style": {"font_size": 42}})
assert renderer.font_size == 42, renderer.font_size
assert renderer.win._font_size == 42

renderer._apply_control({"type": "control", "target": "render", "action": "render.style", "style": {"history_lines": 2}})
assert renderer.history_lines == 2

renderer._apply_control({"type": "control", "target": "pipeline", "action": "render.style", "style": {"font_size": 20}})
assert renderer.font_size == 42, "pipeline-targeted frames must be ignored"

renderer._apply_control({"type": "control", "target": "render", "action": "capture.device", "device": "x"})
assert renderer.font_size == 42

from simulcast import store

saved = store.load_render()
assert saved["font_size"] == 42 and saved["history_lines"] == 2, saved

tmp.cleanup()
print("render control         ok")
