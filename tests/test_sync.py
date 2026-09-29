"""Unit tests for zero-knowledge synchronization and replication."""

import json
import sqlite3
import tempfile
from pathlib import Path

import numpy as np
import pytest

from engram.store import Memory, Store
from engram.sync import crypto, journal
from engram.sync.engine import SyncEngine


def test_crypto_envelope_roundtrip():
    key_b64 = crypto.generate_sync_key()
    assert len(key_b64) == 44  # base64 32 bytes

    import base64
    key = base64.b64decode(key_b64)

    payload = {
        "memory": {
            "id": "019dca9f-7d01-7c33-981e-722195058d48",
            "content": "Secret sovereign memory content that should never leak to cloud relays.",
            "layer": "procedural",
            "importance": 0.95,
        },
        "embedding": [0.12, -0.45, 0.78, 0.01],
    }

    envelope = crypto.encrypt_envelope(
        payload=payload,
        key=key,
        device_id="test-macbook",
        sequence=1,
        memory_id="019dca9f-7d01-7c33-981e-722195058d48",
        operation="upsert",
    )

    # Check envelope schema properties
    assert envelope["event_id"]
    assert envelope["memory_id"] == "019dca9f-7d01-7c33-981e-722195058d48"
    assert envelope["device_id"] == "test-macbook"
    assert envelope["sequence"] == 1
    assert envelope["crypto"]["algorithm"] == "ChaCha20-Poly1305"
    assert "ciphertext" in envelope["crypto"]
    assert "tag" in envelope["crypto"]
    assert "nonce" in envelope["crypto"]

    # Ensure ciphertext does NOT contain plaintext content
    raw_json = json.dumps(envelope)
    assert "Secret sovereign memory" not in raw_json

    # Decrypt and verify exact match
    decrypted = crypto.decrypt_envelope(envelope, key)
    assert decrypted["memory"]["content"] == payload["memory"]["content"]
    assert decrypted["embedding"] == payload["embedding"]


def test_crypto_wrong_key_fails():
    key1 = base64_key = crypto.generate_sync_key()
    import base64
    k1 = base64.b64decode(key1)
    k2 = base64.b64decode(crypto.generate_sync_key())

    payload = {"test": "data"}
    env = crypto.encrypt_envelope(payload, k1, "dev1", 1, "mem1")

    with pytest.raises(Exception):
        crypto.decrypt_envelope(env, k2)


def test_journal_and_lamport_clock():
    with tempfile.NamedTemporaryFile() as tmp:
        conn = sqlite3.connect(tmp.name)
        journal.init_sync_tables(conn)

        dev_id = journal.get_device_id(conn)
        assert dev_id
        assert journal.get_current_sequence(conn) == 0

        seq1 = journal.next_sequence(conn)
        assert seq1 == 1

        seq2 = journal.next_sequence(conn)
        assert seq2 == 2

        # Record event
        dummy_env = {
            "event_id": "ev-1",
            "memory_id": "mem-1",
            "device_id": dev_id,
            "sequence": seq1,
            "timestamp": 1000,
            "operation": "upsert",
            "crypto": {},
        }
        journal.record_event(conn, dummy_env)
        assert journal.has_event(conn, "ev-1")
        assert not journal.has_event(conn, "ev-2")

        events = journal.get_events_since(conn, since_sequence=0)
        assert len(events) == 1
        assert events[0]["event_id"] == "ev-1"


