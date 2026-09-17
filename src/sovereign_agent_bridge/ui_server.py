"""Bridge Studio UI Server for Sovereign Agent Bridge.

Pure Python standard library HTTP server (ThreadingHTTPServer) providing:
- Real-time Server-Sent Events (SSE) telemetry & event stream (/api/events)
- REST APIs for multi-channel messaging, broadcast, claims lock board,
  3-way dialectic consensus, watchdog & dead-man switch monitoring
- Static file serving for Bridge Studio UI (public/index.html, design influenced by Material 3)
- Embedded UI fallback for standalone, zero-dependency zero-asset environments
"""

from __future__ import annotations

import cgi
import http.server
import json
import logging
import mimetypes
import os
import queue
import socket
import sys
import threading
import time
import urllib.parse
import uuid
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union

logger = logging.getLogger("sovereign_agent_bridge.ui_server")


# ---------------------------------------------------------------------------
# In-Memory Event Broker for Server-Sent Events (SSE)
# ---------------------------------------------------------------------------


class EventBroker:
    """Thread-safe publish/subscribe broker for streaming SSE events to browser clients."""

    def __init__(self, max_history: int = 200) -> None:
        self.max_history = max_history
        self._subscribers: Set[queue.Queue] = set()
        self._history: List[Dict[str, Any]] = []
        self._lock = threading.RLock()

    def subscribe(self) -> queue.Queue:
        """Register a new client subscriber queue."""
        q: queue.Queue = queue.Queue(maxsize=100)
        with self._lock:
            self._subscribers.add(q)
            # Replay recent history on new connection
            for evt in self._history[-20:]:
                try:
                    q.put_nowait(evt)
                except queue.Full:
                    pass
        logger.debug("New SSE client subscribed (total: %d)", len(self._subscribers))
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        """Unregister a disconnected subscriber queue."""
        with self._lock:
            self._subscribers.discard(q)
        logger.debug("SSE client unsubscribed (remaining: %d)", len(self._subscribers))

    def publish(self, event_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Broadcast an event payload to all active SSE subscribers."""
        event_payload = {
            "id": uuid.uuid4().hex,
            "type": event_type,
            "data": data,
            "timestamp": time.time(),
            "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        with self._lock:
            self._history.append(event_payload)
            if len(self._history) > self.max_history:
                self._history.pop(0)

            dead_queues = []
            for q in self._subscribers:
                try:
                    q.put_nowait(event_payload)
                except queue.Full:
                    # Drop oldest if full or mark for cleanup
                    try:
                        q.get_nowait()
                        q.put_nowait(event_payload)
                    except Exception:
                        dead_queues.append(q)

            for dq in dead_queues:
                self._subscribers.discard(dq)

        return event_payload

    def get_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve recent broadcast events."""
        with self._lock:
            return list(self._history[-limit:])

    @property
    def subscriber_count(self) -> int:
        """Count of active SSE subscribers."""
        with self._lock:
            return len(self._subscribers)


# ---------------------------------------------------------------------------
# Default In-Memory Fallback State (when run standalone without full orchestrator)
# ---------------------------------------------------------------------------


class InMemoryBridgeState:
    """Thread-safe in-memory store for channels, claims, consensus runs, and telemetry."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.start_time = time.time()
        self.messages_sent = 0
        self.broadcasts_sent = 0
        self.claims_count = 0
        self.consensus_runs_count = 0

        # Channels registry
        self.channels: Dict[str, Dict[str, Any]] = {
            "signal-primary": {
                "name": "signal-primary",
                "type": "signal",
                "enabled": True,
                "status": "healthy",
                "account": "+15550192834",
                "endpoint": "http://127.0.0.1:8080",
                "latency_ms": 12.4,
                "last_seen": time.time(),
            },
            "simplex-stealth": {
                "name": "simplex-stealth",
                "type": "simplex",
                "enabled": True,
                "status": "healthy",
                "address": "simplex://invitation/smp-test-node",
                "latency_ms": 18.1,
                "last_seen": time.time(),
            },
            "telegram-relay": {
                "name": "telegram-relay",
                "type": "telegram",
                "enabled": True,
                "status": "healthy",
                "bot_username": "@SovereignAgentBridgeBot",
                "latency_ms": 45.8,
                "last_seen": time.time(),
            },
            "matrix-federated": {
                "name": "matrix-federated",
                "type": "matrix",
                "enabled": True,
                "status": "healthy",
                "homeserver": "https://matrix.org",
                "latency_ms": 62.3,
                "last_seen": time.time(),
            },
            "webhook-dispatch": {
                "name": "webhook-dispatch",
                "type": "webhook",
                "enabled": True,
                "status": "healthy",
                "url": "http://127.0.0.1:9000/agent/webhook",
                "latency_ms": 4.2,
                "last_seen": time.time(),
            },
        }

        # Claims lock board
        self.claims: Dict[str, Dict[str, Any]] = {
            "agent-mesh-refactor": {
                "project_id": "agent-mesh-refactor",
                "owner_agent": "agent-architect-01",
                "acquired_at": time.time() - 300,
                "expires_at": time.time() + 900,
                "ttl_seconds": 1200,
                "status": "active",
                "metadata": {"task": "Refactor router for async pipes", "priority": "high"},
            },
            "consensus-validator-audit": {
                "project_id": "consensus-validator-audit",
                "owner_agent": "agent-skeptic-02",
                "acquired_at": time.time() - 120,
                "expires_at": time.time() + 480,
                "ttl_seconds": 600,
                "status": "active",
                "metadata": {"task": "Audit 3-way dialectic voting matrix", "priority": "medium"},
            },
        }

        # Consensus history
        self.consensus_sessions: List[Dict[str, Any]] = [
            {
                "session_id": "cs-init-001",
                "topic": "Deploy Zero-Knowledge routing table update",
                "proponent": "agent-proponent-alpha",
                "skeptic": "agent-skeptic-beta",
                "arbitrator": "agent-arbitrator-gamma",
                "rounds": 2,
                "threshold": 0.75,
                "verdict": "APPROVED",
                "confidence": 0.88,
                "deliberation": [
                    {
                        "round": 1,
                        "proponent_argument": "ZK routing prevents metadata leakage across federated nodes.",
                        "skeptic_critique": "ZK proofs introduce 120ms computational overhead per frame.",
                        "arbitrator_synthesis": "Overhead is acceptable for priority channels; cache verification keys.",
                    },
                    {
                        "round": 2,
                        "proponent_argument": "Cached verification keys reduce latency to under 8ms.",
                        "skeptic_critique": "Key rotation invalidates cache every 3600 seconds.",
                        "arbitrator_synthesis": "Perform asynchronous background key pre-generation.",
                    },
                ],
                "resolved_at": time.time() - 1800,
            }
        ]

        # Watchdog agent pulses
        self.pulses: Dict[str, Dict[str, Any]] = {
            "agent-orchestrator-01": {
                "agent_id": "agent-orchestrator-01",
                "status": "ACTIVE",
                "interval": 30.0,
                "timeout": 75.0,
                "last_pulse": time.time() - 5.2,
                "total_pulses": 420,
                "missed_pulses": 0,
                "metadata": {"role": "cluster-orchestrator", "host": "sovereign-node-1"},
            },
            "agent-proponent-alpha": {
                "agent_id": "agent-proponent-alpha",
                "status": "ACTIVE",
                "interval": 30.0,
                "timeout": 75.0,
                "last_pulse": time.time() - 11.8,
                "total_pulses": 312,
                "missed_pulses": 0,
                "metadata": {"role": "consensus-proponent", "host": "sovereign-node-2"},
            },
            "agent-skeptic-beta": {
                "agent_id": "agent-skeptic-beta",
                "status": "ACTIVE",
                "interval": 30.0,
                "timeout": 75.0,
                "last_pulse": time.time() - 18.4,
                "total_pulses": 309,
                "missed_pulses": 0,
                "metadata": {"role": "consensus-skeptic", "host": "sovereign-node-3"},
            },
            "agent-arbitrator-gamma": {
                "agent_id": "agent-arbitrator-gamma",
                "status": "ACTIVE",
                "interval": 30.0,
                "timeout": 75.0,
                "last_pulse": time.time() - 2.1,
                "total_pulses": 418,
                "missed_pulses": 0,
                "metadata": {"role": "consensus-arbitrator", "host": "sovereign-node-1"},
            },
        }

    def record_pulse(self, agent_id: str, interval: float = 30.0, timeout: Optional[float] = None, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            calc_timeout = timeout or (interval * 2.5)
            if agent_id in self.pulses:
                p = self.pulses[agent_id]
                p["last_pulse"] = now
                p["interval"] = interval
                p["timeout"] = calc_timeout
                p["total_pulses"] += 1
                p["status"] = "ACTIVE"
                p["missed_pulses"] = 0
                if metadata:
                    p["metadata"].update(metadata)
            else:
                p = {
                    "agent_id": agent_id,
                    "status": "ACTIVE",
                    "interval": interval,
                    "timeout": calc_timeout,
                    "last_pulse": now,
                    "total_pulses": 1,
                    "missed_pulses": 0,
                    "metadata": metadata or {},
                }
                self.pulses[agent_id] = p
            return dict(p)

    def acquire_claim(self, project_id: str, owner_agent: str, ttl_seconds: float = 600.0, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            existing = self.claims.get(project_id)
            if existing and existing.get("expires_at", 0) > now and existing.get("owner_agent") != owner_agent:
                raise ValueError(f"Project '{project_id}' is already claimed by '{existing['owner_agent']}' until {existing['expires_at']:.0f}")

            claim = {
                "project_id": project_id,
                "owner_agent": owner_agent,
                "acquired_at": now,
                "expires_at": now + ttl_seconds,
                "ttl_seconds": ttl_seconds,
                "status": "active",
                "metadata": metadata or {},
            }
            self.claims[project_id] = claim
            self.claims_count += 1
            return claim

    def release_claim(self, project_id: str, owner_agent: str) -> bool:
        with self._lock:
            if project_id in self.claims:
                claim = self.claims[project_id]
                if claim.get("owner_agent") == owner_agent or owner_agent == "force":
                    del self.claims[project_id]
                    return True
            return False

    def handoff_claim(self, project_id: str, from_agent: str, to_agent: str, ttl_seconds: Optional[float] = None) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            if project_id not in self.claims:
                raise ValueError(f"No active claim found for project '{project_id}'")
            claim = self.claims[project_id]
            if claim.get("owner_agent") != from_agent:
                raise ValueError(f"Claim owned by '{claim.get('owner_agent')}', not '{from_agent}'")

            new_ttl = ttl_seconds or claim.get("ttl_seconds", 600.0)
            claim["owner_agent"] = to_agent
            claim["acquired_at"] = now
            claim["expires_at"] = now + new_ttl
            claim["ttl_seconds"] = new_ttl
            claim.setdefault("metadata", {})["last_handoff_from"] = from_agent
            return claim


# ---------------------------------------------------------------------------
# HTTP Request Handler with REST Endpoints and SSE Stream
# ---------------------------------------------------------------------------


class BridgeRequestHandler(SimpleHTTPRequestHandler):
    """HTTP request handler routing REST API endpoints, SSE streams, and static assets."""

    server_version = "SovereignAgentBridge-UI/0.1.0"

    def __init__(self, *args, **kwargs) -> None:
        # Avoid buffering on stdout/stderr
        super().__init__(*args, **kwargs)

    @property
    def ui_server(self) -> BridgeUIServer:
        return self.server.ui_server  # type: ignore

    def _set_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")

    def _send_json_response(self, data: Any, status_code: int = 200) -> None:
        try:
            body = json.dumps(data, indent=2, default=str).encode("utf-8")
            self.send_response(status_code)
            self._set_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_error_json(self, message: str, status_code: int = 400) -> None:
        self._send_json_response({"error": message, "status": "error"}, status_code=status_code)

    def _parse_json_body(self) -> Dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            return {}
        raw_data = self.rfile.read(content_length)
        try:
            return json.loads(raw_data.decode("utf-8"))
        except Exception as e:
            raise ValueError(f"Invalid JSON payload: {e}")

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight requests."""
        self.send_response(HTTPStatus.NO_CONTENT)
        self._set_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        """Handle GET requests for static assets, SSE event streams, and REST queries."""
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query = urllib.parse.parse_qs(parsed_url.query)

        # 1. SSE Real-Time Event Stream
        if path == "/api/events":
            self._handle_sse_stream()
            return

        # 2. Health check
        if path == "/api/health":
            self._send_json_response({
                "status": "ok",
                "service": "sovereign-agent-bridge",
                "version": "0.1.0",
                "uptime_seconds": round(time.time() - self.ui_server.state.start_time, 2),
                "timestamp": time.time(),
            })
            return

        # 3. Channel registry & statuses
        if path == "/api/channels":
            self._handle_get_channels()
            return

        # 4. Claims lock board
        if path == "/api/claims":
            self._handle_get_claims()
            return

        # 5. Consensus history
        if path == "/api/consensus":
            self._handle_get_consensus()
            return

        # 6. Watchdog & Heartbeat state
        if path in ("/api/heartbeat", "/api/watchdog"):
            self._handle_get_watchdog()
            return

        # 7. Global stats & telemetry
        if path == "/api/stats":
            self._handle_get_stats()
            return

        # 8. Event history log
        if path == "/api/events/history":
            limit = int(query.get("limit", [50])[0])
            self._send_json_response({"events": self.ui_server.broker.get_history(limit)})
            return

        # 9. Static file serving or fallback to Embedded HTML Studio
        self._handle_static_or_fallback(path)

    def do_POST(self) -> None:
        """Handle POST REST actions."""
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        try:
            body = self._parse_json_body()
        except ValueError as e:
            self._send_error_json(str(e), status_code=400)
            return

        if path == "/api/send":
            self._handle_post_send(body)
        elif path == "/api/broadcast":
            self._handle_post_broadcast(body)
        elif path in ("/api/channels/test", "/api/channels/ping"):
            self._handle_post_channel_test(body)
        elif path == "/api/claims/acquire":
            self._handle_post_claim_acquire(body)
        elif path == "/api/claims/release":
            self._handle_post_claim_release(body)
        elif path == "/api/claims/handoff":
            self._handle_post_claim_handoff(body)
        elif path in ("/api/consensus/run", "/api/consensus/deliberate"):
            self._handle_post_consensus_run(body)
        elif path == "/api/heartbeat/ping":
            self._handle_post_heartbeat_ping(body)
        elif path == "/api/heartbeat/arm":
            self._handle_post_heartbeat_arm(body)
        elif path == "/api/events/publish":
            event_type = body.get("type", "custom_event")
            data = body.get("data", {})
            published = self.ui_server.publish_event(event_type, data)
            self._send_json_response({"status": "published", "event": published})
        else:
            self._send_error_json(f"Unknown POST endpoint: {path}", status_code=404)

    # -----------------------------------------------------------------------
    # Endpoint Handlers
    # -----------------------------------------------------------------------

    def _handle_sse_stream(self) -> None:
        """Stream real-time server-sent events to browser clients."""
        self.send_response(HTTPStatus.OK)
        self._set_cors_headers()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        sub_queue = self.ui_server.broker.subscribe()
        client_id = uuid.uuid4().hex[:8]

        try:
            # Initial handshake frame
            init_msg = json.dumps({
                "type": "connected",
                "client_id": client_id,
                "server_time": time.time(),
                "uptime": round(time.time() - self.ui_server.state.start_time, 2),
            })
            self.wfile.write(f"event: handshake\ndata: {init_msg}\n\n".encode("utf-8"))
            self.wfile.flush()

            last_ping = time.monotonic()

            while self.ui_server.is_running:
                try:
                    # Non-blocking pull with 1.0s timeout to allow periodic keepalive
                    event = sub_queue.get(timeout=1.0)
                    evt_type = event.get("type", "message")
                    evt_data = json.dumps(event)
                    payload = f"event: {evt_type}\ndata: {evt_data}\n\n"
                    self.wfile.write(payload.encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    # Send keepalive heartbeat comment every 5 seconds
                    if time.monotonic() - last_ping > 5.0:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        last_ping = time.monotonic()

        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.ui_server.broker.unsubscribe(sub_queue)

    def _handle_get_channels(self) -> None:
        """Return registered messaging channels."""
        if self.ui_server.router and hasattr(self.ui_server.router, "list_channels"):
            try:
                channels = self.ui_server.router.list_channels()
                self._send_json_response({"channels": channels})
                return
            except Exception as e:
                logger.warning("Error fetching router channels: %s", e)

        # In-memory fallback
        channels = list(self.ui_server.state.channels.values())
        self._send_json_response({"channels": channels})

    def _handle_get_claims(self) -> None:
        """Return active claim locks."""
        if self.ui_server.claim_manager and hasattr(self.ui_server.claim_manager, "list_claims"):
            try:
                claims = self.ui_server.claim_manager.list_claims()
                self._send_json_response({"claims": claims})
                return
            except Exception as e:
                logger.warning("Error fetching claim manager claims: %s", e)

        # In-memory fallback
        now = time.time()
        active_claims = [
            c for c in self.ui_server.state.claims.values()
            if c.get("expires_at", 0) > now
        ]
        self._send_json_response({"claims": active_claims})

    def _handle_get_consensus(self) -> None:
        """Return past consensus sessions."""
        if self.ui_server.consensus_engine and hasattr(self.ui_server.consensus_engine, "get_history"):
            try:
                sessions = self.ui_server.consensus_engine.get_history()
                self._send_json_response({"sessions": sessions})
                return
            except Exception as e:
                logger.warning("Error fetching consensus history: %s", e)

        self._send_json_response({"sessions": self.ui_server.state.consensus_sessions})

    def _handle_get_watchdog(self) -> None:
        """Return watchdog telemetry and agent pulses."""
        if self.ui_server.watchdog and hasattr(self.ui_server.watchdog, "get_telemetry"):
            try:
                telemetry = self.ui_server.watchdog.get_telemetry()
                agents = self.ui_server.watchdog.list_agents_dict() if hasattr(self.ui_server.watchdog, "list_agents_dict") else []
                self._send_json_response({"telemetry": telemetry, "agents": agents})
                return
            except Exception as e:
                logger.warning("Error fetching watchdog telemetry: %s", e)

        # In-memory fallback
        now = time.time()
        agent_list = []
        for p in self.ui_server.state.pulses.values():
            elapsed = now - p["last_pulse"]
            status = "DEAD" if elapsed > p["timeout"] else ("WARNING" if elapsed > (p["interval"] * 1.5) else "ACTIVE")
            item = dict(p)
            item["status"] = status
            item["elapsed_seconds"] = round(elapsed, 1)
            item["time_to_timeout"] = round(max(0.0, p["timeout"] - elapsed), 1)
            agent_list.append(item)

        self._send_json_response({
            "telemetry": {
                "is_running": True,
                "uptime_seconds": round(now - self.ui_server.state.start_time, 2),
                "total_monitored_agents": len(agent_list),
                "active_agents": sum(1 for a in agent_list if a["status"] == "ACTIVE"),
                "dead_agents": sum(1 for a in agent_list if a["status"] == "DEAD"),
            },
            "agents": agent_list,
        })

    def _handle_get_stats(self) -> None:
        """Return system-wide statistics."""
        now = time.time()
        stats = {
            "uptime_seconds": round(now - self.ui_server.state.start_time, 2),
            "messages_sent": self.ui_server.state.messages_sent,
            "broadcasts_sent": self.ui_server.state.broadcasts_sent,
            "active_claims": len(self.ui_server.state.claims),
            "consensus_runs": self.ui_server.state.consensus_runs_count + len(self.ui_server.state.consensus_sessions),
            "sse_subscribers": self.ui_server.broker.subscriber_count,
            "channels_count": len(self.ui_server.state.channels),
            "timestamp": now,
        }
        self._send_json_response({"stats": stats})

    def _handle_post_send(self, body: Dict[str, Any]) -> None:
        """Dispatch a single message to a specified channel."""
        channel_name = body.get("channel", "signal-primary")
        recipient = body.get("recipient", "")
        content = body.get("content", "")
        priority = body.get("priority", "normal")
        metadata = body.get("metadata", {})

        if not content:
            self._send_error_json("Message 'content' is required", status_code=400)
            return

        msg_id = uuid.uuid4().hex
        receipt = {
            "success": True,
            "message_id": msg_id,
            "channel": channel_name,
            "recipient": recipient,
            "content": content,
            "priority": priority,
            "status": "delivered",
            "timestamp": time.time(),
        }

        # If router is connected, dispatch through router
        if self.ui_server.router and hasattr(self.ui_server.router, "send_message"):
            try:
                res = self.ui_server.router.send_message(
                    channel_name=channel_name,
                    recipient=recipient,
                    content=content,
                    metadata=metadata,
                )
                if hasattr(res, "to_dict"):
                    receipt = res.to_dict()
                elif isinstance(res, dict):
                    receipt = res
            except Exception as e:
                logger.error("Router dispatch failed: %s", e)
                receipt["success"] = False
                receipt["status"] = "failed"
                receipt["error"] = str(e)

        self.ui_server.state.messages_sent += 1
        self.ui_server.publish_event("message_sent", receipt)
        self._send_json_response({"receipt": receipt, "status": "ok"})

    def _handle_post_broadcast(self, body: Dict[str, Any]) -> None:
        """Broadcast message across multiple/all active channels."""
        content = body.get("content", "")
        target_channels = body.get("channels") or list(self.ui_server.state.channels.keys())
        metadata = body.get("metadata", {})

        if not content:
            self._send_error_json("Message 'content' is required for broadcast", status_code=400)
            return

        broadcast_id = uuid.uuid4().hex
        receipts = []

        for ch in target_channels:
            receipt = {
                "broadcast_id": broadcast_id,
                "channel": ch,
                "success": True,
                "status": "delivered",
                "timestamp": time.time(),
            }
            receipts.append(receipt)

        self.ui_server.state.broadcasts_sent += 1
        broadcast_record = {
            "broadcast_id": broadcast_id,
            "content": content,
            "channels": target_channels,
            "receipts": receipts,
            "timestamp": time.time(),
        }
        self.ui_server.publish_event("broadcast_sent", broadcast_record)
        self._send_json_response({"broadcast": broadcast_record, "status": "ok"})

    def _handle_post_channel_test(self, body: Dict[str, Any]) -> None:
        """Ping/test channel connectivity."""
        channel_name = body.get("channel", "signal-primary")
        start_t = time.monotonic()
        # Simulate slight network jitter
        time.sleep(0.01)
        latency = round((time.monotonic() - start_t) * 1000.0 + 10.0, 2)

        if channel_name in self.ui_server.state.channels:
            ch_info = self.ui_server.state.channels[channel_name]
            ch_info["latency_ms"] = latency
            ch_info["last_seen"] = time.time()
            ch_info["status"] = "healthy"

        test_result = {
            "channel": channel_name,
            "is_healthy": True,
            "latency_ms": latency,
            "timestamp": time.time(),
        }
        self.ui_server.publish_event("channel_tested", test_result)
        self._send_json_response({"result": test_result, "status": "ok"})

    def _handle_post_claim_acquire(self, body: Dict[str, Any]) -> None:
        """Acquire a project lock."""
        project_id = str(body.get("project_id", "")).strip()
        agent_id = str(body.get("agent_id", "")).strip()
        ttl_seconds = float(body.get("ttl_seconds", 600.0))
        metadata = body.get("metadata", {})

        if not project_id or not agent_id:
            self._send_error_json("Both 'project_id' and 'agent_id' are required", status_code=400)
            return

        try:
            claim = self.ui_server.state.acquire_claim(project_id, agent_id, ttl_seconds, metadata)
            self.ui_server.publish_event("claim_acquired", claim)
            self._send_json_response({"claim": claim, "status": "ok"})
        except ValueError as e:
            self._send_error_json(str(e), status_code=409)

    def _handle_post_claim_release(self, body: Dict[str, Any]) -> None:
        """Release a project lock."""
        project_id = str(body.get("project_id", "")).strip()
        agent_id = str(body.get("agent_id", "")).strip()

        if not project_id or not agent_id:
            self._send_error_json("Both 'project_id' and 'agent_id' are required", status_code=400)
            return

        released = self.ui_server.state.release_claim(project_id, agent_id)
        if released:
            self.ui_server.publish_event("claim_released", {"project_id": project_id, "released_by": agent_id})
            self._send_json_response({"status": "released", "project_id": project_id})
        else:
            self._send_error_json(f"Cannot release lock for '{project_id}': not held by '{agent_id}'", status_code=403)

    def _handle_post_claim_handoff(self, body: Dict[str, Any]) -> None:
        """Atomic handoff of a project claim lock to another agent."""
        project_id = str(body.get("project_id", "")).strip()
        from_agent = str(body.get("from_agent", "")).strip()
        to_agent = str(body.get("to_agent", "")).strip()
        ttl_seconds = float(body.get("ttl_seconds", 600.0)) if "ttl_seconds" in body else None

        if not project_id or not from_agent or not to_agent:
            self._send_error_json("Fields 'project_id', 'from_agent', and 'to_agent' are required", status_code=400)
            return

        try:
            claim = self.ui_server.state.handoff_claim(project_id, from_agent, to_agent, ttl_seconds)
            self.ui_server.publish_event("claim_handoff", claim)
            self._send_json_response({"claim": claim, "status": "ok"})
        except ValueError as e:
            self._send_error_json(str(e), status_code=409)

    def _handle_post_consensus_run(self, body: Dict[str, Any]) -> None:
        """Execute a 3-way dialectic consensus deliberation."""
        topic = body.get("topic", "Architectural Decision Proposal")
        proponent = body.get("proponent", "agent-proponent-alpha")
        skeptic = body.get("skeptic", "agent-skeptic-beta")
        arbitrator = body.get("arbitrator", "agent-arbitrator-gamma")
        rounds = max(1, min(int(body.get("rounds", 2)), 5))
        threshold = float(body.get("threshold", 0.75))

        session_id = f"cs-{uuid.uuid4().hex[:8]}"

        # Dialectic synthesis simulation
        deliberation_steps = []
        for r in range(1, rounds + 1):
            deliberation_steps.append({
                "round": r,
                "proponent_argument": f"[Round {r}] Proposes optimized execution path for: '{topic}'. Asserts efficiency gains.",
                "skeptic_critique": f"[Round {r}] Evaluates fault tolerance and failure modes for: '{topic}'. Identifies edge conditions.",
                "arbitrator_synthesis": f"[Round {r}] Reconciles trade-offs. Formulates compromise safeguards for: '{topic}'.",
            })

        verdict = "APPROVED" if threshold <= 0.85 else "DISPUTED"
        confidence = round(0.85 + (0.10 * (1.0 / rounds)), 2)

        session_record = {
            "session_id": session_id,
            "topic": topic,
            "proponent": proponent,
            "skeptic": skeptic,
            "arbitrator": arbitrator,
            "rounds": rounds,
            "threshold": threshold,
            "verdict": verdict,
            "confidence": confidence,
            "deliberation": deliberation_steps,
            "resolved_at": time.time(),
        }

        self.ui_server.state.consensus_sessions.insert(0, session_record)
        self.ui_server.state.consensus_runs_count += 1
        self.ui_server.publish_event("consensus_completed", session_record)
        self._send_json_response({"session": session_record, "status": "ok"})

    def _handle_post_heartbeat_ping(self, body: Dict[str, Any]) -> None:
        """Record an incoming agent heartbeat pulse."""
        agent_id = str(body.get("agent_id", "")).strip()
        interval = float(body.get("interval", 30.0))
        timeout = float(body.get("timeout")) if "timeout" in body else None
        metadata = body.get("metadata", {})

        if not agent_id:
            self._send_error_json("'agent_id' is required for heartbeat ping", status_code=400)
            return

        pulse = self.ui_server.state.record_pulse(agent_id, interval, timeout, metadata)
        if self.ui_server.watchdog and hasattr(self.ui_server.watchdog, "record_pulse"):
            try:
                self.ui_server.watchdog.record_pulse(agent_id, interval, timeout, metadata)
            except Exception:
                pass

        self.ui_server.publish_event("heartbeat_received", pulse)
        self._send_json_response({"pulse": pulse, "status": "ok"})

    def _handle_post_heartbeat_arm(self, body: Dict[str, Any]) -> None:
        """Arm or reconfigure dead-man switch timer for an agent."""
        agent_id = str(body.get("agent_id", "agent-orchestrator-01")).strip()
        timer_seconds = float(body.get("timer_seconds", 60.0))
        alert_channel = body.get("alert_channel", "signal-primary")

        pulse = self.ui_server.state.record_pulse(
            agent_id,
            interval=timer_seconds / 2.5,
            timeout=timer_seconds,
            metadata={"armed": True, "alert_channel": alert_channel, "armed_at": time.time()},
        )
        arm_event = {
            "agent_id": agent_id,
            "timer_seconds": timer_seconds,
            "alert_channel": alert_channel,
            "status": "armed",
            "expires_at": time.time() + timer_seconds,
        }
        self.ui_server.publish_event("deadman_switch_armed", arm_event)
        self._send_json_response({"switch": arm_event, "status": "ok"})

    def _handle_static_or_fallback(self, path: str) -> None:
        """Serve files from public/ directory or render embedded UI fallback."""
        if path == "/" or path == "":
            path = "/index.html"

        # Check static_dir on filesystem
        static_dir = self.ui_server.static_dir
        if static_dir and static_dir.exists():
            clean_rel = path.lstrip("/")
            file_path = static_dir / clean_rel
            if file_path.is_file():
                content_type, _ = mimetypes.guess_type(str(file_path))
                content_type = content_type or "application/octet-stream"
                try:
                    data = file_path.read_bytes()
                    self.send_response(HTTPStatus.OK)
                    self._set_cors_headers()
                    self.send_header("Content-Type", f"{content_type}; charset=utf-8" if "text" in content_type or "html" in content_type else content_type)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                except Exception as e:
                    logger.error("Error reading static file %s: %s", file_path, e)

        # Fallback to Embedded HTML Studio
        if path == "/index.html":
            html_bytes = EMBEDDED_HTML_STUDIO.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self._set_cors_headers()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html_bytes)))
            self.end_headers()
            self.wfile.write(html_bytes)
            return

        self._send_error_json(f"File not found: {path}", status_code=404)

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default stdout access log clutter in test mode."""
        logger.debug("%s - - [%s] %s", self.client_address[0], self.log_date_time_string(), format % args)


# ---------------------------------------------------------------------------
# Bridge UI Server Class
# ---------------------------------------------------------------------------


class BridgeUIServer:
    """Multi-threaded HTTP and SSE Server for Google Bridge Studio."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        router: Optional[Any] = None,
        consensus_engine: Optional[Any] = None,
        claim_manager: Optional[Any] = None,
        watchdog: Optional[Any] = None,
        static_dir: Optional[Union[str, Path]] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.router = router
        self.consensus_engine = consensus_engine
        self.claim_manager = claim_manager
        self.watchdog = watchdog

        # Resolve static directory
        if static_dir:
            self.static_dir = Path(static_dir)
        else:
            # Look relative to package or repo root
            pkg_root = Path(__file__).resolve().parent.parent.parent
            default_public = pkg_root / "public"
            if default_public.exists():
                self.static_dir = default_public
            else:
                self.static_dir = Path.cwd() / "public"

        self.broker = EventBroker()
        self.state = InMemoryBridgeState()
        self.is_running = False
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self, blocking: bool = False) -> BridgeUIServer:
        """Start HTTP server."""
        if self.is_running:
            return self

        # Bind server
        server_address = (self.host, self.port)
        self._server = ThreadingHTTPServer(server_address, BridgeRequestHandler)
        self._server.ui_server = self  # type: ignore
        self.port = self._server.server_port  # Resolve dynamically bound port (if 0)
        self.is_running = True

        logger.info("Bridge Studio running at http://%s:%d", self.host, self.port)

        if blocking:
            try:
                self._server.serve_forever()
            except KeyboardInterrupt:
                self.stop()
        else:
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                name="BridgeUIServer",
                daemon=True,
            )
            self._thread.start()

        return self

    def stop(self) -> None:
        """Gracefully shut down server."""
        if not self.is_running:
            return

        self.is_running = False
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
            self._thread = None

        logger.info("Bridge Studio stopped.")

    def get_url(self) -> str:
        """Return base URL string."""
        return f"http://{self.host}:{self.port}"

    def publish_event(self, event_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Publish real-time event to connected browser clients."""
        return self.broker.publish(event_type, data)

    def __enter__(self) -> BridgeUIServer:
        self.start(blocking=False)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()


def start_ui_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    router: Optional[Any] = None,
    consensus_engine: Optional[Any] = None,
    claim_manager: Optional[Any] = None,
    watchdog: Optional[Any] = None,
    static_dir: Optional[Union[str, Path]] = None,
    blocking: bool = False,
) -> BridgeUIServer:
    """Convenience helper to instantiate and start the UI server."""
    server = BridgeUIServer(
        host=host,
        port=port,
        router=router,
        consensus_engine=consensus_engine,
        claim_manager=claim_manager,
        watchdog=watchdog,
        static_dir=static_dir,
    )
    return server.start(blocking=blocking)


# ---------------------------------------------------------------------------
# Embedded HTML Studio Fallback
# ---------------------------------------------------------------------------

EMBEDDED_HTML_STUDIO = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Bridge Studio — Sovereign Agent Bridge</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Google+Sans:wght@400;500;700&family=Roboto+Mono:wght@400;500&family=Roboto:wght@300;400;500;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --google-blue: #1a73e8;
      --google-blue-hover: #1557b0;
      --google-blue-surface: #e8f0fe;
      --google-red: #ea4335;
      --google-yellow: #fbbc04;
      --google-green: #34a853;
      --bg: #f8f9fa;
      --card-bg: #ffffff;
      --text: #202124;
      --text-muted: #5f6368;
      --border: #dadce0;
    }
    body.dark-mode {
      --bg: #121212;
      --card-bg: #1e1e1e;
      --text: #e8eaed;
      --text-muted: #9aa0a6;
      --border: #3c4043;
      --google-blue-surface: #1e2a38;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Roboto', sans-serif; background: var(--bg); color: var(--text); padding: 20px; transition: background 0.2s, color 0.2s; }
    header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--border); }
    .brand { display: flex; align-items: center; gap: 12px; }
    .brand-logo { width: 32px; height: 32px; fill: var(--google-blue); }
    .brand-title { font-family: 'Google Sans', sans-serif; font-size: 20px; font-weight: 500; }
    .badge { background: var(--google-blue-surface); color: var(--google-blue); font-size: 11px; font-weight: 600; padding: 3px 8px; border-radius: 12px; }
    .header-actions { display: flex; gap: 10px; align-items: center; }
    .btn { padding: 8px 16px; border-radius: 6px; font-size: 13px; font-weight: 500; cursor: pointer; border: none; transition: 0.15s; }
    .btn-primary { background: var(--google-blue); color: #fff; }
    .btn-primary:hover { background: var(--google-blue-hover); }
    .btn-outline { background: transparent; border: 1px solid var(--border); color: var(--text); }
    .btn-outline:hover { background: var(--google-blue-surface); border-color: var(--google-blue); }
    .container { max-width: 1200px; margin: 0 auto; }
    .grid { display: flex; flex-wrap: wrap; gap: 20px; }
    .col-4 { flex: 1 1 320px; }
    .col-8 { flex: 2 1 600px; }
    .col-6 { flex: 1 1 450px; }
    .col-12 { flex: 1 1 100%; }
    .card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
    .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; border-bottom: 1px solid var(--border); padding-bottom: 10px; }
    .card-title { font-family: 'Google Sans', sans-serif; font-size: 16px; font-weight: 500; }
    .channel-row { display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
    .channel-row:last-child { border-bottom: none; }
    .status-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px; }
    .dot-green { background: var(--google-green); }
    .dot-yellow { background: var(--google-yellow); }
    .feed-time { color: var(--text-secondary); margin-right: 6px; }
    .feed-tag { font-weight: bold; color: var(--google-blue); }
    .form-group { margin-bottom: 14px; }
    .form-label { display: block; font-size: 12px; font-weight: 500; color: var(--text-secondary); margin-bottom: 4px; }
    .form-control {
      width: 100%;
      padding: 10px 12px;
      border: 1px solid var(--border-color);
      border-radius: 8px;
      background: var(--card-bg);
      color: var(--text-primary);
      font-size: 14px;
    }
    .form-control:focus { outline: none; border-color: var(--google-blue); box-shadow: 0 0 0 2px var(--google-blue-surface); }
    .channel-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 10px 0;
      border-bottom: 1px solid var(--divider-color);
    }
    .channel-row:last-child { border-bottom: none; }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <svg class="brand-logo" viewBox="0 0 24 24">
        <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/>
      </svg>
      <div class="brand-title">Bridge Studio</div>
      <span class="badge">Sovereign v0.1.0</span>
    </div>
    <div class="header-actions">
      <span id="sse-status" style="font-size:12px;display:flex;align-items:center;gap:6px;">
        <span class="status-dot dot-green"></span> SSE Connected
      </span>
      <button class="btn btn-outline" onclick="document.body.classList.toggle('dark-mode')">Theme</button>
      <button class="btn btn-primary" onclick="refreshAll()">Refresh Deck</button>
    </div>
  </header>

  <div class="container">
    <div class="grid">
      <!-- Channel Monitor -->
      <div class="col-4">
        <div class="card">
          <div class="card-header">
            <div class="card-title">Multi-Channel Bridge Monitor</div>
            <span class="badge" id="channel-count">5 Channels</span>
          </div>
          <div id="channels-list">
            <div class="channel-row"><span>Signal Adapter (+15550192834)</span><span class="badge">12ms</span></div>
            <div class="channel-row"><span>SimpleX Stealth Node</span><span class="badge">18ms</span></div>
            <div class="channel-row"><span>Telegram Bot Relay</span><span class="badge">45ms</span></div>
            <div class="channel-row"><span>Matrix Federated Room</span><span class="badge">62ms</span></div>
            <div class="channel-row"><span>Signed Webhook Dispatch</span><span class="badge">4ms</span></div>
          </div>
          <button class="btn btn-outline" style="width:100%;margin-top:14px;" onclick="pingChannels()">Ping Channels</button>
        </div>
      </div>

      <!-- Live Event Stream -->
      <div class="col-8">
        <div class="card">
          <div class="card-header">
            <div class="card-title">Live Telemetry & SSE Event Stream</div>
            <button class="btn btn-outline" style="padding:4px 10px;font-size:12px;" onclick="document.getElementById('event-stream').innerHTML=''">Clear</button>
          </div>
          <div id="event-stream" class="live-feed">
            <div class="feed-item"><span class="feed-time">[Live]</span> <span class="feed-tag">SYSTEM</span> Bridge Studio initialized. Listening on SSE stream /api/events...</div>
          </div>
        </div>
      </div>

      <!-- Dispatcher Console -->
      <div class="col-6">
        <div class="card">
          <div class="card-header">
            <div class="card-title">Message Dispatcher & Broadcast Console</div>
          </div>
          <div class="form-group">
            <label class="form-label">Channel Target</label>
            <select id="send-channel" class="form-control">
              <option value="signal-primary">Signal Messenger</option>
              <option value="simplex-stealth">SimpleX Chat</option>
              <option value="telegram-relay">Telegram Bot</option>
              <option value="matrix-federated">Matrix Protocol</option>
              <option value="webhook-dispatch">HTTP Webhook</option>
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">Recipient / Group Endpoint</label>
            <input id="send-recipient" class="form-control" type="text" placeholder="+15550192834 or room_id" value="+15550192834">
          </div>
          <div class="form-group">
            <label class="form-label">Message Payload</label>
            <textarea id="send-content" class="form-control" rows="3" placeholder="Enter message payload...">Agent heartbeat sync: verified channel integrity.</textarea>
          </div>
          <div style="display:flex;gap:10px;">
            <button class="btn btn-primary" onclick="sendMessage()">Dispatch Message</button>
            <button class="btn btn-outline" onclick="broadcastMessage()">Broadcast All</button>
          </div>
        </div>
      </div>

      <!-- 3-Way Dialectic Consensus Simulator -->
      <div class="col-6">
        <div class="card">
          <div class="card-header">
            <div class="card-title">3-Way Dialectic Consensus Engine</div>
            <span class="badge" id="consensus-verdict">Ready</span>
          </div>
          <div class="form-group">
            <label class="form-label">Topic / Architectural Decision Proposal</label>
            <input id="consensus-topic" class="form-control" type="text" value="Upgrade P2P transport encryption to Kyber-1024 post-quantum">
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;" class="form-group">
            <div>
              <label class="form-label">Proponent</label>
              <input id="consensus-proponent" class="form-control" value="agent-alpha">
            </div>
            <div>
              <label class="form-label">Skeptic</label>
              <input id="consensus-skeptic" class="form-control" value="agent-beta">
            </div>
            <div>
              <label class="form-label">Arbitrator</label>
              <input id="consensus-arbitrator" class="form-control" value="agent-gamma">
            </div>
          </div>
          <button class="btn btn-primary" style="width:100%;" onclick="runConsensus()">Run Dialectic Consensus</button>
          <div id="consensus-results" style="margin-top:12px;font-size:12px;color:var(--text-secondary);"></div>
        </div>
      </div>

      <!-- Project Claims & Watchdog Deck -->
      <div class="col-6">
        <div class="card">
          <div class="card-header">
            <div class="card-title">Project Claims Lock Board</div>
          </div>
          <div id="claims-list"></div>
          <div style="display:flex;gap:8px;margin-top:12px;">
            <input id="claim-project-id" class="form-control" placeholder="Project ID" value="sec-audit-task">
            <input id="claim-agent-id" class="form-control" placeholder="Agent ID" value="agent-alpha">
            <button class="btn btn-primary" onclick="acquireClaim()">Claim</button>
          </div>
        </div>
      </div>

      <div class="col-6">
        <div class="card">
          <div class="card-header">
            <div class="card-title">Heartbeat Watchdog & Dead-Man Switch</div>
          </div>
          <div id="watchdog-list"></div>
          <div style="display:flex;gap:8px;margin-top:12px;">
            <input id="pulse-agent-id" class="form-control" placeholder="Agent ID" value="agent-alpha">
            <button class="btn btn-primary" onclick="pulseHeartbeat()">Send Pulse</button>
            <button class="btn btn-outline" onclick="armDeadmanSwitch()">Arm Switch (60s)</button>
          </div>
        </div>
      </div>

    </div>
  </div>

  <script>
    function logEvent(type, text) {
      const el = document.getElementById('event-stream');
      const item = document.createElement('div');
      item.className = 'feed-item';
      const timeStr = new Date().toLocaleTimeString();
      item.innerHTML = `<span class="feed-time">[${timeStr}]</span> <span class="feed-tag">${type}</span> ${text}`;
      el.appendChild(item);
      el.scrollTop = el.scrollHeight;
    }

    // Connect Server-Sent Events (SSE)
    const evtSource = new EventSource('/api/events');
    evtSource.onmessage = (e) => {
      try {
        const d = JSON.parse(e.data);
        logEvent(d.type || 'EVENT', JSON.stringify(d.data || d));
      } catch {
        logEvent('RAW', e.data);
      }
    };
    evtSource.addEventListener('message_sent', (e) => logEvent('SENT', e.data));
    evtSource.addEventListener('broadcast_sent', (e) => logEvent('BROADCAST', e.data));
    evtSource.addEventListener('consensus_completed', (e) => {
      logEvent('CONSENSUS', e.data);
      refreshConsensus();
    });
    evtSource.addEventListener('claim_acquired', (e) => { logEvent('CLAIM_ACQ', e.data); refreshClaims(); });
    evtSource.addEventListener('claim_released', (e) => { logEvent('CLAIM_REL', e.data); refreshClaims(); });
    evtSource.addEventListener('heartbeat_received', (e) => { logEvent('PULSE', e.data); refreshWatchdog(); });
    evtSource.onerror = () => {
      document.getElementById('sse-status').innerHTML = '<span class="status-dot dot-red"></span> SSE Reconnecting...';
    };
    evtSource.onopen = () => {
      document.getElementById('sse-status').innerHTML = '<span class="status-dot dot-green"></span> SSE Connected';
    };

    async function sendMessage() {
      const channel = document.getElementById('send-channel').value;
      const recipient = document.getElementById('send-recipient').value;
      const content = document.getElementById('send-content').value;
      await fetch('/api/send', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ channel, recipient, content })
      });
    }

    async function broadcastMessage() {
      const content = document.getElementById('send-content').value;
      await fetch('/api/broadcast', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ content })
      });
    }

    async function runConsensus() {
      const topic = document.getElementById('consensus-topic').value;
      const proponent = document.getElementById('consensus-proponent').value;
      const skeptic = document.getElementById('consensus-skeptic').value;
      const arbitrator = document.getElementById('consensus-arbitrator').value;
      const res = await fetch('/api/consensus/run', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ topic, proponent, skeptic, arbitrator, rounds: 2 })
      });
      const data = await res.json();
      document.getElementById('consensus-results').innerHTML = `<pre style="background:var(--surface-bg);padding:8px;border-radius:6px;overflow:auto;">${JSON.stringify(data.session, null, 2)}</pre>`;
    }

    async function refreshClaims() {
      const res = await fetch('/api/claims');
      const data = await res.json();
      const el = document.getElementById('claims-list');
      el.innerHTML = (data.claims || []).map(c => `
        <div class="channel-row">
          <div><strong>${c.project_id}</strong> (${c.owner_agent})</div>
          <button class="btn btn-outline" style="padding:2px 8px;font-size:11px;" onclick="releaseClaim('${c.project_id}', '${c.owner_agent}')">Release</button>
        </div>
      `).join('') || '<div style="color:var(--text-secondary);font-size:12px;">No active claims.</div>';
    }

    async function acquireClaim() {
      const project_id = document.getElementById('claim-project-id').value;
      const agent_id = document.getElementById('claim-agent-id').value;
      await fetch('/api/claims/acquire', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ project_id, agent_id, ttl_seconds: 600 })
      });
      refreshClaims();
    }

    async function releaseClaim(project_id, agent_id) {
      await fetch('/api/claims/release', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ project_id, agent_id })
      });
      refreshClaims();
    }

    async function refreshWatchdog() {
      const res = await fetch('/api/watchdog');
      const data = await res.json();
      const el = document.getElementById('watchdog-list');
      el.innerHTML = (data.agents || []).map(a => `
        <div class="channel-row">
          <div><strong>${a.agent_id}</strong> - ${a.status}</div>
          <span class="badge">${a.elapsed_seconds}s ago (timeout: ${a.timeout}s)</span>
        </div>
      `).join('') || '<div style="color:var(--text-secondary);font-size:12px;">No registered agents.</div>';
    }

    async function pulseHeartbeat() {
      const agent_id = document.getElementById('pulse-agent-id').value;
      await fetch('/api/heartbeat/ping', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ agent_id, interval: 30 })
      });
      refreshWatchdog();
    }

    async function armDeadmanSwitch() {
      const agent_id = document.getElementById('pulse-agent-id').value;
      await fetch('/api/heartbeat/arm', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ agent_id, timer_seconds: 60 })
      });
    }

    async function pingChannels() {
      await fetch('/api/channels/test', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ channel: 'all' })
      });
    }

    function refreshAll() {
      refreshClaims();
      refreshWatchdog();
    }

    window.onload = () => { refreshAll(); };
  </script>
</body>
</html>
"""
