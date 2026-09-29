"""Sync journal and Lamport clock sequence tracking for SQLite and Postgres backends.

Maintains the local append-only replication log and ensures deterministic ordering.
"""

from __future__ import annotations

import json
import socket
import sqlite3
import time
import uuid
from typing import Any


def init_sync_tables(conn: sqlite3.Connection) -> None:
    """Initialize synchronization schema tables and indexes in SQLite."""
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_events (
                event_id TEXT PRIMARY KEY,
                memory_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                timestamp INTEGER NOT NULL,
                operation TEXT NOT NULL,
                envelope_json TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sync_events_seq ON sync_events (sequence ASC);"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sync_events_memory ON sync_events (memory_id);"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )


def get_device_id(conn: sqlite3.Connection) -> str:
    """Retrieve or generate a persistent unique identifier for this device."""
    init_sync_tables(conn)
    cur = conn.execute("SELECT value FROM sync_state WHERE key = 'device_id';")
    row = cur.fetchone()
    if row:
        return row[0]

    hostname = socket.gethostname().split(".")[0].lower()
    dev_id = f"{hostname}-{uuid.uuid4().hex[:8]}"
    with conn:
        conn.execute(
            "INSERT INTO sync_state (key, value) VALUES ('device_id', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value;",
            (dev_id,),
        )
    return dev_id


def get_current_sequence(conn: sqlite3.Connection) -> int:
    """Get the current Lamport sequence number for this local instance."""
    init_sync_tables(conn)
    cur = conn.execute("SELECT value FROM sync_state WHERE key = 'current_sequence';")
    row = cur.fetchone()
    if row:
        try:
            return int(row[0])
        except ValueError:
            return 0
    return 0


def next_sequence(conn: sqlite3.Connection) -> int:
    """Atomically increment and return the next monotonically increasing Lamport sequence."""
    seq = get_current_sequence(conn) + 1
    with conn:
        conn.execute(
            "INSERT INTO sync_state (key, value) VALUES ('current_sequence', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value;",
            (str(seq),),
        )
    return seq


def advance_sequence_if_higher(conn: sqlite3.Connection, peer_seq: int) -> int:
    """Advance local Lamport clock if incoming peer event has a higher sequence number."""
    cur_seq = get_current_sequence(conn)
    if peer_seq > cur_seq:
        with conn:
            conn.execute(
                "INSERT INTO sync_state (key, value) VALUES ('current_sequence', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value;",
                (str(peer_seq),),
            )
        return peer_seq
    return cur_seq


def record_event(conn: sqlite3.Connection, envelope: dict[str, Any]) -> None:
    """Persist an encrypted sync envelope into the local replication journal."""
    init_sync_tables(conn)
    event_id = envelope["event_id"]
    memory_id = envelope["memory_id"]
    device_id = envelope["device_id"]
    sequence = envelope["sequence"]
    timestamp = envelope["timestamp"]
    operation = envelope["operation"]
    envelope_json = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False)

    with conn:
        conn.execute(
            """
            INSERT INTO sync_events (
                event_id, memory_id, device_id, sequence, timestamp, operation, envelope_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO NOTHING;
            """,
            (
                event_id,
                memory_id,
                device_id,
                sequence,
                timestamp,
                operation,
                envelope_json,
                time.time(),
            ),
        )


def has_event(conn: sqlite3.Connection, event_id: str) -> bool:
    """Check if an event ID has already been recorded in the journal."""
    init_sync_tables(conn)
    cur = conn.execute("SELECT 1 FROM sync_events WHERE event_id = ? LIMIT 1;", (event_id,))
    return cur.fetchone() is not None


def get_events_since(
    conn: sqlite3.Connection, since_sequence: int = 0, limit: int = 500
) -> list[dict[str, Any]]:
    """Retrieve replication envelopes recorded since a specific sequence number."""
    init_sync_tables(conn)
    cur = conn.execute(
        """
        SELECT envelope_json FROM sync_events
        WHERE sequence > ?
        ORDER BY sequence ASC
        LIMIT ?;
        """,
        (since_sequence, limit),
    )
    rows = cur.fetchall()
    return [json.loads(row[0]) for row in rows]


def get_peers(conn: sqlite3.Connection) -> list[str]:
    """Retrieve configured peer endpoints."""
    cur = conn.execute("SELECT value FROM sync_state WHERE key = 'peers';")
    row = cur.fetchone()
    if row and row[0]:
        try:
            return json.loads(row[0])
        except Exception:
            return []
    return []


def save_peers(conn: sqlite3.Connection, peers: list[str]) -> None:
    """Save peer endpoints."""
    with conn:
        conn.execute(
            "INSERT INTO sync_state (key, value) VALUES ('peers', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value;",
            (json.dumps(sorted(list(set(peers)))),),
        )