def test_sync_engine_replication_flow():
    with tempfile.TemporaryDirectory() as tmpdir:
        dir_path = Path(tmpdir)
        db1_path = dir_path / "node1.db"
        db2_path = dir_path / "node2.db"

        import base64
        master_key = base64.b64decode(crypto.generate_sync_key())

        from engram.config import Config

        cfg1 = Config(db_path=str(db1_path))
        cfg2 = Config(db_path=str(db2_path))

        store1 = Store(cfg1)
        store1.init_db()

        store2 = Store(cfg2)
        store2.init_db()

        engine1 = SyncEngine(store1, key=master_key)
        engine2 = SyncEngine(store2, key=master_key)

        # 1. Create a memory in store1
        emb = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        mem = Memory(
            id="mem-sync-101",
            content="Testing cross-device replication with sovereign sync engine.",
            layer="procedural",
            memory_type="procedure",
            importance=0.9,
            embedding=emb,
        )
        store1.save_memory(mem)
        env = engine1.record_local_upsert(mem)

        # 2. Export delta from engine1
        delta_file = dir_path / "delta.ndjson"
        count = engine1.export_delta(delta_file, since_sequence=0)
        assert count == 1

        # 3. Import delta into engine2
        applied, skipped = engine2.import_delta(delta_file)
        assert applied == 1
        assert skipped == 0

        # Verify memory exists in store2
        mem_in_store2 = store2.get_memory("mem-sync-101")
        assert mem_in_store2 is not None
        assert mem_in_store2.content == mem.content
        assert mem_in_store2.layer == "procedural"
        assert np.allclose(mem_in_store2.embedding, emb)

        # 4. Duplicate import is skipped
        applied2, skipped2 = engine2.import_delta(delta_file)
        assert applied2 == 0
        assert skipped2 == 1

        # 5. Tombstone deletion flow
        del_env = engine1.record_local_delete("mem-sync-101")
        applied_del = engine2.apply_envelope(del_env)
        assert applied_del is True
        # Verify store2 reflected deletion
        mem_after_del = store2.get_memory("mem-sync-101")
        assert mem_after_del is None or mem_after_del.forgotten


def test_sync_cli_keygen_and_status(monkeypatch, tmp_path):
    import argparse
    from engram.sync.cli import handle_sync_cli

    key_file = tmp_path / "test_sync.key"

    # 1. keygen
    args = argparse.Namespace(sync_action="keygen", path=key_file, force=True)
    ret = handle_sync_cli(args)
    assert ret == 0
    assert key_file.exists()
    assert len(key_file.read_text().strip()) == 44

    # 2. status
    db_file = tmp_path / "test_store.db"
    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"db_path: {db_file}\n")

    monkeypatch.setenv("ENGRAM_SYNC_KEY", key_file.read_text().strip())
    args_status = argparse.Namespace(sync_action="status", config=str(config_file))
    ret_status = handle_sync_cli(args_status)
    assert ret_status == 0


def test_sync_web_routes(monkeypatch, tmp_path):
    from starlette.testclient import TestClient
    from engram.config import Config
    from engram.web.app import create_app

    key_b64 = crypto.generate_sync_key()
    monkeypatch.setenv("ENGRAM_SYNC_KEY", key_b64)

    db_path = tmp_path / "web_sync.db"
    cfg = Config(db_path=str(db_path))
    store = Store(cfg)
    store.init_db()

    app = create_app(cfg)
    client = TestClient(app)

    # 1. GET /api/sync/status
    res = client.get("/api/sync/status")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "device_id" in data
    assert data["sequence"] == 0

    # 2. GET /api/sync/events (empty)
    res_ev = client.get("/api/sync/events?since=0")
    assert res_ev.status_code == 200
    assert res_ev.json()["count"] == 0

    # 3. POST /api/sync/events
    import base64
    raw_key = base64.b64decode(key_b64)
    payload = {
        "memory": {
            "id": "mem-web-sync-1",
            "content": "Pushed via web sync route",
            "layer": "semantic",
            "importance": 0.8,
        },
        "embedding": None,
    }
    env = crypto.encrypt_envelope(payload, raw_key, "peer-box", 5, "mem-web-sync-1")

    res_post = client.post("/api/sync/events", json={"events": [env]})
    assert res_post.status_code == 200
    post_data = res_post.json()
    assert post_data["received"] == 1
    assert post_data["applied"] == 1

    # Verify memory was persisted
    stored = store.get_memory("mem-web-sync-1")
    assert stored is not None
    assert stored.content == "Pushed via web sync route"


