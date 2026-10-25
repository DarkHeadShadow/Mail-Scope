"""Run the local Mail-Scope web application."""

__author__ = "Bálint Szabolcs"

import argparse
import logging

from .app import create_server
from version import __version__


def main() -> None:
    """Start the browser UI on the local machine only."""
    parser = argparse.ArgumentParser(description="Run the local Mail-Scope application.")
    parser.add_argument("--port", type=int, default=8765, help="Local HTTP port.")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    http_server = create_server(port=arguments.port)
    print(
        f"Mail-Scope {__version__} is ready: "
        f"http://127.0.0.1:{arguments.port}"
    )
    try:
        http_server.serve_forever()
    except KeyboardInterrupt:
        print("\nMail-Scope server stopped.")
    finally:
        http_server.server_close()


if __name__ == "__main__":
    main()
