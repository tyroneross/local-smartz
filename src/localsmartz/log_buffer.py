"""In-memory ring buffer of recent server log lines.

Used by the in-app debug panel (GET /api/logs). Pure stdlib.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Iterable

DEFAULT_CAPACITY = 500


class _RingBuffer:
    def __init__(self, capacity: int = DEFAULT_CAPACITY):
        self._buf: deque[dict] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._next_seq = 1

    def append(self, level: str, source: str, message: str) -> None:
        entry = {
            "seq": self._next_seq,
            "ts": time.time(),
            "level": level,
            "source": source,
            "message": message,
        }
        with self._lock:
            self._buf.append(entry)
            self._next_seq += 1

    def since(self, seq: int) -> list[dict]:
        with self._lock:
            return [e for e in self._buf if e["seq"] > seq]

    def all(self) -> list[dict]:
        with self._lock:
            return list(self._buf)

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()


_buffer = _RingBuffer()


def log(level: str, source: str, message: str) -> None:
    """Public entry point — record a log line.
    Levels: 'info', 'warn', 'error'. Source: free-form (e.g. 'serve', 'agent', 'mcp')."""
    _buffer.append(level, source, message)


def info(source: str, message: str) -> None:
    log("info", source, message)


def warn(source: str, message: str) -> None:
    log("warn", source, message)


def error(source: str, message: str) -> None:
    log("error", source, message)


def since(seq: int = 0) -> list[dict]:
    return _buffer.since(seq)


def snapshot() -> list[dict]:
    return _buffer.all()


def clear() -> None:
    _buffer.clear()
