"""Zero-knowledge sync relay server for multi-device replication across NATs.

Operates as a completely untrusted mailbox for ChaCha20-Poly1305 encrypted envelopes.
Never requires, loads, or sees the private encryption key.

Zero third-party dependencies — built entirely on Python standard library.
"""

from __future__ import annotations

import argparse
import http.server
import json
import sqlite3
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any


class RelayStore:
    """SQLite-backed append-only store for encrypted envelopes."""

    def __init__(self, db_path: str = "relay.db") -> None:
        self.db_path = db_path
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.init_db()

    def init_db(self) -> None:
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS relay_events (
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
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_relay_seq ON relay_events (sequence ASC);"
            )

    def get_max_sequence(self) -> int:
        cur = self.conn.execute("SELECT COALESCE(MAX(sequence), 0) FROM relay_events;")
        row = cur.fetchone()
        return row[0] if row else 0

    def get_event_count(self) -> int:
        cur = self.conn.execute("SELECT COUNT(*) FROM relay_events;")
        row = cur.fetchone()
        return row[0] if row else 0

    def store_events(self, events: list[dict[str, Any]]) -> int:
        stored = 0
        with self.conn:
            for env in events:
                event_id = env.get("event_id")
                memory_id = env.get("memory_id")
                device_id = env.get("device_id")
                sequence = env.get("sequence", 0)
                timestamp = env.get("timestamp", 0)
                operation = env.get("operation", "upsert")
                if not (event_id and memory_id and device_id):
                    continue

                envelope_json = json.dumps(env, separators=(",", ":"), ensure_ascii=False)
                cur = self.conn.execute(
                    """
                    INSERT INTO relay_events (
                        event_id, memory_id, device_id, sequence, timestamp, operation, envelope_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_id) DO NOTHING;
                    """,
                    (event_id, memory_id, device_id, sequence, timestamp, operation, envelope_json, time.time()),
                )
                if cur.rowcount > 0:
                    stored += 1
        return stored

    def get_events_since(self, since: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            """
            SELECT envelope_json FROM relay_events
            WHERE sequence > ?
            ORDER BY sequence ASC
            LIMIT ?;
            """,
            (since, limit),
        )
        rows = cur.fetchall()
        return [json.loads(row[0]) for row in rows]


class RelayRequestHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler implementing the Open Cognitive Memory Sync Protocol."""

    store: RelayStore

    def log_message(self, format: str, *args: Any) -> None:
        # Standard clean log output
        sys.stderr.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {self.address_string()} - {format % args}\n")

    def send_json(self, status: int, data: Any) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        query = urllib.parse.parse_qs(parsed.query)

        if path in ("", "/health"):
            self.send_json(200, {
                "status": "ok",
                "service": "engram-zero-knowledge-relay",
                "version": "0.1.0",
                "events_count": self.store.get_event_count(),
            })
        elif path == "/api/sync/status":
            self.send_json(200, {
                "status": "ok",
                "relay": True,
                "device_id": "zero-knowledge-relay",
                "sequence": self.store.get_max_sequence(),
                "events_count": self.store.get_event_count(),
                "peers": [],
                "has_key": False,
            })
        elif path == "/api/sync/events":
            since = int(query.get("since", [0])[0])
            limit = min(int(query.get("limit", [500])[0]), 2000)
            events = self.store.get_events_since(since=since, limit=limit)
            self.send_json(200, {
                "status": "ok",
                "events": events,
                "count": len(events),
            })
        else:
            self.send_json(404, {"error": "not found", "path": path})

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        content_len = int(self.headers.get("Content-Length", 0))
        if content_len == 0:
            self.send_json(400, {"error": "empty body"})
            return

        try:
            body = json.loads(self.rfile.read(content_len).decode("utf-8"))
        except Exception as e:
            self.send_json(400, {"error": f"invalid json: {e}"})
            return

        if path == "/api/sync/events":
            events = body.get("events", [])
            if not isinstance(events, list):
                self.send_json(400, {"error": "events must be an array"})
                return

            stored = self.store.store_events(events)
            self.send_json(200, {
                "status": "ok",
                "received": len(events),
                "stored": stored,
            })
        else:
            self.send_json(404, {"error": "not found", "path": path})


def run_relay(host: str = "0.0.0.0", port: int = 8421, db_path: str = "relay.db") -> None:
    store = RelayStore(db_path)
    handler = type("ConfiguredRelayHandler", (RelayRequestHandler,), {"store": store})
    server = http.server.ThreadingHTTPServer((host, port), handler)
    print(f"Zero-Knowledge Sync Relay listening on http://{host}:{port}")
    print(f"Database: {db_path} ({store.get_event_count()} stored events)")
    print("Relay is completely blind to memory text and embeddings.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down relay.")
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an untrusted zero-knowledge replication relay.")
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8421, help="Port to listen on (default: 8421)")
    parser.add_argument("--db", default="relay.db", help="Path to SQLite event store (default: relay.db)")
    args = parser.parse_args()

    run_relay(host=args.host, port=args.port, db_path=args.db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
