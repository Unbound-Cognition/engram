# zero-knowledge multi-device sync

synchronize sovereign memories across personal machines without exposing plaintext or vector embeddings to intermediate networks or cloud relays.

conforms to [spec/05-zero-knowledge-sync.md](https://github.com/Unbound-Cognition/spec/blob/main/spec/05-zero-knowledge-sync.md).

---

## how it works

most sync solutions require trusting a server with your unencrypted data. 

engram's sync protocol treats all networks, intermediate servers, and sync relays as untrusted:
1. **client-side encryption**: both the substantive markdown content and the dense floating-point vector arrays are packed into a JSON payload and encrypted locally using 256-bit ChaCha20-Poly1305 (AEAD).
2. **zero-plaintext leak**: the relay only ever sees random nonces, base64 ciphertext, and authentication tags. no model or relay can search or reconstruct your vector space without the symmetric key.
3. **conflict resolution**: every update increments a local Lamport sequence clock. simple edits resolve via Last-Write-Wins (LWW). deletions propagate as authenticated tombstones to prevent deleted memories from reappearing. concurrent substantive edits preserve both records via causal links (`causal_parent`).

---

## 1. key setup

all devices sharing the same memory store must share the same 256-bit symmetric master key.

### generate a key on device A
```bash
engram sync keygen
```

this creates a 32-byte key at `~/.config/engram/sync.key` with `0600` file permissions and prints the base64 string:

```text
Generated new 256-bit ChaCha20-Poly1305 sync key:
File: /Users/ari/.config/engram/sync.key (mode 0600)
Key:  a1b2c3d4...
```

### configure device B
on your second machine (e.g. VPS or desktop), either:
1. paste the key into `~/.config/engram/sync.key` and run `chmod 600 ~/.config/engram/sync.key`.
2. or export the environment variable:
   ```bash
   export ENGRAM_SYNC_KEY="a1b2c3d4..."
   ```

---

## 2. direct network sync (tailscale / local network)

if your machines can reach each other over a local network or Tailscale mesh, they can synchronize directly over HTTP.

### check status
```bash
engram sync status
```
outputs your local device ID, sequence clock, and configured peers:
```text
device_id: aris-macbook-air-7a2f1b0c
sequence:  42
key_path:  /Users/ari/.config/engram/sync.key
peers:     none
```

### add a peer
```bash
engram sync peer add http://cute-vps:8420
```

### manual sync
```bash
# pull encrypted events from peer
engram sync pull

# push local encrypted events to peer
engram sync push
```

### automated background sync
when running `engram serve --web`, the daemon starts an automated background sync worker. if peers and a sync key are configured, the daemon pulls and pushes new encrypted deltas every 60 seconds.

---

## 3. offline delta export / import

if machines cannot connect over the network (e.g. air-gapped machine or USB transfer), sync using encrypted delta files:

### on device A: export
```bash
# export all events recorded since sequence 0 (or pass --since <seq>)
engram sync export delta.ndjson
```
this writes an NDJSON file where each line is an encrypted envelope. you can safely email this file or upload it to a public S3 bucket; without the master key, it is unreadable.

### on device B: import
```bash
engram sync import delta.ndjson
```
device B decrypts each envelope, applies changes to its local SQLite database, inserts vector embeddings, and advances its sequence clock.
