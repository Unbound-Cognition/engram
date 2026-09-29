"""Zero-knowledge encryption and envelope handling for sovereign memory replication.

Conforms to Open Cognitive Memory Specification (spec/05-zero-knowledge-sync.md).
Uses ChaCha20-Poly1305 authenticated encryption with random 96-bit nonces.
"""

from __future__ import annotations

import base64
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes


DEFAULT_KEY_PATH = Path("~/.config/engram/sync.key").expanduser()


def generate_sync_key() -> str:
    """Generate a high-entropy 256-bit ChaCha20-Poly1305 master key encoded as base64."""
    key = ChaCha20Poly1305.generate_key()
    return base64.b64encode(key).decode("ascii")


def derive_key(passphrase: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    """Derive a 256-bit key from a passphrase using PBKDF2-HMAC-SHA256."""
    if salt is None:
        salt = os.urandom(16)
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100_000,
    )
    key = kdf.derive(passphrase.encode("utf-8"))
    return key, salt


def load_sync_key(key_path: Path | None = None) -> bytes:
    """Load the sync encryption key from environment, specified path, or default config path."""
    env_key = os.environ.get("ENGRAM_SYNC_KEY")
    if env_key:
        return base64.b64decode(env_key.strip())

    path = (key_path or DEFAULT_KEY_PATH).expanduser()
    if not path.exists():
        raise FileNotFoundError(
            f"Sync key not found at {path}. Run `engram sync keygen` or set ENGRAM_SYNC_KEY."
        )

    content = path.read_text(encoding="utf-8").strip()
    return base64.b64decode(content)


def save_sync_key(key_b64: str, key_path: Path | None = None) -> Path:
    """Save a base64 sync key to disk with strict 0600 file permissions."""
    path = (key_path or DEFAULT_KEY_PATH).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key_b64.strip() + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def encrypt_envelope(
    payload: dict[str, Any],
    key: bytes,
    device_id: str,
    sequence: int,
    memory_id: str,
    operation: str = "upsert",
    key_id: str = "primary",
) -> dict[str, Any]:
    """Encrypt a payload dictionary into an authenticated sync envelope.

    Both text content and dense vector embeddings are packed inside the payload
    before encryption so zero plaintext or similarity data leaks to untrusted relays.
    """
    if len(key) != 32:
        raise ValueError(f"ChaCha20-Poly1305 requires a 32-byte key, got {len(key)}")

    json_bytes = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    nonce = os.urandom(12)

    chacha = ChaCha20Poly1305(key)
    # cryptography's encrypt returns ciphertext + 16-byte Poly1305 tag
    ct_with_tag = chacha.encrypt(nonce, json_bytes, None)
    ciphertext = ct_with_tag[:-16]
    tag = ct_with_tag[-16:]

    event_id = str(uuid.uuid4())
    now_ms = int(time.time() * 1000)

    return {
        "event_id": event_id,
        "memory_id": memory_id,
        "device_id": device_id,
        "sequence": sequence,
        "timestamp": now_ms,
        "operation": operation,
        "crypto": {
            "algorithm": "ChaCha20-Poly1305",
            "key_id": key_id,
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            "tag": base64.b64encode(tag).decode("ascii"),
        },
    }


def decrypt_envelope(envelope: dict[str, Any], key: bytes) -> dict[str, Any]:
    """Decrypt an authenticated sync envelope back into its payload dictionary.

    Raises an exception if the Poly1305 auth tag fails or key is invalid.
    """
    if len(key) != 32:
        raise ValueError(f"ChaCha20-Poly1305 requires a 32-byte key, got {len(key)}")

    crypto_meta = envelope.get("crypto", {})
    algorithm = crypto_meta.get("algorithm")
    if algorithm != "ChaCha20-Poly1305":
        raise ValueError(f"Unsupported encryption algorithm: {algorithm}")

    nonce = base64.b64decode(crypto_meta["nonce"])
    ciphertext = base64.b64decode(crypto_meta["ciphertext"])
    tag = base64.b64decode(crypto_meta["tag"])

    ct_with_tag = ciphertext + tag
    chacha = ChaCha20Poly1305(key)
    decrypted_bytes = chacha.decrypt(nonce, ct_with_tag, None)

    return json.loads(decrypted_bytes.decode("utf-8"))
