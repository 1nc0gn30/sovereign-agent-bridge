"""Pytest test configuration and fixtures for Sovereign Agent Bridge."""

from __future__ import annotations

import os
import socket
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Generator

import pytest

from sovereign_agent_bridge.channels.base import (
    ChannelAttachment,
    ChannelMessage,
    ChannelType,
    DeliveryReceipt,
)
from sovereign_agent_bridge.compat import ensure_dir
from sovereign_agent_bridge.consensus import (
    ConsensusEngine,
    CritiquePoint,
    CritiqueSeverity,
)
from sovereign_agent_bridge.mcp_server import FallbackClaimManager, MCPServer
from sovereign_agent_bridge.router import SwarmMessage, SwarmRouter
from sovereign_agent_bridge.ui_server import BridgeUIServer
from sovereign_agent_bridge.watchdog import HeartbeatWatchdog


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    """Provide a dedicated temporary directory for test storage."""
    ws = tmp_path / "sovereign_test_workspace"
    return ensure_dir(ws)


@pytest.fixture
def sample_attachment() -> ChannelAttachment:
    """Create a sample channel attachment."""
    return ChannelAttachment(
        name="test_payload.json",
        content_type="application/json",
        data=b'{"status": "ok", "agent": "test-agent"}',
    )


@pytest.fixture
def sample_message(sample_attachment: ChannelAttachment) -> ChannelMessage:
    """Create a sample channel message."""
    return ChannelMessage(
        message_id="msg-test-12345",
        channel_name="signal-test",
        channel_type=ChannelType.SIGNAL.value,
        sender_id="+15550000001",
        recipient_id="+15550000002",
        content="Hello from test suite!",
        attachments=[sample_attachment],
        metadata={"priority": "high", "env": "pytest"},
    )


@pytest.fixture
def watchdog_engine(temp_workspace: Path) -> Generator[HeartbeatWatchdog, None, None]:
    """Provide an isolated HeartbeatWatchdog engine."""
    storage = temp_workspace / "heartbeats.json"
    alerts = temp_workspace / "alerts.jsonl"
    wd = HeartbeatWatchdog(
        check_interval=0.5,
        storage_path=storage,
        alert_log_path=alerts,
        auto_save=True,
    )
    yield wd
    if wd.is_running():
        wd.stop()


@pytest.fixture
def claim_manager() -> FallbackClaimManager:
    """Provide a claim mutex lock manager."""
    return FallbackClaimManager()


@pytest.fixture
def consensus_engine() -> ConsensusEngine:
    """Provide a 3-way dialectic consensus engine."""
    return ConsensusEngine()


@pytest.fixture
def swarm_router(temp_workspace: Path) -> SwarmRouter:
    """Provide an initialized SwarmRouter."""
    journal = temp_workspace / "router_journal.jsonl"
    return SwarmRouter(journal_path=journal, ring_buffer_capacity=500)


@pytest.fixture
def mcp_server() -> MCPServer:
    """Provide an MCP server instance."""
    return MCPServer()


def find_free_port() -> int:
    """Find an available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def running_ui_server(
    temp_workspace: Path,
    swarm_router: SwarmRouter,
    watchdog_engine: HeartbeatWatchdog,
) -> Generator[BridgeUIServer, None, None]:
    """Start and yield a running BridgeUIServer on an ephemeral free port."""
    port = find_free_port()
    public_dir = temp_workspace / "public"
    public_dir.mkdir(parents=True, exist_ok=True)
    index_file = public_dir / "index.html"
    index_file.write_text("<!DOCTYPE html><html><body><h1>Test Studio</h1></body></html>", encoding="utf-8")

    server = BridgeUIServer(
        host="127.0.0.1",
        port=port,
        router=swarm_router,
        watchdog=watchdog_engine,
        static_dir=public_dir,
    )
    server.start(blocking=False)
    time.sleep(0.1)  # Brief warm up

    yield server

    server.stop()
