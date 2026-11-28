"""Start: python3 -m multitool  (auf dem Pi)  oder  python3 -m multitool --sim  (Simulation am PC)."""

import argparse
import logging
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="multitool")
    parser.add_argument("--sim", action="store_true",
                        help="Simulation: keine echte Hardware, Webseite auf http://localhost:8080")
    parser.add_argument("--data", default=None, help="Datenordner (Standard /opt/multitool/data)")
    parser.add_argument("--port", type=int, default=None, help="Port der Webseite")
    parser.add_argument("--debug", action="store_true", help="ausführliche Meldungen")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    data_dir = args.data
    if data_dir is None:
        data_dir = os.path.expanduser("~/multitool-data") if args.sim else "/opt/multitool/data"

    from .app import App
    app = App(data_dir=data_dir, sim=args.sim, port=args.port)
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
