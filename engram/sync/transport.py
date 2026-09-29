"""Network transports for direct P2P and relay synchronization.

Transports only ever transmit encrypted envelopes. Relays and network peers
never see unencrypted memory text or dense embedding vectors.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engram.sync.engine import SyncEngine


def pull_from_peer(peer_url: str, engine: SyncEngine, timeout: float = 10.0) -> tuple[int, int]:
    """Pull encrypted sync events from a remote peer or relay."""
    base = peer_url.rstrip("/")
    cur_seq = engine.get_sequence()
    url = f"{base}/api/sync/events?since={cur_seq}"

    req = urllib.request.Request(url, headers={"User-Agent": "Engram-Sync/1.0"})
    applied = 0
    skipped = 0

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
            payload = json.loads(data)
            events = payload.get("events", [])
            for env in events:
                if engine.apply_envelope(env):
                    applied += 1
                else:
                    skipped += 1
    except urllib.error.URLError as e:
        raise ConnectionError(f"Failed to connect to sync peer {peer_url}: {e.reason}") from e

    return applied, skipped


def push_to_peer(peer_url: str, engine: SyncEngine, timeout: float = 10.0) -> int:
    """Push local encrypted sync events to a remote peer or relay."""
    base = peer_url.rstrip("/")

    # Check peer's current sequence
    status_url = f"{base}/api/sync/status"
    req_status = urllib.request.Request(status_url, headers={"User-Agent": "Engram-Sync/1.0"})
    try:
        with urllib.request.urlopen(req_status, timeout=timeout) as resp:
            status_data = json.loads(resp.read().decode("utf-8"))
            peer_seq = status_data.get("sequence", 0)
    except Exception:
        peer_seq = 0

    from engram.sync import journal
    events = journal.get_events_since(engine.conn, since_sequence=peer_seq, limit=1000)
    if not events:
        return 0

    push_url = f"{base}/api/sync/events"
    body = json.dumps({"events": events}, separators=(",", ":")).encode("utf-8")
    req_push = urllib.request.Request(
        push_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Engram-Sync/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req_push, timeout=timeout) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            return res.get("received", len(events))
    except urllib.error.URLError as e:
        raise ConnectionError(f"Failed to push events to peer {peer_url}: {e.reason}") from e
