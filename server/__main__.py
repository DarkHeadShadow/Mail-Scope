"""Run the local Mail-Scope web application."""

__author__ = "Bálint Szabolcs"

import argparse
import logging
import sys
import threading
import webbrowser

from .app import create_server


def _notify_startup_problem(message: str) -> None:
    """Show startup failures in a dialog when the executable has no console."""
    if getattr(sys, "frozen", False):
        from tkinter import messagebox

        messagebox.showerror("Mail-Scope", message)
        return
    print(message, file=sys.stderr)


def main() -> None:
    """Start the browser UI on the local machine only."""
    parser = argparse.ArgumentParser(description="Run the local Mail-Scope application.")
    parser.add_argument("--port", type=int, default=8765, help="Local HTTP port.")
    arguments = parser.parse_args()
    if getattr(sys, "frozen", False) and sys.stderr is None:
        logging.basicConfig(level=logging.INFO, handlers=[logging.NullHandler()])
    else:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        http_server = create_server(port=arguments.port)
    except OSError as error:
        _notify_startup_problem(
            f"A Mail-Scope nem tudott elindulni a {arguments.port}-es porton. "
            f"Lehet, hogy már fut egy példány, vagy a port foglalt.\n\n{error}"
        )
        return
    address = f"http://127.0.0.1:{http_server.server_address[1]}"
    server_thread = threading.Thread(
        target=http_server.serve_forever,
        name="Mail-Scope web server",
        daemon=True,
    )
    server_thread.start()
    try:
        if not webbrowser.open(address, new=2):
            logging.getLogger(__name__).warning(
                "The default browser did not open automatically: %s", address
            )
            _notify_startup_problem(
                f"A Mail-Scope elindult, de a böngésző nem nyílt meg automatikusan.\n\n"
                f"Nyissa meg ezt a címet: {address}"
            )
        server_thread.join()
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Mail-Scope server stopped.")
    finally:
        http_server.server_close()


if __name__ == "__main__":
    main()
