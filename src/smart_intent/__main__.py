import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .application import Application
from .config import load_config
from .server import serve


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Smart Intent local read-only MCP server")
    parser.add_argument(
        "--config", type=Path, required=True, help="Absolute TOML configuration path"
    )
    parser.add_argument(
        "--check-config", action="store_true", help="Validate config without serving"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
    try:
        config = load_config(args.config)
        if args.check_config:
            print("Configuration valid; data sources and host connection not yet verified.")
            return 0
        app = Application(config)
    except Exception as exc:
        print(
            f"Startup failed ({type(exc).__name__}); check configuration and storage.",
            file=sys.stderr,
        )
        return 2
    try:
        asyncio.run(serve(app))
    except KeyboardInterrupt:
        return 0
    finally:
        app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
