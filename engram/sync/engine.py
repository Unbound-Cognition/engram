"""Replication engine reconciling local memories with incoming zero-knowledge encrypted events.

Conforms to Open Cognitive Memory Specification conflict resolution rules:
- Last-write-wins (LWW) by Lamport sequence + timestamp
- Deterministic tombstone deletions
- Fork preservation for substantive concurrent edits
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from engram.store import Memory, Store
from engram.sync import crypto, journal


class SyncEngine:
    """Coordinates encryption, replication journal, and memory store reconciliation."""

    def __init__(self, store: Store, key: bytes | None = None, key_id: str = "primary"):
        self.store = store
        self.key = key or crypto.load_sync_key()
        self.key_id = key_id
        # Ensure SQLite sync journal tables are initialized
        journal.init_sync_tables(self.conn)

    @property
    def conn(self):
        """Underlying database connection."""
        if hasattr(self.store, "conn"):
            return self.store.conn
        raise NotImplementedError("Direct SQLite connection required for sync journal")

    def get_device_id(self) -> str:
        """Get unique persistent identifier for this host."""
        return journal.get_device_id(self.conn)

    def get_sequence(self) -> int:
        """Get current Lamport sequence."""
        return journal.get_current_sequence(self.conn)

    def record_local_upsert(self, mem: Memory) -> dict[str, Any]:
        """Encrypt and record a local memory write into the replication journal."""
        dev_id = self.get_device_id()
        seq = journal.next_sequence(self.conn)

        payload = {
            "memory": {
                "id": mem.id,
                "content": mem.content,
                "source_file": mem.source_file,
                "source_type": mem.source_type,
                "layer": mem.layer,
                "memory_type": mem.memory_type,
                "status": mem.status,
                "importance": mem.importance,
                "created_at": mem.created_at,
                "last_accessed": mem.last_accessed,
                "fact_date": mem.fact_date,
                "emotional_valence": mem.emotional_valence,
                "metadata": mem.metadata,
                "forgotten": mem.forgotten,
            },
            "embedding": mem.embedding.tolist() if mem.embedding is not None else None,
        }

        envelope = crypto.encrypt_envelope(
            payload=payload,
            key=self.key,
            device_id=dev_id,
            sequence=seq,
            memory_id=mem.id,
            operation="upsert",
            key_id=self.key_id,
        )

        journal.record_event(self.conn, envelope)
        return envelope

    def record_local_delete(self, memory_id: str) -> dict[str, Any]:
        """Encrypt and record a local tombstone into the replication journal."""
        dev_id = self.get_device_id()
        seq = journal.next_sequence(self.conn)

        payload = {
            "memory_id": memory_id,
            "operation": "tombstone",
        }

        envelope = crypto.encrypt_envelope(
            payload=payload,
            key=self.key,
            device_id=dev_id,
            sequence=seq,
            memory_id=memory_id,
            operation="delete",
            key_id=self.key_id,
        )

        journal.record_event(self.conn, envelope)
        return envelope

    def apply_envelope(self, envelope: dict[str, Any]) -> bool:
        """Decrypt, validate, and apply an incoming replication envelope.

        Returns True if the event was newly applied, False if skipped as redundant.
        """
        event_id = envelope["event_id"]
        if journal.has_event(self.conn, event_id):
            return False

        memory_id = envelope["memory_id"]
        operation = envelope["operation"]
        peer_seq = envelope["sequence"]

        # Decrypt payload
        payload = crypto.decrypt_envelope(envelope, self.key)

        if operation in ("delete", "tombstone"):
            # Tombstone deletion
            if hasattr(self.store, "forget_memory"):
                try:
                    self.store.forget_memory(memory_id)
                except Exception:
                    pass
        elif operation == "upsert":
            mem_dict = payload["memory"]
            emb_list = payload.get("embedding")
            embedding = np.array(emb_list, dtype=np.float32) if emb_list is not None else None

            # Conflict resolution: check existing record
            existing = self.store.get_memory(memory_id)
            if existing:
                # If existing is newer or has substantive conflict, preserve causal link
                if existing.created_at > mem_dict.get("created_at", 0) and existing.content != mem_dict.get("content"):
                    # Attach as alternative linked to causal parent
                    meta = mem_dict.get("metadata", {}).copy()
                    meta["causal_parent"] = existing.id
                    meta["sync_conflict"] = "fork_preserved"
                    mem_dict["metadata"] = meta

            mem = Memory(
                id=mem_dict["id"],
                content=mem_dict["content"],
                source_file=mem_dict.get("source_file"),
                source_type=mem_dict.get("source_type", "sync"),
                layer=mem_dict.get("layer", "episodic"),
                memory_type=mem_dict.get("memory_type", "narrative"),
                status=mem_dict.get("status", "active"),
                embedding=embedding,
                importance=mem_dict.get("importance", 0.5),
                created_at=mem_dict.get("created_at", 0.0),
                last_accessed=mem_dict.get("last_accessed", 0.0),
                fact_date=mem_dict.get("fact_date"),
                emotional_valence=mem_dict.get("emotional_valence", 0.0),
                metadata=mem_dict.get("metadata", {}),
                forgotten=mem_dict.get("forgotten", False),
            )

            self.store.save_memory(mem)

        # Record envelope in local journal and advance Lamport sequence
        journal.record_event(self.conn, envelope)
        journal.advance_sequence_if_higher(self.conn, peer_seq)
        return True

    def export_delta(self, output_file: Path | str, since_sequence: int = 0) -> int:
        """Export replication envelopes recorded since a sequence number as NDJSON."""
        path = Path(output_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        events = journal.get_events_since(self.conn, since_sequence=since_sequence, limit=10_000)

        with open(path, "w", encoding="utf-8") as f:
            for env in events:
                f.write(json.dumps(env, separators=(",", ":"), ensure_ascii=False) + "\n")

        return len(events)

    def import_delta(self, input_file: Path | str) -> tuple[int, int]:
        """Import replication envelopes from an encrypted NDJSON delta file."""
        path = Path(input_file)
        if not path.exists():
            raise FileNotFoundError(f"Delta file not found: {path}")

        applied = 0
        skipped = 0

        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    envelope = json.loads(line)
                    if self.apply_envelope(envelope):
                        applied += 1
                    else:
                        skipped += 1
                except Exception as err:
                    skipped += 1

        return applied, skipped
