"""The control loop thread, which also records each tick to the `Store`
(SQLite) that backs `/api/history`.

The loop lives in its own thread so a slow or stuck web request can never
stall the PID (see PLAN.md).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import List, Optional

from .controller import Controller
from .store import HistoryPoint, Store

logger = logging.getLogger(__name__)


class ControlLoop:
    def __init__(self, controller: Controller, interval_s: float = 1.0, store: Optional[Store] = None):
        self._controller = controller
        self._interval_s = interval_s
        self._store = store if store is not None else Store()
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
        try:
            self._controller.failsafe("shutdown", is_fault=False)
        except Exception:
            logger.exception("could not command the failsafe position at shutdown")

    @property
    def interval_s(self) -> float:
        return self._interval_s

    @property
    def retention_days(self) -> float:
        return self._store.retention_days

    def history(
        self,
        since: Optional[float] = None,
        limit: Optional[int] = None,
        until: Optional[float] = None,
        step: Optional[float] = None,
    ) -> List[HistoryPoint]:
        return self._store.history(since=since, limit=limit, until=until, step=step)

    def events(self, since: Optional[float] = None, until: Optional[float] = None):
        return self._store.events(since=since, until=until)

    def export_rows(self, since: Optional[float] = None, until: Optional[float] = None):
        return self._store.rows_for_export(since, until)

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                state = self._controller.tick()
            except Exception:
                logger.exception("control loop iteration failed")
                try:
                    state = self._controller.failsafe("control loop error")
                except Exception:
                    logger.exception("failsafe failed")
                    state = None
            if state is not None:
                try:
                    self._store.add_history(time.time(), state)
                except Exception:
                    logger.exception("could not record history")
            self._stop.wait(max(0.0, self._interval_s - (time.monotonic() - started)))
