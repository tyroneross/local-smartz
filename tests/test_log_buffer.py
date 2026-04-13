"""Tests for localsmartz.log_buffer.

Uses the private _RingBuffer class directly so tests can pin a small capacity
and verify wrap-around / since() / clear() semantics without relying on
module-global state.
"""

from __future__ import annotations

import pytest

from localsmartz import log_buffer
from localsmartz.log_buffer import _RingBuffer


def test_ring_buffer_caps_at_capacity():
    buf = _RingBuffer(capacity=3)
    buf.append("info", "t", "one")
    buf.append("info", "t", "two")
    buf.append("info", "t", "three")
    buf.append("info", "t", "four")
    all_entries = buf.all()
    assert len(all_entries) == 3
    # Oldest got dropped; remaining should be two/three/four.
    messages = [e["message"] for e in all_entries]
    assert messages == ["two", "three", "four"]
    # Seq numbers keep incrementing even when entries get dropped.
    seqs = [e["seq"] for e in all_entries]
    assert seqs == [2, 3, 4]


def test_since_returns_only_newer_entries():
    buf = _RingBuffer(capacity=10)
    for i in range(5):
        buf.append("info", "t", f"m{i}")
    # seq values are 1..5; since(2) returns entries with seq > 2.
    newer = buf.since(2)
    assert [e["seq"] for e in newer] == [3, 4, 5]
    # since(0) returns everything.
    assert len(buf.since(0)) == 5
    # since(seq of last) returns empty.
    assert buf.since(5) == []


def test_clear_empties_buffer():
    buf = _RingBuffer(capacity=5)
    buf.append("warn", "src", "hello")
    buf.append("error", "src", "bye")
    assert len(buf.all()) == 2
    buf.clear()
    assert buf.all() == []
    assert buf.since(0) == []


def test_module_level_helpers_record_levels():
    # Clear first so we have a known starting point.
    log_buffer.clear()
    log_buffer.info("src", "info-msg")
    log_buffer.warn("src", "warn-msg")
    log_buffer.error("src", "err-msg")
    snap = log_buffer.snapshot()
    assert len(snap) == 3
    levels = [e["level"] for e in snap]
    assert levels == ["info", "warn", "error"]
    # Each entry has the documented shape.
    for e in snap:
        assert set(e.keys()) >= {"seq", "ts", "level", "source", "message"}
    log_buffer.clear()


def test_module_since_matches_buffer_semantics():
    log_buffer.clear()
    log_buffer.info("s", "a")
    log_buffer.info("s", "b")
    entries = log_buffer.since(0)
    assert [e["message"] for e in entries] == ["a", "b"]
    last_seq = entries[-1]["seq"]
    assert log_buffer.since(last_seq) == []
    log_buffer.clear()
