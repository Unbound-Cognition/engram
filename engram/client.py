"""Zero-dependency client SDK for Engram cognitive memory engines.

Requires only the Python standard library (urllib, json, typing).
Usable standalone or integrated into agent loops, scripts, and frameworks.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MemoryResult:
    """Represents a recalled cognitive memory item."""

    id: str
    content: str
    layer: str
    score: float
    memory_type: str | None = None
    tags: list[str] = field(default_factory=list)
    sources: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryResult:
        return cls(
            id=data.get("id", ""),
            content=data.get("content", ""),
            layer=data.get("layer", "episodic"),
            score=float(data.get("score", 0.0)),
            memory_type=data.get("memory_type"),
            tags=data.get("tags") or [],
            sources=data.get("sources") or {},
            metadata=data.get("metadata") or {},
        )


class EngramClient:
    """Lightweight HTTP client communicating with an Engram daemon."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8420",
        auth_token: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token
        self.timeout = timeout

    def _request(
        self,
        endpoint: str,
        method: str = "GET",
        query_params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        if query_params:
            clean_params = {k: v for k, v in query_params.items() if v is not None}
            if clean_params:
                url = f"{url}?{urllib.parse.urlencode(clean_params)}"

        headers = {
            "User-Agent": "EngramClient/1.0",
            "Accept": "application/json",
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        data = None
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(json_body).encode("utf-8")

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            try:
                err_json = json.loads(err_body)
                msg = err_json.get("detail", err_json.get("error", err_body))
            except Exception:
                msg = err_body or str(e.reason)
            raise ConnectionError(f"Engram server error [{e.code}]: {msg}") from e
        except urllib.error.URLError as e:
            raise ConnectionError(f"Could not reach Engram daemon at {self.base_url}: {e.reason}") from e

    def health(self) -> dict[str, Any]:
        """Check daemon health and database metrics."""
        return self._request("/api/health")

    def search(
        self,
        query: str,
        top_k: int = 5,
        layer: str | None = None,
    ) -> list[MemoryResult]:
        """Search stored memories using 5-channel hybrid recall."""
        params = {"q": query, "top_k": top_k}
        if layer and layer != "all":
            params["layer"] = layer
        data = self._request("/api/search", query_params=params)
        raw_results = data.get("results", [])
        return [MemoryResult.from_dict(r) for r in raw_results]

    def remember(
        self,
        content: str,
        layer: str = "semantic",
        memory_type: str = "fact",
        importance: float = 0.7,
        source_type: str = "agent",
    ) -> dict[str, Any]:
        """Store a new memory record with automatic surprise computation."""
        payload = {
            "content": content,
            "layer": layer,
            "memory_type": memory_type,
            "importance": importance,
            "source_type": source_type,
        }
        return self._request("/api/remember", method="POST", json_body=payload)

    def remember_decision(
        self,
        decision: str,
        rationale: str,
        context: str | None = None,
    ) -> dict[str, Any]:
        """Record an architectural decision and rationale into procedural memory."""
        body = f"DECISION: {decision.strip()}\n\nRATIONALE: {rationale.strip()}"
        if context:
            body += f"\n\nCONTEXT: {context.strip()}"
        return self.remember(body, layer="procedural", memory_type="decision", importance=0.85)

    def remember_error(
        self,
        error: str,
        fix: str,
        context: str | None = None,
    ) -> dict[str, Any]:
        """Record an error signature and verified fix into procedural memory."""
        body = f"ERROR: {error.strip()}\n\nFIX: {fix.strip()}"
        if context:
            body += f"\n\nCONTEXT: {context.strip()}"
        return self.remember(body, layer="procedural", memory_type="error", importance=0.85)

    def remember_negative(
        self,
        assertion: str,
        context: str | None = None,
    ) -> dict[str, Any]:
        """Record explicit negative knowledge to block hallucinated patterns."""
        body = f"DO NOT: {assertion.strip()}"
        if context:
            body += f"\n\nCONTEXT: {context.strip()}"
        return self.remember(body, layer="procedural", memory_type="negative", importance=0.9)

    def checkpoint(
        self,
        task: str,
        summary: str,
        decisions: list[str] | None = None,
        next_steps: list[str] | None = None,
        blockers: list[str] | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Save a structured session handoff so work can resume across models or sessions."""
        payload = {
            "session_id": session_id,
            "summary": summary,
            "metadata": {
                "task": task,
                "decisions": decisions or [],
                "next_steps": next_steps or [],
                "blockers": blockers or [],
                "project_id": project_id or "",
            },
        }
        return self._request("/api/session-handoffs", method="POST", json_body=payload)

    def list_checkpoints(self, limit: int = 10) -> list[dict[str, Any]]:
        """Retrieve recent session handoffs."""
        data = self._request("/api/session-handoffs", query_params={"limit": limit})
        return data.get("handoffs", [])

    def sync_status(self) -> dict[str, Any]:
        """Query zero-knowledge replication status, device ID, and Lamport sequence."""
        return self._request("/api/sync/status")

    def trigger_sync(self) -> dict[str, Any]:
        """Initiate an immediate replication pass across all configured peer nodes."""
        return self._request("/api/sync/trigger", method="POST")

    def update_peers(self, peer_url: str, action: str = "add") -> list[str]:
        """Register or deregister a sync peer URL."""
        res = self._request("/api/sync/peers", method="POST", json_body={"peer": peer_url, "action": action})
        return res.get("peers", [])
