from __future__ import annotations

import logging
import time
from typing import Callable

from PySide6.QtCore import QCoreApplication
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)


def acquire(name: str, on_message: Callable[[str], None]) -> QLocalServer | None:
    """Become the single instance called name, or ping the running one and return None."""
    probe = QLocalSocket()
    probe.connectToServer(name)
    if probe.waitForConnected(300):
        probe.write(b"show")
        probe.flush()
        deadline = time.monotonic() + 1.0
        while probe.bytesToWrite() > 0 and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            probe.waitForBytesWritten(20)
        probe.disconnectFromServer()
        log.info("another instance owns %s, asked it to show", name)
        return None

    QLocalServer.removeServer(name)
    server = QLocalServer()
    if not server.listen(name):
        log.error("cannot listen on %s: %s", name, server.errorString())
        return None

    def _accept() -> None:
        conn = server.nextPendingConnection()
        if conn is None:
            return

        def _read() -> None:
            try:
                on_message(bytes(conn.readAll()).decode("utf-8", "ignore"))
            except Exception:
                pass

        conn.readyRead.connect(_read)
        if conn.bytesAvailable():
            _read()

    server.newConnection.connect(_accept)
    return server
