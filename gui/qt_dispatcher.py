"""
qt_dispatcher

Adapts callback scheduling to Qt signals so work completed outside the GUI thread can safely dispatch updates to the Qt event loop.

Input:
    Thread-safety helper.
"""
from __future__ import annotations
from PySide6.QtCore import QObject, QTimer, Signal, Slot

class QtDispatcher(QObject):
    dispatched = Signal(object, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.dispatched.connect(self._dispatch)

    def after(self, delay_ms: int, callback) -> None:
        self.dispatched.emit(callback, max(0, int(delay_ms)))

    @Slot(object, int)
    def _dispatch(self, callback, delay_ms: int) -> None:
        if delay_ms:
            QTimer.singleShot(delay_ms, callback)
        else:
            callback()
