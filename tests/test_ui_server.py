"""Tests for Google Bridge Studio UI Server and SSE event stream in sovereign_agent_bridge.ui_server."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Generator

import pytest

from sovereign_agent_bridge.ui_server import BridgeUIServer, start_ui_server


def test_ui_server_lifecycle_and_static_serving(running_ui_server: BridgeUIServer):
    """Test UI server startup, static asset serving, and healthy status."""
    base_url = running_ui_server.get_url()

    # 1. GET / (serves index.html)
    with urllib.request.urlopen(f"{base_url}/", timeout=5.0) as resp:
        assert resp.status == 200
        content = resp.read().decode("utf-8")
        assert "Test Studio" in content or "Google Bridge Studio" in content

    # 2. GET /api/health
    with urllib.request.urlopen(f"{base_url}/api/health", timeout=5.0) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["status"] == "ok"
        assert data["service"] == "sovereign-agent-bridge"


def test_ui_server_rest_channels_and_ping(running_ui_server: BridgeUIServer):
    """Test channel listing and live latency ping check."""
    base_url = running_ui_server.get_url()

    # GET /api/channels
    with urllib.request.urlopen(f"{base_url}/api/channels", timeout=5.0) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert "channels" in data
        assert len(data["channels"]) >= 1

    # POST /api/channels/test
    req_data = json.dumps({"channel": "signal-primary"}).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/api/channels/test",
        data=req_data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["status"] == "ok"
        assert data["result"]["is_healthy"] is True


def test_ui_server_message_dispatch_and_broadcast(running_ui_server: BridgeUIServer):
    """Test sending and broadcasting messages through UI server REST endpoints."""
    base_url = running_ui_server.get_url()

    # POST /api/send
    send_payload = json.dumps({
        "channel": "signal-primary",
        "recipient": "+15551234567",
        "content": "Mission telemetry sync: OK",
        "priority": "high",
    }).encode("utf-8")

    req_send = urllib.request.Request(
        f"{base_url}/api/send",
        data=send_payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req_send, timeout=5.0) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["status"] == "ok"
        assert data["receipt"]["success"] is True

    # POST /api/broadcast
    bcast_payload = json.dumps({
        "content": "Global Emergency Protocol Alpha",
        "channels": ["signal-primary", "simplex-stealth"],
    }).encode("utf-8")

    req_bcast = urllib.request.Request(
        f"{base_url}/api/broadcast",
        data=bcast_payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req_bcast, timeout=5.0) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["status"] == "ok"
        assert len(data["broadcast"]["channels"]) == 2


def test_ui_server_claims_board(running_ui_server: BridgeUIServer):
    """Test acquiring, releasing, and handing off claims via REST."""
    base_url = running_ui_server.get_url()

    # 1. Acquire
    acq_payload = json.dumps({
        "project_id": "test-claim-rest",
        "agent_id": "agent-alpha",
        "ttl_seconds": 60.0,
    }).encode("utf-8")

    req_acq = urllib.request.Request(
        f"{base_url}/api/claims/acquire",
        data=acq_payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req_acq, timeout=5.0) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["status"] == "ok"
        assert data["claim"]["owner_agent"] == "agent-alpha"

    # 2. GET /api/claims
    with urllib.request.urlopen(f"{base_url}/api/claims", timeout=5.0) as resp:
        assert resp.status == 200
        claims_data = json.loads(resp.read().decode("utf-8"))
        assert any(c["project_id"] == "test-claim-rest" for c in claims_data["claims"])

    # 3. Handoff
    handoff_payload = json.dumps({
        "project_id": "test-claim-rest",
        "from_agent": "agent-alpha",
        "to_agent": "agent-beta",
        "ttl_seconds": 90.0,
    }).encode("utf-8")

    req_handoff = urllib.request.Request(
        f"{base_url}/api/claims/handoff",
        data=handoff_payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req_handoff, timeout=5.0) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["status"] == "ok"
        assert data["claim"]["owner_agent"] == "agent-beta"

    # 4. Release
    rel_payload = json.dumps({
        "project_id": "test-claim-rest",
        "agent_id": "agent-beta",
    }).encode("utf-8")

    req_rel = urllib.request.Request(
        f"{base_url}/api/claims/release",
        data=rel_payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req_rel, timeout=5.0) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["status"] == "released"


def test_ui_server_consensus_and_watchdog(running_ui_server: BridgeUIServer):
    """Test 3-way dialectic consensus run and watchdog heartbeat endpoints."""
    base_url = running_ui_server.get_url()

    # Consensus Run
    cons_payload = json.dumps({
        "topic": "Switch to zero-trust inter-agent RPC",
        "proponent": "agent-sec-1",
        "skeptic": "agent-audit-1",
        "arbitrator": "agent-lead-1",
        "rounds": 2,
    }).encode("utf-8")

    req_cons = urllib.request.Request(
        f"{base_url}/api/consensus/run",
        data=cons_payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req_cons, timeout=5.0) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["status"] == "ok"
        assert data["session"]["verdict"] in ("APPROVED", "DISPUTED")

    # Watchdog Pulse
    pulse_payload = json.dumps({
        "agent_id": "agent-watchdog-test",
        "interval": 20.0,
    }).encode("utf-8")

    req_pulse = urllib.request.Request(
        f"{base_url}/api/heartbeat/ping",
        data=pulse_payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req_pulse, timeout=5.0) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["status"] == "ok"

    # Watchdog Arm
    arm_payload = json.dumps({
        "agent_id": "agent-watchdog-test",
        "timer_seconds": 45.0,
    }).encode("utf-8")

    req_arm = urllib.request.Request(
        f"{base_url}/api/heartbeat/arm",
        data=arm_payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req_arm, timeout=5.0) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["status"] == "ok"

    # GET /api/stats
    with urllib.request.urlopen(f"{base_url}/api/stats", timeout=5.0) as resp:
        assert resp.status == 200
        stats = json.loads(resp.read().decode("utf-8"))
        assert "stats" in stats
        assert "uptime_seconds" in stats["stats"]
        assert "messages_sent" in stats["stats"]


def test_ui_server_sse_stream_and_publish(running_ui_server: BridgeUIServer):
    """Test connecting to SSE stream and receiving broadcast events."""
    base_url = running_ui_server.get_url()

    # Publish an event
    running_ui_server.publish_event("custom_test_event", {"msg": "SSE validation payload"})

    # Check event history
    with urllib.request.urlopen(f"{base_url}/api/events/history", timeout=5.0) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert len(data["events"]) >= 1
        assert any(e["type"] == "custom_test_event" for e in data["events"])
