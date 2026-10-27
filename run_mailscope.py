"""Frozen-executable entry point for the Mail-Scope local server."""

__author__ = "Bálint Szabolcs"

import multiprocessing

from server.__main__ import main


if __name__ == "__main__":
    # Attachment parsers run in spawned child processes of the frozen exe.
    multiprocessing.freeze_support()
    main()
