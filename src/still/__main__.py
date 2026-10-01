"""Run the daemon: `still --config config/still.yaml` (or `python -m still`)."""

from __future__ import annotations

import argparse
import logging
import os
import sys

import uvicorn

from .api import create_app
from .config import load_config
from .controller import Controller
from .hardware import SimulatedHW, WidgetlordsHW
from .runner import ControlLoop


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="still", description=__doc__)
    parser.add_argument("--config", default="config/still.yaml")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (use the LAN address to expose it)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--simulate", action="store_true", help="use SimulatedHW instead of the boards")
    parser.add_argument("--interval", type=float, default=1.0, help="control loop period, seconds")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    token = os.environ.get("STILL_TOKEN")
    if not token:
        print("STILL_TOKEN must be set to the API token", file=sys.stderr)
        return 2

    config = load_config(args.config)
    hw = SimulatedHW() if args.simulate else WidgetlordsHW(config)
    controller = Controller(hw, config)
    loop = ControlLoop(controller, interval_s=args.interval)
    app = create_app(controller, loop, config, token)

    loop.start()
    try:
        uvicorn.run(app, host=args.host, port=args.port)
    finally:
        loop.stop()
        hw.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
