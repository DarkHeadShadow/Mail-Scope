"""Deliberately misbehaving parsers for the attachment sandbox tests."""

__author__ = "Bálint Szabolcs"

import os


def echo(value: bytes) -> int:
    """Return the length of the received bytes."""
    return len(value)


def crash() -> None:
    """Terminate the process abruptly, like a crashing native parser."""
    os._exit(3)


def hang() -> None:
    """Never return, like a parser stuck in an endless loop."""
    while True:
        pass


def hog() -> int:
    """Try to allocate far more memory than the worker is allowed."""
    return len(bytearray(1024 * 1024 * 1024))


def fail() -> None:
    """Raise an ordinary parser error."""
    raise ValueError("sérült fájl")
