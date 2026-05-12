"""TelemetryBuffer — SQLite-backed FIFO spool for failed MQTT publishes.

Models the ESP32's NVS (Non-Volatile Storage) behaviour: when the broker
is unreachable, telemetry is persisted to disk and replayed in order on
the next successful connect. Survives process restarts, power cuts, and
moves between machines (the spool is a single .db file).

Usage
-----
    buf = TelemetryBuffer("spool.db", max_size=10_000)

    # Publish path: try to send, spool on failure.
    if not client.try_publish(topic, payload):
        buf.enqueue(topic, payload)

    # Reconnect path: drain in order, stop on the first failure so the
    # remainder stays queued for the next attempt.
    buf.flush(lambda topic, payload: client.try_publish(topic, payload))

`enqueue` is thread-safe; many bin threads can spool concurrently.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional, Union


PublishFn = Callable[[str, str], bool]
"""(topic, payload_json) -> True on successful publish, False to stop the flush."""


@dataclass(frozen=True)
class SpooledMessage:
    id: int
    topic: str
    payload: str
    queued_at: str   # ISO-8601 UTC, set when the row was inserted


class TelemetryBuffer:
    """Durable FIFO queue for telemetry payloads keyed by insertion order.

    LRU-style eviction: when `max_size` is reached, the oldest rows are
    dropped to make room for the new one. Mirrors the ESP32 NVS policy
    where flash space is finite and recent readings outrank stale ones.
    """

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS spool (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            topic     TEXT    NOT NULL,
            payload   TEXT    NOT NULL,
            queued_at TEXT    NOT NULL
        )
    """

    def __init__(
        self,
        path: Union[str, Path] = "spool.db",
        *,
        max_size: int = 10_000,
    ) -> None:
        if max_size <= 0:
            raise ValueError("max_size must be positive")
        self.path = str(path)
        self.max_size = max_size
        self._lock = threading.RLock()
        # check_same_thread=False because bin threads call enqueue concurrently;
        # the RLock above serialises every DB op so this is safe.
        self._conn = sqlite3.connect(
            self.path,
            check_same_thread=False,
            isolation_level=None,   # autocommit; we manage transactions explicitly
        )
        self._conn.row_factory = sqlite3.Row
        # WAL gives us concurrent readers + a single writer with low fsync cost,
        # which matches the spool's "many small inserts, occasional drains" pattern.
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute(self._SCHEMA)

    # -- write path --------------------------------------------------------

    def enqueue(self, topic: str, payload: str) -> int:
        """Append a message to the spool. Returns the assigned row id.

        If the spool is full, the oldest row is evicted first (FIFO drop).
        """
        if not topic:
            raise ValueError("topic must be non-empty")
        if not payload:
            raise ValueError("payload must be non-empty")
        queued_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._txn() as cur:
            self._evict_locked(cur, room_for=1)
            cur.execute(
                "INSERT INTO spool (topic, payload, queued_at) VALUES (?, ?, ?)",
                (topic, payload, queued_at),
            )
            return cur.lastrowid

    # -- drain path --------------------------------------------------------

    def flush(self, publish_fn: PublishFn, *, batch: int = 100) -> int:
        """Drain the spool in FIFO order via `publish_fn`.

        Stops at the first message where `publish_fn` returns False (or
        raises) and leaves the remainder queued. Returns the number of
        messages successfully published in this call.

        `batch` controls how many rows are read into memory at a time — a
        full drain still happens, just chunked so we don't load a huge
        spool all at once.
        """
        if batch <= 0:
            raise ValueError("batch must be positive")
        sent = 0
        while True:
            chunk = self._peek(batch)
            if not chunk:
                return sent
            for msg in chunk:
                ok = False
                try:
                    ok = bool(publish_fn(msg.topic, msg.payload))
                except Exception:
                    # Caller's publish raised — treat as failure, keep row.
                    self._conn.commit()
                    raise
                if not ok:
                    return sent
                self._delete(msg.id)
                sent += 1

    # -- introspection -----------------------------------------------------

    def depth(self) -> int:
        """Number of messages currently queued."""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM spool").fetchone()
            return int(row["n"])

    def peek(self, limit: int = 10) -> list[SpooledMessage]:
        """Return the next `limit` messages in FIFO order without removing them."""
        if limit <= 0:
            return []
        return self._peek(limit)

    def is_empty(self) -> bool:
        return self.depth() == 0

    # -- maintenance -------------------------------------------------------

    def clear(self) -> int:
        """Delete every queued message. Returns the number removed."""
        with self._txn() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM spool")
            n = int(cur.fetchone()["n"])
            cur.execute("DELETE FROM spool")
            return n

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "TelemetryBuffer":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- internals ---------------------------------------------------------

    @contextmanager
    def _txn(self) -> Iterator[sqlite3.Cursor]:
        """Acquire the lock and run a BEGIN/COMMIT block.

        With `isolation_level=None` we drive transactions ourselves, which
        keeps eviction + insert atomic (no chance of being preempted between
        the DELETE and the INSERT).
        """
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN")
            try:
                yield cur
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise

    def _evict_locked(self, cur: sqlite3.Cursor, *, room_for: int) -> None:
        cur.execute("SELECT COUNT(*) AS n FROM spool")
        n = int(cur.fetchone()["n"])
        overflow = (n + room_for) - self.max_size
        if overflow > 0:
            cur.execute(
                "DELETE FROM spool WHERE id IN ("
                "  SELECT id FROM spool ORDER BY id ASC LIMIT ?"
                ")",
                (overflow,),
            )

    def _peek(self, limit: int) -> list[SpooledMessage]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, topic, payload, queued_at FROM spool "
                "ORDER BY id ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            SpooledMessage(
                id=int(r["id"]),
                topic=r["topic"],
                payload=r["payload"],
                queued_at=r["queued_at"],
            )
            for r in rows
        ]

    def _delete(self, row_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM spool WHERE id = ?", (row_id,))
