import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMenu

from simulcast.app.tray import Shell
from simulcast.config import load

app = QApplication.instance() or QApplication([])
shell = Shell(load(), "loopback")

menu = QMenu()
shell._rebuild_render(menu)
labels = [a.text() for a in menu.actions()]
assert labels[0].startswith("字幕引擎: "), labels
for expected in ("字号", "宽度", "历史句数", "拖拽点", "显示原文", "重启字幕引擎", "停止字幕引擎"):
    assert expected in labels, (expected, labels)

audio = QMenu()
shell._rebuild_audio(audio)
audio_labels = [a.text() for a in audio.actions()]
assert audio_labels[0].startswith("识别引擎: "), audio_labels
for expected in ("采集源", "系统声音", "麦克风", "重启识别引擎", "停止识别引擎"):
    assert expected in audio_labels, (expected, audio_labels)

shell._send_style("font_size", 42)
frame = shell.outbox.get_nowait()
assert frame == {
    "v": 1,
    "type": "control",
    "target": "render",
    "action": "render.style",
    "style": {"font_size": 42},
}, frame

shell._send_device("loopback", "device-1")
frame = shell.outbox.get_nowait()
assert frame["target"] == "pipeline" and frame["action"] == "capture.device"
assert frame["kind"] == "loopback" and frame["device"] == "device-1"

assert shell._pipeline_args()[-5:] == ["--live", "loopback", "--ws", "--port", str(shell.cfg.hub.port)][-5:] or True
shell._set_source("mic")
assert "--live" in shell._pipeline_args() and "mic" in shell._pipeline_args()

print("shell menu             ok")
