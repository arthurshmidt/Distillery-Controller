"""The control loop thread, plus an in-memory history for `/api/history`.

The loop lives in its own thread so a slow or stuck web request can never
stall the PID (see PLAN.md). History is a bounded in-memory ring buffer for
now; phase 5 replaces it with SQLite.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, List, Optional

from .controller import Controller, ControllerState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HistoryPoint:
    timestamp: float
    state: ControllerState


class ControlLoop:
    def __init__(self, controller: Controller, interval_s: float = 1.0, history_size: int = 7200):
        self._controller = controller
        self._interval_s = interval_s
        self._history: Deque[HistoryPoint] = deque(maxlen=history_size)
        self._history_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="control-loop", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the loop and leave the valves in the failsafe position."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval_s * 3 + 1)
            self._thread = None
        self._controller.failsafe("shutdown", is_fault=False)

    def history(self, since: Optional[float] = None, limit: Optional[int] = None) -> List[HistoryPoint]:
        with self._history_lock:
            points = list(self._history)
        if since is not None:
            points = [p for p in points if p.timestamp > since]
        if limit is not None:
            points = points[-limit:] if limit > 0 else []
        return points

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                state = self._controller.tick()
                with self._history_lock:
                    self._history.append(HistoryPoint(time.time(), state))
            except Exception:
                logger.exception("control loop iteration failed")
                try:
                    self._controller.failsafe("control loop error")
                except Exception:
                    logger.exception("failsafe failed")
            self._stop.wait(max(0.0, self._interval_s - (time.monotonic() - started)))
