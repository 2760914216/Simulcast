import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QApplication

from simulcast.single import acquire

app = QApplication.instance() or QApplication([])

name = "simulcast-test-single"
seen = []
first = acquire(name, seen.append)
assert first is not None, "first instance should own the name"

second = acquire(name, seen.append)
assert second is None, "second instance should back off"

deadline = time.time() + 3
while not seen and time.time() < deadline:
    app.processEvents()
    time.sleep(0.05)
assert seen == ["show"], seen

first.close()
QLocalServer.removeServer(name)
print("single instance        ok")
