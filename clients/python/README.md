# engram-client

Zero-dependency Python client SDK for [Engram](https://github.com/Unbound-Cognition/engram) cognitive memory engines.

Requires only the Python standard library (`urllib`, `json`, `dataclasses`). No heavy dependencies (no PyTorch, MLX, or local transformers needed).

## Installation

```bash
pip install engram-client
```

## Quick Start

```python
from engram_client import EngramClient

# Connect to local or remote daemon (defaults to http://127.0.0.1:8420)
client = EngramClient("http://127.0.0.1:8420")

# Check status
health = client.health()
print(f"Connected to Engram. Stored memories: {health['memories']['total']}")

# Query memories across hybrid search signals
results = client.search("database connection pool timeout", top_k=5, layer="procedural")
for item in results:
    print(f"[{item.layer}] (score: {item.score:.3f}): {item.content}")

# Store new context
mem_id = client.remember(
    content="Always enable keepalive on postgres pool connections in Docker",
    layer="procedural",
    tags=["postgres", "docker", "infra"]
)

# Store decision with rationale
client.remember_decision(
    decision="Use ChaCha20-Poly1305 for replication event encryption",
    rationale="Hardware-accelerated and constant-time on mobile and desktop without AES-NI requirements",
    alternatives=["AES-256-GCM"],
    tags=["crypto", "sync"]
)

# Save agent session handoff
client.save_handoff(
    session_id="agent-run-42",
    summary="Configured multi-peer replication mesh",
    task="Deploy zero-knowledge sync relay",
    decisions=["Use SQLite for untrusted relay mailbox"],
    next_steps=["Test NAT traversal with STUN/relay fallback"]
)
```

## License

MIT
