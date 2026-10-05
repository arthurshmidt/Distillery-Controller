"""Run the daemon: `still --config config/still.yaml` (or `python -m still`)."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys

import uvicorn

from .api import create_app
from .config import load_config
from .controller import Controller
from .hardware import SimulatedHW, WidgetlordsHW
from .runner import ControlLoop
from .store import Store

logger = logging.getLogger(__name__)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="still", description=__doc__)
    parser.add_argument("--config", default="config/still.yaml")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (use the LAN address to expose it)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--simulate", action="store_true", help="use SimulatedHW instead of the boards")
    parser.add_argument("--db", default="still.db", help="SQLite file for history and saved settings")
    parser.add_argument("--interval", type=float, default=1.0, help="control loop period, seconds")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    token = os.environ.get("STILL_TOKEN")
    if not token:
        print("STILL_TOKEN must be set to the API token", file=sys.stderr)
        return 2

    config = load_config(args.config)
    hw = SimulatedHW() if args.simulate else WidgetlordsHW(config)
    store = Store(args.db)
    controller = Controller(hw, config, store=store)
    loop = ControlLoop(controller, interval_s=args.interval, store=store)
    app = create_app(controller, loop, config, token, simulator=hw if isinstance(hw, SimulatedHW) else None)

    loop.start()
    # Once its own shutdown finishes, uvicorn restores whichever SIGTERM handler
    # was installed before run() and re-raises the signal through it (so a
    # process supervisor sees the expected exit status). With no handler
    # installed that is the OS default of terminating the process immediately,
    # which happens inside uvicorn.run() and skips the `finally` below (and so
    # the valve failsafe) entirely. A no-op handler makes the re-raise harmless
    # and lets uvicorn.run() return normally instead. SIGINT doesn't need this:
    # Python's default handler there raises KeyboardInterrupt, which already
    # propagates out of uvicorn.run() as a normal return.
    signal.signal(signal.SIGTERM, lambda *_: None)
    try:
        # The SSE stream (/api/stream) only ends when its client disconnects, so on
        # SIGTERM uvicorn would otherwise wait forever for it and never reach the
        # `finally` below. Cap the wait well under deploy/still.service's
        # TimeoutStopSec=15, which would otherwise SIGKILL us first and skip the
        # valve failsafe entirely.
        uvicorn.run(app, host=args.host, port=args.port, timeout_graceful_shutdown=5)
    finally:
        # Each step is guarded so a failure in one cannot skip the rest.
        logger.info("shutting down, opening valves")
        for step in (loop.stop, hw.close, store.close):
            try:
                step()
            except Exception:
                logger.exception("shutdown step %s failed", getattr(step, "__qualname__", step))
    return 0


if __name__ == "__main__":
    sys.exit(main())
