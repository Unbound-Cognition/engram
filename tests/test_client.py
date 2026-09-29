"""Tests for the zero-dependency EngramClient SDK."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from engram.client import EngramClient, MemoryResult


def test_memory_result_parsing():
    raw = {
        "id": "mem-123",
        "content": "Use LMDB backend for Knot DNS",
        "layer": "procedural",
        "score": 0.92,
        "memory_type": "decision",
        "tags": ["dns", "knot"],
        "sources": {"dense": 0.88, "bm25": 0.95},
        "metadata": {"project": "raya-dns"},
    }
    res = MemoryResult.from_dict(raw)
    assert res.id == "mem-123"
    assert res.content == "Use LMDB backend for Knot DNS"
    assert res.layer == "procedural"
    assert res.score == 0.92
    assert res.memory_type == "decision"
    assert res.tags == ["dns", "knot"]
    assert res.sources["bm25"] == 0.95


@patch("urllib.request.urlopen")
def test_client_search(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({
        "results": [
            {
                "id": "1",
                "content": "test recall",
                "layer": "procedural",
                "score": 0.85,
            }
        ]
    }).encode("utf-8")
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    client = EngramClient("http://localhost:8420")
    results = client.search("test", top_k=1, layer="procedural")

    assert len(results) == 1
    assert results[0].id == "1"
    assert results[0].layer == "procedural"
    assert results[0].score == 0.85


@patch("urllib.request.urlopen")
def test_client_remember_helpers(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"id": "new-1", "status": "stored"}).encode("utf-8")
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    client = EngramClient("http://localhost:8420")

    # 1. remember_decision
    res1 = client.remember_decision("Switch to LMDB", "Fast concurrent reads")
    assert res1["status"] == "stored"

    # 2. remember_error
    res2 = client.remember_error("SIGSEGV on close", "Flush socket buffer first")
    assert res2["status"] == "stored"

    # 3. remember_negative
    res3 = client.remember_negative("Never run FUSE over Steam library")
    assert res3["status"] == "stored"


@patch("urllib.request.urlopen")
def test_client_sync_helpers(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({
        "status": "ok",
        "device_id": "test-mac",
        "sequence": 42,
        "peers": [],
        "has_key": True,
    }).encode("utf-8")
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    client = EngramClient("http://localhost:8420")
    status = client.sync_status()
    assert status["device_id"] == "test-mac"
    assert status["sequence"] == 42
