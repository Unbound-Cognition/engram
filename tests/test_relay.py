"""Tests for the zero-knowledge sync relay server."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from engram.sync.relay import RelayStore


def test_relay_store_roundtrip():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test_relay.db")
        store = RelayStore(db_path)

        assert store.get_event_count() == 0
        assert store.get_max_sequence() == 0

        # Store sample events
        events = [
            {
                "event_id": "ev-1",
                "memory_id": "mem-1",
                "device_id": "laptop",
                "sequence": 1,
                "timestamp": 1000,
                "operation": "upsert",
                "crypto": {"ciphertext": "enc1"},
            },
            {
                "event_id": "ev-2",
                "memory_id": "mem-2",
                "device_id": "desktop",
                "sequence": 2,
                "timestamp": 1005,
                "operation": "upsert",
                "crypto": {"ciphertext": "enc2"},
            },
        ]
        stored = store.store_events(events)
        assert stored == 2
        assert store.get_event_count() == 2
        assert store.get_max_sequence() == 2

        # Duplicate event should be ignored
        dup_stored = store.store_events(events)
        assert dup_stored == 0
        assert store.get_event_count() == 2

        # Query events since sequence 1
        fetched = store.get_events_since(since=1, limit=10)
        assert len(fetched) == 1
        assert fetched[0]["event_id"] == "ev-2"

        # Query all
        all_events = store.get_events_since(since=0, limit=10)
        assert len(all_events) == 2
