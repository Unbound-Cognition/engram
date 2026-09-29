"""CLI interface for zero-knowledge synchronization."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from engram.config import Config
from engram.store import Store
from engram.sync import crypto, journal
from engram.sync.engine import SyncEngine
from engram.sync.transport import pull_from_peer, push_to_peer


def register_sync_subparsers(subparsers: argparse._SubParsersAction) -> None:
    """Register sync commands on the root engram CLI."""
    sync_parser = subparsers.add_parser("sync", help="Zero-knowledge multi-device memory replication")
    sync_subs = sync_parser.add_subparsers(dest="sync_action", required=True)

    # keygen
    key_p = sync_subs.add_parser("keygen", help="Generate or display a ChaCha20-Poly1305 master sync key")
    key_p.add_argument("--force", action="store_true", help="Overwrite existing key file")
    key_p.add_argument("--path", type=Path, default=None, help="Custom path for sync.key")

    # status
    sync_subs.add_parser("status", help="Show local sync state, sequence clock, and peers")

    # export
    exp_p = sync_subs.add_parser("export", help="Export encrypted replication delta log to file")
    exp_p.add_argument("file", nargs="?", default="engram-sync-delta.ndjson", help="Output file path")
    exp_p.add_argument("--since", type=int, default=0, help="Export events after this sequence number")

    # import
    imp_p = sync_subs.add_parser("import", help="Import encrypted replication delta log from file")
    imp_p.add_argument("file", help="Input delta file path")

    # peer
    peer_p = sync_subs.add_parser("peer", help="Manage synchronization peers")
    peer_subs = peer_p.add_subparsers(dest="peer_action", required=True)
    peer_subs.add_parser("list", help="List configured peer endpoints")
    peer_add = peer_subs.add_parser("add", help="Add a new sync peer endpoint")
    peer_add.add_argument("url", help="Peer base URL (e.g. http://100.64.0.1:8420)")
    peer_rm = peer_subs.add_parser("remove", help="Remove a peer endpoint")
    peer_rm.add_argument("url", help="Peer base URL")

    # pull
    pull_p = sync_subs.add_parser("pull", help="Pull encrypted events from peer")
    pull_p.add_argument("url", nargs="?", default=None, help="Peer URL (uses configured peer if omitted)")

    # push
    push_p = sync_subs.add_parser("push", help="Push local encrypted events to peer")
    push_p.add_argument("url", nargs="?", default=None, help="Peer URL (uses configured peer if omitted)")


def handle_sync_cli(args: argparse.Namespace) -> int:
    """Handle engram sync subcommands."""
    action = args.sync_action

    if action == "keygen":
        key_path = (args.path or crypto.DEFAULT_KEY_PATH).expanduser()
        if key_path.exists() and not args.force:
            print(f"Sync key already exists at: {key_path}")
            print("Use --force to overwrite, or copy this key to other devices:")
            print(key_path.read_text().strip())
            return 0

        key_b64 = crypto.generate_sync_key()
        saved = crypto.save_sync_key(key_b64, key_path)
        print(f"Generated new 256-bit ChaCha20-Poly1305 sync key:")
        print(f"File: {saved} (mode 0600)")
        print(f"Key:  {key_b64}")
        print("\nCopy this key to your other devices (~/.config/engram/sync.key or ENGRAM_SYNC_KEY env).")
        return 0

    # Commands requiring store & key
    cfg = Config.load(getattr(args, "config", None))
    store = Store(cfg)

    try:
        key = crypto.load_sync_key()
    except FileNotFoundError:
        print("error: sync key not found.", file=sys.stderr)
        print("run `engram sync keygen` to generate one first.", file=sys.stderr)
        return 1

    engine = SyncEngine(store, key=key)

    if action == "status":
        seq = engine.get_sequence()
        dev_id = engine.get_device_id()
        peers = journal.get_peers(engine.conn)
        print(f"device_id: {dev_id}")
        print(f"sequence:  {seq}")
        print(f"key_path:  {crypto.DEFAULT_KEY_PATH}")
        print(f"peers:     {', '.join(peers) if peers else 'none'}")
        return 0

    if action == "export":
        out_file = Path(args.file)
        count = engine.export_delta(out_file, since_sequence=args.since)
        print(f"exported {count} encrypted sync event(s) to {out_file}")
        return 0

    if action == "import":
        in_file = Path(args.file)
        if not in_file.exists():
            print(f"error: file not found: {in_file}", file=sys.stderr)
            return 1
        applied, skipped = engine.import_delta(in_file)
        print(f"imported {applied} event(s) ({skipped} skipped/duplicate)")
        return 0

    if action == "peer":
        peers = journal.get_peers(engine.conn)
        if args.peer_action == "list":
            if not peers:
                print("no sync peers configured.")
            else:
                for p in peers:
                    print(f"- {p}")
            return 0
        elif args.peer_action == "add":
            url = args.url.rstrip("/")
            if url not in peers:
                peers.append(url)
                journal.save_peers(engine.conn, peers)
            print(f"added peer: {url}")
            return 0
        elif args.peer_action == "remove":
            url = args.url.rstrip("/")
            if url in peers:
                peers.remove(url)
                journal.save_peers(engine.conn, peers)
            print(f"removed peer: {url}")
            return 0

    if action in ("pull", "push"):
        target_url = args.url
        if not target_url:
            peers = journal.get_peers(engine.conn)
            if not peers:
                print("error: no peer specified and no peers configured.", file=sys.stderr)
                print("use `engram sync peer add <url>` or pass URL directly.", file=sys.stderr)
                return 1
            target_url = peers[0]

        if action == "pull":
            applied, skipped = pull_from_peer(target_url, engine)
            print(f"pulled from {target_url}: {applied} applied, {skipped} skipped.")
            return 0
        elif action == "push":
            pushed = push_to_peer(target_url, engine)
            print(f"pushed {pushed} event(s) to {target_url}.")
            return 0

    return 0
