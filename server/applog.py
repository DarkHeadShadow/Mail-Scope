"""Application and case log files for the local Mail-Scope server."""

__author__ = "Bálint Szabolcs"

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path


_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_lock = threading.Lock()
_application_log: Path | None = None
_case_handler: logging.Handler | None = None


class _AppendingFileHandler(logging.Handler):
    """Append each record and close the file, so a case folder is never locked."""

    def __init__(self, path: Path) -> None:
        """Remember the target log file."""
        super().__init__()
        self.path = path

    def emit(self, record: logging.LogRecord) -> None:
        """Write one formatted record."""
        try:
            with self.path.open("a", encoding="utf-8") as log_file:
                log_file.write(self.format(record) + "\n")
        except Exception:
            self.handleError(record)


def application_log_directory() -> Path:
    """Return the per-user log directory outside any case or source folder."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Mail-Scope" / "logs"


def configure_logging() -> Path | None:
    """Log to a rotating application file and, when available, the console."""
    global _application_log
    handlers: list[logging.Handler] = []
    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        handlers.append(console)
    log_path: Path | None = application_log_directory() / "mail-scope.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(logging.Formatter(_FORMAT))
        handlers.append(file_handler)
    except OSError:
        log_path = None
    if not handlers:
        handlers.append(logging.NullHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)
    _application_log = log_path
    return log_path


def application_log_path() -> Path | None:
    """Return the configured application log file, if file logging is active."""
    return _application_log


def attach_case_log(case_directory: Path) -> Path | None:
    """Send all further log records to the case's own .mailscope log as well.

    Only the running application configures logging; embedded uses such as
    tests keep the root logger untouched.
    """
    global _case_handler
    if _application_log is None:
        return None
    log_path = case_directory / ".mailscope" / "mailscope.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = _AppendingFileHandler(log_path)
    handler.setFormatter(logging.Formatter(_FORMAT))
    root = logging.getLogger()
    with _lock:
        if _case_handler is not None:
            root.removeHandler(_case_handler)
            _case_handler.close()
        root.addHandler(handler)
        _case_handler = handler
    return log_path
