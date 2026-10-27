"""Run attachment parsers in a separate, time- and memory-limited process.

A damaged or malicious file can crash or hang a parser. Each analysis
therefore runs in its own short-lived process: if it exceeds the time limit
it is killed, and if it dies the server keeps working and reports the error.
"""

__author__ = "Bálint Szabolcs"

import importlib
import multiprocessing
import sys
import time
from typing import Any


DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MEMORY_MB = 2048


class AttachmentProcessingError(RuntimeError):
    """An isolated attachment analysis failed, crashed, or ran out of time."""


def run_isolated(
    target: str,
    *args: Any,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    memory_mb: int = DEFAULT_MEMORY_MB,
) -> Any:
    """Call ``module:function`` with ``args`` in a child process and return its result."""
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_child,
        args=(sender, target, args, memory_mb),
        name="Mail-Scope attachment worker",
        daemon=True,
    )
    process.start()
    sender.close()
    try:
        deadline = time.monotonic() + timeout
        # Poll in short steps: a child that dies (even before it starts the
        # parser) is noticed at once instead of after the whole time limit.
        while not receiver.poll(0.1):
            if not process.is_alive():
                process.join(1)
                if receiver.poll(0):
                    break
                raise AttachmentProcessingError(
                    f"A melléklet feldolgozó folyamata váratlanul leállt (kilépési kód: {process.exitcode})."
                )
            if time.monotonic() >= deadline:
                raise AttachmentProcessingError(
                    f"A melléklet feldolgozása túllépte az időkorlátot ({int(timeout)} s), ezért leállítottuk."
                )
        try:
            status, value = receiver.recv()
        except EOFError as error:
            process.join(5)
            raise AttachmentProcessingError(
                f"A melléklet feldolgozó folyamata váratlanul leállt (kilépési kód: {process.exitcode})."
            ) from error
    finally:
        receiver.close()
        if process.is_alive():
            process.kill()
        process.join(5)
    if status == "ok":
        return value
    raise AttachmentProcessingError(f"A melléklet nem dolgozható fel: {value}")


def _child(sender: Any, target: str, args: tuple[Any, ...], memory_mb: int) -> None:
    """Child-process entry point: limit memory, run the parser, send the outcome."""
    try:
        _limit_memory(memory_mb)
        module_name, function_name = target.split(":", 1)
        function = getattr(importlib.import_module(module_name), function_name)
        result = function(*args)
        sender.send(("ok", result))
    except MemoryError:
        sender.send(("error", "a feldolgozás túllépte a memóriakorlátot"))
    except BaseException as error:  # the child must always report, never hang
        sender.send(("error", f"{type(error).__name__}: {error}"))
    finally:
        sender.close()


def _limit_memory(memory_mb: int) -> None:
    """Cap the child's memory: a Job Object on Windows, RLIMIT_AS elsewhere."""
    limit = memory_mb * 1024 * 1024
    if sys.platform == "win32":
        try:
            _windows_job_limit(limit)
        except (OSError, AttributeError, ValueError, TypeError):
            # Without a job object the time limit still protects the server.
            pass
        return
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (ImportError, ValueError, OSError):
        pass


def _windows_job_limit(limit: int) -> None:
    """Put the current process into a Job Object with a process memory limit."""
    import ctypes
    from ctypes import wintypes

    class BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimit),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    job_object_limit_process_memory = 0x00000100
    job_object_extended_limit_information = 9
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD
    )
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return
    info = ExtendedLimit()
    info.BasicLimitInformation.LimitFlags = job_object_limit_process_memory
    info.ProcessMemoryLimit = limit
    if kernel32.SetInformationJobObject(
        job, job_object_extended_limit_information, ctypes.byref(info), ctypes.sizeof(info)
    ):
        kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess())
