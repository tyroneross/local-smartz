"""Tests for the SqliteSaver-backed durable checkpointer.

Covers the contract: after writing a checkpoint, closing the connection,
and reopening a fresh SqliteSaver against the same db file, we can still
list the prior checkpoint. This is the whole point of moving off
MemorySaver — research threads survive a backend restart.

These use a real sqlite file, not a mock — the behavior we care about is
on-disk durability.
"""

import sqlite3
import tempfile
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver


def _put_one(saver: SqliteSaver, thread_id: str, checkpoint_id: str) -> None:
    """Write a minimal checkpoint under ``thread_id``."""
    config = {
        "configurable": {"thread_id": thread_id, "checkpoint_ns": ""},
    }
    checkpoint = {
        "v": 1,
        "id": checkpoint_id,
        "ts": "2026-04-13T00:00:00+00:00",
        "channel_values": {"messages": ["hello"]},
        "channel_versions": {"messages": 1},
        "versions_seen": {},
    }
    metadata = {"source": "input", "step": 1, "writes": {}, "parents": {}}
    saver.put(config, checkpoint, metadata, {})


def test_checkpoint_survives_connection_close(tmp_path: Path) -> None:
    """Core durability: write → close DB → reopen fresh connection →
    read back. If this fails we're back to MemorySaver semantics and the
    durability claim is false."""
    db_path = tmp_path / "checkpoints.db"

    conn1 = sqlite3.connect(str(db_path), check_same_thread=False)
    saver1 = SqliteSaver(conn1)
    saver1.setup()
    _put_one(saver1, thread_id="research-1", checkpoint_id="ckpt-1")
    conn1.close()

    # Fresh connection — simulates a backend restart.
    conn2 = sqlite3.connect(str(db_path), check_same_thread=False)
    saver2 = SqliteSaver(conn2)
    try:
        config = {"configurable": {"thread_id": "research-1", "checkpoint_ns": ""}}
        retrieved = list(saver2.list(config, limit=5))
        assert len(retrieved) == 1
        assert retrieved[0].checkpoint["id"] == "ckpt-1"
    finally:
        conn2.close()


def test_checkpoint_isolates_threads(tmp_path: Path) -> None:
    """Two distinct thread_ids must not read each other's checkpoints —
    this is what prevents research threads from cross-contaminating."""
    db_path = tmp_path / "checkpoints.db"

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    _put_one(saver, thread_id="alpha", checkpoint_id="ckpt-a")
    _put_one(saver, thread_id="beta", checkpoint_id="ckpt-b")

    alpha_cfg = {"configurable": {"thread_id": "alpha", "checkpoint_ns": ""}}
    beta_cfg = {"configurable": {"thread_id": "beta", "checkpoint_ns": ""}}

    alpha_ids = [c.checkpoint["id"] for c in saver.list(alpha_cfg, limit=5)]
    beta_ids = [c.checkpoint["id"] for c in saver.list(beta_cfg, limit=5)]

    assert alpha_ids == ["ckpt-a"]
    assert beta_ids == ["ckpt-b"]
    conn.close()


def test_setup_is_idempotent(tmp_path: Path) -> None:
    """Calling setup() twice must not fail — agent.create_agent runs it
    on every fresh agent construction."""
    db_path = tmp_path / "checkpoints.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    try:
        saver = SqliteSaver(conn)
        saver.setup()
        saver.setup()  # second call should be a no-op
    finally:
        conn.close()
